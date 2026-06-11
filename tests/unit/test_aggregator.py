"""Tests for the aggregation service."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.fakes import AuthExpiringProvider, FailingProvider, FakeProvider

from personal_health_mcp.aggregator import Aggregator
from personal_health_mcp.models import DataPoint, MetricPref, ResolutionMode
from personal_health_mcp.storage import Store

pytestmark = pytest.mark.asyncio

START = datetime(2030, 1, 1, tzinfo=UTC)
END = datetime(2030, 1, 8, tzinfo=UTC)


def weight_dp(provider: str, kg: float, day: int = 1) -> DataPoint:
    return DataPoint(
        metric="weight",
        value=kg,
        unit="kg",
        start=datetime(2030, 1, day, tzinfo=UTC),
        provider=provider,
    )


def make_token_getter(connected: set[str]):
    async def getter(name: str) -> str | None:
        return "token" if name in connected else None

    return getter


async def test_get_metric_includes_provider_and_converts_units(store: Store):
    providers = {
        "withings": FakeProvider("withings", {"weight": [weight_dp("withings", 80.0)]}),
        "oura": FakeProvider("oura", {"weight": [weight_dp("oura", 81.0)]}),
    }
    await store.set_metric_pref(
        MetricPref(metric="weight", mode=ResolutionMode.AUTHORITY, authority="withings")
    )
    agg = Aggregator(store, providers, make_token_getter({"withings", "oura"}))

    env = await agg.get_metric("weight", START, END, unit="kg")
    assert env.providers == ["withings"]
    assert env.resolution == "authority:withings"
    assert env.unit == "kg"
    assert env.points[0].value == 80.0
    assert env.points[0].provider == "withings"  # provenance present

    # Same call but request pounds -> server-side conversion.
    env_lb = await agg.get_metric("weight", START, END, unit="lb")
    assert env_lb.unit == "lb"
    assert round(env_lb.points[0].value, 2) == 176.37


async def test_disconnected_provider_is_skipped(store: Store):
    providers = {
        "withings": FakeProvider("withings", {"weight": [weight_dp("withings", 80.0)]}),
        "oura": FakeProvider("oura", {"weight": [weight_dp("oura", 81.0)]}),
    }
    # Authority is oura, but oura isn't connected -> fallback to withings.
    await store.set_metric_pref(
        MetricPref(
            metric="weight",
            mode=ResolutionMode.AUTHORITY,
            authority="oura",
            fallback_order=["withings"],
        )
    )
    agg = Aggregator(store, providers, make_token_getter({"withings"}))
    env = await agg.get_metric("weight", START, END)
    assert env.resolution == "fallback:withings"


async def test_failing_provider_is_isolated_and_recorded(store: Store):
    providers = {
        "withings": FailingProvider("withings", {"weight": []}),
        "oura": FakeProvider("oura", {"weight": [weight_dp("oura", 81.0)]}),
    }
    agg = Aggregator(store, providers, make_token_getter({"withings", "oura"}))
    env = await agg.get_metric("weight", START, END)  # auto by default
    # withings failed -> only oura contributes
    assert env.providers == ["oura"]
    status = await store.get_status("withings")
    assert status is not None and status.last_error is not None


async def test_auth_error_triggers_refresh_and_retry(store: Store):
    provider = AuthExpiringProvider("withings", {"weight": [weight_dp("withings", 80.0)]})
    refreshed: list[str] = []

    async def force_refresh(name: str) -> str | None:
        refreshed.append(name)
        return "fresh-token"

    agg = Aggregator(
        store, {"withings": provider}, make_token_getter({"withings"}), force_refresh=force_refresh
    )
    env = await agg.get_metric("weight", START, END, unit="kg")  # auto by default
    assert refreshed == ["withings"]  # refresh attempted once
    assert len(provider.calls) == 2  # initial 401 + retry
    assert env.providers == ["withings"] and env.points[0].value == 80.0


async def test_auth_error_without_refresh_is_isolated(store: Store):
    # No force_refresh wired -> the 401 is reported, not retried, and yields no data.
    provider = AuthExpiringProvider("withings", {"weight": [weight_dp("withings", 80.0)]})
    agg = Aggregator(store, {"withings": provider}, make_token_getter({"withings"}))
    env = await agg.get_metric("weight", START, END)
    assert env.providers == []
    status = await store.get_status("withings")
    assert status is not None and status.last_error is not None


async def test_explicit_provider_bypasses_resolution(store: Store):
    providers = {
        "withings": FakeProvider("withings", {"weight": [weight_dp("withings", 80.0)]}),
        "oura": FakeProvider("oura", {"weight": [weight_dp("oura", 81.0)]}),
    }
    await store.set_metric_pref(
        MetricPref(metric="weight", mode=ResolutionMode.AUTHORITY, authority="withings")
    )
    agg = Aggregator(store, providers, make_token_getter({"withings", "oura"}))
    env = await agg.get_metric("weight", START, END, unit="kg", provider="oura")
    assert env.resolution == "explicit:oura"
    assert env.points[0].value == 81.0


async def test_compare_metric_returns_all_providers_unresolved(store: Store):
    providers = {
        "withings": FakeProvider("withings", {"weight": [weight_dp("withings", 80.0)]}),
        "oura": FakeProvider("oura", {"weight": [weight_dp("oura", 81.0)]}),
    }
    agg = Aggregator(store, providers, make_token_getter({"withings", "oura"}))
    out = await agg.compare_metric("weight", START, END, unit="kg")
    assert set(out["providers"]) == {"withings", "oura"}
    assert out["providers"]["withings"][0]["value"] == 80.0
    assert out["providers"]["oura"][0]["value"] == 81.0


async def test_providers_supporting_and_connected(store: Store):
    providers = {
        "withings": FakeProvider("withings", units={"weight": "kg"}),
        "oura": FakeProvider("oura", units={"sleep_duration": "s"}),
    }
    agg = Aggregator(store, providers, make_token_getter({"withings"}))
    assert agg.providers_supporting("weight") == ["withings"]
    assert await agg.connected_providers() == ["withings"]
