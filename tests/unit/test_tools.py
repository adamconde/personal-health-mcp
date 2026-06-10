"""Tests for MCP tool helpers (date-window validation)."""

from __future__ import annotations

import pytest

from personal_health_mcp.tools import _MAX_RANGE_DAYS, _day_bounds


def test_day_bounds_single_day_inclusive():
    start_dt, end_dt = _day_bounds("2030-01-01")
    assert start_dt.isoformat() == "2030-01-01T00:00:00+00:00"
    assert end_dt.date().isoformat() == "2030-01-01"
    assert end_dt.hour == 23 and end_dt.minute == 59


def test_day_bounds_rejects_malformed_date():
    with pytest.raises(ValueError, match="Invalid date"):
        _day_bounds("not-a-date")


def test_day_bounds_rejects_datetime_string():
    # fromisoformat would accept this and silently drop the time; we reject it.
    with pytest.raises(ValueError, match="Invalid date"):
        _day_bounds("2030-01-01T12:00:00")


def test_day_bounds_rejects_end_before_start():
    with pytest.raises(ValueError, match="precedes"):
        _day_bounds("2030-01-10", "2030-01-01")


def test_day_bounds_rejects_oversized_range():
    with pytest.raises(ValueError, match="too large"):
        _day_bounds("2000-01-01", "9999-12-31")


def test_day_bounds_allows_range_at_limit():
    # A window exactly at the cap is allowed.
    from datetime import date, timedelta

    start = date(2020, 1, 1)
    end = start + timedelta(days=_MAX_RANGE_DAYS)
    _day_bounds(start.isoformat(), end.isoformat())  # does not raise
