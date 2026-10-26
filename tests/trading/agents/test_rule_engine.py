"""Tests for the RuleEngine — position exits and daily P&L limits.

12 test cases covering:
  - Stop-loss / take-profit per position (4 tests)
  - Daily profit target / loss limit (3 tests)
  - Realized P&L from fills (1 test)
  - Trend signal (3 tests)
  - Day-open state reset (1 test)
"""

from __future__ import annotations

import pytest

from trading.agents.strategy.rule_engine import RuleEngine, RuleEngineParams
from trading.core.schemas import FillEvent, PositionInfo, TradeIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(
    contract: str = "O:SPY250106C00595000",
    avg_entry: float = 2.00,
    mark_price: float = 2.00,
    qty: int = 2,
    side: str = "LONG",
    dte: int = 0,
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side=side,
        qty=qty,
        avg_entry=avg_entry,
        mark_price=mark_price,
        unrealized_pnl=(mark_price - avg_entry) * qty * 100,
        dte=dte,
        stale=False,
        stale_minutes=0,
    )


def _make_fill(
    contract: str = "O:SPY250106C00595000",
    side: str = "SELL",
    qty: int = 2,
    price: float = 3.00,
    fees: float = 1.30,
) -> FillEvent:
    return FillEvent(
        trade_id="t1",
        order_id="o1",
        contract=contract,
        side=side,
        qty=qty,
        price=price,
        fees=fees,
        fill_ts="2025-01-06T10:30:00",
    )


# ---------------------------------------------------------------------------
# 1-4: Stop-loss / take-profit
# ---------------------------------------------------------------------------

class TestPositionExits:
    """Position-level stop-loss and take-profit checks."""

    def test_stop_loss_triggers(self):
        """Position at -26% -> returns SELL intent."""
        engine = RuleEngine(RuleEngineParams(stop_loss_pct=-25.0))
        # pnl_pct = (1.48 - 2.00) / 2.00 * 100 = -26%
        pos = _make_position(avg_entry=2.00, mark_price=1.48)
        intents = engine.check_position_exits([pos])

        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert intents[0].contract == pos.contract
        assert intents[0].qty == pos.qty
        assert "Stop-loss" in intents[0].rationale

    def test_stop_loss_not_triggered(self):
        """Position at -20% (above -25% threshold) -> returns empty."""
        engine = RuleEngine(RuleEngineParams(stop_loss_pct=-25.0))
        # pnl_pct = (1.60 - 2.00) / 2.00 * 100 = -20%
        pos = _make_position(avg_entry=2.00, mark_price=1.60)
        intents = engine.check_position_exits([pos])

        assert intents == []

    def test_take_profit_triggers(self):
        """Position at +55% -> returns SELL intent."""
        engine = RuleEngine(RuleEngineParams(take_profit_pct=50.0))
        # pnl_pct = (3.10 - 2.00) / 2.00 * 100 = +55%
        pos = _make_position(avg_entry=2.00, mark_price=3.10)
        intents = engine.check_position_exits([pos])

        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert "Take-profit" in intents[0].rationale

    def test_take_profit_not_triggered(self):
        """Position at +25% (below +50% threshold) -> returns empty."""
        engine = RuleEngine(RuleEngineParams(take_profit_pct=50.0))
        # pnl_pct = (2.50 - 2.00) / 2.00 * 100 = +25%
        pos = _make_position(avg_entry=2.00, mark_price=2.50)
        intents = engine.check_position_exits([pos])

        assert intents == []


# ---------------------------------------------------------------------------
# 5-7: Daily limits
# ---------------------------------------------------------------------------

class TestDailyLimits:
    """Daily profit target and loss limit checks."""

    def test_daily_profit_target_triggers(self):
        """Realized +$85 with target $80 -> returns 'profit_target' and sets stopped."""
        engine = RuleEngine(RuleEngineParams(daily_profit_target=80.0))
        engine._daily_realized = 85.0

        result = engine.check_daily_limits()

        assert result == "profit_target"
        assert engine.trading_stopped is True

    def test_daily_loss_limit_triggers(self):
        """Realized -$110 with limit $100 -> returns 'loss_limit' and sets stopped."""
        engine = RuleEngine(RuleEngineParams(daily_loss_limit=100.0))
        engine._daily_realized = -110.0

        result = engine.check_daily_limits()

        assert result == "loss_limit"
        assert engine.trading_stopped is True

    def test_daily_limits_not_triggered(self):
        """Realized +$50 with target $80 -> no action, not stopped."""
        engine = RuleEngine(RuleEngineParams(daily_profit_target=80.0, daily_loss_limit=100.0))
        engine._daily_realized = 50.0

        result = engine.check_daily_limits()

        assert result is None
        assert engine.trading_stopped is False


# ---------------------------------------------------------------------------
# 8: Realized P&L from fills
# ---------------------------------------------------------------------------

class TestRealizedPnL:
    """Realized P&L accumulation from confirmed fill events."""

    def test_update_realized_from_fill(self):
        """SELL fill at $3.00 with entry at $2.00, qty=2 -> realized += $198.70.

        Calculation: (3.00 - 2.00) * 2 * 100 - 1.30 = 200.00 - 1.30 = 198.70
        """
        engine = RuleEngine()
        contract = "O:SPY250106C00595000"

        # Record the BUY fill first
        buy_fill = _make_fill(contract=contract, side="BUY", qty=2, price=2.00, fees=0.65)
        engine.update_realized_from_fills([buy_fill])
        assert engine.daily_realized == 0.0  # BUY does not change realized

        # Now the SELL fill
        sell_fill = _make_fill(contract=contract, side="SELL", qty=2, price=3.00, fees=1.30)
        engine.update_realized_from_fills([sell_fill])

        assert engine.daily_realized == pytest.approx(198.70)


# ---------------------------------------------------------------------------
# 9-11: Trend signal
# ---------------------------------------------------------------------------

class TestTrendSignal:
    """SPY trend signal based on 15-bar lookback."""

    def _make_prices(self, old: float, new: float) -> list[float]:
        """Create a 16-element price list with old at index 0, new at index 15."""
        # Linear interpolation between old and new for 16 points
        return [old + (new - old) * i / 15 for i in range(16)]

    def test_trend_signal_bullish(self):
        """SPY up +0.10% with threshold 0.05% -> 'bullish'."""
        engine = RuleEngine(RuleEngineParams(trend_threshold_pct=0.05))
        # change = (500.50 - 500.00) / 500.00 * 100 = +0.10%
        prices = self._make_prices(old=500.00, new=500.50)
        assert engine.get_trend_signal(prices) == "bullish"

    def test_trend_signal_bearish(self):
        """SPY down -0.08% with threshold 0.05% -> 'bearish'."""
        engine = RuleEngine(RuleEngineParams(trend_threshold_pct=0.05))
        # change = (499.60 - 500.00) / 500.00 * 100 = -0.08%
        prices = self._make_prices(old=500.00, new=499.60)
        assert engine.get_trend_signal(prices) == "bearish"

    def test_trend_signal_neutral(self):
        """SPY change +0.02% with threshold 0.05% -> 'neutral'."""
        engine = RuleEngine(RuleEngineParams(trend_threshold_pct=0.05))
        # change = (500.10 - 500.00) / 500.00 * 100 = +0.02%
        prices = self._make_prices(old=500.00, new=500.10)
        assert engine.get_trend_signal(prices) == "neutral"


# ---------------------------------------------------------------------------
# 12: Day-open reset
# ---------------------------------------------------------------------------

class TestShortPositionExits:
    """Position-level exits for SHORT positions."""

    def test_stop_loss_closes_short_with_buy(self):
        """SHORT position stop-loss should generate BUY exit."""
        pos = PositionInfo(
            contract="O:SPY250106P00590000",
            side="SHORT",
            qty=-3,
            avg_entry=2.00,
            mark_price=1.50,
            unrealized_pnl=-150.0,
            dte=0,
            stale=False,
            stale_minutes=0,
        )
        # pnl_pct = (1.50 - 2.00) / 2.00 * 100 = -25%, exactly at stop threshold
        # Use a threshold of -24% so -25% triggers
        engine = RuleEngine(RuleEngineParams(stop_loss_pct=-24.0))
        intents = engine.check_position_exits([pos])

        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].qty == 3
        assert intents[0].contract == pos.contract
        assert "Stop-loss" in intents[0].rationale

    def test_take_profit_closes_short_with_buy(self):
        """SHORT position take-profit should generate BUY exit."""
        pos = PositionInfo(
            contract="O:SPY250106P00590000",
            side="SHORT",
            qty=-3,
            avg_entry=2.00,
            mark_price=3.20,
            unrealized_pnl=360.0,
            dte=0,
            stale=False,
            stale_minutes=0,
        )
        # pnl_pct = (3.20 - 2.00) / 2.00 * 100 = +60%, above 50% threshold
        engine = RuleEngine(RuleEngineParams(take_profit_pct=50.0))
        intents = engine.check_position_exits([pos])

        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].qty == 3
        assert "Take-profit" in intents[0].rationale


class TestDayOpen:
    """Day lifecycle reset."""

    def test_on_day_open_resets_state(self):
        """After on_day_open(), daily_realized=0, trading_stopped=False, open_positions cleared."""
        engine = RuleEngine()

        # Dirty up the state
        engine._daily_realized = 150.0
        engine._trading_stopped = True
        engine._open_positions["O:SPY250106C00595000"] = _make_fill(side="BUY")

        engine.on_day_open()

        assert engine.daily_realized == 0.0
        assert engine.trading_stopped is False
        assert len(engine._open_positions) == 0
