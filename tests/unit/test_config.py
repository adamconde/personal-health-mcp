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


# ── web login settings ───────────────────────────────────────────────────


def test_web_password_cidrs_default_is_lan():
    nets = _settings().web_password_allowed_networks()
    from ipaddress import ip_address

    assert any(ip_address("192.168.1.5") in n for n in nets)
    assert any(ip_address("10.1.2.3") in n for n in nets)
    assert any(ip_address("127.0.0.1") in n for n in nets)
    assert not any(ip_address("8.8.8.8") in n for n in nets)


def test_web_password_cidrs_explicit_overrides_default():
    from ipaddress import ip_address

    nets = _settings(web_password_allowed_cidrs="203.0.113.0/24").web_password_allowed_networks()
    assert [str(n) for n in nets] == ["203.0.113.0/24"]
    assert any(ip_address("203.0.113.9") in n for n in nets)
    assert not any(ip_address("192.168.1.5") in n for n in nets)  # LAN default no longer applied


def test_trusted_proxy_networks_parse():
    nets = _settings(trusted_proxy_cidrs="172.16.0.0/12, garbage, 10.0.0.0/8").trusted_proxy_networks()
    assert [str(n) for n in nets] == ["172.16.0.0/12", "10.0.0.0/8"]  # invalid entry skipped


def test_trusted_proxy_networks_empty_by_default():
    assert _settings().trusted_proxy_networks() == []


def test_web_github_login_enabled():
    assert not _settings().web_github_login_enabled
    assert _settings(github_client_id="id", github_client_secret="sec").web_github_login_enabled


def test_github_web_redirect_uri():
    s = _settings(public_base_url="https://health.example")
    assert s.github_web_redirect_uri == "https://health.example/auth/callback/web"
