"""ASGI server entry point.

Builds a single Starlette application that mounts:
  * ``/mcp``  — the FastMCP Streamable-HTTP app, guarded by a static bearer token.
  * ``/``     — the web UI + OAuth callback routes.

FastMCP's HTTP app carries a lifespan (the MCP session manager); it is threaded
into the parent app's lifespan alongside app-context startup/shutdown.

Run in production with uvicorn factory mode::

    uvicorn personal_health_mcp.server:create_default_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from secrets import compare_digest
from urllib.parse import urlparse

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from .app import AppContext, create_context
from .mcp_auth import GitHubAllowlistMiddleware, build_github_provider
from .tools import register_tools
from .web import create_web_routes
from .web.security import AuthGuardMiddleware, SecurityHeadersMiddleware


def build_mcp(ctx: AppContext) -> FastMCP:
    """Create the FastMCP server with all health tools registered.

    If GitHub OAuth is configured, the MCP endpoint is protected by it (with a
    GitHub-login allowlist); otherwise the static bearer token is used (applied
    as ASGI middleware in :func:`create_asgi_app`).
    """
    if ctx.settings.mcp_oauth_enabled:
        mcp: FastMCP = FastMCP("personal_health_mcp", auth=build_github_provider(ctx.settings))
        mcp.add_middleware(GitHubAllowlistMiddleware(ctx.settings.github_allowed_logins()))
    else:
        mcp = FastMCP("personal_health_mcp")
    register_tools(mcp, ctx)
    return mcp


class BearerAuthMiddleware:
    """ASGI middleware enforcing a static bearer token on the wrapped app.

    Used to guard the ``/mcp`` mount. Comparison is constant-time. A blank token
    disables the guard (intended only for tests).
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}" if token else ""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._expected:
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode()
        if not compare_digest(provided, self._expected):
            await self._send_401(send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _send_401(send: Send) -> None:
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _form_action_origins(ctx: AppContext) -> list[str]:
    """Health-provider authorize origins to allow in CSP form-action."""
    return sorted(
        {
            f"{u.scheme}://{u.netloc}"
            for p in ctx.providers.values()
            for u in [urlparse(p.oauth.authorize_url)]
            if u.scheme and u.netloc
        }
    )


def _ui_middleware(ctx: AppContext) -> list[Middleware]:
    """The web-UI middleware stack (outermost first)."""
    return [
        Middleware(SecurityHeadersMiddleware, form_action_origins=_form_action_origins(ctx)),
        Middleware(
            SessionMiddleware,
            secret_key=ctx.settings.session_secret or "dev-insecure-session-secret",
            https_only=ctx.settings.cookie_secure,
            same_site="lax",
        ),
        Middleware(AuthGuardMiddleware),
    ]


def create_asgi_app(ctx: AppContext) -> Starlette:
    """Assemble the root ASGI app.

    Two compositions:
      * **bearer** (default): our Starlette serves the web UI and mounts the
        FastMCP app under ``/mcp`` behind a static-bearer middleware.
      * **GitHub OAuth**: the FastMCP app runs at the *root* (so its OAuth
        ``.well-known`` discovery and ``/auth`` callback live at the origin),
        with the web UI routes added alongside and the UI middleware applied.
    """
    mcp = build_mcp(ctx)
    if ctx.settings.mcp_oauth_enabled:
        return _create_oauth_app(ctx, mcp)
    return _create_bearer_app(ctx, mcp)


def _create_bearer_app(ctx: AppContext, mcp: FastMCP) -> Starlette:
    """Web UI + FastMCP mounted at /mcp behind the static bearer token."""
    mcp_app = mcp.http_app(path="/")
    guarded_mcp = BearerAuthMiddleware(mcp_app, ctx.settings.mcp_auth_token)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await ctx.startup()
        async with mcp_app.router.lifespan_context(mcp_app):
            try:
                yield
            finally:
                await ctx.shutdown()

    routes = [*create_web_routes(ctx), Mount("/mcp", app=guarded_mcp)]
    return Starlette(routes=routes, lifespan=lifespan, middleware=_ui_middleware(ctx))


def _create_oauth_app(ctx: AppContext, mcp: FastMCP) -> Starlette:
    """FastMCP at the root (OAuth discovery at origin) with the web UI alongside."""
    app = mcp.http_app(path="/mcp")
    # Co-locate the web UI routes on the same (root) app.
    app.router.routes.extend(create_web_routes(ctx))
    # Merge our DB startup/shutdown into FastMCP's session-manager lifespan.
    inner_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await ctx.startup()
        async with inner_lifespan(app):
            try:
                yield
            finally:
                await ctx.shutdown()

    app.router.lifespan_context = lifespan
    # add_middleware prepends (outermost-last), so add inner -> outer.
    app.add_middleware(AuthGuardMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=ctx.settings.session_secret or "dev-insecure-session-secret",
        https_only=ctx.settings.cookie_secure,
        same_site="lax",
    )
    app.add_middleware(SecurityHeadersMiddleware, form_action_origins=_form_action_origins(ctx))
    return app


def create_default_app() -> Starlette:
    """Build the app from environment-derived settings (uvicorn factory target)."""
    return create_asgi_app(create_context())


def main() -> None:
    """Console-script entry point: run uvicorn with env settings."""
    import uvicorn

    from .config import get_settings

    settings = get_settings()
    uvicorn.run(
        create_default_app(),
        host="0.0.0.0",  # noqa: S104 - bound inside the container network only
        port=8000,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
