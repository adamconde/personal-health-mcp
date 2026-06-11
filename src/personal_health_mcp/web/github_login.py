"""GitHub OAuth for the web UI (browser session login).

A standard server-side authorization-code flow, distinct from the FastMCP
GitHub *proxy* that guards ``/mcp``. It reuses the same GitHub OAuth app
credentials and the same ``GITHUB_ALLOWED_USERS`` allowlist, and authenticates
the browser session (sets ``session["authenticated"]``) rather than minting MCP
tokens. The redirect URI is a subdirectory of the MCP callback, so the same
OAuth app serves both with no extra GitHub configuration.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from ..config import Settings

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 - public endpoint, not a secret
_USER_URL = "https://api.github.com/user"
_TIMEOUT = httpx.Timeout(10.0)


def authorize_url(settings: Settings, state: str) -> str:
    """Build the GitHub authorize URL for the web login flow.

    Args:
        settings: App settings (client id + redirect URI).
        state: Anti-CSRF state to bind to the user's session.
    """
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_web_redirect_uri,
        "scope": "read:user",
        "state": state,
        "allow_signup": "false",
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


async def fetch_login(settings: Settings, code: str) -> str:
    """Exchange an authorization ``code`` for a token and return the GitHub login.

    Args:
        settings: App settings (client id/secret + redirect URI).
        code: The authorization code from the callback.

    Returns:
        The authenticated user's GitHub login (username).

    Raises:
        RuntimeError: If the token exchange fails or no login is returned.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        token_resp = await client.post(
            _TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.github_web_redirect_uri,
            },
        )
        token_resp.raise_for_status()
        payload = token_resp.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise RuntimeError(f"GitHub token exchange failed: {payload.get('error', 'no token')}")

        user_resp = await client.get(
            _USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        user_resp.raise_for_status()
        login = user_resp.json().get("login")
        if not login:
            raise RuntimeError("GitHub user response had no login.")
        return str(login)
