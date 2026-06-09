"""Tests for MCP GitHub-auth helpers."""

from __future__ import annotations

from personal_health_mcp.mcp_auth import login_allowed


def test_login_allowed_rules():
    assert login_allowed("adamconde", {"adamconde"})
    assert login_allowed("AdamConde", {"adamconde"})  # case-insensitive
    assert not login_allowed("someone", {"adamconde"})
    assert login_allowed("anyone", set())  # empty allowlist = any authenticated user
