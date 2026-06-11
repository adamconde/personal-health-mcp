"""Tests for the GitHub-OAuth MCP composition (opt-in)."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from personal_health_mcp.app import create_context
from personal_health_mcp.config import Settings
from personal_health_mcp.server import create_asgi_app

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def oauth_app(tmp_path, enc_key):
    settings = Settings(
        _env_file=None,
        public_base_url="https://health.test",
        session_secret="x",
        web_password="pw",
        token_enc_key=enc_key,
        database_path=":memory:",
        cookie_secure=False,
        github_client_id="Ov23liTEST",
        github_client_secret="sekret",
        github_allowed_users="adamconde",
    )
    assert settings.mcp_oauth_enabled
    ctx = create_context(settings, database_url=f"sqlite+aiosqlite:///{tmp_path / 'gh.db'}")
    await ctx.startup()
    app = create_asgi_app(ctx)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://health.test", follow_redirects=False
    ) as client:
        try:
            yield client
        finally:
            await ctx.shutdown()


async def test_oauth_discovery_metadata_served_at_origin_root(oauth_app):
    # RFC 9728 / 8414 discovery must be at the origin root, not under /mcp.
    assert (await oauth_app.get("/.well-known/oauth-protected-resource/mcp")).status_code == 200
    assert (await oauth_app.get("/.well-known/oauth-authorization-server")).status_code == 200


async def test_mcp_unauthenticated_is_401_with_resource_metadata(oauth_app):
    resp = await oauth_app.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert "oauth-protected-resource" in (resp.headers.get("www-authenticate") or "")


async def test_oauth_endpoints_not_swallowed_by_auth_guard(oauth_app):
    # The full FastMCP OAuth-proxy surface must reach FastMCP, NOT be redirected
    # to /login by the UI auth guard (the bug that broke the token exchange).
    # Because the guard is secure-by-default (deny unless explicitly opened),
    # this asserts every OAuth path is in the open set: a missing one fails here.
    for resp in (
        await oauth_app.post("/token", data={"grant_type": "authorization_code"}),
        await oauth_app.post("/register", json={}),
        await oauth_app.get("/authorize"),
        await oauth_app.get("/auth/callback"),
    ):
        assert resp.headers.get("location") != "/login"
        assert resp.status_code != 303


async def test_web_ui_is_coresident_in_oauth_mode(oauth_app):
    # Web UI still works at the root alongside FastMCP.
    assert (await oauth_app.get("/healthz")).status_code == 200
    assert (await oauth_app.get("/static/m3.css")).status_code == 200
    root = await oauth_app.get("/")
    assert root.status_code == 303 and root.headers["location"] == "/login"
    assert (await oauth_app.get("/login")).status_code == 200


# ── web GitHub sign-in ─────────────────────────────────────────────────────

import re  # noqa: E402


async def _start_github_login(client: httpx.AsyncClient) -> str:
    """POST /login/github and return the ``state`` from the interstitial URL."""
    page = await client.get("/login")
    assert "Sign in with GitHub" in page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    resp = await client.post("/login/github", data={"csrf": csrf})
    assert resp.status_code == 200
    assert "github.com/login/oauth/authorize" in resp.text
    # The URL is HTML-escaped in the template (& -> &amp;), so match up to the
    # next ampersand or quote.
    return re.search(r"state=([^&\"]+)", resp.text).group(1)


async def test_github_web_login_success(oauth_app, monkeypatch):
    async def fake_fetch_login(_settings, _code):
        return "adamconde"  # on the allowlist

    monkeypatch.setattr(
        "personal_health_mcp.web.github_login.fetch_login", fake_fetch_login
    )
    state = await _start_github_login(oauth_app)
    resp = await oauth_app.get(f"/auth/callback/web?code=abc&state={state}")
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    # Session is now authenticated.
    assert (await oauth_app.get("/")).status_code == 200


async def test_github_web_login_rejects_unlisted_user(oauth_app, monkeypatch):
    async def fake_fetch_login(_settings, _code):
        return "intruder"  # NOT on the allowlist

    monkeypatch.setattr(
        "personal_health_mcp.web.github_login.fetch_login", fake_fetch_login
    )
    state = await _start_github_login(oauth_app)
    resp = await oauth_app.get(f"/auth/callback/web?code=abc&state={state}")
    assert resp.status_code == 200
    assert "not authorized" in resp.text
    # Still locked out.
    assert (await oauth_app.get("/")).headers["location"] == "/login"


async def test_github_web_login_state_mismatch_rejected(oauth_app):
    # No prior /login/github -> no session state; a forged state is rejected.
    resp = await oauth_app.get("/auth/callback/web?code=abc&state=forged")
    assert resp.status_code == 200
    assert "state mismatch" in resp.text
    assert (await oauth_app.get("/")).headers["location"] == "/login"
