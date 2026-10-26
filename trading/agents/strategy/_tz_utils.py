"""Timezone utilities for strategy modules.

PMB emits timestamps in UTC.  Strategy code that needs Eastern Time
(e.g. minutes-to-close calculations) should use the helpers here
rather than naively parsing HH:MM from the raw ISO string.

Since the engine timestamp refactor (Task 2 of the Web Interface Plan A),
``TradingState.current_ts`` is a tz-aware UTC ``datetime``.  These helpers
accept both ``datetime`` and legacy ISO ``str`` for backward compatibility
with callers that still pass strings (e.g. PMB raw clock values).
"""

from __future__ import annotations

from datetime import datetime, time

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
MARKET_CLOSE = time(16, 0)  # 4:00 PM ET


def _to_dt(current_ts: datetime | str) -> datetime:
    """Coerce a datetime or ISO string to a tz-aware datetime.

    Naive-datetime inputs raise, matching the time_contract invariant.
    Naive-string inputs are assumed ET (PMB legacy convention; also matches
    greeks_math.compute_time_to_expiry). PMB real-world output is tz-aware
    UTC so the naive branches are legacy/test-only.
    """
    if isinstance(current_ts, datetime):
        if current_ts.tzinfo is None:
            raise ValueError("_to_dt requires tz-aware datetime input")
        return current_ts
    dt = datetime.fromisoformat(str(current_ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=ET)


def et_market_open_utc(date_str: str) -> str:
    """Return UTC ISO string for 09:30 ET on the given date (YYYY-MM-DD)."""
    from datetime import date as _date
    d = _date.fromisoformat(date_str)
    et_open = datetime(d.year, d.month, d.day, 9, 30, 0, tzinfo=ET)
    utc_open = et_open.astimezone(UTC)
    return utc_open.strftime("%Y-%m-%dT%H:%M:%S")


def et_market_close_utc(date_str: str) -> str:
    """Return UTC ISO string for 16:00 ET on the given date (YYYY-MM-DD)."""
    from datetime import date as _date
    d = _date.fromisoformat(date_str)
    et_close = datetime(d.year, d.month, d.day, 16, 0, 0, tzinfo=ET)
    utc_close = et_close.astimezone(UTC)
    return utc_close.strftime("%Y-%m-%dT%H:%M:%S")


def minutes_to_close_et(current_ts: datetime | str) -> int:
    """Return minutes remaining until 16:00 ET.

    Accepts a tz-aware datetime or an ISO string (naive assumed UTC).
    Returns 0 if past market close.
    """
    try:
        dt_et = _to_dt(current_ts).astimezone(ET)
        market_close_minutes = 16 * 60
        current_minutes = dt_et.hour * 60 + dt_et.minute
        remaining = market_close_minutes - current_minutes
        return max(remaining, 0)
    except (ValueError, IndexError, TypeError):
        return 0


def is_market_hours_et(current_ts: datetime | str) -> bool:
    """Return True if *current_ts* falls within regular trading hours (09:30–16:00 ET)."""
    try:
        dt_et = _to_dt(current_ts).astimezone(ET)
        t = dt_et.time()
        return time(9, 30) <= t < time(16, 0)
    except (ValueError, IndexError, TypeError):
        return True  # fallback: treat unknown timestamps as market hours


def ts_to_et_display(current_ts: datetime | str) -> str:
    """Convert a UTC PMB timestamp to HH:MM ET display string."""
    try:
        return _to_dt(current_ts).astimezone(ET).strftime("%H:%M")
    except (ValueError, IndexError, TypeError):
        s = str(current_ts)
        return s[11:16] if len(s) >= 16 else "?"
