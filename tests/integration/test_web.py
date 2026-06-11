"""Integration tests for the web UI (driven via the ASGI app)."""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_asyncio

from personal_health_mcp.app import create_context
from personal_health_mcp.server import create_asgi_app
from personal_health_mcp.storage import ProviderCredential

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def web(tmp_path, settings):
    """Yield (httpx client, ctx) for the assembled app."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'web.db'}"
    ctx = create_context(settings, database_url=db_url)
    await ctx.startup()  # create tables (web routes don't need the MCP lifespan)
    app = create_asgi_app(ctx)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        try:
            yield client, ctx
        finally:
            await ctx.shutdown()


async def _login(client: httpx.AsyncClient) -> str:
    """Log in and return a fresh CSRF token from an authenticated page."""
    page = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    resp = await client.post("/login", data={"csrf": csrf, "password": "hunter2"})
    assert resp.status_code == 303
    dash = await client.get("/")
    return re.search(r'name="csrf" value="([^"]+)"', dash.text).group(1)


async def test_unauthenticated_redirects_to_login(web):
    client, _ctx = web
    resp = await client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


async def test_healthz_is_open(web):
    client, _ctx = web
    assert (await client.get("/healthz")).status_code == 200


async def test_login_wrong_password(web):
    client, _ctx = web
    page = await client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    resp = await client.post("/login", data={"csrf": csrf, "password": "wrong"})
    assert resp.status_code == 200
    assert "Incorrect password" in resp.text


async def test_login_then_dashboard(web):
    client, _ctx = web
    await _login(client)
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


def _client_from_ip(ctx, source_ip: str) -> httpx.AsyncClient:
    """A client whose requests appear to originate from ``source_ip``."""
    transport = httpx.ASGITransport(app=create_asgi_app(ctx), client=(source_ip, 1234))
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    )


async def test_password_login_denied_from_non_lan_ip(web):
    # Default WEB_PASSWORD_ALLOWED_CIDRS is LAN/loopback only; a public source IP
    # (no trusted proxy configured -> peer is taken as-is) must be rejected.
    _client, ctx = web
    async with _client_from_ip(ctx, "8.8.8.8") as client:
        resp = await client.post("/login", data={"csrf": "x", "password": "hunter2"})
        assert resp.status_code == 200
        assert "not available from your network" in resp.text
        # Session was not authenticated.
        assert (await client.get("/")).headers["location"] == "/login"


async def test_login_page_hides_password_field_off_lan(web):
    _client, ctx = web
    async with _client_from_ip(ctx, "8.8.8.8") as client:
        page = await client.get("/login")
        assert 'name="password"' not in page.text


async def test_password_login_allowed_from_lan_ip(web):
    _client, ctx = web
    async with _client_from_ip(ctx, "192.168.1.50") as client:
        page = await client.get("/login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        resp = await client.post("/login", data={"csrf": csrf, "password": "hunter2"})
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"


async def test_security_headers_present(web):
    client, _ctx = web
    resp = await client.get("/healthz")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


async def test_csp_form_action_allows_oauth_origins(web):
    # form-action must list each provider's authorize origin, else browsers
    # block the Connect redirect (form POST -> 303 to the vendor).
    client, _ctx = web
    csp = (await client.get("/healthz")).headers["Content-Security-Policy"]
    assert "form-action 'self'" in csp
    for origin in (
        "https://accounts.google.com",
        "https://cloud.ouraring.com",
        "https://account.withings.com",
    ):
        assert origin in csp


async def test_csrf_rejected_on_post(web):
    client, _ctx = web
    await _login(client)
    resp = await client.post(
        "/providers/oura/credentials", data={"csrf": "bogus", "client_id": "x"}
    )
    assert resp.status_code == 400


async def test_save_credentials_secret_encrypted_and_not_echoed(web):
    client, ctx = web
    csrf = await _login(client)
    resp = await client.post(
        "/providers/oura/credentials",
        data={"csrf": csrf, "client_id": "cid-1", "client_secret": "topsecret"},
    )
    assert resp.status_code == 303

    # Secret stored encrypted, not in plaintext.
    async with ctx.store._session() as s:  # noqa: SLF001
        row = await s.get(ProviderCredential, "oura")
        assert row.client_secret_enc and row.client_secret_enc != "topsecret"

    # The providers page never renders the secret back.
    page = await client.get("/providers")
    assert "topsecret" not in page.text
    assert "cid-1" in page.text  # client id is shown
    assert "secret set" in page.text


async def test_update_credentials_without_secret_keeps_it(web):
    client, ctx = web
    csrf = await _login(client)
    await client.post(
        "/providers/oura/credentials",
        data={"csrf": csrf, "client_id": "cid-1", "client_secret": "s1"},
    )
    await client.post(
        "/providers/oura/credentials",
        data={"csrf": csrf, "client_id": "cid-2", "client_secret": ""},
    )
    creds = await ctx.store.get_credentials("oura")
    assert creds == ("cid-2", "s1")


async def test_set_units_persists(web):
    client, ctx = web
    csrf = await _login(client)
    resp = await client.post("/units", data={"csrf": csrf, "mass": "lb", "distance": "mi"})
    assert resp.status_code == 303
    prefs = await ctx.store.get_unit_prefs()
    assert prefs["mass"] == "lb"
    assert prefs["distance"] == "mi"


async def test_set_metric_pref_persists(web):
    client, ctx = web
    csrf = await _login(client)
    resp = await client.post(
        "/metrics",
        data={
            "csrf": csrf,
            "mode[weight]": "authority",
            "authority[weight]": "withings",
            "fallback_order[weight]": "google, oura",
        },
    )
    assert resp.status_code == 303
    pref = await ctx.store.get_metric_pref("weight")
    assert pref.mode.value == "authority"
    assert pref.authority == "withings"
    assert pref.fallback_order == ["google", "oura"]


async def test_oauth_start_returns_interstitial_not_cross_origin_redirect(web):
    # Must be a 200 interstitial (document navigation), not a 303 to the vendor,
    # so CSP form-action never blocks the provider's redirect chain.
    client, ctx = web
    await ctx.store.set_credentials("oura", "cid", "secret")
    csrf = await _login(client)
    resp = await client.post("/oauth/oura/start", data={"csrf": csrf})
    assert resp.status_code == 200
    assert 'http-equiv="refresh"' in resp.text
    assert "cloud.ouraring.com/oauth/authorize" in resp.text


async def test_oauth_callback_state_mismatch_is_rejected(web):
    client, ctx = web
    await _login(client)
    # No stored oauth state in session -> mismatch path.
    resp = await client.get("/oauth/oura/callback?code=abc&state=evil")
    assert resp.status_code == 303
    status = await ctx.store.get_status("oura")
    assert status is not None and "state mismatch" in (status.last_error or "")
