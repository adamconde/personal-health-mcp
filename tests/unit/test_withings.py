"""Tests for Withings response parsing (pagination + malformed-record skipping)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from personal_health_mcp.providers.withings import WithingsProvider

pytestmark = pytest.mark.asyncio

START = datetime(2030, 1, 1, tzinfo=UTC)
END = datetime(2030, 1, 8, tzinfo=UTC)


async def test_pagination_stops_without_forward_progress(monkeypatch):
    # 'more' is truthy but 'offset' never advances past 0 -> must not loop forever.
    body = {
        "measuregrps": [{"date": 1893456000, "measures": [{"type": 1, "value": 80, "unit": 0}]}],
        "more": 1,
        "offset": 0,
    }
    calls = {"n": 0}

    async def fake_post(path, data, token):
        calls["n"] += 1
        return body

    p = WithingsProvider()
    monkeypatch.setattr(p, "_post", fake_post)
    pts = await p._fetch_measures("weight", START, END, "tok")
    assert calls["n"] == 1  # stopped after one page (no progress), not infinite
    assert [round(x.value, 1) for x in pts] == [80.0]


async def test_skips_malformed_measure_groups(monkeypatch):
    body = {
        "measuregrps": [
            {"measures": [{"type": 1, "value": 80, "unit": 0}]},  # no 'date' -> skip group
            {"date": 1893456000, "measures": [{"type": 1}]},  # no value/unit -> skip measure
            {"date": 1893456000, "measures": [{"type": 1, "value": 815, "unit": -1}]},  # good
        ],
        "more": 0,
    }

    async def fake_post(path, data, token):
        return body

    p = WithingsProvider()
    monkeypatch.setattr(p, "_post", fake_post)
    pts = await p._fetch_measures("weight", START, END, "tok")
    # Only the well-formed group survives; the rest are skipped, not fatal.
    assert [round(x.value, 1) for x in pts] == [81.5]
