from datetime import datetime, timezone, timedelta
import pytest
from trading.core.time_contract import (
    utc_now, utc_from_iso, to_iso_z, trading_day_from_ts, et_time_from_ts,
    ET_TZ, UTC_TZ,
)

def test_utc_now_is_tz_aware():
    t = utc_now()
    assert t.tzinfo is not None and t.utcoffset() == timedelta(0)

def test_to_iso_z_formats_with_Z_suffix():
    t = datetime(2026, 4, 18, 12, 34, 56, 123000, tzinfo=timezone.utc)
    assert to_iso_z(t) == "2026-04-18T12:34:56.123Z"

def test_utc_from_iso_parses_Z_suffix():
    t = utc_from_iso("2026-04-18T12:34:56.123Z")
    assert t == datetime(2026, 4, 18, 12, 34, 56, 123000, tzinfo=timezone.utc)

def test_utc_from_iso_rejects_naive():
    with pytest.raises(ValueError, match="tz"):
        utc_from_iso("2026-04-18T12:34:56")

def test_trading_day_from_ts_uses_ET():
    # 2026-04-19T03:30:00Z is 2026-04-18 23:30 ET — still counts as 18
    t = datetime(2026, 4, 19, 3, 30, tzinfo=timezone.utc)
    assert trading_day_from_ts(t) == "2026-04-18"

def test_et_time_from_ts_returns_hhmm():
    t = datetime(2026, 4, 18, 18, 30, tzinfo=timezone.utc)   # 14:30 ET
    assert et_time_from_ts(t) == "14:30"
