"""Tests for the resolution engine."""

from __future__ import annotations

from datetime import UTC, datetime

from personal_health_mcp.metrics import get_metric
from personal_health_mcp.models import DataPoint, MetricPref, ResolutionMode
from personal_health_mcp.resolution import resolve


def dp(provider: str, value: float, day: int, metric="steps", unit="count") -> DataPoint:
    return DataPoint(
        metric=metric,
        value=value,
        unit=unit,
        start=datetime(2030, 1, day, tzinfo=UTC),
        provider=provider,
    )


STEPS = get_metric("steps")


def test_authority_present_uses_authority():
    pts = {
        "oura": [dp("oura", 100, 1)],
        "withings": [dp("withings", 200, 1)],
    }
    pref = MetricPref(metric="steps", mode=ResolutionMode.AUTHORITY, authority="oura")
    res = resolve(STEPS, pts, pref)
    assert res.resolution == "authority:oura"
    assert [p.value for p in res.points] == [100]
    assert res.providers == ["oura"]


def test_authority_empty_falls_back_in_order():
    pts = {
        "oura": [],
        "withings": [dp("withings", 200, 1)],
        "google": [dp("google", 300, 1)],
    }
    pref = MetricPref(
        metric="steps",
        mode=ResolutionMode.AUTHORITY,
        authority="oura",
        fallback_order=["google", "withings"],
    )
    res = resolve(STEPS, pts, pref)
    assert res.resolution == "fallback:google"
    assert [p.value for p in res.points] == [300]
    assert res.note is not None


def test_authority_and_fallbacks_all_empty():
    pref = MetricPref(
        metric="steps",
        mode=ResolutionMode.AUTHORITY,
        authority="oura",
        fallback_order=["google"],
    )
    res = resolve(STEPS, {"oura": [], "google": []}, pref)
    assert res.resolution == "authority:none"
    assert res.points == []
    assert res.note is not None


def test_auto_unions_distinct_day_buckets():
    pts = {
        "oura": [dp("oura", 100, 1)],
        "withings": [dp("withings", 200, 2)],
    }
    res = resolve(STEPS, pts, MetricPref(metric="steps", mode=ResolutionMode.AUTO))
    assert res.resolution == "auto"
    assert [p.value for p in res.points] == [100, 200]
    assert set(res.providers) == {"oura", "withings"}


def test_auto_most_recent_wins_for_same_bucket():
    # Two providers report day 1; the later-timestamped one wins.
    early = DataPoint(
        metric="weight", value=80.0, unit="kg",
        start=datetime(2030, 1, 1, 6, tzinfo=UTC), provider="oura",
    )
    late = DataPoint(
        metric="weight", value=81.0, unit="kg",
        start=datetime(2030, 1, 1, 20, tzinfo=UTC), provider="withings",
    )
    weight = get_metric("weight")  # SAMPLE -> exact-timestamp buckets
    res = resolve(weight, {"oura": [early], "withings": [late]},
                  MetricPref(metric="weight", mode=ResolutionMode.AUTO))
    # distinct timestamps -> both kept, sorted
    assert [p.value for p in res.points] == [80.0, 81.0]


def test_auto_daily_same_day_tiebreak_by_priority():
    a = dp("oura", 100, 1)
    b = dp("withings", 200, 1)
    res = resolve(
        STEPS,
        {"oura": [a], "withings": [b]},
        MetricPref(metric="steps", mode=ResolutionMode.AUTO),
        priority=["withings", "oura"],  # withings ranked first
    )
    assert len(res.points) == 1
    assert res.points[0].provider == "withings"


def test_auto_daily_tiebreak_deterministic_without_priority():
    # No priority supplied: a same-day tie must resolve to the same provider
    # regardless of dict insertion order (sorted-name rank, not iteration order).
    a = dp("withings", 200, 1)
    b = dp("oura", 100, 1)
    pref = MetricPref(metric="steps", mode=ResolutionMode.AUTO)
    res1 = resolve(STEPS, {"withings": [a], "oura": [b]}, pref)
    res2 = resolve(STEPS, {"oura": [b], "withings": [a]}, pref)
    assert res1.points[0].provider == res2.points[0].provider == "oura"
