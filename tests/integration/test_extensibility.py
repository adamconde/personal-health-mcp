"""Extensibility test: a new provider drops in with no core changes.

Registers a stub 4th provider via the public ``@register`` mechanism and asserts
it surfaces through ``build_providers`` and the MCP tool surface without touching
the aggregator, resolution, units, or tools.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastmcp import Client

from personal_health_mcp.aggregator import Aggregator
from personal_health_mcp.app import create_context
from personal_health_mcp.models import DataPoint
from personal_health_mcp.providers import build_providers
from personal_health_mcp.providers.base import (
    PROVIDER_REGISTRY,
    HealthProvider,
    OAuthConfig,
    ProviderCapability,
    register,
)
from personal_health_mcp.server import build_mcp

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fourth_provider():
    """Register a stub provider for the duration of a test, then unregister."""

    @register
    class FitbitProvider(HealthProvider):
        name = "fitbit"
        display_name = "Fitbit"
        oauth = OAuthConfig(
            authorize_url="https://example/authorize",
            token_url="https://example/token",
            scopes=["activity"],
        )

        def capabilities(self):
            return [ProviderCapability(metric="steps", native_units=["count"])]

        async def fetch_metric(self, metric, start, end, access_token, native_unit=None):
            return [
                DataPoint(
                    metric="steps",
                    value=12345.0,
                    unit="count",
                    start=datetime(2030, 1, 1, tzinfo=UTC),
                    provider="fitbit",
                )
            ]

    yield FitbitProvider
    PROVIDER_REGISTRY.pop("fitbit", None)


async def test_new_provider_in_registry(fourth_provider):
    providers = build_providers()
    assert "fitbit" in providers
    assert providers["fitbit"].supports("steps")


async def test_new_provider_surfaces_in_tools(tmp_path, settings, fourth_provider):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ext.db'}"
    ctx = create_context(settings, database_url=db_url)
    await ctx.startup()

    async def token_getter(name: str):
        return "tok"

    # Rebuild aggregator with all registered providers (incl. the new one).
    ctx.aggregator = Aggregator(ctx.store, ctx.providers, token_getter)
    try:
        mcp = build_mcp(ctx)
        async with Client(mcp) as client:
            res = await client.call_tool("health_list_metrics", {})
            metrics = json.loads(res.content[0].text)["metrics"]
            steps = next(m for m in metrics if m["metric"] == "steps")
            assert "fitbit" in steps["providers"]

            res = await client.call_tool(
                "health_get_metric",
                {"metric": "steps", "start": "2030-01-01", "provider": "fitbit"},
            )
            env = json.loads(res.content[0].text)
            assert env["providers"] == ["fitbit"]
            assert env["points"][0]["value"] == 12345.0
    finally:
        await ctx.shutdown()
