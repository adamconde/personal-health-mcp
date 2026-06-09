"""Provider package.

Importing this package registers every built-in provider (via the ``@register``
decorator on each class). :func:`build_providers` returns a fresh instance map.
Adding a vendor is a new module imported here — no other code changes.
"""

from __future__ import annotations

from .base import PROVIDER_REGISTRY, HealthProvider
from .google import GoogleHealthProvider  # noqa: F401  (import triggers registration)
from .oura import OuraProvider  # noqa: F401
from .withings import WithingsProvider  # noqa: F401


def build_providers() -> dict[str, HealthProvider]:
    """Instantiate every registered provider, keyed by name."""
    return {name: cls() for name, cls in PROVIDER_REGISTRY.items()}


__all__ = ["PROVIDER_REGISTRY", "HealthProvider", "build_providers"]
