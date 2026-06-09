"""Withings provider.

Withings uses POST endpoints with an ``action`` parameter, wraps responses in
``{"status": 0, "body": {...}}``, and encodes measurement values as
``real_value = value * 10**unit``. Its OAuth token endpoint is also non-standard
(``action=requesttoken``) and **rotates the refresh token on every refresh**, so
:meth:`exchange_code` and :meth:`refresh` are overridden.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import httpx

from ..models import DataPoint, Token
from ..timeutil import now_utc
from .base import (
    DEFAULT_TIMEOUT,
    HealthProvider,
    OAuthConfig,
    ProviderCapability,
    ProviderError,
    register,
)

API_BASE = "https://wbsapi.withings.net"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"


def _day_start(day: str) -> datetime:
    d = datetime.fromisoformat(day).date()
    return datetime.combine(d, time.min, tzinfo=UTC)


@register
class WithingsProvider(HealthProvider):
    """Integration with the Withings Health Mate API."""

    name = "withings"
    display_name = "Withings"
    credentials_url = "https://developer.withings.com/dashboard/"
    oauth = OAuthConfig(
        authorize_url="https://account.withings.com/oauth2_user/authorize2",
        token_url=TOKEN_URL,
        scopes=["user.info", "user.metrics", "user.activity", "user.sleepevents"],
        scope_separator=",",
    )

    # ── getmeas: meastype code -> (canonical metric, canonical unit) ──────
    _MEAS_TYPES: dict[str, tuple[int, str]] = {
        "weight": (1, "kg"),
        "height": (4, "m"),
        "fat_free_mass": (5, "kg"),
        "body_fat": (6, "%"),
        "blood_pressure_diastolic": (9, "mmHg"),
        "blood_pressure_systolic": (10, "mmHg"),
        "heart_rate": (11, "bpm"),
        "spo2": (54, "%"),
        "body_temperature": (71, "C"),
        "muscle_mass": (76, "kg"),
        "hydration": (77, "kg"),
        "bone_mass": (88, "kg"),
        "vo2_max": (123, "ml/kg/min"),
        "bmr": (226, "kcal"),
    }

    # ── getactivity: canonical metric -> (field, canonical unit) ──────────
    _ACTIVITY_FIELDS: dict[str, tuple[str, str]] = {
        "steps": ("steps", "count"),
        "distance": ("distance", "m"),
        "floors": ("elevation", "count"),
        "active_calories": ("calories", "kcal"),
        "total_calories": ("totalcalories", "kcal"),
        "active_minutes": ("active", "s"),
    }

    # ── getsummary (sleep): canonical metric -> (field, unit, scale) ──────
    _SLEEP_FIELDS: dict[str, tuple[str, str, float]] = {
        "sleep_duration": ("total_sleep_time", "s", 1.0),
        "sleep_deep_duration": ("deepsleepduration", "s", 1.0),
        "sleep_light_duration": ("lightsleepduration", "s", 1.0),
        "sleep_rem_duration": ("remsleepduration", "s", 1.0),
        "sleep_awake_duration": ("wakeupduration", "s", 1.0),
        "sleep_latency": ("durationtosleep", "s", 1.0),
        "sleep_efficiency": ("sleep_efficiency", "%", 100.0),  # ratio -> percent
    }

    def capabilities(self) -> list[ProviderCapability]:
        caps: list[ProviderCapability] = []
        for m, (_code, unit) in self._MEAS_TYPES.items():
            caps.append(ProviderCapability(metric=m, native_units=[unit]))
        for m, (_field, unit) in self._ACTIVITY_FIELDS.items():
            caps.append(ProviderCapability(metric=m, native_units=[unit]))
        for m, (_field, unit, _scale) in self._SLEEP_FIELDS.items():
            caps.append(ProviderCapability(metric=m, native_units=[unit]))
        return caps

    async def fetch_metric(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        access_token: str,
        native_unit: str | None = None,
    ) -> list[DataPoint]:
        if metric in self._MEAS_TYPES:
            return await self._fetch_measures(metric, start, end, access_token)
        if metric in self._ACTIVITY_FIELDS:
            return await self._fetch_activity(metric, start, end, access_token)
        if metric in self._SLEEP_FIELDS:
            return await self._fetch_sleep(metric, start, end, access_token)
        return []

    # ── HTTP wrapper ─────────────────────────────────────────────────────
    async def _post(self, path: str, data: dict, access_token: str) -> dict:
        """POST a form-encoded Withings request and unwrap ``{status, body}``."""
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(f"{API_BASE}{path}", data=data, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        status = payload.get("status")
        if status != 0:
            raise ProviderError(f"Withings API error (status={status}).")
        return payload.get("body", {})

    # ── getmeas ──────────────────────────────────────────────────────────
    async def _fetch_measures(
        self, metric: str, start: datetime, end: datetime, access_token: str
    ) -> list[DataPoint]:
        code, unit = self._MEAS_TYPES[metric]
        points: list[DataPoint] = []
        offset = 0
        while True:
            data = {
                "action": "getmeas",
                "meastypes": str(code),
                "category": "1",
                "startdate": str(int(start.timestamp())),
                "enddate": str(int(end.timestamp())),
            }
            if offset:
                data["offset"] = str(offset)
            body = await self._post("/measure", data, access_token)
            for grp in body.get("measuregrps", []):
                ts = datetime.fromtimestamp(grp["date"], tz=UTC)
                for meas in grp.get("measures", []):
                    if meas.get("type") != code:
                        continue
                    value = float(meas["value"]) * (10 ** int(meas["unit"]))
                    points.append(
                        DataPoint(
                            metric=metric,
                            value=value,
                            unit=unit,
                            start=ts,
                            provider=self.name,
                            device=grp.get("model"),
                        )
                    )
            if body.get("more"):
                offset = int(body.get("offset", 0))
            else:
                break
        return points

    # ── getactivity ──────────────────────────────────────────────────────
    async def _fetch_activity(
        self, metric: str, start: datetime, end: datetime, access_token: str
    ) -> list[DataPoint]:
        field, unit = self._ACTIVITY_FIELDS[metric]
        data = {
            "action": "getactivity",
            "startdateymd": start.date().isoformat(),
            "enddateymd": end.date().isoformat(),
            "data_fields": field,
        }
        body = await self._post("/v2/measure", data, access_token)
        points: list[DataPoint] = []
        for act in body.get("activities", []):
            value = act.get(field)
            if value is None:
                continue
            points.append(
                DataPoint(
                    metric=metric,
                    value=float(value),
                    unit=unit,
                    start=_day_start(act["date"]),
                    provider=self.name,
                    device=act.get("model"),
                )
            )
        return points

    # ── getsummary (sleep) ───────────────────────────────────────────────
    async def _fetch_sleep(
        self, metric: str, start: datetime, end: datetime, access_token: str
    ) -> list[DataPoint]:
        field, unit, scale = self._SLEEP_FIELDS[metric]
        data = {
            "action": "getsummary",
            "startdateymd": start.date().isoformat(),
            "enddateymd": end.date().isoformat(),
            "data_fields": field,
        }
        body = await self._post("/v2/sleep", data, access_token)
        series = body.get("series", []) if isinstance(body, dict) else body
        points: list[DataPoint] = []
        for night in series:
            payload = night.get("data", night)
            value = payload.get(field)
            if value is None:
                continue
            day = night.get("date")
            ts = _day_start(day) if day else datetime.fromtimestamp(
                night["startdate"], tz=UTC
            )
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

    # ── OAuth overrides (non-standard wrapper + refresh rotation) ─────────
    async def _withings_token(self, data: dict) -> Token:
        """POST an ``action=requesttoken`` request and parse the wrapped body."""
        data = {"action": "requesttoken", **data}
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(TOKEN_URL, data=data)
            resp.raise_for_status()
            payload = resp.json()
        if payload.get("status") != 0:
            raise ProviderError(f"Withings token error (status={payload.get('status')}).")
        body = payload["body"]
        expires_at = None
        if body.get("expires_in") is not None:
            from datetime import timedelta

            expires_at = now_utc() + timedelta(seconds=float(body["expires_in"]))
        scope = body.get("scope", "")
        scopes = scope.split(",") if scope else []
        return Token(
            provider=self.name,
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=expires_at,
            scopes=scopes,
            provider_user_id=str(body["userid"]) if body.get("userid") is not None else None,
        )

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str | None = None,
    ) -> Token:
        return await self._withings_token(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )

    async def refresh(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> Token:
        token = await self._withings_token(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
        )
        # Withings rotates the refresh token; if absent keep the old one.
        if token.refresh_token is None:
            token.refresh_token = refresh_token
        return token
