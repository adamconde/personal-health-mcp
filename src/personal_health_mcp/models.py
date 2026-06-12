"""Shared domain models.

These Pydantic models are the lingua franca between providers, the resolution
engine, the aggregator, and the MCP tool layer. Providers emit canonical
:class:`DataPoint`s; the aggregator resolves them into a :class:`MetricSeries`
and wraps the result in a :class:`ResponseEnvelope` whose first field is always
the provider attribution — satisfying the "every response names the provider"
requirement structurally.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ResolutionMode(StrEnum):
    """How to reconcile a metric when multiple providers have data.

    Values:
        AUTHORITY: Prefer a designated provider; fall back to an ordered list
            only when the authority has no data for the window.
        AUTO: Take the most recent value per time bucket across all providers.
    """

    AUTHORITY = "authority"
    AUTO = "auto"


class Token(BaseModel):
    """OAuth token set for a provider.

    Attributes:
        provider: Provider name (e.g. ``"oura"``).
        access_token: Current access token.
        refresh_token: Refresh token (Withings rotates this on every refresh).
        expires_at: Absolute UTC expiry of the access token.
        scopes: Granted scopes.
        provider_user_id: Provider-side user id, where available.
    """

    model_config = ConfigDict(extra="ignore")

    provider: str
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    provider_user_id: str | None = None


class DataPoint(BaseModel):
    """A single canonical-unit measurement from one provider.

    Attributes:
        metric: Canonical metric key.
        value: Magnitude in the metric's canonical unit.
        unit: Canonical unit name.
        start: Timestamp (sample time, or interval/day start).
        end: Interval end, when applicable.
        provider: Originating provider name.
        device: Optional source device/model for provenance.
    """

    model_config = ConfigDict(extra="forbid")

    metric: str
    value: float
    unit: str
    start: datetime
    end: datetime | None = None
    provider: str
    device: str | None = None


class ResolvedPoint(BaseModel):
    """A resolved data point carrying the provider that supplied it.

    Attributes:
        value: Magnitude in the requested display unit.
        unit: Display unit name.
        formatted: Human-friendly rendering for compound units (e.g. ``5' 9"``
            for ``ft/in``); ``None`` when the numeric value suffices.
        start: Sample/interval/day start.
        end: Interval end, when applicable.
        provider: Provider that supplied this value.
        device: Optional source device/model.
    """

    value: float
    unit: str
    formatted: str | None = None
    start: datetime
    end: datetime | None = None
    provider: str
    device: str | None = None


class MetricSeries(BaseModel):
    """A resolved series for one metric, with explainable provenance.

    Attributes:
        metric: Canonical metric key.
        unit: Display unit of the points.
        mode: Resolution mode that was applied.
        resolution: Human-readable description of which branch fired
            (e.g. ``"authority:oura"``, ``"fallback:withings"``, ``"auto"``).
        providers: Distinct providers that contributed to the result.
        points: Resolved data points.
        note: Optional diagnostic (e.g. why a series is empty).
    """

    metric: str
    unit: str
    mode: ResolutionMode
    resolution: str
    providers: list[str] = Field(default_factory=list)
    points: list[ResolvedPoint] = Field(default_factory=list)
    note: str | None = None


class ResponseEnvelope(BaseModel):
    """Top-level tool response. Provider attribution comes first by design.

    Attributes:
        providers: Providers that contributed data (named first, always present).
        metric: Canonical metric key.
        unit: Display unit.
        mode: Resolution mode applied.
        resolution: Which resolution branch fired.
        start: Query window start.
        end: Query window end.
        count: Number of resolved points.
        points: Resolved data points.
        errors: Provider name -> error message for providers that failed to
            return data (e.g. an auth/HTTP error). Lets a client distinguish a
            provider failure from a genuine absence of data.
        note: Optional diagnostic.
    """

    providers: list[str]
    metric: str
    unit: str
    mode: ResolutionMode
    resolution: str
    start: datetime
    end: datetime
    count: int
    points: list[ResolvedPoint]
    errors: dict[str, str] = Field(default_factory=dict)
    note: str | None = None


class MetricPref(BaseModel):
    """User preference for resolving one metric.

    Attributes:
        metric: Canonical metric key.
        mode: AUTHORITY or AUTO.
        authority: Authority provider (when mode is AUTHORITY).
        fallback_order: Ordered fallback providers used if the authority is empty.
    """

    metric: str
    mode: ResolutionMode = ResolutionMode.AUTO
    authority: str | None = None
    fallback_order: list[str] = Field(default_factory=list)
