"""Persistence layer.

A single SQLite database (async, WAL) holds provider OAuth credentials, tokens,
connection status, and user preferences. OAuth tokens and client secrets are
encrypted at rest via :class:`~personal_health_mcp.crypto.Crypto`.

The :class:`Store` is the only object the rest of the app uses to read/write
state, which keeps encryption and the env-fallback credential rule in one place.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import Settings
from .crypto import Crypto
from .metrics import PREF_GROUPS, metric_keys
from .models import MetricPref, ResolutionMode, Token
from .timeutil import now_utc


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class ProviderCredential(Base):
    """Per-provider OAuth client credentials (client_secret encrypted)."""

    __tablename__ = "provider_credentials"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(512), default="")
    client_secret_enc: Mapped[str | None] = mapped_column(Text, default=None)
    scopes_override: Mapped[str | None] = mapped_column(Text, default=None)
    updated_at: Mapped[str | None] = mapped_column(String(40), default=None)


class OAuthToken(Base):
    """Per-provider OAuth tokens (access & refresh encrypted)."""

    __tablename__ = "oauth_tokens"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, default=None)
    expires_at: Mapped[str | None] = mapped_column(String(40), default=None)
    scopes: Mapped[str] = mapped_column(Text, default="[]")
    provider_user_id: Mapped[str | None] = mapped_column(String(128), default=None)


class ProviderStatus(Base):
    """Connection status / diagnostics per provider."""

    __tablename__ = "provider_status"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    connected: Mapped[bool] = mapped_column(default=False)
    last_sync: Mapped[str | None] = mapped_column(String(40), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class MetricPrefRow(Base):
    """Per-metric resolution preference."""

    __tablename__ = "metric_prefs"

    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default=ResolutionMode.AUTO.value)
    authority_provider: Mapped[str | None] = mapped_column(String(32), default=None)
    fallback_order: Mapped[str] = mapped_column(Text, default="[]")


class UnitPrefRow(Base):
    """Per-preference-group display unit choice."""

    __tablename__ = "unit_prefs"

    pref_group: Mapped[str] = mapped_column(String(32), primary_key=True)
    unit: Mapped[str] = mapped_column(String(16))


class AppMeta(Base):
    """Misc key/value app metadata (schema version, password hash, etc.)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return now_utc().isoformat()


class Store:
    """Async data-access object wrapping the SQLite database + encryption.

    Args:
        settings: Application settings (for the DB path and env credential fallback).
        crypto: Encryption helper.
        database_url: Optional SQLAlchemy URL override (tests use in-memory).
    """

    def __init__(
        self,
        settings: Settings,
        crypto: Crypto,
        database_url: str | None = None,
    ) -> None:
        self._settings = settings
        self._crypto = crypto
        # Only manage the on-disk directory when the URL is derived from
        # settings.database_path; an explicit database_url (e.g. tests) owns its
        # own location, so we must not mkdir settings.database_path's parent.
        self._manage_db_dir = database_url is None
        url = database_url or f"sqlite+aiosqlite:///{settings.database_path}"
        self._engine = create_async_engine(url, future=True)
        self._session: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    # ── lifecycle ────────────────────────────────────────────────────────
    async def init_models(self) -> None:
        """Create tables if absent and enable WAL journaling."""
        path = self._settings.database_path
        if self._manage_db_dir and path and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        async with self._engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        """Dispose the engine connection pool."""
        await self._engine.dispose()

    # ── provider credentials ─────────────────────────────────────────────
    async def set_credentials(
        self,
        provider: str,
        client_id: str,
        client_secret: str | None,
        scopes_override: str | None = None,
    ) -> None:
        """Upsert a provider's OAuth client credentials (secret encrypted).

        A ``None`` ``client_secret`` leaves the stored secret unchanged, so the
        write-only UI field can update the id without re-entering the secret.
        """
        async with self._session() as s, s.begin():
            row = await s.get(ProviderCredential, provider)
            if row is None:
                row = ProviderCredential(provider=provider)
                s.add(row)
            row.client_id = client_id
            if client_secret is not None:
                row.client_secret_enc = self._crypto.encrypt(client_secret)
            if scopes_override is not None:
                row.scopes_override = scopes_override
            row.updated_at = _now_iso()

    async def get_credentials(self, provider: str) -> tuple[str, str | None] | None:
        """Return decrypted ``(client_id, client_secret)`` stored in the DB, or None."""
        async with self._session() as s:
            row = await s.get(ProviderCredential, provider)
            if row is None or not row.client_id:
                return None
            return row.client_id, self._crypto.decrypt(row.client_secret_enc)

    async def has_secret(self, provider: str) -> bool:
        """Return True if a non-empty client secret is stored (UI ``configured`` flag)."""
        async with self._session() as s:
            row = await s.get(ProviderCredential, provider)
            return bool(row and row.client_secret_enc)

    async def resolve_credentials(self, provider: str) -> tuple[str, str] | None:
        """Resolve usable credentials: DB first, then env fallback.

        Returns:
            ``(client_id, client_secret)`` if both are available, else ``None``.
        """
        db = await self.get_credentials(provider)
        if db and db[0] and db[1]:
            return db[0], db[1]
        cid, secret = self._settings.env_credentials(provider)
        if cid and secret:
            return cid, secret
        return None

    # ── tokens ───────────────────────────────────────────────────────────
    async def save_token(self, token: Token) -> None:
        """Encrypt and upsert a provider's token set."""
        async with self._session() as s, s.begin():
            row = await s.get(OAuthToken, token.provider)
            if row is None:
                row = OAuthToken(provider=token.provider, access_token_enc="")
                s.add(row)
            row.access_token_enc = self._crypto.encrypt(token.access_token)  # type: ignore[assignment]
            row.refresh_token_enc = self._crypto.encrypt(token.refresh_token)
            row.expires_at = token.expires_at.isoformat() if token.expires_at else None
            row.scopes = json.dumps(token.scopes)
            row.provider_user_id = token.provider_user_id

    async def get_token(self, provider: str) -> Token | None:
        """Return the decrypted token set for ``provider``, or None."""
        async with self._session() as s:
            row = await s.get(OAuthToken, provider)
            if row is None:
                return None
            return Token(
                provider=provider,
                access_token=self._crypto.decrypt(row.access_token_enc) or "",
                refresh_token=self._crypto.decrypt(row.refresh_token_enc),
                expires_at=datetime.fromisoformat(row.expires_at) if row.expires_at else None,
                scopes=json.loads(row.scopes or "[]"),
                provider_user_id=row.provider_user_id,
            )

    async def delete_token(self, provider: str) -> None:
        """Remove a provider's stored token set."""
        async with self._session() as s, s.begin():
            row = await s.get(OAuthToken, provider)
            if row is not None:
                await s.delete(row)

    # ── provider status ──────────────────────────────────────────────────
    async def set_status(
        self,
        provider: str,
        *,
        connected: bool | None = None,
        last_sync: str | None = None,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        """Upsert provider connection status / diagnostics."""
        async with self._session() as s, s.begin():
            row = await s.get(ProviderStatus, provider)
            if row is None:
                row = ProviderStatus(provider=provider)
                s.add(row)
            if connected is not None:
                row.connected = connected
            if last_sync is not None:
                row.last_sync = last_sync
            if clear_error:
                row.last_error = None
            elif last_error is not None:
                row.last_error = last_error

    async def get_status(self, provider: str) -> ProviderStatus | None:
        """Return the status row for ``provider``, or None."""
        async with self._session() as s:
            return await s.get(ProviderStatus, provider)

    # ── metric preferences ───────────────────────────────────────────────
    async def get_metric_pref(self, metric: str) -> MetricPref:
        """Return the stored preference for ``metric``, or an AUTO default."""
        async with self._session() as s:
            row = await s.get(MetricPrefRow, metric)
            if row is None:
                return MetricPref(metric=metric, mode=ResolutionMode.AUTO)
            return MetricPref(
                metric=metric,
                mode=ResolutionMode(row.mode),
                authority=row.authority_provider,
                fallback_order=json.loads(row.fallback_order or "[]"),
            )

    async def all_metric_prefs(self) -> dict[str, MetricPref]:
        """Return preferences for every registered metric (defaults filled in)."""
        async with self._session() as s:
            rows = (await s.execute(select(MetricPrefRow))).scalars().all()
        stored = {
            r.metric: MetricPref(
                metric=r.metric,
                mode=ResolutionMode(r.mode),
                authority=r.authority_provider,
                fallback_order=json.loads(r.fallback_order or "[]"),
            )
            for r in rows
        }
        return {
            key: stored.get(key, MetricPref(metric=key, mode=ResolutionMode.AUTO))
            for key in metric_keys()
        }

    async def set_metric_pref(self, pref: MetricPref) -> None:
        """Upsert a metric resolution preference."""
        async with self._session() as s, s.begin():
            row = await s.get(MetricPrefRow, pref.metric)
            if row is None:
                row = MetricPrefRow(metric=pref.metric)
                s.add(row)
            row.mode = pref.mode.value
            row.authority_provider = pref.authority
            row.fallback_order = json.dumps(pref.fallback_order)

    # ── unit preferences ─────────────────────────────────────────────────
    async def get_unit_prefs(self) -> dict[str, str]:
        """Return preference-group -> unit, with defaults for unset groups."""
        async with self._session() as s:
            rows = (await s.execute(select(UnitPrefRow))).scalars().all()
        stored = {r.pref_group: r.unit for r in rows}
        return {
            group: stored.get(group, str(spec["default"]))
            for group, spec in PREF_GROUPS.items()
        }

    async def set_unit_pref(self, pref_group: str, unit: str) -> None:
        """Upsert a display-unit choice for a preference group."""
        async with self._session() as s, s.begin():
            row = await s.get(UnitPrefRow, pref_group)
            if row is None:
                row = UnitPrefRow(pref_group=pref_group, unit=unit)
                s.add(row)
            else:
                row.unit = unit

    # ── app metadata ─────────────────────────────────────────────────────
    async def get_meta(self, key: str) -> str | None:
        """Return an app-meta value, or None."""
        async with self._session() as s:
            row = await s.get(AppMeta, key)
            return row.value if row else None

    async def set_meta(self, key: str, value: str) -> None:
        """Upsert an app-meta key/value."""
        async with self._session() as s, s.begin():
            row = await s.get(AppMeta, key)
            if row is None:
                row = AppMeta(key=key, value=value)
                s.add(row)
            else:
                row.value = value
