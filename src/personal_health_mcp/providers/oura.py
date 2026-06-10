"""Oura provider.

Maps the Oura v2 ``usercollection`` endpoints into canonical data points. Oura
already returns metric units (kg, metres, seconds, kcal, bpm, Celsius), so the
mapping is mostly field selection. Daily documents carry a ``day`` (YYYY-MM-DD);
time-series documents carry timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import httpx

from ..models import DataPoint
from .base import (
    DEFAULT_TIMEOUT,
    HealthProvider,
    OAuthConfig,
    ProviderCapability,
    raise_for_auth,
    register,
)

API_BASE = "https://api.ouraring.com"

# canonical metric -> (collection, field, canonical_unit, scale)
# ``field`` is read from each document; value is multiplied by ``scale``.
_DAILY_ACTIVITY = "daily_activity"
_SLEEP = "sleep"


def _day_start(day: str) -> datetime:
    """Parse a YYYY-MM-DD day into a UTC midnight datetime."""
    d = datetime.fromisoformat(day).date()
    return datetime.combine(d, time.min, tzinfo=UTC)


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into a UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@register
class OuraProvider(HealthProvider):
    """Integration with the Oura Ring cloud API."""

    name = "oura"
    display_name = "Oura"
    credentials_url = "https://cloud.ouraring.com/oauth/applications"
    oauth = OAuthConfig(
        authorize_url="https://cloud.ouraring.com/oauth/authorize",
        token_url="https://api.ouraring.com/oauth/token",
        scopes=["personal", "daily", "heartrate", "workout", "session", "spo2"],
        use_pkce=True,
    )

    # metric -> (collection, document field, canonical unit, scale factor)
    _DAILY_FIELDS: dict[str, tuple[str, str, str, float]] = {
        "steps": (_DAILY_ACTIVITY, "steps", "count", 1.0),
        "distance": (_DAILY_ACTIVITY, "equivalent_walking_distance", "m", 1.0),
        "active_calories": (_DAILY_ACTIVITY, "active_calories", "kcal", 1.0),
        "total_calories": (_DAILY_ACTIVITY, "total_calories", "kcal", 1.0),
        "activity_score": (_DAILY_ACTIVITY, "score", "score", 1.0),
        "sleep_duration": (_SLEEP, "total_sleep_duration", "s", 1.0),
        "sleep_deep_duration": (_SLEEP, "deep_sleep_duration", "s", 1.0),
        "sleep_light_duration": (_SLEEP, "light_sleep_duration", "s", 1.0),
        "sleep_rem_duration": (_SLEEP, "rem_sleep_duration", "s", 1.0),
        "sleep_awake_duration": (_SLEEP, "awake_time", "s", 1.0),
        "sleep_latency": (_SLEEP, "latency", "s", 1.0),
        "sleep_efficiency": (_SLEEP, "efficiency", "%", 1.0),
        "hrv": (_SLEEP, "average_hrv", "ms", 1.0),
        "respiratory_rate": (_SLEEP, "average_breath", "br/min", 1.0),
        "resting_heart_rate": (_SLEEP, "lowest_heart_rate", "bpm", 1.0),
        "sleep_score": ("daily_sleep", "score", "score", 1.0),
        "readiness_score": ("daily_readiness", "score", "score", 1.0),
        "temperature_deviation": ("daily_readiness", "temperature_deviation", "Cd", 1.0),
        "spo2": ("daily_spo2", "spo2_percentage", "%", 1.0),
        "vo2_max": ("vO2_max", "vo2_max", "ml/kg/min", 1.0),
    }

    def capabilities(self) -> list[ProviderCapability]:
        caps = [
            ProviderCapability(metric=m, native_units=[unit])
            for m, (_c, _f, unit, _s) in self._DAILY_FIELDS.items()
        ]
        caps.append(ProviderCapability(metric="heart_rate", native_units=["bpm"]))
        caps.append(ProviderCapability(metric="weight", native_units=["kg"]))
        caps.append(ProviderCapability(metric="height", native_units=["m"]))
        return caps

    async def fetch_metric(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        access_token: str,
        native_unit: str | None = None,
    ) -> list[DataPoint]:
        if metric == "heart_rate":
            return await self._fetch_heart_rate(start, end, access_token)
        if metric in ("weight", "height"):
            return await self._fetch_personal(metric, access_token)
        if metric in self._DAILY_FIELDS:
            return await self._fetch_daily(metric, start, end, access_token)
        return []

    # ── endpoint helpers ─────────────────────────────────────────────────
    async def _get(
        self,
        path: str,
        params: dict[str, str],
        access_token: str,
    ) -> list[dict]:
        """GET a paginated Oura collection, following ``next_token``."""
        headers = {"Authorization": f"Bearer {access_token}"}
        out: list[dict] = []
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            token: str | None = None
            while True:
                q = dict(params)
                if token:
                    q["next_token"] = token
                resp = await client.get(f"{API_BASE}{path}", params=q, headers=headers)
                raise_for_auth(resp.status_code, "Oura")
                resp.raise_for_status()
                body = resp.json()
                out.extend(body.get("data", []))
                token = body.get("next_token")
                if not token:
                    break
        return out

    async def _fetch_daily(
        self, metric: str, start: datetime, end: datetime, access_token: str
    ) -> list[DataPoint]:
        collection, field, unit, scale = self._DAILY_FIELDS[metric]
        params = {
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        }
        docs = await self._get(f"/v2/usercollection/{collection}", params, access_token)
        points: list[DataPoint] = []
        for doc in docs:
            value = doc.get(field)
            day = doc.get("day")
            timestamp = doc.get("timestamp")
            if value is None or (day is None and timestamp is None):
                continue
            ts = _day_start(day) if day else _parse_ts(timestamp)
            points.append(
                DataPoint(
                    metric=metric,
                    value=float(value) * scale,
                    unit=unit,
                    start=ts,
                    provider=self.name,
                )
            )
        return points

    async def _fetch_heart_rate(
        self, start: datetime, end: datetime, access_token: str
    ) -> list[DataPoint]:
        params = {
            "start_datetime": start.isoformat(),
            "end_datetime": end.isoformat(),
        }
        docs = await self._get("/v2/usercollection/heartrate", params, access_token)
        points: list[DataPoint] = []
        for doc in docs:
            bpm = doc.get("bpm")
            ts = doc.get("timestamp")
            if bpm is None or ts is None:
                continue
            points.append(
                DataPoint(
                    metric="heart_rate",
                    value=float(bpm),
                    unit="bpm",
                    start=_parse_ts(ts),
                    provider=self.name,
                )
            )
        return points

    async def _fetch_personal(self, metric: str, access_token: str) -> list[DataPoint]:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{API_BASE}/v2/usercollection/personal_info", headers=headers
            )
            raise_for_auth(resp.status_code, "Oura")
            resp.raise_for_status()
            doc = resp.json()
        field, unit = ("weight", "kg") if metric == "weight" else ("height", "m")
        value = doc.get(field)
        if value is None:
            return []
        return [
            DataPoint(
                metric=metric,
                value=float(value),
                unit=unit,
                start=datetime.now(UTC),
                provider=self.name,
            )
        ]
