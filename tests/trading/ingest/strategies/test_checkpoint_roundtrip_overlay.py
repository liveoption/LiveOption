"""Round-trip checkpoint tests for OverlayProfit / OverlayHedge strategies."""
from __future__ import annotations

import pytest

from trading.agents.strategy.overlay import (
    OverlayHedgeStrategy,
    OverlayProfitStrategy,
)


def test_overlay_profit_checkpoint_roundtrip(fake_config):
    s1 = OverlayProfitStrategy(underlying="SPY")
    s1._last_rebalance = "2025-01-06"
    s1._last_share_qty = 10_000
    s1._assignment_pending = True
    s1._rebalances_without_put_buy = 1

    snap = s1.save_checkpoint()

    s2 = OverlayProfitStrategy(underlying="SPY")
    s2.load_checkpoint(snap)

    assert s2._last_rebalance == "2025-01-06"
    assert s2._last_share_qty == 10_000
    assert s2._assignment_pending is True
    assert s2._rebalances_without_put_buy == 1


def test_overlay_hedge_checkpoint_roundtrip(fake_config):
    s1 = OverlayHedgeStrategy(underlying="QQQ")
    s1._last_rebalance = "2025-02-03"
    s1._last_share_qty = None
    s1._assignment_pending = False
    s1._rebalances_without_put_buy = 0

    snap = s1.save_checkpoint()

    s2 = OverlayHedgeStrategy(underlying="QQQ")
    s2.load_checkpoint(snap)

    assert s2._last_rebalance == "2025-02-03"
    assert s2._last_share_qty is None
    assert s2._assignment_pending is False
    assert s2._rebalances_without_put_buy == 0


def test_overlay_checkpoint_version_mismatch(fake_config):
    s = OverlayProfitStrategy(underlying="SPY")
    with pytest.raises(ValueError, match="schema version"):
        s.load_checkpoint({"_version": 99})
