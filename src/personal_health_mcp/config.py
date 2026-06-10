"""Application configuration loaded from environment variables.

Centralizes all runtime settings and secrets. Provider OAuth client
credentials are intentionally *optional* here — they are normally managed
through the web UI and stored (encrypted) in the database; these env values
act only as a headless bootstrap fallback (see ``storage.resolve_credentials``).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings sourced from environment / ``.env``.

    Attributes:
        public_base_url: External HTTPS origin (no trailing slash) used to build
            OAuth redirect URIs.
        mcp_auth_token: Static bearer token required on the ``/mcp`` endpoint.
        web_password: Plaintext single-user UI password; hashed at boot, never stored.
        session_secret: Key used to sign session cookies.
        token_enc_key: Fernet key(s) (comma-separated, newest first) for encrypting
            tokens and client secrets at rest.
        database_path: Filesystem path to the SQLite database.
        log_level: Logging verbosity.
        *_client_id / *_client_secret: Optional provider OAuth credentials (fallback).
        cf_tunnel_token / caddy_domain: Deployment-overlay specific values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    public_base_url: str = Field(default="http://localhost:8000")
    mcp_auth_token: str = Field(default="")
    web_password: str = Field(default="")
    session_secret: str = Field(default="")
    token_enc_key: str = Field(default="")
    database_path: str = Field(default="/data/health.db")
    log_level: str = Field(default="info")
    # Set the Secure flag on the session cookie. Keep True in production (TLS is
    # terminated at the proxy/tunnel); tests over plain HTTP set this False.
    cookie_secure: bool = Field(default=True)

    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    oura_client_id: str = Field(default="")
    oura_client_secret: str = Field(default="")
    withings_client_id: str = Field(default="")
    withings_client_secret: str = Field(default="")

    cf_tunnel_token: str = Field(default="")
    caddy_domain: str = Field(default="")

    # ── MCP endpoint auth via GitHub OAuth (optional) ────────────────────
    # When both id+secret are set, the /mcp endpoint uses GitHub OAuth instead
    # of the static MCP_AUTH_TOKEN. github_allowed_users restricts access to
    # specific GitHub logins (comma-separated); leave blank to allow any
    # authenticated GitHub account (NOT recommended for personal health data).
    github_client_id: str = Field(default="")
    github_client_secret: str = Field(default="")
    github_allowed_users: str = Field(default="")

    @property
    def base_url(self) -> str:
        """Public base URL without a trailing slash."""
        return self.public_base_url.rstrip("/")

    def redirect_uri(self, provider: str) -> str:
        """Construct the OAuth redirect URI for ``provider``."""
        return f"{self.base_url}/oauth/{provider}/callback"

    def env_credentials(self, provider: str) -> tuple[str, str]:
        """Return ``(client_id, client_secret)`` from env for ``provider`` (may be empty)."""
        cid = getattr(self, f"{provider}_client_id", "")
        secret = getattr(self, f"{provider}_client_secret", "")
        return cid, secret

    @property
    def mcp_oauth_enabled(self) -> bool:
        """True if GitHub OAuth should guard the MCP endpoint (vs. bearer token)."""
        return bool(self.github_client_id and self.github_client_secret)

    def github_allowed_logins(self) -> set[str]:
        """Return the set of allowed GitHub logins (lowercased; empty = any)."""
        return {u.strip().lower() for u in self.github_allowed_users.split(",") if u.strip()}

    def validate_security(self) -> None:
        """Fail fast on insecure/blank secrets for the active auth mode.

        Catches misconfigurations that would otherwise *fail open* — signing
        session cookies with a public default, leaving ``/mcp`` unauthenticated,
        or admitting any GitHub account — by refusing to start instead.

        Raises:
            ValueError: If any required secret is missing for the configured
                mode. All problems are aggregated into one message.
        """
        _gen = '`python -c "import secrets; print(secrets.token_urlsafe(48))"`'
        problems: list[str] = []
        if not self.session_secret:
            problems.append(
                "SESSION_SECRET is not set: session cookies would be signed with a "
                f"public default, allowing forged logins. Generate one with {_gen}."
            )
        if self.mcp_oauth_enabled:
            if not self.github_allowed_logins():
                problems.append(
                    "GITHUB_ALLOWED_USERS is empty while GitHub OAuth is enabled: any "
                    "GitHub account that authorizes the app could read your health "
                    "data. Set it to your comma-separated GitHub login(s)."
                )
        elif not self.mcp_auth_token:
            problems.append(
                "MCP_AUTH_TOKEN is not set and GitHub OAuth is not configured: the "
                f"/mcp endpoint would be unauthenticated. Set MCP_AUTH_TOKEN ({_gen}) "
                "or configure GitHub OAuth."
            )
        if problems:
            raise ValueError(
                "Insecure configuration; refusing to start:\n- " + "\n- ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()
