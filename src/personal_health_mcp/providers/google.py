"""Google Health provider.

Reads the v4 ``users/me/dataTypes/{type}/dataPoints`` endpoint with an AIP-160
``filter`` for the time window, then decodes Google's internal SI units (grams,
millimetres) into canonical units. Each data point carries its typed payload
under a camelCase key (e.g. ``point["weight"]["weightGrams"]``) plus a
``dataSource`` used for provenance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..models import DataPoint
from .base import (
    DEFAULT_TIMEOUT,
    HealthProvider,
    OAuthConfig,
    ProviderCapability,
    ProviderError,
    register,
)

API_BASE = "https://health.googleapis.com"


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _typed_timestamp(typed: dict) -> datetime | None:
    """Extract a start timestamp from a typed payload (interval or sample time)."""
    interval = typed.get("interval") or {}
    if interval.get("startTime"):
        return _parse(interval["startTime"])
    sample = typed.get("sampleTime") or {}
    if sample.get("physicalTime"):
        return _parse(sample["physicalTime"])
    return None


@dataclass(frozen=True)
class _G:
    """A Google metric mapping.

    Attributes:
        data_type: kebab-case data type used in the URL path.
        typed_key: camelCase key under which the typed payload sits in a point.
        unit: canonical unit of the produced value.
        extract: callable mapping the typed payload to a canonical float (or None).
    """

    data_type: str
    typed_key: str
    unit: str
    extract: Callable[[dict], float | None]


def _field(name: str, scale: float = 1.0) -> Callable[[dict], float | None]:
    """Build an extractor reading ``name`` and scaling it to canonical units."""

    def _ex(typed: dict) -> float | None:
        v = typed.get(name)
        return None if v is None else float(v) * scale

    return _ex


def _sleep_asleep(typed: dict) -> float | None:
    summary = typed.get("summary") or {}
    v = summary.get("minutesAsleep")
    return None if v is None else float(v) * 60.0  # minutes -> seconds


@register
class GoogleHealthProvider(HealthProvider):
    """Integration with the Google Health v4 API."""

    name = "google"
    display_name = "Google Health"
    oauth = OAuthConfig(
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
        ],
        use_pkce=True,
        # Required for Google to return a refresh token.
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    )

    _METRICS: dict[str, _G] = {
        "steps": _G("steps", "steps", "count", _field("count")),
        "distance": _G("distance", "distance", "m", _field("millimeters", 0.001)),
        "floors": _G("floors", "floors", "count", _field("count")),
        "active_calories": _G(
            "active-energy-burned", "activeEnergyBurned", "kcal", _field("kcal")
        ),
        "total_calories": _G("total-calories", "totalCalories", "kcal", _field("kcal")),
        "weight": _G("weight", "weight", "kg", _field("weightGrams", 0.001)),
        "height": _G("height", "height", "m", _field("heightMillimeters", 0.001)),
        "body_fat": _G("body-fat", "bodyFat", "%", _field("percentage")),
        "heart_rate": _G("heart-rate", "heartRate", "bpm", _field("beatsPerMinute")),
        "resting_heart_rate": _G(
            "daily-resting-heart-rate", "dailyRestingHeartRate", "bpm",
            _field("beatsPerMinute"),
        ),
        "hrv": _G(
            "heart-rate-variability", "heartRateVariability", "ms",
            _field("rootMeanSquareOfSuccessiveDifferencesMilliseconds"),
        ),
        "spo2": _G("oxygen-saturation", "oxygenSaturation", "%", _field("percentage")),
        "respiratory_rate": _G(
            "daily-respiratory-rate", "dailyRespiratoryRate", "br/min",
            _field("breathsPerMinute"),
        ),
        "body_temperature": _G(
            "core-body-temperature", "coreBodyTemperature", "C",
            _field("temperatureCelsius"),
        ),
        "blood_glucose": _G(
            "blood-glucose", "bloodGlucose", "mg/dL",
            _field("bloodGlucoseMilligramsPerDeciliter"),
        ),
        "vo2_max": _G("vo2-max", "vo2Max", "ml/kg/min", _field("vo2Max")),
        "sleep_duration": _G("sleep", "sleep", "s", _sleep_asleep),
    }

    def capabilities(self) -> list[ProviderCapability]:
        return [
            ProviderCapability(metric=m, native_units=[g.unit])
            for m, g in self._METRICS.items()
        ]

    async def fetch_metric(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        access_token: str,
        native_unit: str | None = None,
    ) -> list[DataPoint]:
        spec = self._METRICS.get(metric)
        if spec is None:
            return []
        field_root = spec.data_type.replace("-", "_")
        filt = (
            f'{field_root}.interval.start_time >= "{start.isoformat()}" '
            f'AND {field_root}.interval.start_time < "{end.isoformat()}"'
        )
        points: list[DataPoint] = []
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            page_token: str | None = None
            while True:
                params = {"filter": filt, "pageSize": "1000"}
                if page_token:
                    params["pageToken"] = page_token
                url = f"{API_BASE}/v4/users/me/dataTypes/{spec.data_type}/dataPoints"
                resp = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 401:
                    raise ProviderError("Google Health authentication failed (401).")
                resp.raise_for_status()
                body = resp.json()
                for point in body.get("dataPoints", []):
                    dp = self._map_point(metric, spec, point)
                    if dp is not None:
                        points.append(dp)
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
        return points

    def _map_point(self, metric: str, spec: _G, point: dict) -> DataPoint | None:
        typed = point.get(spec.typed_key)
        if not isinstance(typed, dict):
            return None
        value = spec.extract(typed)
        ts = _typed_timestamp(typed)
        if value is None or ts is None:
            return None
        source = point.get("dataSource") or {}
        return DataPoint(
            metric=metric,
            value=value,
            unit=spec.unit,
            start=ts,
            provider=self.name,
            device=source.get("dataSourceName"),
        )
