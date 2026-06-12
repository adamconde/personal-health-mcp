"""Provider raw->canonical mapping tests (HTTP mocked with respx).

Uses sample payloads shaped like each vendor's spec, asserting the canonical
value/unit/provenance, including the unit-encoding edge cases:
  * Withings value*10^unit  -> 65.75 kg
  * Google  weightGrams      -> 75.0 kg
  * Oura    kg               -> 75.5 kg
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from personal_health_mcp.providers.base import ProviderError
from personal_health_mcp.providers.google import GoogleHealthProvider
from personal_health_mcp.providers.oura import OuraProvider
from personal_health_mcp.providers.withings import WithingsProvider

pytestmark = pytest.mark.asyncio

START = datetime(2030, 1, 1, tzinfo=UTC)
END = datetime(2030, 1, 8, tzinfo=UTC)


# ── Oura ─────────────────────────────────────────────────────────────────
@respx.mock
async def test_oura_personal_weight():
    respx.get("https://api.ouraring.com/v2/usercollection/personal_info").mock(
        return_value=httpx.Response(200, json={"id": "u", "weight": 75.5, "height": 1.75})
    )
    points = await OuraProvider().fetch_metric("weight", START, END, "tok")
    assert len(points) == 1
    assert points[0].value == 75.5
    assert points[0].unit == "kg"
    assert points[0].provider == "oura"


@respx.mock
async def test_oura_daily_steps_and_pagination():
    route = respx.get("https://api.ouraring.com/v2/usercollection/daily_activity")
    route.side_effect = [
        httpx.Response(
            200,
            json={"data": [{"day": "2030-01-01", "steps": 8000}], "next_token": "n1"},
        ),
        httpx.Response(
            200,
            json={"data": [{"day": "2030-01-02", "steps": 9000}], "next_token": None},
        ),
    ]
    points = await OuraProvider().fetch_metric("steps", START, END, "tok")
    assert [p.value for p in points] == [8000.0, 9000.0]
    assert points[0].start == datetime(2030, 1, 1, tzinfo=UTC)


@respx.mock
async def test_oura_heart_rate_time_series():
    respx.get("https://api.ouraring.com/v2/usercollection/heartrate").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"bpm": 60, "timestamp": "2030-01-01T10:00:00+00:00"},
                    {"bpm": 72, "timestamp": "2030-01-01T10:05:00+00:00"},
                ],
                "next_token": None,
            },
        )
    )
    points = await OuraProvider().fetch_metric("heart_rate", START, END, "tok")
    assert [p.value for p in points] == [60.0, 72.0]
    assert points[0].unit == "bpm"


@respx.mock
async def test_oura_401_raises():
    respx.get("https://api.ouraring.com/v2/usercollection/daily_activity").mock(
        return_value=httpx.Response(401, json={"detail": "no"})
    )
    with pytest.raises(ProviderError):
        await OuraProvider().fetch_metric("steps", START, END, "tok")


# ── Withings ───────────────────────────────────────────────────────────────
@respx.mock
async def test_withings_weight_value_unit_decoding():
    respx.post("https://wbsapi.withings.net/measure").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 0,
                "body": {
                    "measuregrps": [
                        {
                            "date": 1893456000,
                            "model": "Body Cardio",
                            "measures": [{"value": 65750, "type": 1, "unit": -3}],
                        }
                    ],
                    "more": False,
                    "offset": 0,
                },
            },
        )
    )
    points = await WithingsProvider().fetch_metric("weight", START, END, "tok")
    assert len(points) == 1
    assert round(points[0].value, 3) == 65.75
    assert points[0].unit == "kg"
    assert points[0].device == "Body Cardio"


@respx.mock
async def test_withings_activity_steps():
    respx.post("https://wbsapi.withings.net/v2/measure").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 0,
                "body": {
                    "activities": [{"date": "2030-01-01", "steps": 6200}],
                    "more": False,
                },
            },
        )
    )
    points = await WithingsProvider().fetch_metric("steps", START, END, "tok")
    assert points[0].value == 6200.0
    assert points[0].unit == "count"


@respx.mock
async def test_withings_sleep_efficiency_ratio_to_percent():
    respx.post("https://wbsapi.withings.net/v2/sleep").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 0,
                "body": {
                    "series": [
                        {"date": "2030-01-01", "data": {"sleep_efficiency": 0.875}}
                    ]
                },
            },
        )
    )
    points = await WithingsProvider().fetch_metric("sleep_efficiency", START, END, "tok")
    assert points[0].value == 87.5
    assert points[0].unit == "%"


@respx.mock
async def test_withings_nonzero_status_raises():
    respx.post("https://wbsapi.withings.net/measure").mock(
        return_value=httpx.Response(200, json={"status": 601, "body": {}})
    )
    with pytest.raises(ProviderError):
        await WithingsProvider().fetch_metric("weight", START, END, "tok")


@respx.mock
async def test_withings_token_exchange_unwraps_body_and_userid():
    respx.post("https://wbsapi.withings.net/v2/oauth2").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 0,
                "body": {
                    "access_token": "acc",
                    "refresh_token": "ref",
                    "expires_in": 10800,
                    "scope": "user.metrics,user.activity",
                    "userid": 12345,
                },
            },
        )
    )
    token = await WithingsProvider().exchange_code("code", "https://r", "cid", "sec")
    assert token.access_token == "acc"
    assert token.refresh_token == "ref"
    assert token.provider_user_id == "12345"
    assert token.scopes == ["user.metrics", "user.activity"]


@respx.mock
async def test_withings_refresh_keeps_old_token_if_absent():
    respx.post("https://wbsapi.withings.net/v2/oauth2").mock(
        return_value=httpx.Response(
            200,
            json={"status": 0, "body": {"access_token": "new-acc", "expires_in": 10800}},
        )
    )
    token = await WithingsProvider().refresh("old-ref", "cid", "sec")
    assert token.access_token == "new-acc"
    assert token.refresh_token == "old-ref"  # rotation fallback


# ── Google ───────────────────────────────────────────────────────────────
@respx.mock
async def test_google_weight_grams_to_kg():
    # Weight is a *sample* type: it carries sampleTime and must be filtered on
    # sample_time.physical_time (not interval.start_time, which 400s).
    route = respx.get(
        "https://health.googleapis.com/v4/users/me/dataTypes/weight/dataPoints"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "dataPoints": [
                    {
                        "weight": {
                            "weightGrams": 75000,
                            "sampleTime": {"physicalTime": "2030-01-01T08:00:00Z"},
                        },
                        "dataSource": {"dataSourceName": "Fitbit"},
                    }
                ]
            },
        )
    )
    points = await GoogleHealthProvider().fetch_metric("weight", START, END, "tok")
    assert points[0].value == 75.0
    assert points[0].unit == "kg"
    assert points[0].device == "Fitbit"
    assert "weight.sample_time.physical_time" in route.calls.last.request.url.params["filter"]


@respx.mock
async def test_google_resting_heart_rate_daily_date_filter():
    # Daily-summary type: filtered on `.date` with YYYY-MM-DD literals, and its
    # timestamp comes from the {year, month, day} `date` object. This is the
    # case that previously 400'd with an interval.start_time filter.
    route = respx.get(
        "https://health.googleapis.com/v4/users/me/dataTypes/daily-resting-heart-rate/dataPoints"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "dataPoints": [
                    {
                        "dailyRestingHeartRate": {
                            "beatsPerMinute": 58,
                            "date": {"year": 2030, "month": 1, "day": 3},
                        }
                    }
                ]
            },
        )
    )
    points = await GoogleHealthProvider().fetch_metric("resting_heart_rate", START, END, "tok")
    assert points[0].value == 58.0
    assert points[0].unit == "bpm"
    assert points[0].start == datetime(2030, 1, 3, tzinfo=UTC)
    filt = route.calls.last.request.url.params["filter"]
    assert 'daily_resting_heart_rate.date >= "2030-01-01"' in filt
    # End (2030-01-08) is inclusive -> upper bound is the next day.
    assert 'daily_resting_heart_rate.date < "2030-01-09"' in filt


@respx.mock
async def test_google_distance_mm_to_m_with_paging():
    route = respx.get(
        "https://health.googleapis.com/v4/users/me/dataTypes/distance/dataPoints"
    )
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "dataPoints": [
                    {
                        "distance": {
                            "millimeters": 5000000,
                            "interval": {"startTime": "2030-01-01T00:00:00Z"},
                        }
                    }
                ],
                "nextPageToken": "p2",
            },
        ),
        httpx.Response(
            200,
            json={
                "dataPoints": [
                    {
                        "distance": {
                            "millimeters": 1000000,
                            "interval": {"startTime": "2030-01-02T00:00:00Z"},
                        }
                    }
                ]
            },
        ),
    ]
    points = await GoogleHealthProvider().fetch_metric("distance", START, END, "tok")
    assert [p.value for p in points] == [5000.0, 1000.0]  # metres


async def test_google_does_not_advertise_total_calories():
    # Google's v4 dataPoints API has no standalone total-calories type, so the
    # provider must not claim it (it would 400). active_calories is still served.
    provider = GoogleHealthProvider()
    assert provider.supports("active_calories")
    assert not provider.supports("total_calories")


@respx.mock
async def test_google_sleep_minutes_to_seconds():
    respx.get(
        "https://health.googleapis.com/v4/users/me/dataTypes/sleep/dataPoints"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "dataPoints": [
                    {
                        "sleep": {
                            "interval": {"startTime": "2030-01-01T23:00:00Z"},
                            "summary": {"minutesAsleep": 480},
                        }
                    }
                ]
            },
        )
    )
    points = await GoogleHealthProvider().fetch_metric("sleep_duration", START, END, "tok")
    assert points[0].value == 480 * 60
    assert points[0].unit == "s"
