"""Tests for the canonical metric registry."""

from __future__ import annotations

import pytest

from personal_health_mcp.metrics import (
    MetricKind,
    UnknownMetricError,
    all_metrics,
    get_metric,
    metric_keys,
)
from personal_health_mcp.units import unit_def


def test_known_metric_lookup():
    weight = get_metric("weight")
    assert weight.canonical_unit == "kg"
    assert weight.kind == MetricKind.SAMPLE
    assert weight.dimension == "mass"


def test_unknown_metric_raises():
    with pytest.raises(UnknownMetricError):
        get_metric("does_not_exist")


def test_all_metrics_sorted_and_unique():
    keys = metric_keys()
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def test_every_metric_unit_is_defined():
    # Each metric's canonical unit must exist in the unit table.
    for m in all_metrics():
        assert unit_def(m.canonical_unit) is not None


def test_temperature_deviation_uses_delta_dimension():
    # Deviations must not be conflated with absolute temperature.
    assert get_metric("temperature_deviation").dimension == "temperature_delta"
    assert get_metric("body_temperature").dimension == "temperature"


def test_broad_coverage_present():
    keys = set(metric_keys())
    expected = {
        "steps",
        "weight",
        "heart_rate",
        "hrv",
        "spo2",
        "sleep_duration",
        "readiness_score",
        "blood_glucose",
        "vo2_max",
    }
    assert expected <= keys
