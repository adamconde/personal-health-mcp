"""Tests for OAuth flow building and token refresh."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx

from personal_health_mcp.models import Token
from personal_health_mcp.oauth import AuthFlow, TokenManager
from personal_health_mcp.providers import build_providers
from personal_health_mcp.storage import Store
from personal_health_mcp.timeutil import now_utc

pytestmark = pytest.mark.asyncio


async def test_authorize_url_contains_state_pkce_and_redirect(store: Store, settings):
    await store.set_credentials("oura", "client-123", "secret")
    flow = AuthFlow(settings, store, build_providers())
    start = await flow.start("oura")
    assert "cloud.ouraring.com/oauth/authorize" in start.authorize_url
    assert "client_id=client-123" in start.authorize_url
    assert "code_challenge=" in start.authorize_url  # PKCE
    assert start.code_verifier  # verifier returned for session storage
    assert "state=" in start.authorize_url
    # redirect_uri is URL-encoded in the query string
    assert "oura%2Fcallback" in start.authorize_url


async def test_start_requires_credentials(store: Store, settings):
    flow = AuthFlow(settings, store, build_providers())
    with pytest.raises(RuntimeError):
        await flow.start("oura")


@respx.mock
async def test_finish_exchanges_and_persists(store: Store, settings):
    await store.set_credentials("oura", "cid", "secret")
    respx.post("https://api.ouraring.com/oauth/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "acc",
                "refresh_token": "ref",
                "expires_in": 3600,
                "scope": "daily heartrate",
            },
        )
    )
    flow = AuthFlow(settings, store, build_providers())
    await flow.finish("oura", "the-code", "verifier")
    token = await store.get_token("oura")
    assert token.access_token == "acc"
    status = await store.get_status("oura")
    assert status.connected is True


async def test_token_manager_returns_token_when_fresh(store: Store):
    await store.save_token(
        Token(
            provider="oura",
            access_token="fresh",
            refresh_token="r",
            expires_at=now_utc() + timedelta(hours=2),
        )
    )
    tm = TokenManager(store, build_providers())
    assert await tm.get_access_token("oura") == "fresh"


async def test_token_manager_none_when_not_connected(store: Store):
    tm = TokenManager(store, build_providers())
    assert await tm.get_access_token("oura") is None


@respx.mock
async def test_token_manager_refreshes_when_expired(store: Store):
    await store.set_credentials("oura", "cid", "secret")
    await store.save_token(
        Token(
            provider="oura",
            access_token="old",
            refresh_token="old-ref",
            expires_at=now_utc() - timedelta(seconds=5),
        )
    )
    respx.post("https://api.ouraring.com/oauth/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "new", "refresh_token": "new-ref", "expires_in": 3600}
        )
    )
    tm = TokenManager(store, build_providers())
    assert await tm.get_access_token("oura") == "new"
    stored = await store.get_token("oura")
    assert stored.access_token == "new"
    assert stored.refresh_token == "new-ref"


@respx.mock
async def test_token_manager_withings_rotation_persisted(store: Store):
    await store.set_credentials("withings", "cid", "secret")
    await store.save_token(
        Token(
            provider="withings",
            access_token="old",
            refresh_token="rot-1",
            expires_at=now_utc() - timedelta(seconds=5),
            provider_user_id="42",
        )
    )
    respx.post("https://wbsapi.withings.net/v2/oauth2").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 0,
                "body": {"access_token": "acc2", "refresh_token": "rot-2", "expires_in": 10800},
            },
        )
    )
    tm = TokenManager(store, build_providers())
    assert await tm.get_access_token("withings") == "acc2"
    stored = await store.get_token("withings")
    assert stored.refresh_token == "rot-2"
    assert stored.provider_user_id == "42"  # preserved across refresh


@respx.mock
async def test_token_manager_marks_disconnected_on_refresh_failure(store: Store):
    await store.set_credentials("oura", "cid", "secret")
    await store.save_token(
        Token(
            provider="oura",
            access_token="old",
            refresh_token="bad",
            expires_at=now_utc() - timedelta(seconds=5),
        )
    )
    respx.post("https://api.ouraring.com/oauth/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    tm = TokenManager(store, build_providers())
    assert await tm.get_access_token("oura") is None
    status = await store.get_status("oura")
    assert status.connected is False
    assert "refresh failed" in (status.last_error or "").lower()
