"""Round-trip checkpoint tests for PaperAlignedStrategy."""
from __future__ import annotations

import pytest

from trading.agents.strategy.paper_aligned import PaperAlignedStrategy
from trading.core.schemas import BarData, MarketEvent


def _make_event(ts: str, symbol: str = "SPY", close: float = 500.0) -> MarketEvent:
    bar = BarData(
        symbol=symbol,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
        window_start=ts,
        instrument_type="STOCK",
    )
    return MarketEvent(event_ts=ts, bars=[bar], bar_history={symbol: [bar]})


def test_paper_aligned_checkpoint_roundtrip(fake_config):
    s1 = PaperAlignedStrategy()
    s1._bar_buffer = [
        _make_event("2025-01-06T14:30:00.000Z"),
        _make_event("2025-01-06T14:31:00.000Z", close=501.0),
    ]
    s1._steps_since_decision = 7

    snap = s1.save_checkpoint()

    s2 = PaperAlignedStrategy()
    s2.load_checkpoint(snap)

    assert s2._steps_since_decision == 7
    assert len(s2._bar_buffer) == 2
    assert s2._bar_buffer[0].event_ts == "2025-01-06T14:30:00.000Z"
    assert s2._bar_buffer[1].bars[0].close == 501.0
    # Ensure re-hydrated entries are MarketEvent instances
    assert isinstance(s2._bar_buffer[0], MarketEvent)


def test_paper_aligned_checkpoint_version_mismatch(fake_config):
    s = PaperAlignedStrategy()
    with pytest.raises(ValueError, match="schema version"):
        s.load_checkpoint({"_version": 99})
