"""Tests for display-unit resolution."""

from __future__ import annotations

from personal_health_mcp.display import resolve_display_unit
from personal_health_mcp.metrics import get_metric


def test_canonical_when_no_prefs():
    assert resolve_display_unit(get_metric("weight")) == "kg"


def test_mass_pref_applied():
    assert resolve_display_unit(get_metric("weight"), {"mass": "lb"}) == "lb"


def test_distance_and_height_are_independent():
    prefs = {"distance": "mi", "height": "in"}
    assert resolve_display_unit(get_metric("distance"), prefs) == "mi"
    # height shares the physical 'length' dimension but its own pref group
    assert resolve_display_unit(get_metric("height"), prefs) == "in"


def test_override_wins_over_pref():
    assert resolve_display_unit(get_metric("weight"), {"mass": "lb"}, override="st") == "st"


def test_override_ignored_if_wrong_dimension():
    # Cannot display weight in miles; fall back to pref/canonical.
    assert resolve_display_unit(get_metric("weight"), {"mass": "lb"}, override="mi") == "lb"


def test_temperature_choice_maps_to_delta_unit():
    # body_temperature is absolute; deviation is a delta.
    assert resolve_display_unit(get_metric("body_temperature"), {"temperature": "F"}) == "F"
    assert resolve_display_unit(get_metric("temperature_deviation"), {"temperature": "F"}) == "Fd"


def test_metric_without_group_ignores_prefs():
    assert resolve_display_unit(get_metric("steps"), {"mass": "lb"}) == "count"
