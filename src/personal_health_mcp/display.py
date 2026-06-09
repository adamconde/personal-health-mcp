"""Display-unit resolution.

Decides which unit a metric's values should be presented in, given (in priority
order): an explicit per-call override, the user's saved preference for the
metric's preference group, then the metric's canonical unit. Conversion itself
lives in :mod:`units`; this module only chooses the target unit.
"""

from __future__ import annotations

from .metrics import PREF_GROUPS, MetricDef
from .units import same_dimension


def resolve_display_unit(
    metric: MetricDef,
    unit_prefs: dict[str, str] | None = None,
    override: str | None = None,
) -> str:
    """Return the unit a metric should be displayed in.

    Args:
        metric: The metric definition.
        unit_prefs: Mapping of preference-group name -> chosen unit (e.g.
            ``{"mass": "lb", "distance": "mi", "temperature": "F"}``).
        override: Explicit per-call unit; wins if it shares the metric's
            physical dimension (otherwise ignored).

    Returns:
        A concrete unit name (always dimension-compatible with the canonical unit).
    """
    canonical = metric.canonical_unit

    # 1. Explicit override (only if dimension-compatible).
    if override and same_dimension(override, canonical):
        return override

    # 2. Saved preference for this metric's group.
    if metric.pref_group and unit_prefs:
        chosen = unit_prefs.get(metric.pref_group)
        if chosen:
            target = _concretize(metric.pref_group, chosen, canonical)
            if target and same_dimension(target, canonical):
                return target

    # 3. Canonical default.
    return canonical


def _concretize(group: str, choice: str, canonical_unit: str) -> str | None:
    """Map a preference-group choice to the concrete unit for a metric's dimension.

    Handles the temperature/temperature-delta split: a "F" choice becomes "Fd"
    for a deviation metric whose canonical unit is "Cd".
    """
    spec = PREF_GROUPS.get(group)
    if spec is None:
        return choice
    delta_map = spec.get("delta")
    if isinstance(delta_map, dict) and canonical_unit in delta_map.values():
        # canonical is a delta unit -> translate the choice into delta space.
        return delta_map.get(choice, choice)
    return choice
