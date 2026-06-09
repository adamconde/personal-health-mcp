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

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from .app import AppContext, create_context
from .tools import register_tools
from .web import create_web_routes
from .web.security import AuthGuardMiddleware, SecurityHeadersMiddleware


def build_mcp(ctx: AppContext) -> FastMCP:
    """Create the FastMCP server with all health tools registered."""
    mcp: FastMCP = FastMCP("personal_health_mcp")
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


def create_asgi_app(ctx: AppContext) -> Starlette:
    """Assemble the root Starlette app from a wired :class:`AppContext`."""
    mcp = build_mcp(ctx)
    mcp_app = mcp.http_app(path="/")
    guarded_mcp = BearerAuthMiddleware(mcp_app, ctx.settings.mcp_auth_token)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await ctx.startup()
        # Thread the FastMCP session-manager lifespan in.
        async with mcp_app.router.lifespan_context(mcp_app):
            try:
                yield
            finally:
                await ctx.shutdown()

    routes = [*create_web_routes(ctx), Mount("/mcp", app=guarded_mcp)]
    middleware = [
        Middleware(SecurityHeadersMiddleware),
        Middleware(
            SessionMiddleware,
            secret_key=ctx.settings.session_secret or "dev-insecure-session-secret",
            https_only=ctx.settings.cookie_secure,
            same_site="lax",
        ),
        Middleware(AuthGuardMiddleware),
    ]
    return Starlette(routes=routes, lifespan=lifespan, middleware=middleware)


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
