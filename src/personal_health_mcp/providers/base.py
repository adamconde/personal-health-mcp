"""Provider abstraction and registry.

A :class:`HealthProvider` knows how to (a) describe which canonical metrics it
supplies, (b) run the OAuth token exchange/refresh for its vendor, and (c) fetch
a metric over a date range and map the raw response into canonical
:class:`~personal_health_mcp.models.DataPoint`s.

Standard OAuth2 token handling lives in the base class; vendors with quirks
(Withings) override :meth:`exchange_code` / :meth:`refresh`. Providers register
themselves with :func:`register`, so the rest of the app only ever sees the
registry — adding a vendor is a new module, nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar

import httpx

from ..models import Token
from ..timeutil import now_utc

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class OAuthConfig:
    """Per-provider OAuth endpoints and behaviour.

    Attributes:
        authorize_url: Authorization endpoint (user is redirected here).
        token_url: Token endpoint (code exchange / refresh).
        scopes: Scopes to request.
        scope_separator: Character joining scopes in the authorize URL.
        use_pkce: Whether to use PKCE (S256).
        extra_authorize_params: Extra static query params (e.g. Google's
            ``access_type=offline`` & ``prompt=consent``).
    """

    authorize_url: str
    token_url: str
    scopes: list[str]
    scope_separator: str = " "
    use_pkce: bool = False
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapability:
    """Declares that a provider supplies a metric, and in which native units.

    Attributes:
        metric: Canonical metric key.
        native_units: Units the provider can deliver. The first entry is what
            :meth:`HealthProvider.fetch_metric` returns (always a canonical or
            convertible unit). Informational for the UI; conversion to the user's
            display unit always happens at the aggregation edge.
    """

    metric: str
    native_units: list[str]


class ProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a request (auth/HTTP/mapping)."""


# Populated by the @register decorator.
PROVIDER_REGISTRY: dict[str, type[HealthProvider]] = {}


def register(cls: type[HealthProvider]) -> type[HealthProvider]:
    """Class decorator registering a provider by its ``name``."""
    if not getattr(cls, "name", ""):
        raise ValueError(f"{cls.__name__} must set a 'name' class attribute")
    PROVIDER_REGISTRY[cls.name] = cls
    return cls


class HealthProvider(ABC):
    """Base class for a health-data vendor integration."""

    name: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    oauth: ClassVar[OAuthConfig]

    # ── capability declaration ───────────────────────────────────────────
    @abstractmethod
    def capabilities(self) -> list[ProviderCapability]:
        """Return the metrics (and native units) this provider supplies."""

    def supports(self, metric: str) -> bool:
        """Return True if this provider supplies ``metric``."""
        return any(c.metric == metric for c in self.capabilities())

    def native_units_for(self, metric: str) -> list[str]:
        """Return the native units for ``metric`` (empty if unsupported)."""
        for c in self.capabilities():
            if c.metric == metric:
                return c.native_units
        return []

    def supported_metrics(self) -> list[str]:
        """Return the sorted list of supported metric keys."""
        return sorted(c.metric for c in self.capabilities())

    # ── data fetch ───────────────────────────────────────────────────────
    @abstractmethod
    async def fetch_metric(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        access_token: str,
        native_unit: str | None = None,
    ) -> list:
        """Fetch ``metric`` over ``[start, end]`` and return canonical DataPoints.

        Args:
            metric: Canonical metric key (guaranteed supported by this provider).
            start: Inclusive window start (UTC).
            end: Inclusive window end (UTC).
            access_token: A valid access token (refresh handled upstream).
            native_unit: Optional hint for vendors that can serve a unit natively;
                the returned points are still in the metric's canonical unit.

        Returns:
            A list of :class:`~personal_health_mcp.models.DataPoint`.

        Raises:
            ProviderError: On unrecoverable API/mapping failures.
        """

    # ── OAuth: standard implementations (override per quirky vendor) ──────
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str | None = None,
    ) -> Token:
        """Exchange an authorization code for tokens (standard OAuth2)."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        payload = await self._token_request(data)
        return self._token_from_payload(payload)

    async def refresh(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> Token:
        """Refresh an access token (standard OAuth2 refresh grant)."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        payload = await self._token_request(data)
        token = self._token_from_payload(payload)
        # Many providers omit a new refresh token on refresh; keep the old one.
        if token.refresh_token is None:
            token.refresh_token = refresh_token
        return token

    # ── shared helpers ───────────────────────────────────────────────────
    async def _token_request(self, data: dict[str, str]) -> dict:
        """POST form-encoded ``data`` to the token endpoint and return JSON."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(self.oauth.token_url, data=data)
            resp.raise_for_status()
            return resp.json()

    def _token_from_payload(self, payload: dict) -> Token:
        """Build a :class:`Token` from a standard OAuth2 token response."""
        expires_at = None
        if payload.get("expires_in") is not None:
            expires_at = now_utc() + timedelta(seconds=float(payload["expires_in"]))
        scope = payload.get("scope", "")
        scopes = scope.split() if isinstance(scope, str) else list(scope or [])
        return Token(
            provider=self.name,
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
            scopes=scopes,
        )
