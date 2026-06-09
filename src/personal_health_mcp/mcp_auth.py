"""GitHub OAuth for the MCP endpoint (optional, opt-in).

When GitHub credentials are configured, the ``/mcp`` endpoint is protected by
GitHub OAuth (a transparent proxy to GitHub via FastMCP) instead of the static
bearer token. Because a GitHub OAuth app authorizes *any* GitHub user who
approves it, a :class:`GitHubAllowlistMiddleware` restricts access to a
configured set of GitHub logins — essential for single-user, personal health data.
"""

from __future__ import annotations

import logging

from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from .config import Settings

logger = logging.getLogger(__name__)

# GitHub OAuth callback path, relative to the MCP app's base URL. Registered in
# the GitHub OAuth app as <PUBLIC_BASE_URL>/mcp/auth/callback (see build_github_provider).
GITHUB_REDIRECT_PATH = "/auth/callback"
_UNAUTHORIZED = -32001


def login_allowed(login: str, allowed: set[str]) -> bool:
    """Return True if ``login`` may access. Empty ``allowed`` means any user."""
    if not allowed:
        return True
    return login.lower() in allowed


def build_github_provider(settings: Settings) -> GitHubProvider:
    """Construct the GitHub OAuth provider for the MCP endpoint.

    ``base_url`` is the origin (the FastMCP app runs at the root in OAuth mode so
    its ``.well-known`` discovery endpoints sit at the origin root, per RFC 9728).
    Consent is disabled: it's a single-user server, and skipping FastMCP's consent
    page avoids a second cross-origin form-action hop in the browser.
    """
    return GitHubProvider(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        base_url=settings.base_url,
        redirect_path=GITHUB_REDIRECT_PATH,
        required_scopes=["read:user"],
        require_authorization_consent=False,
    )


class GitHubAllowlistMiddleware(Middleware):
    """Reject authenticated requests whose GitHub login isn't on the allowlist.

    Args:
        allowed: Lowercased GitHub logins permitted access. Empty = allow any
            authenticated GitHub account (logged as a warning at startup).
    """

    def __init__(self, allowed: set[str]) -> None:
        self._allowed = allowed
        if not allowed:
            logger.warning(
                "GITHUB_ALLOWED_USERS is not set: any GitHub account that "
                "authorizes the app can access your health data."
            )

    async def on_request(self, context: MiddlewareContext, call_next):
        token = get_access_token()
        claims = getattr(token, "claims", None) or {}
        login = str(claims.get("login") or claims.get("username") or getattr(token, "subject", "") or "")
        if not login_allowed(login, self._allowed):
            raise McpError(
                ErrorData(
                    code=_UNAUTHORIZED,
                    message=f"GitHub user {login!r} is not authorized for this server.",
                )
            )
        return await call_next(context)
