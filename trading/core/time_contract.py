"""UTC-Z / ET-offset time contract helpers. See spec Section 3.5."""

from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC_TZ = timezone.utc
ET_TZ = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    """Return tz-aware UTC now."""
    return datetime.now(UTC_TZ)


def to_iso_z(dt: datetime) -> str:
    """Serialize tz-aware datetime as RFC3339 with Z suffix, ms precision."""
    if dt.tzinfo is None:
        raise ValueError("to_iso_z requires tz-aware datetime")
    dt_utc = dt.astimezone(UTC_TZ)
    # Use milliseconds precision
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt_utc.microsecond // 1000:03d}Z"


def utc_from_iso(s: str) -> datetime:
    """Parse RFC3339 string; must include tz info."""
    # Accept Z suffix
    s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(s2)
    if dt.tzinfo is None:
        raise ValueError(f"utc_from_iso requires tz-aware input, got: {s}")
    return dt.astimezone(UTC_TZ)


def trading_day_from_ts(dt: datetime) -> str:
    """Return YYYY-MM-DD in ET from a tz-aware datetime."""
    if dt.tzinfo is None:
        raise ValueError("trading_day_from_ts requires tz-aware datetime")
    return dt.astimezone(ET_TZ).strftime("%Y-%m-%d")


def et_time_from_ts(dt: datetime) -> str:
    """Return HH:MM in ET from a tz-aware datetime."""
    if dt.tzinfo is None:
        raise ValueError("et_time_from_ts requires tz-aware datetime")
    return dt.astimezone(ET_TZ).strftime("%H:%M")
