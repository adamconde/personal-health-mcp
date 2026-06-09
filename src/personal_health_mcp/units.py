"""Unit definitions and conversion.

A single table maps every supported unit to its physical *dimension* and an
affine transform to that dimension's canonical pivot unit
(``canonical = raw * factor + offset``). :func:`convert` pivots through the
canonical unit, so any two units of the same dimension interconvert.

Notes:
    * Absolute temperatures (``C``/``F``/``K``) use an offset; temperature
      *deltas* (deviations) live in a separate ``temperature_delta`` dimension
      with no offset, because converting a difference must not shift the zero.
    * Most health metrics never need conversion (scores, counts, bpm). They
      still get a unit + dimension so :func:`same_dimension` can reject
      nonsensical cross-dimension conversions.
"""

from __future__ import annotations

from dataclasses import dataclass

# Dimensions that the user may pick a display preference for (web UI / tool override).
USER_SELECTABLE_DIMENSIONS = ("mass", "length", "temperature")


@dataclass(frozen=True)
class UnitDef:
    """One unit: its dimension and affine transform to the dimension pivot.

    Attributes:
        dimension: Physical dimension key (units of the same dimension convert).
        factor: Multiplicative factor toward the canonical pivot value.
        offset: Additive offset toward the canonical pivot value.
    """

    dimension: str
    factor: float
    offset: float = 0.0


# unit name -> definition. Pivot units have factor=1, offset=0.
_UNITS: dict[str, UnitDef] = {
    # mass (pivot kg)
    "kg": UnitDef("mass", 1.0),
    "g": UnitDef("mass", 0.001),
    "lb": UnitDef("mass", 0.45359237),
    "st": UnitDef("mass", 6.35029318),
    "oz": UnitDef("mass", 0.028349523125),
    # length / distance (pivot m)
    "m": UnitDef("length", 1.0),
    "km": UnitDef("length", 1000.0),
    "cm": UnitDef("length", 0.01),
    "mm": UnitDef("length", 0.001),
    "mi": UnitDef("length", 1609.344),
    "yd": UnitDef("length", 0.9144),
    "ft": UnitDef("length", 0.3048),
    "in": UnitDef("length", 0.0254),
    # absolute temperature (pivot degC)
    "C": UnitDef("temperature", 1.0, 0.0),
    "F": UnitDef("temperature", 5.0 / 9.0, -160.0 / 9.0),
    "K": UnitDef("temperature", 1.0, -273.15),
    # temperature delta / deviation (pivot degC-delta, no offset)
    "Cd": UnitDef("temperature_delta", 1.0),
    "Fd": UnitDef("temperature_delta", 5.0 / 9.0),
    # time / duration (pivot s)
    "s": UnitDef("time", 1.0),
    "ms": UnitDef("time", 0.001),
    "min": UnitDef("time", 60.0),
    "h": UnitDef("time", 3600.0),
    # energy (pivot kcal)
    "kcal": UnitDef("energy", 1.0),
    "kJ": UnitDef("energy", 0.2390057361),
    # blood glucose (pivot mg/dL)
    "mg/dL": UnitDef("glucose", 1.0),
    "mmol/L": UnitDef("glucose", 18.0156),
    # pressure (pivot mmHg)
    "mmHg": UnitDef("pressure", 1.0),
    # rates (no conversions, distinct dimensions to block cross-conversion)
    "bpm": UnitDef("heart_rate", 1.0),
    "br/min": UnitDef("resp_rate", 1.0),
    # dimensionless-ish
    "%": UnitDef("percent", 1.0),
    "count": UnitDef("count", 1.0),
    "score": UnitDef("score", 1.0),
    "index": UnitDef("index", 1.0),
    "ml/kg/min": UnitDef("vo2", 1.0),
    "years": UnitDef("age", 1.0),
}


class UnknownUnitError(ValueError):
    """Raised when a unit name is not in the unit table."""


class IncompatibleUnitsError(ValueError):
    """Raised when converting between units of different dimensions."""


def unit_def(unit: str) -> UnitDef:
    """Return the :class:`UnitDef` for ``unit`` or raise :class:`UnknownUnitError`."""
    try:
        return _UNITS[unit]
    except KeyError as exc:
        raise UnknownUnitError(f"Unknown unit: {unit!r}") from exc


def dimension_of(unit: str) -> str:
    """Return the dimension key for ``unit``."""
    return unit_def(unit).dimension


def same_dimension(a: str, b: str) -> bool:
    """Return True if units ``a`` and ``b`` share a dimension."""
    return dimension_of(a) == dimension_of(b)


def units_for_dimension(dimension: str) -> list[str]:
    """Return all unit names belonging to ``dimension``."""
    return [name for name, d in _UNITS.items() if d.dimension == dimension]


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert ``value`` from ``from_unit`` to ``to_unit``.

    Args:
        value: Numeric magnitude in ``from_unit``.
        from_unit: Source unit name.
        to_unit: Target unit name.

    Returns:
        The value expressed in ``to_unit``.

    Raises:
        UnknownUnitError: If either unit is unrecognized.
        IncompatibleUnitsError: If the units belong to different dimensions.
    """
    if from_unit == to_unit:
        return value
    src = unit_def(from_unit)
    dst = unit_def(to_unit)
    if src.dimension != dst.dimension:
        raise IncompatibleUnitsError(
            f"Cannot convert {from_unit!r} ({src.dimension}) to "
            f"{to_unit!r} ({dst.dimension})"
        )
    canonical = value * src.factor + src.offset
    return (canonical - dst.offset) / dst.factor
