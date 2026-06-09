"""OAuth orchestration: authorize-URL building, code exchange, and token refresh.

* :class:`AuthFlow` builds provider authorize URLs (with state + optional PKCE)
  and exchanges callback codes for tokens, persisting them.
* :class:`TokenManager` returns a valid access token for a provider, refreshing
  lazily when the current token is near expiry. A per-provider lock prevents
  concurrent refreshes from racing; Withings' rotated refresh token is persisted.

The web layer owns binding ``state`` to the user session; this module only
generates and consumes the cryptographic material.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from .config import Settings
from .providers.base import HealthProvider
from .storage import Store
from .timeutil import now_utc

# Refresh when the access token expires within this many seconds.
REFRESH_SKEW_SECONDS = 120


def _pkce_pair() -> tuple[str, str]:
    """Return a ``(code_verifier, code_challenge)`` PKCE S256 pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


@dataclass
class AuthStart:
    """Material needed to begin an OAuth flow.

    Attributes:
        authorize_url: Fully-built URL to redirect the user to.
        state: CSRF/anti-injection state to store in the session.
        code_verifier: PKCE verifier to store in the session (empty if no PKCE).
    """

    authorize_url: str
    state: str
    code_verifier: str


class AuthFlow:
    """Builds authorize URLs and exchanges authorization codes for tokens."""

    def __init__(self, settings: Settings, store: Store, providers: dict[str, HealthProvider]):
        self._settings = settings
        self._store = store
        self._providers = providers

    async def start(self, provider_name: str) -> AuthStart:
        """Build the authorize URL and CSRF/PKCE material for ``provider_name``.

        Raises:
            KeyError: If the provider is unknown.
            RuntimeError: If no client credentials are configured.
        """
        provider = self._providers[provider_name]
        creds = await self._store.resolve_credentials(provider_name)
        if creds is None:
            raise RuntimeError(
                f"No client credentials configured for {provider_name!r}. "
                "Add them on the providers page."
            )
        client_id, _secret = creds
        cfg = provider.oauth
        state = secrets.token_urlsafe(32)
        verifier = ""
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self._settings.redirect_uri(provider_name),
            "scope": cfg.scope_separator.join(cfg.scopes),
            "state": state,
        }
        params.update(cfg.extra_authorize_params)
        if cfg.use_pkce:
            verifier, challenge = _pkce_pair()
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        url = f"{cfg.authorize_url}?{urlencode(params)}"
        return AuthStart(authorize_url=url, state=state, code_verifier=verifier)

    async def finish(self, provider_name: str, code: str, code_verifier: str | None) -> None:
        """Exchange ``code`` for tokens and persist them; mark provider connected.

        Raises:
            RuntimeError: If no client credentials are configured.
        """
        provider = self._providers[provider_name]
        creds = await self._store.resolve_credentials(provider_name)
        if creds is None:
            raise RuntimeError(f"No client credentials configured for {provider_name!r}.")
        client_id, client_secret = creds
        token = await provider.exchange_code(
            code=code,
            redirect_uri=self._settings.redirect_uri(provider_name),
            client_id=client_id,
            client_secret=client_secret,
            code_verifier=code_verifier or None,
        )
        await self._store.save_token(token)
        await self._store.set_status(provider_name, connected=True, clear_error=True)


class TokenManager:
    """Supplies valid access tokens, refreshing lazily and safely."""

    def __init__(self, store: Store, providers: dict[str, HealthProvider]):
        self._store = store
        self._providers = providers
        self._locks: dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in providers}

    async def get_access_token(self, provider_name: str) -> str | None:
        """Return a currently-valid access token for ``provider_name`` or None.

        Returns ``None`` when the provider isn't connected or a refresh failed
        (in which case the provider is marked disconnected with an error).
        """
        if provider_name not in self._providers:
            return None
        token = await self._store.get_token(provider_name)
        if token is None or not token.access_token:
            return None
        if not self._needs_refresh(token):
            return token.access_token

        async with self._locks[provider_name]:
            # Re-read inside the lock; another task may have refreshed already.
            token = await self._store.get_token(provider_name)
            if token is None:
                return None
            if not self._needs_refresh(token):
                return token.access_token
            return await self._refresh(provider_name, token)

    def _needs_refresh(self, token) -> bool:
        if token.expires_at is None:
            return False
        remaining = (token.expires_at - now_utc()).total_seconds()
        return remaining <= REFRESH_SKEW_SECONDS

    async def _refresh(self, provider_name: str, token) -> str | None:
        if not token.refresh_token:
            await self._store.set_status(
                provider_name, connected=False, last_error="No refresh token; reconnect."
            )
            return None
        creds = await self._store.resolve_credentials(provider_name)
        if creds is None:
            await self._store.set_status(
                provider_name, connected=False, last_error="Missing client credentials."
            )
            return None
        client_id, client_secret = creds
        provider = self._providers[provider_name]
        try:
            new = await provider.refresh(token.refresh_token, client_id, client_secret)
        except Exception as exc:  # noqa: BLE001 - surface as reconnect prompt
            await self._store.set_status(
                provider_name, connected=False, last_error=f"Token refresh failed: {exc}"
            )
            return None
        # Preserve identity if the refresh response omitted it.
        if new.provider_user_id is None:
            new.provider_user_id = token.provider_user_id
        await self._store.save_token(new)
        await self._store.set_status(provider_name, connected=True, clear_error=True)
        return new.access_token
