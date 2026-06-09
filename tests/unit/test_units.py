"""Tests for unit conversion."""

from __future__ import annotations

import math

import pytest

from personal_health_mcp.units import (
    IncompatibleUnitsError,
    UnknownUnitError,
    convert,
    dimension_of,
    same_dimension,
    units_for_dimension,
)


def test_identity_conversion():
    assert convert(5.0, "kg", "kg") == 5.0


@pytest.mark.parametrize(
    ("value", "frm", "to", "expected"),
    [
        (1.0, "kg", "lb", 2.2046226218),
        (100.0, "lb", "kg", 45.359237),
        (1.0, "km", "mi", 0.6213711922),
        (1.0, "mi", "km", 1.609344),
        (1000.0, "m", "km", 1.0),
        (5000.0, "m", "mi", 3.1068559612),
        (1.0, "st", "kg", 6.35029318),
        (3600.0, "s", "h", 1.0),
        (1.0, "h", "min", 60.0),
    ],
)
def test_factor_conversions(value, frm, to, expected):
    assert math.isclose(convert(value, frm, to), expected, rel_tol=1e-9)


@pytest.mark.parametrize(
    ("value", "frm", "to", "expected"),
    [
        (0.0, "C", "F", 32.0),
        (100.0, "C", "F", 212.0),
        (98.6, "F", "C", 37.0),
        (37.0, "C", "K", 310.15),
    ],
)
def test_absolute_temperature(value, frm, to, expected):
    assert math.isclose(convert(value, frm, to), expected, rel_tol=1e-9)


def test_temperature_delta_has_no_offset():
    # A 1 degC deviation is a 1.8 degF deviation (no +32 shift).
    assert math.isclose(convert(1.0, "Cd", "Fd"), 1.8, rel_tol=1e-9)
    assert math.isclose(convert(0.0, "Cd", "Fd"), 0.0, abs_tol=1e-12)


def test_glucose_conversion():
    # 90 mg/dL ~= 5.0 mmol/L
    assert math.isclose(convert(90.0, "mg/dL", "mmol/L"), 90.0 / 18.0156, rel_tol=1e-9)


def test_roundtrip_stability():
    for unit in ("lb", "mi", "F", "mmol/L"):
        base = 42.0
        canonical_unit = {"lb": "kg", "mi": "m", "F": "C", "mmol/L": "mg/dL"}[unit]
        there = convert(base, unit, canonical_unit)
        back = convert(there, canonical_unit, unit)
        assert math.isclose(back, base, rel_tol=1e-9)


def test_incompatible_dimensions_raise():
    with pytest.raises(IncompatibleUnitsError):
        convert(1.0, "kg", "m")


def test_unknown_unit_raises():
    with pytest.raises(UnknownUnitError):
        convert(1.0, "kg", "furlong")


def test_dimension_helpers():
    assert dimension_of("kg") == "mass"
    assert same_dimension("km", "mi")
    assert not same_dimension("kg", "C")
    assert set(units_for_dimension("mass")) >= {"kg", "lb", "st", "g", "oz"}
