"""Web UI routes: auth, dashboard, providers, metrics, units, and OAuth.

Single-user, session-authenticated. Provider API credentials are entered here
and stored encrypted; the client-secret field is write-only (never rendered
back). Every state-changing POST is CSRF-protected.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..app import AppContext
from ..mcp_auth import login_allowed
from ..metrics import PREF_GROUPS, all_metrics
from ..models import MetricPref, ResolutionMode
from . import github_login
from .clientip import client_ip, ip_allowed
from .security import check_csrf, ensure_csrf

_WEB_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


def create_web_routes(ctx: AppContext) -> list[BaseRoute]:
    """Build the web UI routes bound to ``ctx``."""

    def render(request: Request, name: str, context: dict | None = None) -> Response:
        data = {"csrf": ensure_csrf(request), "nav": _nav(request)}
        data.update(context or {})
        return _TEMPLATES.TemplateResponse(request, name, data)

    # ── liveness ─────────────────────────────────────────────────────────
    async def healthz(_request: Request) -> JSONResponse:
        """Unauthenticated liveness probe."""
        return JSONResponse({"status": "ok"})

    # ── auth ─────────────────────────────────────────────────────────────
    def _password_allowed_here(request: Request) -> bool:
        """Return True if password break-glass is permitted from this client IP.

        Always False when no password is configured. When GitHub login is the
        primary method, the password is still IP-gated to the LAN/allowlist.
        """
        if not ctx.has_password:
            return False
        ip = client_ip(request, ctx.settings.trusted_proxy_networks())
        return ip_allowed(ip, ctx.settings.web_password_allowed_networks())

    def _login_page(request: Request, error: str | None) -> Response:
        # nav=[] -> base.html renders the nav-less centered auth layout.
        return render(
            request,
            "login.html",
            {
                "error": error,
                "nav": [],
                "github_enabled": ctx.settings.web_github_login_enabled,
                "password_available": _password_allowed_here(request),
            },
        )

    async def login(request: Request) -> Response:
        """Show the login form (GET) or authenticate via password (POST).

        Password login is break-glass: accepted only from an allowlisted client
        IP (see ``WEB_PASSWORD_ALLOWED_CIDRS``). GitHub sign-in, when configured,
        is handled by ``/login/github`` and has no IP restriction.
        """
        if request.session.get("authenticated"):
            return RedirectResponse("/", status_code=303)
        error = None
        if request.method == "POST":
            form = await request.form()
            if not _password_allowed_here(request):
                error = "Password sign-in is not available from your network."
            elif not check_csrf(request, _form_value(form, "csrf")):
                error = "Invalid session token; please retry."
            elif ctx.verify_password(str(form.get("password", ""))):
                request.session["authenticated"] = True
                return RedirectResponse("/", status_code=303)
            else:
                error = "Incorrect password."
        return _login_page(request, error)

    async def github_login_start(request: Request) -> Response:
        """Begin web GitHub sign-in: store state in session, hand off to GitHub.

        Uses the same ``<meta refresh>`` interstitial as provider connect so the
        cross-origin hop isn't subject to the page's CSP ``form-action``.
        """
        if request.session.get("authenticated"):
            return RedirectResponse("/", status_code=303)
        if not ctx.settings.web_github_login_enabled:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if not check_csrf(request, _form_value(form, "csrf")):
            return Response("Invalid CSRF token", status_code=400)
        state = secrets.token_urlsafe(32)
        request.session["gh_login_state"] = state
        return _TEMPLATES.TemplateResponse(
            request,
            "oauth_redirect.html",
            {
                "authorize_url": github_login.authorize_url(ctx.settings, state),
                "provider": "GitHub",
            },
        )

    async def github_login_callback(request: Request) -> Response:
        """Handle the GitHub return: verify state, exchange code, check allowlist."""
        if request.session.get("authenticated"):
            return RedirectResponse("/", status_code=303)
        params = request.query_params
        saved_state = request.session.pop("gh_login_state", None)
        if params.get("error"):
            return _login_page(request, f"GitHub sign-in failed: {params['error']}")
        if not saved_state or saved_state != params.get("state"):
            return _login_page(request, "GitHub sign-in state mismatch; please retry.")
        try:
            login = await github_login.fetch_login(ctx.settings, params.get("code", ""))
        except Exception:  # noqa: BLE001 - report a generic failure to the user
            return _login_page(request, "GitHub sign-in failed; please retry.")
        if not login_allowed(login.lower(), ctx.settings.github_allowed_logins()):
            return _login_page(request, f"GitHub user {login!r} is not authorized.")
        request.session["authenticated"] = True
        request.session["user_login"] = login
        return RedirectResponse("/", status_code=303)

    async def logout(request: Request) -> Response:
        """Clear the session."""
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ── dashboard ────────────────────────────────────────────────────────
    async def dashboard(request: Request) -> Response:
        """Overview of provider status and effective preferences."""
        providers = await _provider_rows(ctx)
        unit_prefs = await ctx.store.get_unit_prefs()
        return render(
            request,
            "dashboard.html",
            {
                "providers": providers,
                "unit_prefs": unit_prefs,
                "base_url": ctx.settings.base_url,
            },
        )

    # ── providers ────────────────────────────────────────────────────────
    async def providers_page(request: Request) -> Response:
        """List providers with credential + connection controls."""
        return render(
            request,
            "providers.html",
            {"providers": await _provider_rows(ctx), "saved": request.query_params.get("saved")},
        )

    async def save_credentials(request: Request) -> Response:
        """Persist a provider's client id/secret (secret write-only)."""
        provider = request.path_params["provider"]
        if provider not in ctx.providers:
            return RedirectResponse("/providers", status_code=303)
        form = await request.form()
        if not check_csrf(request, _form_value(form, "csrf")):
            return Response("Invalid CSRF token", status_code=400)
        client_id = str(form.get("client_id", "")).strip()
        secret_raw = str(form.get("client_secret", "")).strip()
        # Blank secret means "leave unchanged".
        await ctx.store.set_credentials(provider, client_id, secret_raw or None)
        return RedirectResponse("/providers?saved=1", status_code=303)

    async def disconnect(request: Request) -> Response:
        """Delete a provider's tokens and mark it disconnected."""
        provider = request.path_params["provider"]
        form = await request.form()
        if not check_csrf(request, _form_value(form, "csrf")):
            return Response("Invalid CSRF token", status_code=400)
        await ctx.store.delete_token(provider)
        await ctx.store.set_status(provider, connected=False, clear_error=True)
        return RedirectResponse("/providers", status_code=303)

    # ── OAuth ────────────────────────────────────────────────────────────
    async def oauth_start(request: Request) -> Response:
        """Begin the OAuth flow: store state/PKCE in session, then hand off to vendor.

        Returns a small interstitial page that navigates to the vendor via
        ``<meta refresh>`` + a link, rather than a cross-origin 303. A 303 from
        this form POST is checked against the page's CSP ``form-action`` for
        *every* hop in the vendor's redirect chain (which is unpredictable);
        a document navigation is not, so this works for any provider.
        """
        if not request.session.get("authenticated"):
            return RedirectResponse("/login", status_code=303)
        provider = request.path_params["provider"]
        if provider not in ctx.providers:
            return RedirectResponse("/providers", status_code=303)
        form = await request.form()
        if not check_csrf(request, _form_value(form, "csrf")):
            return Response("Invalid CSRF token", status_code=400)
        try:
            start = await ctx.auth_flow.start(provider)
        except RuntimeError as exc:
            await ctx.store.set_status(provider, last_error=str(exc))
            return RedirectResponse("/providers", status_code=303)
        request.session["oauth"] = {
            "provider": provider,
            "state": start.state,
            "code_verifier": start.code_verifier,
        }
        return _TEMPLATES.TemplateResponse(
            request,
            "oauth_redirect.html",
            {
                "authorize_url": start.authorize_url,
                "provider": ctx.providers[provider].display_name,
            },
        )

    async def oauth_callback(request: Request) -> Response:
        """Handle the OAuth redirect: verify state, exchange code, store tokens."""
        if not request.session.get("authenticated"):
            return RedirectResponse("/login", status_code=303)
        provider = request.path_params["provider"]
        saved = request.session.pop("oauth", None)
        params = request.query_params
        if params.get("error"):
            await ctx.store.set_status(provider, last_error=f"OAuth error: {params['error']}")
            return RedirectResponse("/providers", status_code=303)
        if (
            not saved
            or saved.get("provider") != provider
            or saved.get("state") != params.get("state")
        ):
            await ctx.store.set_status(provider, last_error="OAuth state mismatch; retry.")
            return RedirectResponse("/providers", status_code=303)
        try:
            await ctx.auth_flow.finish(provider, params.get("code", ""), saved.get("code_verifier"))
        except Exception as exc:  # noqa: BLE001 - report to UI
            await ctx.store.set_status(provider, connected=False, last_error=f"Connect failed: {exc}")
        return RedirectResponse("/providers", status_code=303)

    # ── metric preferences ───────────────────────────────────────────────
    async def metrics_page(request: Request) -> Response:
        """List metrics with their resolution preference controls."""
        prefs = await ctx.store.all_metric_prefs()
        rows = []
        for m in all_metrics():
            supporting = [n for n, p in ctx.providers.items() if p.supports(m.key)]
            if not supporting:
                continue
            pref = prefs[m.key]
            rows.append(
                {
                    "metric": m.key,
                    "description": m.description,
                    "providers": sorted(supporting),
                    "mode": pref.mode.value,
                    "authority": pref.authority,
                    "fallback_order": ",".join(pref.fallback_order),
                }
            )
        return render(request, "metrics.html", {"rows": rows, "saved": request.query_params.get("saved")})

    async def save_metrics(request: Request) -> Response:
        """Persist resolution preferences for every metric on the page."""
        form = await request.form()
        if not check_csrf(request, _form_value(form, "csrf")):
            return Response("Invalid CSRF token", status_code=400)
        for m in all_metrics():
            key = m.key
            supporting = [n for n, p in ctx.providers.items() if p.supports(key)]
            if not supporting:
                continue
            mode = ResolutionMode(str(form.get(f"mode[{key}]", "auto")))
            authority = str(form.get(f"authority[{key}]", "")).strip() or None
            fallback_raw = str(form.get(f"fallback_order[{key}]", "")).strip()
            fallback = [p.strip() for p in fallback_raw.split(",") if p.strip()]
            await ctx.store.set_metric_pref(
                MetricPref(metric=key, mode=mode, authority=authority, fallback_order=fallback)
            )
        return RedirectResponse("/metrics?saved=1", status_code=303)

    # ── logging ──────────────────────────────────────────────────────────
    async def logging_page(request: Request) -> Response:
        """Show the rolling provider-error log in a sortable/filterable table."""
        rows = await ctx.store.get_error_log()
        errors = [
            {
                "provider": r.provider,
                "level": r.level,
                "message": r.message,
                "created_at": r.created_at,
            }
            for r in rows
        ]
        return render(
            request,
            "logging.html",
            {
                "errors": errors,
                "providers": sorted({e["provider"] for e in errors}),
                "levels": sorted({e["level"] for e in errors}),
            },
        )

    # ── unit preferences ─────────────────────────────────────────────────
    async def units_page(request: Request) -> Response:
        """Show and edit display-unit preferences per group."""
        current = await ctx.store.get_unit_prefs()
        groups = [
            {"group": g, "choices": spec["choices"], "current": current.get(g)}
            for g, spec in PREF_GROUPS.items()
        ]
        return render(request, "units.html", {"groups": groups, "saved": request.query_params.get("saved")})

    async def save_units(request: Request) -> Response:
        """Persist display-unit preferences."""
        form = await request.form()
        if not check_csrf(request, _form_value(form, "csrf")):
            return Response("Invalid CSRF token", status_code=400)
        for group, spec in PREF_GROUPS.items():
            chosen = _form_value(form, group).strip()
            choices = spec["choices"]
            if chosen and isinstance(choices, list) and chosen in choices:
                await ctx.store.set_unit_pref(group, chosen)
        return RedirectResponse("/units?saved=1", status_code=303)

    return [
        Mount("/static", app=StaticFiles(directory=str(_WEB_DIR / "static")), name="static"),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/login", login, methods=["GET", "POST"]),
        Route("/login/github", github_login_start, methods=["POST"]),
        Route("/auth/callback/web", github_login_callback, methods=["GET"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/", dashboard, methods=["GET"]),
        Route("/providers", providers_page, methods=["GET"]),
        Route("/providers/{provider}/credentials", save_credentials, methods=["POST"]),
        Route("/providers/{provider}/disconnect", disconnect, methods=["POST"]),
        Route("/oauth/{provider}/start", oauth_start, methods=["POST"]),
        Route("/oauth/{provider}/callback", oauth_callback, methods=["GET"]),
        Route("/metrics", metrics_page, methods=["GET"]),
        Route("/metrics", save_metrics, methods=["POST"]),
        Route("/units", units_page, methods=["GET"]),
        Route("/units", save_units, methods=["POST"]),
        Route("/logging", logging_page, methods=["GET"]),
    ]


def _form_value(form: FormData, key: str) -> str:
    """Return a form field as a string (empty for missing/file fields)."""
    value = form.get(key)
    return value if isinstance(value, str) else ""


def _nav(request: Request) -> list[dict]:
    """Build the navigation destinations with the active item flagged.

    ``icon`` selects an inline SVG in the base template.
    """
    path = request.url.path
    items = [
        ("/", "Dashboard", "dashboard"),
        ("/providers", "Providers", "devices"),
        ("/metrics", "Metrics", "tune"),
        ("/units", "Units", "straighten"),
        ("/logging", "Logging", "logging"),
    ]
    return [
        {"href": h, "label": label, "icon": icon, "active": path == h}
        for h, label, icon in items
    ]


async def _provider_rows(ctx: AppContext) -> list[dict]:
    """Assemble per-provider display rows for the dashboard/providers pages."""
    rows = []
    for name in sorted(ctx.providers):
        provider = ctx.providers[name]
        status = await ctx.store.get_status(name)
        creds = await ctx.store.get_credentials(name)
        rows.append(
            {
                "name": name,
                "display_name": provider.display_name,
                "client_id": creds[0] if creds else "",
                "secret_configured": await ctx.store.has_secret(name),
                "credentials_ready": await ctx.store.resolve_credentials(name) is not None,
                "connected": bool(status and status.connected),
                "last_sync": status.last_sync if status else None,
                "scopes": provider.oauth.scopes,
                "redirect_uri": ctx.settings.redirect_uri(name),
                "credentials_url": provider.credentials_url,
                "metric_count": len(provider.supported_metrics()),
            }
        )
    return rows
