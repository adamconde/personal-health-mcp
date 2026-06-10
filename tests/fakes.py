"""Test doubles: in-memory providers returning canned canonical data points."""

from __future__ import annotations

from datetime import datetime

from personal_health_mcp.models import DataPoint
from personal_health_mcp.providers.base import (
    HealthProvider,
    OAuthConfig,
    ProviderAuthError,
    ProviderCapability,
)


class FakeProvider(HealthProvider):
    """A provider that returns pre-seeded points, for tests.

    Args:
        name: Provider name.
        data: Mapping of metric -> list of DataPoint to return for any window.
        units: Mapping of metric -> canonical unit it declares support for.
    """

    oauth = OAuthConfig(
        authorize_url="https://example.test/authorize",
        token_url="https://example.test/token",
        scopes=["all"],
    )

    def __init__(
        self,
        name: str,
        data: dict[str, list[DataPoint]] | None = None,
        units: dict[str, str] | None = None,
    ) -> None:
        self.name = name  # type: ignore[misc]
        self.display_name = name.title()  # type: ignore[misc]
        self._data = data or {}
        self._units = units or {}
        self.calls: list[tuple[str, datetime, datetime]] = []

    def capabilities(self) -> list[ProviderCapability]:
        metrics = set(self._data) | set(self._units)
        return [
            ProviderCapability(metric=m, native_units=[self._units.get(m, "")])
            for m in sorted(metrics)
        ]

    async def fetch_metric(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        access_token: str,
        native_unit: str | None = None,
    ) -> list[DataPoint]:
        self.calls.append((metric, start, end))
        return list(self._data.get(metric, []))


class FailingProvider(FakeProvider):
    """A provider whose fetch always raises, to test error isolation."""

    async def fetch_metric(self, *args, **kwargs):  # type: ignore[override]
        raise RuntimeError("simulated provider failure")


class AuthExpiringProvider(FakeProvider):
    """Raises ``ProviderAuthError`` on the first fetch, then returns its data.

    Models an expired/revoked token so the read path's refresh-and-retry can be
    exercised. ``calls`` records every attempt (so a retry shows up as 2 calls).
    """

    async def fetch_metric(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        access_token: str,
        native_unit: str | None = None,
    ) -> list[DataPoint]:
        self.calls.append((metric, start, end))
        if len(self.calls) == 1:
            raise ProviderAuthError("simulated expired token")
        return list(self._data.get(metric, []))
