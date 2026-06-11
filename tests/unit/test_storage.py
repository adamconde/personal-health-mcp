"""Tests for the persistence layer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from personal_health_mcp.config import Settings
from personal_health_mcp.crypto import Crypto
from personal_health_mcp.models import MetricPref, ResolutionMode, Token
from personal_health_mcp.storage import OAuthToken, ProviderCredential, Store

pytestmark = pytest.mark.asyncio


async def test_token_roundtrip_and_encryption(store: Store, crypto: Crypto):
    token = Token(
        provider="oura",
        access_token="acc-123",
        refresh_token="ref-456",
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        scopes=["daily", "heartrate"],
        provider_user_id="u1",
    )
    await store.save_token(token)

    got = await store.get_token("oura")
    assert got is not None
    assert got.access_token == "acc-123"
    assert got.refresh_token == "ref-456"
    assert got.scopes == ["daily", "heartrate"]

    # Stored ciphertext must not equal plaintext.
    async with store._session() as s:  # noqa: SLF001 - white-box check
        row = await s.get(OAuthToken, "oura")
        assert row.access_token_enc != "acc-123"
        assert crypto.decrypt(row.access_token_enc) == "acc-123"


async def test_token_rotation_persists_new_refresh(store: Store):
    await store.save_token(Token(provider="withings", access_token="a1", refresh_token="r1"))
    await store.save_token(Token(provider="withings", access_token="a2", refresh_token="r2"))
    got = await store.get_token("withings")
    assert got.access_token == "a2"
    assert got.refresh_token == "r2"


async def test_delete_token(store: Store):
    await store.save_token(Token(provider="oura", access_token="x"))
    await store.delete_token("oura")
    assert await store.get_token("oura") is None


async def test_credentials_secret_encrypted_and_update_without_secret(store: Store, crypto: Crypto):
    await store.set_credentials("oura", "client-id-1", "client-secret-1")
    assert await store.has_secret("oura")
    creds = await store.get_credentials("oura")
    assert creds == ("client-id-1", "client-secret-1")

    async with store._session() as s:  # noqa: SLF001
        row = await s.get(ProviderCredential, "oura")
        assert row.client_secret_enc != "client-secret-1"

    # Updating with client_secret=None keeps the stored secret.
    await store.set_credentials("oura", "client-id-2", None)
    creds = await store.get_credentials("oura")
    assert creds == ("client-id-2", "client-secret-1")


async def test_init_models_with_url_override_does_not_create_settings_dir(
    crypto: Crypto, tmp_path, enc_key: str
):
    # An explicit database_url owns its location; init_models must NOT try to
    # create settings.database_path's parent (which on CI is an unwritable /data).
    unwritable = tmp_path / "should-not-be-created" / "health.db"
    settings = Settings(_env_file=None, token_enc_key=enc_key, database_path=str(unwritable))
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'real.db'}"
    s = Store(settings=settings, crypto=crypto, database_url=db_url)
    await s.init_models()
    try:
        assert not (tmp_path / "should-not-be-created").exists()
    finally:
        await s.dispose()


async def test_resolve_credentials_prefers_db_then_env(crypto: Crypto, tmp_path, enc_key: str):
    settings = Settings(
        _env_file=None,
        token_enc_key=enc_key,
        oura_client_id="env-id",
        oura_client_secret="env-secret",
    )
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'creds.db'}"
    s = Store(settings=settings, crypto=crypto, database_url=db_url)
    await s.init_models()
    try:
        # No DB creds -> env fallback.
        assert await s.resolve_credentials("oura") == ("env-id", "env-secret")
        # DB creds win.
        await s.set_credentials("oura", "db-id", "db-secret")
        assert await s.resolve_credentials("oura") == ("db-id", "db-secret")
        # Provider with neither -> None.
        assert await s.resolve_credentials("google") is None
    finally:
        await s.dispose()


async def test_metric_pref_defaults_and_upsert(store: Store):
    # Default is AUTO.
    assert (await store.get_metric_pref("weight")).mode == ResolutionMode.AUTO
    await store.set_metric_pref(
        MetricPref(
            metric="weight",
            mode=ResolutionMode.AUTHORITY,
            authority="withings",
            fallback_order=["google", "oura"],
        )
    )
    pref = await store.get_metric_pref("weight")
    assert pref.mode == ResolutionMode.AUTHORITY
    assert pref.authority == "withings"
    assert pref.fallback_order == ["google", "oura"]

    all_prefs = await store.all_metric_prefs()
    assert "steps" in all_prefs  # defaults filled for every metric
    assert all_prefs["weight"].authority == "withings"


async def test_unit_prefs_defaults_and_upsert(store: Store):
    prefs = await store.get_unit_prefs()
    assert prefs["mass"] == "lb"
    assert prefs["temperature"] == "F"
    await store.set_unit_pref("mass", "kg")
    assert (await store.get_unit_prefs())["mass"] == "kg"


async def test_status_upsert(store: Store):
    await store.set_status("oura", connected=True, last_sync="2030-01-01")
    row = await store.get_status("oura")
    assert row.connected is True
    await store.set_status("oura", last_error="boom")
    assert (await store.get_status("oura")).last_error == "boom"
    await store.set_status("oura", clear_error=True)
    assert (await store.get_status("oura")).last_error is None


async def test_app_meta(store: Store):
    assert await store.get_meta("schema_version") is None
    await store.set_meta("schema_version", "1")
    assert await store.get_meta("schema_version") == "1"
