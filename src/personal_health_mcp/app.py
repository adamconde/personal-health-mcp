"""Application context wiring.

Builds and holds the shared singletons (settings, encryption, store, providers,
token manager, aggregator, auth flow) used by both the MCP tool layer and the
web UI. Centralizing construction here keeps ``server.py`` thin and makes the
whole graph easy to build in tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .aggregator import Aggregator
from .config import Settings, get_settings
from .crypto import Crypto
from .oauth import AuthFlow, TokenManager
from .providers import build_providers
from .providers.base import HealthProvider
from .storage import Store


@dataclass
class AppContext:
    """Container for the app's shared services."""

    settings: Settings
    crypto: Crypto
    store: Store
    providers: dict[str, HealthProvider]
    token_manager: TokenManager
    aggregator: Aggregator
    auth_flow: AuthFlow
    _password_hasher: PasswordHasher
    _password_hash: str | None

    def verify_password(self, password: str) -> bool:
        """Return True if ``password`` matches the configured UI password.

        Always returns ``False`` when no password is configured (login disabled).
        """
        if not self._password_hash:
            return False
        try:
            return self._password_hasher.verify(self._password_hash, password)
        except VerifyMismatchError:
            return False

    async def startup(self) -> None:
        """Initialize persistence (create tables)."""
        await self.store.init_models()

    async def shutdown(self) -> None:
        """Dispose persistence resources."""
        await self.store.dispose()


def create_context(
    settings: Settings | None = None,
    *,
    database_url: str | None = None,
) -> AppContext:
    """Construct a fully-wired :class:`AppContext`.

    Args:
        settings: Optional settings override (defaults to env-derived singleton).
        database_url: Optional SQLAlchemy URL override (tests use a temp DB).

    Returns:
        A wired :class:`AppContext` (call :meth:`AppContext.startup` before use).
    """
    settings = settings or get_settings()
    crypto = Crypto.from_env_value(settings.token_enc_key)
    store = Store(settings, crypto, database_url=database_url)
    providers = build_providers()
    token_manager = TokenManager(store, providers)
    aggregator = Aggregator(
        store,
        providers,
        token_manager.get_access_token,
        force_refresh=token_manager.force_refresh,
    )
    auth_flow = AuthFlow(settings, store, providers)
    hasher = PasswordHasher()
    password_hash = hasher.hash(settings.web_password) if settings.web_password else None
    return AppContext(
        settings=settings,
        crypto=crypto,
        store=store,
        providers=providers,
        token_manager=token_manager,
        aggregator=aggregator,
        auth_flow=auth_flow,
        _password_hasher=hasher,
        _password_hash=password_hash,
    )
