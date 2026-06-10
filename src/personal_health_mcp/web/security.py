"""Web security middleware and CSRF helpers.

* :class:`SecurityHeadersMiddleware` adds HSTS/CSP/nosniff/frame-deny to every
  response.
* :class:`AuthGuardMiddleware` requires an authenticated session for UI routes,
  while leaving ``/mcp`` (bearer-guarded), ``/healthz``, ``/login`` and static
  assets open.
* CSRF helpers issue a per-session token and validate it on state-changing POSTs.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

# Paths that never require a UI session; everything else is guarded by default
# (secure-by-default: a new UI route is protected unless it's explicitly opened
# here). The open set is the public web pages (/healthz, /login, /static), the
# web provider-connect routes under /oauth/* (which enforce their own session
# check internally), and the FastMCP MCP + OAuth-proxy surface that must reach
# FastMCP directly in OAuth mode. Forgetting to open one of the latter is a loud
# failure (the OAuth flow 303s to /login, caught by the auth-passthrough test);
# forgetting to guard a sensitive route would be a silent one.
_OPEN_PREFIXES = (
    "/mcp",
    "/healthz",
    "/login",
    "/static",
    "/oauth",
    "/authorize",
    "/token",
    "/register",
    "/revoke",
    "/consent",
    "/auth/callback",
    "/.well-known",
)

def _build_csp(form_action_origins: list[str]) -> str:
    """Build the CSP. ``form_action_origins`` are extra origins allowed as the
    target of form submissions (e.g. provider OAuth authorize endpoints).

    Browsers enforce ``form-action`` on the redirect that results from a form
    POST, so the OAuth "Connect" flow (POST -> 303 to the vendor) requires the
    vendor's authorize origin here, or the redirect is silently blocked.
    """
    form_action = " ".join(["'self'", *form_action_origins])
    return (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        f"form-action {form_action}"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardening headers to every response.

    Args:
        app: The wrapped ASGI app.
        form_action_origins: Extra origins to allow in CSP ``form-action`` (the
            providers' OAuth authorize origins). Defaults to none (strict ``'self'``).
    """

    def __init__(self, app, form_action_origins: list[str] | None = None) -> None:
        super().__init__(app)
        self._csp = _build_csp(form_action_origins or [])

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", self._csp)
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
        return response


class AuthGuardMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated browser requests for UI routes to ``/login``.

    Note: ``/oauth/*`` is open at the middleware level but each OAuth handler
    independently requires an authenticated session, so callbacks are still
    protected.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        guarded = not path.startswith(_OPEN_PREFIXES)
        if guarded and not request.session.get("authenticated"):
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)


def ensure_csrf(request: Request) -> str:
    """Return the session CSRF token, creating one if needed."""
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def check_csrf(request: Request, submitted: str | None) -> bool:
    """Return True if ``submitted`` matches the session CSRF token."""
    expected = request.session.get("csrf")
    return bool(expected and submitted and secrets.compare_digest(expected, submitted))
