"""Canonical metric registry.

Every metric the server can serve is declared here exactly once, with its
canonical storage unit and kind. Providers map their raw responses into these
canonical units; the resolution engine and aggregator are driven entirely by
this table. Adding a metric is a one-line edit here plus provider capability
declarations — no control-flow changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .units import dimension_of


class MetricKind(StrEnum):
    """Temporal shape of a metric.

    Values:
        DAILY: One value per calendar day (e.g. step count).
        SAMPLE: Point-in-time measurement / time series (e.g. weight, heart rate).
        INTERVAL: A bounded session with start/end (e.g. a sleep period, workout).
    """

    DAILY = "daily"
    SAMPLE = "sample"
    INTERVAL = "interval"


@dataclass(frozen=True)
class MetricDef:
    """Definition of one canonical metric.

    Attributes:
        key: Stable canonical identifier (snake_case).
        canonical_unit: Unit values are stored/compared in (from ``units``).
        kind: Temporal shape, see :class:`MetricKind`.
        description: Human-readable summary.
        pref_group: Optional display-preference group (``"mass"``, ``"distance"``,
            ``"height"``, ``"temperature"``). Groups decouple the *semantic* display
            choice a user makes from the *physical* dimension used for conversion —
            e.g. height and distance share the ``length`` dimension but belong to
            different preference groups so a user can pick miles for distance and
            centimetres for height independently. ``None`` means no user-facing
            preference (always canonical unless a per-call override is given).
    """

    key: str
    canonical_unit: str
    kind: MetricKind
    description: str
    pref_group: str | None = None

    @property
    def dimension(self) -> str:
        """Physical dimension of this metric's canonical unit."""
        return dimension_of(self.canonical_unit)


def _m(
    key: str,
    unit: str,
    kind: MetricKind,
    desc: str,
    pref_group: str | None = None,
) -> MetricDef:
    return MetricDef(
        key=key, canonical_unit=unit, kind=kind, description=desc, pref_group=pref_group
    )


# Display-preference groups: group -> (choices, mapping of choice -> concrete unit
# per physical dimension). The web UI offers ``choices``; resolution picks the
# concrete unit appropriate to each metric's dimension (e.g. temperature "F" maps
# to "Fd" for a temperature *deviation* metric).
PREF_GROUPS: dict[str, dict[str, object]] = {
    "mass": {"choices": ["kg", "lb", "st"], "default": "lb"},
    "distance": {"choices": ["km", "mi"], "default": "mi"},
    "height": {"choices": ["cm", "ft/in"], "default": "ft/in"},
    "temperature": {
        "choices": ["C", "F"],
        "default": "F",
        # temperature deltas use the delta-dimension units
        "delta": {"C": "Cd", "F": "Fd"},
    },
}


# Canonical metric registry. Broad coverage across the three providers.
_METRICS: dict[str, MetricDef] = {
    m.key: m
    for m in [
        # ── Activity ──────────────────────────────────────────────────────
        _m("steps", "count", MetricKind.DAILY, "Daily step count."),
        _m("distance", "m", MetricKind.DAILY, "Distance travelled per day.", "distance"),
        _m("floors", "count", MetricKind.DAILY, "Floors/elevation climbed per day."),
        _m("elevation_gain", "m", MetricKind.DAILY, "Elevation gained per day.", "distance"),
        _m("active_calories", "kcal", MetricKind.DAILY, "Active energy burned per day."),
        _m("total_calories", "kcal", MetricKind.DAILY, "Total energy burned per day."),
        _m("active_minutes", "s", MetricKind.DAILY, "Active duration per day."),
        # ── Body composition ─────────────────────────────────────────────
        _m("weight", "kg", MetricKind.SAMPLE, "Body weight.", "mass"),
        _m("height", "m", MetricKind.SAMPLE, "Body height.", "height"),
        _m("body_fat", "%", MetricKind.SAMPLE, "Body fat ratio."),
        _m("fat_free_mass", "kg", MetricKind.SAMPLE, "Fat-free mass.", "mass"),
        _m("muscle_mass", "kg", MetricKind.SAMPLE, "Muscle mass.", "mass"),
        _m("bone_mass", "kg", MetricKind.SAMPLE, "Bone mass.", "mass"),
        _m("hydration", "kg", MetricKind.SAMPLE, "Body water mass.", "mass"),
        _m("visceral_fat", "index", MetricKind.SAMPLE, "Visceral fat index."),
        _m("bmr", "kcal", MetricKind.DAILY, "Basal metabolic rate."),
        _m("vo2_max", "ml/kg/min", MetricKind.SAMPLE, "Maximal oxygen uptake."),
        # ── Cardiovascular ───────────────────────────────────────────────
        _m("heart_rate", "bpm", MetricKind.SAMPLE, "Instantaneous heart rate."),
        _m("resting_heart_rate", "bpm", MetricKind.DAILY, "Daily resting heart rate."),
        _m("hrv", "ms", MetricKind.SAMPLE, "Heart-rate variability (RMSSD/SDNN)."),
        _m("spo2", "%", MetricKind.SAMPLE, "Blood oxygen saturation."),
        _m("respiratory_rate", "br/min", MetricKind.SAMPLE, "Respiratory rate."),
        _m("blood_pressure_systolic", "mmHg", MetricKind.SAMPLE, "Systolic blood pressure."),
        _m("blood_pressure_diastolic", "mmHg", MetricKind.SAMPLE, "Diastolic blood pressure."),
        _m("blood_glucose", "mg/dL", MetricKind.SAMPLE, "Blood glucose level."),
        _m("body_temperature", "C", MetricKind.SAMPLE, "Body/skin temperature.", "temperature"),
        _m(
            "temperature_deviation",
            "Cd",
            MetricKind.DAILY,
            "Nightly temperature deviation from baseline.",
            "temperature",
        ),
        # ── Sleep ────────────────────────────────────────────────────────
        _m("sleep_duration", "s", MetricKind.DAILY, "Total time asleep."),
        _m("sleep_deep_duration", "s", MetricKind.DAILY, "Time in deep sleep."),
        _m("sleep_light_duration", "s", MetricKind.DAILY, "Time in light sleep."),
        _m("sleep_rem_duration", "s", MetricKind.DAILY, "Time in REM sleep."),
        _m("sleep_awake_duration", "s", MetricKind.DAILY, "Time awake during sleep period."),
        _m("sleep_latency", "s", MetricKind.DAILY, "Time taken to fall asleep."),
        _m("sleep_efficiency", "%", MetricKind.DAILY, "Sleep efficiency."),
        _m("sleep_score", "score", MetricKind.DAILY, "Overall sleep score."),
        # ── Daily scores ─────────────────────────────────────────────────
        _m("readiness_score", "score", MetricKind.DAILY, "Daily readiness score."),
        _m("activity_score", "score", MetricKind.DAILY, "Daily activity score."),
    ]
}


class UnknownMetricError(KeyError):
    """Raised when a metric key is not in the registry."""


def get_metric(key: str) -> MetricDef:
    """Return the :class:`MetricDef` for ``key`` or raise :class:`UnknownMetricError`."""
    try:
        return _METRICS[key]
    except KeyError as exc:
        raise UnknownMetricError(f"Unknown metric: {key!r}") from exc


def all_metrics() -> list[MetricDef]:
    """Return all registered metric definitions, sorted by key."""
    return [_METRICS[k] for k in sorted(_METRICS)]


def metric_keys() -> list[str]:
    """Return all registered metric keys, sorted."""
    return sorted(_METRICS)
