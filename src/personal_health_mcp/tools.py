"""MCP tool definitions.

Every read tool returns a dict whose ``providers`` key is listed first, so the
data provider is always named in the response. Tools are thin wrappers over the
:class:`~personal_health_mcp.aggregator.Aggregator`; date inputs are accepted as
``YYYY-MM-DD`` strings and expanded to an inclusive UTC day window.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from fastmcp import FastMCP

from .app import AppContext
from .metrics import all_metrics, get_metric
from .models import MetricPref, ResolutionMode


def _day_bounds(start: str, end: str | None = None) -> tuple[datetime, datetime]:
    """Expand ``YYYY-MM-DD`` strings into an inclusive UTC datetime window."""
    s = datetime.fromisoformat(start).date()
    e = datetime.fromisoformat(end).date() if end else s
    start_dt = datetime.combine(s, time.min, tzinfo=UTC)
    end_dt = datetime.combine(e, time.max, tzinfo=UTC)
    return start_dt, end_dt


# Metrics surfaced in the daily-summary composite tool.
_SUMMARY_METRICS = [
    "steps",
    "distance",
    "active_calories",
    "resting_heart_rate",
    "sleep_duration",
    "readiness_score",
]
# Metrics surfaced in the sleep composite tool.
_SLEEP_METRICS = [
    "sleep_duration",
    "sleep_deep_duration",
    "sleep_light_duration",
    "sleep_rem_duration",
    "sleep_awake_duration",
    "sleep_efficiency",
    "sleep_latency",
    "sleep_score",
]


def register_tools(mcp: FastMCP, ctx: AppContext) -> None:
    """Register all health tools on ``mcp``, bound to ``ctx``."""
    agg = ctx.aggregator
    store = ctx.store

    @mcp.tool(
        name="health_list_providers",
        annotations={"title": "List health providers", "readOnlyHint": True},
    )
    async def health_list_providers() -> dict:
        """List configured providers and their connection status.

        Returns:
            dict: ``{"providers": [{"name", "display_name", "connected",
            "configured", "last_sync", "last_error", "metrics": int}]}``.
        """
        out = []
        for name in sorted(ctx.providers):
            provider = ctx.providers[name]
            status = await store.get_status(name)
            out.append(
                {
                    "name": name,
                    "display_name": provider.display_name,
                    "connected": bool(status and status.connected),
                    "configured": await store.resolve_credentials(name) is not None,
                    "last_sync": status.last_sync if status else None,
                    "last_error": status.last_error if status else None,
                    "metrics": len(provider.supported_metrics()),
                }
            )
        return {"providers": out}

    @mcp.tool(
        name="health_provider_auth_status",
        annotations={"title": "Provider auth status", "readOnlyHint": True},
    )
    async def health_provider_auth_status(provider: str) -> dict:
        """Report whether a provider is connected and currently usable.

        Args:
            provider: Provider name (e.g. ``"oura"``).

        Returns:
            dict: ``{"provider", "configured", "connected", "token_valid",
            "last_error"}``.
        """
        if provider not in ctx.providers:
            return {"provider": provider, "error": "unknown provider"}
        status = await store.get_status(provider)
        token = await ctx.token_manager.get_access_token(provider)
        return {
            "provider": provider,
            "configured": await store.resolve_credentials(provider) is not None,
            "connected": bool(status and status.connected),
            "token_valid": token is not None,
            "last_error": status.last_error if status else None,
        }

    @mcp.tool(
        name="health_list_metrics",
        annotations={"title": "List available metrics", "readOnlyHint": True},
    )
    async def health_list_metrics() -> dict:
        """List metrics available given connected providers, with their providers.

        Returns:
            dict: ``{"metrics": [{"metric", "description", "kind",
            "canonical_unit", "providers": [names]}]}``.
        """
        connected = set(await agg.connected_providers())
        metrics = []
        for m in all_metrics():
            providers = [
                name
                for name in agg.providers_supporting(m.key)
                if name in connected
            ]
            if not providers:
                continue
            metrics.append(
                {
                    "metric": m.key,
                    "description": m.description,
                    "kind": m.kind.value,
                    "canonical_unit": m.canonical_unit,
                    "providers": providers,
                }
            )
        return {"metrics": metrics}

    @mcp.tool(
        name="health_get_metric",
        annotations={"title": "Get a health metric", "readOnlyHint": True},
    )
    async def health_get_metric(
        metric: str,
        start: str,
        end: str | None = None,
        unit: str | None = None,
        provider: str | None = None,
    ) -> dict:
        """Get a metric over a date range, resolved across providers.

        Args:
            metric: Canonical metric key (see ``health_list_metrics``).
            start: Start date ``YYYY-MM-DD``.
            end: End date ``YYYY-MM-DD`` (defaults to ``start``).
            unit: Optional display-unit override (e.g. ``"lb"``, ``"mi"``, ``"F"``).
            provider: Optional explicit provider; bypasses authority/auto resolution.

        Returns:
            dict: A response envelope ``{"providers", "metric", "unit", "mode",
            "resolution", "start", "end", "count", "points", "note"}`` where each
            point names the provider that supplied it.
        """
        get_metric(metric)  # validate; raises UnknownMetricError
        start_dt, end_dt = _day_bounds(start, end)
        env = await agg.get_metric(metric, start_dt, end_dt, unit=unit, provider=provider)
        return env.model_dump(mode="json")

    @mcp.tool(
        name="health_compare_metric",
        annotations={"title": "Compare a metric across providers", "readOnlyHint": True},
    )
    async def health_compare_metric(
        metric: str,
        start: str,
        end: str | None = None,
        unit: str | None = None,
    ) -> dict:
        """Show a metric from every provider side by side (no resolution applied).

        Use this to reconcile discrepancies before choosing an authority.

        Args:
            metric: Canonical metric key.
            start: Start date ``YYYY-MM-DD``.
            end: End date ``YYYY-MM-DD`` (defaults to ``start``).
            unit: Optional display-unit override.

        Returns:
            dict: ``{"metric", "unit", "start", "end", "providers": {name: [points]}}``.
        """
        get_metric(metric)
        start_dt, end_dt = _day_bounds(start, end)
        return _jsonable(await agg.compare_metric(metric, start_dt, end_dt, unit=unit))

    @mcp.tool(
        name="health_get_sleep",
        annotations={"title": "Get a night's sleep summary", "readOnlyHint": True},
    )
    async def health_get_sleep(date: str, unit: str | None = None) -> dict:
        """Get a composite sleep summary for one night.

        Args:
            date: Calendar date ``YYYY-MM-DD``.
            unit: Optional display-unit override (affects durations if applicable).

        Returns:
            dict: ``{"date", "providers", "metrics": {metric: {value, unit,
            provider}}}`` — provider named per metric.
        """
        return await _composite(agg, date, _SLEEP_METRICS, unit)

    @mcp.tool(
        name="health_get_daily_summary",
        annotations={"title": "Get a daily health summary", "readOnlyHint": True},
    )
    async def health_get_daily_summary(date: str, unit: str | None = None) -> dict:
        """Get a multi-metric summary for one day.

        Args:
            date: Calendar date ``YYYY-MM-DD``.
            unit: Optional display-unit override.

        Returns:
            dict: ``{"date", "providers", "metrics": {metric: {value, unit,
            provider}}}`` — provider named per metric.
        """
        return await _composite(agg, date, _SUMMARY_METRICS, unit)

    @mcp.tool(
        name="health_set_metric_authority",
        annotations={
            "title": "Set metric resolution preference",
            "readOnlyHint": False,
            "idempotentHint": True,
        },
    )
    async def health_set_metric_authority(
        metric: str,
        mode: str,
        authority: str | None = None,
        fallback_order: list[str] | None = None,
    ) -> dict:
        """Set how a metric is resolved when multiple providers have data.

        Args:
            metric: Canonical metric key.
            mode: ``"authority"`` or ``"auto"``.
            authority: Authority provider (required when ``mode="authority"``).
            fallback_order: Ordered fallback providers used if the authority is empty.

        Returns:
            dict: The saved preference.
        """
        get_metric(metric)
        resolved_mode = ResolutionMode(mode)
        pref = MetricPref(
            metric=metric,
            mode=resolved_mode,
            authority=authority,
            fallback_order=fallback_order or [],
        )
        await store.set_metric_pref(pref)
        return {
            "metric": metric,
            "mode": resolved_mode.value,
            "authority": authority,
            "fallback_order": pref.fallback_order,
        }


async def _composite(agg, date: str, metrics: list[str], unit: str | None) -> dict:
    """Resolve several metrics for a single day into a flat summary."""
    start_dt, end_dt = _day_bounds(date)
    out: dict[str, dict] = {}
    providers: list[str] = []
    for m in metrics:
        env = await agg.get_metric(m, start_dt, end_dt, unit=unit)
        if env.points:
            last = env.points[-1]
            out[m] = {"value": last.value, "unit": last.unit, "provider": last.provider}
            if last.provider not in providers:
                providers.append(last.provider)
    return {"date": date, "providers": providers, "metrics": out}


def _jsonable(data: dict) -> dict:
    """Coerce datetimes in a compare-metric result to ISO strings."""
    out = dict(data)
    for key in ("start", "end"):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return out
