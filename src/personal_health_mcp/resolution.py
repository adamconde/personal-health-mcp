"""Multi-provider resolution engine.

Given canonical data points keyed by provider plus the user's preference for a
metric, decide which points to return:

* **authority** — use the authority provider's points; if it has none in the
  window, walk the ordered fallback list and use the first provider that does.
* **auto** — for each time bucket (calendar day for daily metrics, exact
  timestamp otherwise) keep the most recent value across all providers, breaking
  ties by a stable provider priority.

The engine is unit-agnostic: it operates purely on canonical values and records
which branch fired so the result is explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import MetricDef, MetricKind
from .models import DataPoint, MetricPref, ResolutionMode


@dataclass
class ResolutionResult:
    """Outcome of resolving one metric.

    Attributes:
        points: The selected canonical data points, sorted by start time.
        resolution: Which branch fired (e.g. ``"authority:oura"``, ``"auto"``).
        providers: Distinct providers contributing to ``points``.
        note: Optional diagnostic (e.g. why empty).
    """

    points: list[DataPoint] = field(default_factory=list)
    resolution: str = ""
    providers: list[str] = field(default_factory=list)
    note: str | None = None


def _bucket_key(point: DataPoint, kind: MetricKind) -> object:
    """Return the bucket key for grouping in auto mode."""
    if kind == MetricKind.DAILY:
        return point.start.date()
    return point.start


def _distinct_providers(points: list[DataPoint]) -> list[str]:
    seen: list[str] = []
    for p in points:
        if p.provider not in seen:
            seen.append(p.provider)
    return seen


def resolve(
    metric: MetricDef,
    points_by_provider: dict[str, list[DataPoint]],
    pref: MetricPref,
    priority: list[str] | None = None,
) -> ResolutionResult:
    """Resolve a metric across providers per ``pref``.

    Args:
        metric: The metric definition (used for bucket granularity).
        points_by_provider: Canonical points grouped by provider name.
        pref: User resolution preference for this metric.
        priority: Stable provider ordering for auto-mode tie-breaks. Defaults to
            the sorted provider names for determinism.

    Returns:
        A :class:`ResolutionResult`.
    """
    non_empty = {p: pts for p, pts in points_by_provider.items() if pts}

    if pref.mode == ResolutionMode.AUTHORITY:
        return _resolve_authority(non_empty, pref)
    return _resolve_auto(metric, non_empty, priority)


def _resolve_authority(
    non_empty: dict[str, list[DataPoint]],
    pref: MetricPref,
) -> ResolutionResult:
    """Authority-then-fallback resolution."""
    if pref.authority and pref.authority in non_empty:
        pts = sorted(non_empty[pref.authority], key=lambda p: p.start)
        return ResolutionResult(
            points=pts,
            resolution=f"authority:{pref.authority}",
            providers=[pref.authority],
        )
    for fb in pref.fallback_order:
        if fb in non_empty:
            pts = sorted(non_empty[fb], key=lambda p: p.start)
            return ResolutionResult(
                points=pts,
                resolution=f"fallback:{fb}",
                providers=[fb],
                note=(
                    f"Authority {pref.authority!r} had no data; "
                    f"used fallback {fb!r}."
                ),
            )
    return ResolutionResult(
        points=[],
        resolution="authority:none",
        providers=[],
        note="No data from the authority or any fallback provider.",
    )


def _resolve_auto(
    metric: MetricDef,
    non_empty: dict[str, list[DataPoint]],
    priority: list[str] | None,
) -> ResolutionResult:
    """Most-recent-per-bucket resolution across all providers."""
    order = priority or sorted(non_empty)
    rank = {name: i for i, name in enumerate(order)}

    def sort_key(p: DataPoint) -> tuple:
        # Prefer the most recently *recorded* value; for daily metrics whose
        # start is the day, the tie-break by provider rank decides.
        return (p.start, -rank.get(p.provider, len(order)))

    buckets: dict[object, DataPoint] = {}
    for points in non_empty.values():
        for p in points:
            key = _bucket_key(p, metric.kind)
            current = buckets.get(key)
            if current is None or sort_key(p) > sort_key(current):
                buckets[key] = p

    chosen = sorted(buckets.values(), key=lambda p: p.start)
    return ResolutionResult(
        points=chosen,
        resolution="auto",
        providers=_distinct_providers(chosen),
        note=None if chosen else "No data from any connected provider.",
    )
