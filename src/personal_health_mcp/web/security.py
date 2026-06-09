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

# Paths (prefixes) that never require a UI session.
_OPEN_PREFIXES = ("/mcp", "/healthz", "/login", "/static", "/oauth")

# Server-rendered, no inline scripts needed -> a strict CSP is feasible.
_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardening headers to every response."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", _CSP)
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
        guarded = path == "/" or not any(path.startswith(p) for p in _OPEN_PREFIXES)
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
