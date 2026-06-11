"""Integration tests for the MCP tool surface and server wiring."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from tests.fakes import FakeProvider

from personal_health_mcp.app import AppContext, create_context
from personal_health_mcp.models import DataPoint, MetricPref, ResolutionMode
from personal_health_mcp.server import build_mcp, create_asgi_app

pytestmark = pytest.mark.asyncio


def weight_dp(provider: str, kg: float, day: int = 1) -> DataPoint:
    return DataPoint(
        metric="weight",
        value=kg,
        unit="kg",
        start=datetime(2030, 1, day, tzinfo=UTC),
        provider=provider,
    )


@pytest_asyncio.fixture
async def ctx(tmp_path, settings) -> AppContext:
    """An AppContext with fake, always-connected providers and seeded data."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}"
    context = create_context(settings, database_url=db_url)
    await context.startup()
    # Replace real providers with fakes carrying canned data.
    steps_dp = DataPoint(
        metric="steps", value=8000.0, unit="count",
        start=datetime(2030, 1, 1, tzinfo=UTC), provider="oura",
    )
    sleep_dp = DataPoint(
        metric="sleep_duration", value=28800.0, unit="s",
        start=datetime(2030, 1, 1, tzinfo=UTC), provider="oura",
    )
    context.providers = {
        "withings": FakeProvider("withings", {"weight": [weight_dp("withings", 80.0)]}),
        "oura": FakeProvider(
            "oura",
            {
                "weight": [weight_dp("oura", 81.0)],
                "steps": [steps_dp],
                "sleep_duration": [sleep_dp],
            },
        ),
    }

    async def token_getter(name: str):
        return "tok" if name in context.providers else None

    from personal_health_mcp.aggregator import Aggregator

    context.aggregator = Aggregator(context.store, context.providers, token_getter)
    try:
        yield context
    finally:
        await context.shutdown()


async def _call(client: Client, name: str, args: dict) -> dict:
    result = await client.call_tool(name, args)
    return json.loads(result.content[0].text)


async def test_tools_listed(ctx: AppContext):
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        tools = {t.name for t in await client.list_tools()}
    assert {
        "health_list_providers",
        "health_get_metric",
        "health_compare_metric",
        "health_set_metric_authority",
        "health_get_sleep",
        "health_list_metrics",
    } <= tools


async def test_get_metric_names_provider_and_resolves(ctx: AppContext):
    await ctx.store.set_metric_pref(
        MetricPref(metric="weight", mode=ResolutionMode.AUTHORITY, authority="withings")
    )
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        env = await _call(
            client,
            "health_get_metric",
            {"metric": "weight", "start": "2030-01-01", "unit": "kg"},
        )
    assert env["providers"] == ["withings"]
    assert env["resolution"] == "authority:withings"
    assert env["points"][0]["provider"] == "withings"
    assert env["points"][0]["value"] == 80.0


async def test_get_metric_unit_override(ctx: AppContext):
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        env = await _call(
            client,
            "health_get_metric",
            {"metric": "weight", "start": "2030-01-01", "unit": "lb", "provider": "oura"},
        )
    assert env["unit"] == "lb"
    assert round(env["points"][0]["value"], 2) == 178.57  # 81 kg -> lb
    assert env["resolution"] == "explicit:oura"


async def test_compare_metric_lists_all_providers(ctx: AppContext):
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        out = await _call(
            client, "health_compare_metric", {"metric": "weight", "start": "2030-01-01"}
        )
    assert set(out["providers"]) == {"withings", "oura"}


async def test_set_authority_then_applied(ctx: AppContext):
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        await _call(
            client,
            "health_set_metric_authority",
            {"metric": "weight", "mode": "authority", "authority": "oura"},
        )
        env = await _call(
            client,
            "health_get_metric",
            {"metric": "weight", "start": "2030-01-01", "unit": "kg"},
        )
    assert env["resolution"] == "authority:oura"
    assert env["points"][0]["value"] == 81.0


async def test_list_metrics_only_connected(ctx: AppContext):
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        out = await _call(client, "health_list_metrics", {})
    metric_keys = {m["metric"] for m in out["metrics"]}
    assert {"weight", "steps", "sleep_duration"} <= metric_keys


async def test_daily_summary_composite_names_provider(ctx: AppContext):
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        out = await _call(client, "health_get_daily_summary", {"date": "2030-01-01"})
    assert out["date"] == "2030-01-01"
    assert out["metrics"]["steps"]["value"] == 8000.0
    assert out["metrics"]["steps"]["provider"] == "oura"
    assert "oura" in out["providers"]


async def test_sleep_composite(ctx: AppContext):
    mcp = build_mcp(ctx)
    async with Client(mcp) as client:
        out = await _call(client, "health_get_sleep", {"date": "2030-01-01"})
    assert out["metrics"]["sleep_duration"]["value"] == 28800.0
    assert out["metrics"]["sleep_duration"]["provider"] == "oura"


# ── HTTP transport: bearer auth gate ──────────────────────────────────────
async def test_mcp_requires_bearer_token(ctx: AppContext):
    app = create_asgi_app(ctx)  # settings.mcp_auth_token == "test-mcp-token"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        # Unauthenticated request to the MCP mount is rejected.
        resp = await http.post("/mcp/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        assert resp.status_code == 401
        # Healthz is open.
        assert (await http.get("/healthz")).status_code == 200
