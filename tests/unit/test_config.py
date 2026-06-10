"""Tests for Settings.validate_security (fail-fast on insecure secrets)."""

from __future__ import annotations

import pytest

from personal_health_mcp.config import Settings


def _settings(**overrides) -> Settings:
    """A baseline secure config in bearer mode; override fields per test."""
    base = {
        "_env_file": None,  # never read a developer's real .env
        "session_secret": "s3cret",
        "mcp_auth_token": "tok",
        "token_enc_key": "k",
    }
    base.update(overrides)
    return Settings(**base)


def test_secure_bearer_config_passes():
    _settings().validate_security()  # does not raise


def test_secure_oauth_config_passes():
    _settings(
        mcp_auth_token="",  # OAuth mode doesn't need the bearer token
        github_client_id="id",
        github_client_secret="sec",
        github_allowed_users="adamconde",
    ).validate_security()


def test_blank_session_secret_rejected():
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        _settings(session_secret="").validate_security()


def test_bearer_mode_requires_mcp_auth_token():
    with pytest.raises(ValueError, match="MCP_AUTH_TOKEN"):
        _settings(mcp_auth_token="").validate_security()


def test_oauth_mode_requires_allowlist():
    # GitHub OAuth enabled but no allowlist = fail-open; must be rejected.
    with pytest.raises(ValueError, match="GITHUB_ALLOWED_USERS"):
        _settings(
            mcp_auth_token="",
            github_client_id="id",
            github_client_secret="sec",
            github_allowed_users="",
        ).validate_security()


def test_oauth_mode_does_not_require_mcp_auth_token():
    # With OAuth enabled (and an allowlist), a blank MCP_AUTH_TOKEN is fine.
    _settings(
        mcp_auth_token="",
        github_client_id="id",
        github_client_secret="sec",
        github_allowed_users="adamconde",
    ).validate_security()


def test_multiple_problems_aggregated():
    with pytest.raises(ValueError) as exc:
        _settings(session_secret="", mcp_auth_token="").validate_security()
    assert "SESSION_SECRET" in str(exc.value)
    assert "MCP_AUTH_TOKEN" in str(exc.value)
