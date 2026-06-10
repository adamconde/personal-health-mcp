"""Tests for shared provider helpers."""

from __future__ import annotations

import pytest

from personal_health_mcp.providers.base import ProviderAuthError, raise_for_auth


@pytest.mark.parametrize("code", [401, 403])
def test_raise_for_auth_raises_on_auth_codes(code: int):
    with pytest.raises(ProviderAuthError):
        raise_for_auth(code, "Test")


@pytest.mark.parametrize("code", [200, 404, 429, 500])
def test_raise_for_auth_noop_on_non_auth_codes(code: int):
    # Non-auth statuses are left for raise_for_status / envelope handling.
    raise_for_auth(code, "Test")
