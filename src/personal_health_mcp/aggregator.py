"""Aggregation service.

Orchestrates the read path: fetch canonical points from each connected provider
that supplies a metric, resolve them per the user's preference, convert to the
display unit, and wrap everything in a provider-attributed envelope.

Decoupled from OAuth: it receives a ``token_getter`` callable that returns a
valid access token (refresh handled upstream) so it can be unit-tested with a
fake token source and fake providers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from .display import resolve_display_unit
from .metrics import get_metric
from .models import DataPoint, ResolutionMode, ResolvedPoint, ResponseEnvelope
from .providers.base import HealthProvider, ProviderAuthError
from .resolution import resolve
from .storage import Store
from .timeutil import now_utc
from .units import convert, format_quantity

# async (provider_name) -> access_token or None if not connected / unavailable.
TokenGetter = Callable[[str], Awaitable[str | None]]


class Aggregator:
    """Resolve and serve health metrics across providers.

    Args:
        store: Persistence layer (preferences, status).
        providers: Mapping of provider name -> provider instance.
        token_getter: Async callable returning a valid access token for a
            provider, or ``None`` if the provider isn't connected.
        force_refresh: Optional async callable that force-refreshes a provider's
            token (used to recover from a mid-fetch 401). If ``None``, an auth
            failure is reported without a retry.
    """

    def __init__(
        self,
        store: Store,
        providers: dict[str, HealthProvider],
        token_getter: TokenGetter,
        force_refresh: TokenGetter | None = None,
    ) -> None:
        self._store = store
        self._providers = providers
        self._token_getter = token_getter
        self._force_refresh = force_refresh

    # ── discovery ────────────────────────────────────────────────────────
    async def connected_providers(self) -> list[str]:
        """Return names of providers that currently have a usable token."""
        connected = []
        for name in sorted(self._providers):
            if await self._token_getter(name):
                connected.append(name)
        return connected

    def providers_supporting(self, metric: str) -> list[str]:
        """Return names of providers that declare support for ``metric``."""
        return sorted(
            name for name, p in self._providers.items() if p.supports(metric)
        )

    # ── fetch ────────────────────────────────────────────────────────────
    async def _fetch_one(
        self,
        name: str,
        metric: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[DataPoint], str | None]:
        """Fetch a metric from a single provider.

        Returns ``(points, error)``: ``error`` is a message string when the
        fetch failed (so the caller can surface it instead of masking it as 'no
        data'), or ``None`` on success. A missing token (provider not connected)
        is not an error — it returns ``([], None)``.

        On a token rejection (:class:`ProviderAuthError`) the token is refreshed
        once and the fetch retried, so an expired/revoked token recovers instead
        of failing.
        """
        token = await self._token_getter(name)
        if not token:
            return [], None
        provider = self._providers[name]
        try:
            try:
                points = await provider.fetch_metric(metric, start, end, token)
            except ProviderAuthError:
                token = await self._force_refresh(name) if self._force_refresh else None
                if not token:
                    raise
                points = await provider.fetch_metric(metric, start, end, token)
        except Exception as exc:  # noqa: BLE001 - isolate provider failures
            message = str(exc) or exc.__class__.__name__
            await self._store.set_status(name, last_error=f"{metric}: {message}")
            return [], message
        await self._store.set_status(name, last_sync=now_utc().isoformat(), clear_error=True)
        return points, None

    async def _gather(
        self,
        names: list[str],
        metric: str,
        start: datetime,
        end: datetime,
    ) -> tuple[dict[str, list[DataPoint]], dict[str, str]]:
        """Fetch ``metric`` from each provider in ``names`` concurrently.

        Returns ``(points_by_provider, errors)`` where ``errors`` maps a
        provider name to its failure message (absent when the provider succeeded).
        """
        results = await asyncio.gather(
            *(self._fetch_one(name, metric, start, end) for name in names)
        )
        points_by_provider: dict[str, list[DataPoint]] = {}
        errors: dict[str, str] = {}
        for name, (points, error) in zip(names, results, strict=True):
            points_by_provider[name] = points
            if error is not None:
                errors[name] = error
        return points_by_provider, errors

    def _to_display(self, points: list[DataPoint], display_unit: str) -> list[ResolvedPoint]:
        """Convert canonical points to display-unit resolved points."""
        out = []
        for p in points:
            value = convert(p.value, p.unit, display_unit)
            out.append(
                ResolvedPoint(
                    value=value,
                    unit=display_unit,
                    formatted=format_quantity(value, display_unit),
                    start=p.start,
                    end=p.end,
                    provider=p.provider,
                    device=p.device,
                )
            )
        return out

    # ── public API ───────────────────────────────────────────────────────
    async def get_metric(
        self,
        metric_key: str,
        start: datetime,
        end: datetime,
        unit: str | None = None,
        provider: str | None = None,
    ) -> ResponseEnvelope:
        """Resolve and return a metric over a window.

        Args:
            metric_key: Canonical metric key.
            start: Window start (UTC).
            end: Window end (UTC).
            unit: Optional display-unit override.
            provider: Optional explicit provider; bypasses resolution.

        Returns:
            A :class:`ResponseEnvelope` (provider attribution first).
        """
        metric = get_metric(metric_key)
        unit_prefs = await self._store.get_unit_prefs()
        display_unit = resolve_display_unit(metric, unit_prefs, unit)

        if provider is not None:
            return await self._explicit_provider(
                metric_key, metric, start, end, display_unit, provider
            )

        candidates = self.providers_supporting(metric_key)
        points_by_provider, errors = await self._gather(candidates, metric_key, start, end)
        pref = await self._store.get_metric_pref(metric_key)
        # Auto-mode tie-break order is derived from the providers that returned
        # data (resolve ranks them in sorted-name order); no second token pass.
        result = resolve(metric, points_by_provider, pref)
        resolved = self._to_display(result.points, display_unit)

        # When the series is empty *because* providers errored, say so — an empty
        # result with no note otherwise reads as a genuine absence of data.
        note = result.note
        if errors and not resolved:
            detail = "; ".join(f"{name}: {msg}" for name, msg in sorted(errors.items()))
            note = f"No data returned; provider error(s): {detail}"

        return ResponseEnvelope(
            providers=result.providers,
            metric=metric_key,
            unit=display_unit,
            mode=pref.mode,
            resolution=result.resolution,
            start=start,
            end=end,
            count=len(resolved),
            points=resolved,
            errors=errors,
            note=note,
        )

    async def _explicit_provider(
        self,
        metric_key: str,
        metric,
        start: datetime,
        end: datetime,
        display_unit: str,
        provider: str,
    ) -> ResponseEnvelope:
        """Serve a metric from one named provider, bypassing resolution."""
        note = None
        errors: dict[str, str] = {}
        if provider not in self._providers:
            note = f"Unknown provider {provider!r}."
            points: list[DataPoint] = []
        elif not self._providers[provider].supports(metric_key):
            note = f"Provider {provider!r} does not supply {metric_key!r}."
            points = []
        else:
            points, error = await self._fetch_one(provider, metric_key, start, end)
            if error is not None:
                errors[provider] = error
                note = f"Provider {provider!r} failed: {error}"
            elif not points:
                note = f"Provider {provider!r} returned no data for the window."
        resolved = self._to_display(sorted(points, key=lambda p: p.start), display_unit)
        return ResponseEnvelope(
            providers=[provider] if resolved else [],
            metric=metric_key,
            unit=display_unit,
            mode=ResolutionMode.AUTHORITY,
            resolution=f"explicit:{provider}",
            start=start,
            end=end,
            count=len(resolved),
            points=resolved,
            errors=errors,
            note=note,
        )

    async def compare_metric(
        self,
        metric_key: str,
        start: datetime,
        end: datetime,
        unit: str | None = None,
    ) -> dict:
        """Return per-provider values side by side (never resolved).

        Useful for reconciling discrepancies before choosing an authority.

        Returns:
            A dict with ``metric``, ``unit``, ``start``, ``end``, a ``providers``
            mapping of name -> list of resolved points, and ``errors`` (name ->
            failure message) for any provider that errored.
        """
        metric = get_metric(metric_key)
        unit_prefs = await self._store.get_unit_prefs()
        display_unit = resolve_display_unit(metric, unit_prefs, unit)
        candidates = self.providers_supporting(metric_key)
        points_by_provider, errors = await self._gather(candidates, metric_key, start, end)
        return {
            "metric": metric_key,
            "unit": display_unit,
            "start": start,
            "end": end,
            "providers": {
                name: [p.model_dump(mode="json") for p in self._to_display(pts, display_unit)]
                for name, pts in points_by_provider.items()
            },
            "errors": errors,
        }
