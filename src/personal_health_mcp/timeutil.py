"""Time helpers (timezone-aware UTC throughout)."""

from __future__ import annotations

from datetime import UTC, datetime


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` as timezone-aware UTC (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
