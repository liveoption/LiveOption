"""Round-trip checkpoint tests for EarningsBetStrategy."""
from __future__ import annotations

import pytest

from trading.agents.strategy.earnings_bet import EarningsBetStrategy, EventWindow


def _make_window(ticker: str = "AAPL", earnings_date: str = "2025-01-30") -> EventWindow:
    return EventWindow(
        ticker=ticker,
        upq_ticker=ticker,
        earnings_date=earnings_date,
        is_bmo=True,
        event_data={"estimated_eps": 2.10, "estimated_revenue": 124_000_000_000},
        t_minus_2="2025-01-28",
        t_minus_1="2025-01-29",
        t_plus_1="2025-01-30",
        t_plus_2="2025-01-31",
        phase="pre",
        entry_decided=True,
        entry_date="2025-01-28",
        post_decisions=["2025-01-30T14:30:00Z"],
        positions_opened=True,
        prediction={"direction": "up", "confidence": 0.6, "thesis": "beat"},
        post_assessment={"decision": "hold"},
        t_minus_1_close=234.50,
    )


def test_earnings_bet_checkpoint_roundtrip(fake_config):
    s1 = EarningsBetStrategy()
    w_aapl = _make_window("AAPL", "2025-01-30")
    w_msft = _make_window("MSFT", "2025-01-29")
    s1._event_windows = [w_aapl, w_msft]
    s1._last_post_ts = {"AAPL|2025-01-30": "2025-01-30T14:30:00Z"}
    s1._stopped = {"MSFT|2025-01-29"}
    s1._pre_acted_dates = {
        "AAPL|2025-01-30": {"2025-01-28", "2025-01-29"},
    }
    s1._last_snapshot_ts = "2025-01-30T14:35:00Z"

    snap = s1.save_checkpoint()

    s2 = EarningsBetStrategy()
    s2.load_checkpoint(snap)

    assert len(s2._event_windows) == 2
    w0 = s2._event_windows[0]
    assert isinstance(w0, EventWindow)
    assert w0.ticker == "AAPL"
    assert w0.earnings_date == "2025-01-30"
    assert w0.is_bmo is True
    assert w0.event_data["estimated_eps"] == 2.10
    assert w0.prediction["direction"] == "up"
    assert w0.t_minus_1_close == 234.50
    assert w0.post_decisions == ["2025-01-30T14:30:00Z"]

    assert s2._last_post_ts == {"AAPL|2025-01-30": "2025-01-30T14:30:00Z"}
    assert s2._stopped == {"MSFT|2025-01-29"}
    assert s2._pre_acted_dates == {
        "AAPL|2025-01-30": {"2025-01-28", "2025-01-29"},
    }
    assert s2._last_snapshot_ts == "2025-01-30T14:35:00Z"


def test_earnings_bet_checkpoint_version_mismatch(fake_config):
    s = EarningsBetStrategy()
    with pytest.raises(ValueError, match="schema version"):
        s.load_checkpoint({"_version": 99})
