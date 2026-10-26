"""Tests for HybridIntradayStrategy — rule exits + LLM direction.

10 test cases covering:
  1. Rule exit overrides LLM (position at -30%)
  2. Rule exit fires at non-decision-interval step
  3. Daily profit target stops trading
  4. Daily loss limit stops trading
  5. LLM direction only when flat
  6. LLM direction at decision interval
  7. LLM stop is additive (fires before rule target)
  8. Pure rule mode (both LLM flags off)
  9. on_day_open resets state
 10. Realized P&L from fills updates _daily_realized
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from trading.agents.strategy.hybrid_intraday import HybridIntradayStrategy
from trading.core.schemas import (
    BarData,
    FillEvent,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
    TradeIntent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str = "SPY",
    c: float = 595.0,
    instrument_type: str = "STOCK",
    ts: str = "2025-01-06T10:00:00",
    **kwargs,
) -> BarData:
    return BarData(
        symbol=symbol,
        open=kwargs.get("o", c),
        high=kwargs.get("h", c),
        low=kwargs.get("l", c),
        close=c,
        volume=kwargs.get("v", 100_000),
        window_start=ts,
        instrument_type=instrument_type,
    )


def _make_market_event(
    bars: list[BarData] | None = None,
    ts: str = "2025-01-06T10:00:00",
) -> MarketEvent:
    bars = bars or [_make_bar(ts=ts)]
    return MarketEvent(event_ts=ts, bars=bars, bar_history={})


def _make_portfolio(
    positions: list[PositionInfo] | None = None,
    cash: float = 90_000.0,
    equity: float = 100_000.0,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        positions=positions or [],
    )


def _make_position(
    contract: str = "O:SPY250106C00595000",
    avg_entry: float = 2.00,
    mark_price: float = 2.00,
    qty: int = 2,
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side="LONG",
        qty=qty,
        avg_entry=avg_entry,
        mark_price=mark_price,
        unrealized_pnl=(mark_price - avg_entry) * qty * 100,
        dte=0,
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


def _make_state(
    current_ts: str = "2025-01-06T10:00:00",
    fills: list[FillEvent] | None = None,
) -> MagicMock:
    """Create a minimal TradingState-like mock."""
    state = MagicMock()
    state.current_ts = current_ts
    state.fills = fills or []
    state.errors = []
    return state


def _make_strategy(**kwargs) -> HybridIntradayStrategy:
    """Create a HybridIntradayStrategy with sensible test defaults."""
    defaults = dict(
        stop_loss_pct=-25,
        take_profit_pct=50,
        trend_threshold_pct=0.05,
        daily_profit_target=80,
        daily_loss_limit=100,
        qty_per_trade=2,
        llm_provider="mock",
        llm_model="mock",
        decision_interval_minutes=15,
        enable_llm_direction=True,
        enable_llm_stop=True,
    )
    defaults.update(kwargs)
    return HybridIntradayStrategy(**defaults)


class MockLLM:
    """Mock LLM client that returns predefined raw JSON strings."""

    def __init__(
        self,
        direction_response: str = '{"direction": "neutral"}',
        stop_response: str = '{"stop": false, "rationale": "keep going"}',
    ):
        self.direction_response = direction_response
        self.stop_response = stop_response
        self.call_raw_count = 0
        self._calls: list[tuple[str, str]] = []

    def call_raw(self, system_prompt: str, user_prompt: str) -> str:
        self.call_raw_count += 1
        self._calls.append((system_prompt, user_prompt))
        # Distinguish direction vs stop prompt by system prompt content
        if "direction" in system_prompt.lower():
            return self.direction_response
        if "exit" in system_prompt.lower() or "stop" in system_prompt.lower():
            return self.stop_response
        return '{"direction": "neutral"}'


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHybridIntraday:
    """Tests for HybridIntradayStrategy."""

    # 1. test_rule_exit_overrides_llm
    def test_rule_exit_overrides_llm(self):
        """Position at -30% -> SELL returned, LLM not called."""
        strat = _make_strategy()
        mock_llm = MockLLM()
        strat._llm = mock_llm

        # Position at -30%: (1.40 - 2.00) / 2.00 * 100 = -30%
        pos = _make_position(avg_entry=2.00, mark_price=1.40, qty=2)
        portfolio = _make_portfolio(positions=[pos])
        state = _make_state()
        event = _make_market_event()

        intents = strat.on_step(event, portfolio, state)

        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert intents[0].contract == pos.contract
        assert "Stop-loss" in intents[0].rationale
        # LLM should NOT have been called
        assert mock_llm.call_raw_count == 0

    # 2. test_rule_exit_every_step
    def test_rule_exit_every_step(self):
        """Exit fires at non-decision-interval step (step 3 of 15)."""
        strat = _make_strategy(decision_interval_minutes=15)
        mock_llm = MockLLM()
        strat._llm = mock_llm

        # Run 2 normal steps (no position, no exit)
        state = _make_state()
        for i in range(2):
            strat.on_step(
                _make_market_event(ts=f"2025-01-06T10:{i:02d}:00"),
                _make_portfolio(),
                state,
            )

        # Step 3: position at -30% should trigger exit even though it's not
        # a decision interval (step 3 of 15)
        pos = _make_position(avg_entry=2.00, mark_price=1.40, qty=2)
        portfolio = _make_portfolio(positions=[pos])

        intents = strat.on_step(
            _make_market_event(ts="2025-01-06T10:02:00"),
            portfolio,
            state,
        )

        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert "Stop-loss" in intents[0].rationale

    # 3. test_daily_profit_target_stops
    def test_daily_profit_target_stops(self):
        """Realized > target -> flatten + stopped."""
        strat = _make_strategy(daily_profit_target=80)
        mock_llm = MockLLM()
        strat._llm = mock_llm

        # Set realized above target
        strat._rule_engine._daily_realized = 85.0

        # Have an open position that should be flattened
        pos = _make_position(avg_entry=2.00, mark_price=2.10, qty=2)
        portfolio = _make_portfolio(positions=[pos])
        state = _make_state()
        event = _make_market_event()

        intents = strat.on_step(event, portfolio, state)

        # Should flatten the remaining position
        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert "profit target" in intents[0].rationale.lower()
        assert strat._trading_stopped is True

        # Subsequent steps return empty
        intents2 = strat.on_step(event, _make_portfolio(), state)
        assert intents2 == []

    # 4. test_daily_loss_limit_stops
    def test_daily_loss_limit_stops(self):
        """Realized < -limit -> flatten + stopped."""
        strat = _make_strategy(daily_loss_limit=100)
        mock_llm = MockLLM()
        strat._llm = mock_llm

        # Set realized below limit
        strat._rule_engine._daily_realized = -110.0

        pos = _make_position(avg_entry=2.00, mark_price=1.90, qty=2)
        portfolio = _make_portfolio(positions=[pos])
        state = _make_state()
        event = _make_market_event()

        intents = strat.on_step(event, portfolio, state)

        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert "loss limit" in intents[0].rationale.lower()
        assert strat._trading_stopped is True

    # 5. test_llm_direction_only_when_flat
    def test_llm_direction_only_when_flat(self):
        """Has position -> LLM direction not called."""
        strat = _make_strategy(decision_interval_minutes=1)
        mock_llm = MockLLM(
            direction_response='{"direction": "bullish", "contract": "O:SPY250106C00596000", "qty": 2, "rationale": "test"}',
            stop_response='{"stop": false, "rationale": "keep going"}',
        )
        strat._llm = mock_llm

        # Position exists (no exit trigger, healthy P&L)
        pos = _make_position(avg_entry=2.00, mark_price=2.10, qty=2)
        portfolio = _make_portfolio(positions=[pos])
        state = _make_state()
        event = _make_market_event()

        # Step to reach decision interval
        intents = strat.on_step(event, portfolio, state)

        # LLM stop check was called (1 call), but direction was NOT called
        # because we have a position. Total calls = 1 (stop only).
        assert intents == []
        # The stop check call would fire, but direction should not
        # Verify no BUY intent was returned
        assert all(i.side != "BUY" for i in intents)

    # 6. test_llm_direction_at_interval
    def test_llm_direction_at_interval(self):
        """At decision interval, no position -> LLM called, intent returned."""
        strat = _make_strategy(decision_interval_minutes=3)
        mock_llm = MockLLM(
            direction_response=json.dumps({
                "direction": "bullish",
                "contract": "O:SPY250106C00596000",
                "qty": 3,
                "rationale": "momentum breakout",
            }),
            stop_response='{"stop": false, "rationale": "keep going"}',
        )
        strat._llm = mock_llm

        portfolio = _make_portfolio()  # no positions
        state = _make_state()

        # Steps 1 and 2: no decision
        for i in range(2):
            result = strat.on_step(
                _make_market_event(ts=f"2025-01-06T10:{i:02d}:00"),
                portfolio,
                state,
            )
            assert result == []

        # Step 3: decision interval -> LLM called
        result = strat.on_step(
            _make_market_event(ts="2025-01-06T10:02:00"),
            portfolio,
            state,
        )

        assert len(result) == 1
        assert result[0].side == "BUY"
        assert result[0].contract == "O:SPY250106C00596000"
        assert result[0].qty == 3
        assert mock_llm.call_raw_count >= 1  # at least direction call

    # 7. test_llm_stop_additive
    def test_llm_stop_additive(self):
        """LLM says stop at +$50 (rule target is +$80) -> honors LLM."""
        strat = _make_strategy(
            daily_profit_target=80,
            decision_interval_minutes=1,
        )
        mock_llm = MockLLM(
            stop_response='{"stop": true, "rationale": "good day, lock it in"}',
        )
        strat._llm = mock_llm

        # Realized is +$50, below rule target of $80
        strat._rule_engine._daily_realized = 50.0

        pos = _make_position(avg_entry=2.00, mark_price=2.10, qty=2)
        portfolio = _make_portfolio(positions=[pos])
        state = _make_state()
        event = _make_market_event()

        intents = strat.on_step(event, portfolio, state)

        # LLM said stop -> flatten position
        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert "LLM" in intents[0].rationale
        assert strat._trading_stopped is True

    # 8. test_pure_rule_mode
    def test_pure_rule_mode(self):
        """enable_llm_direction=False, enable_llm_stop=False -> no LLM calls, uses trend signal."""
        strat = _make_strategy(
            enable_llm_direction=False,
            enable_llm_stop=False,
            decision_interval_minutes=1,
            trend_threshold_pct=0.05,
        )
        # Should NOT have LLM initialized
        assert strat._llm is None

        # Build a bar buffer with 16+ SPY bars showing bullish trend
        # Old price: 500.00, New price: 501.00 -> +0.20% > 0.05% threshold
        for i in range(16):
            price = 500.00 + (1.0 * i / 15)
            bars = [
                _make_bar(symbol="SPY", c=price, ts=f"2025-01-06T09:{30+i:02d}:00"),
                _make_bar(
                    symbol="O:SPY250106C00500000",
                    c=2.50,
                    instrument_type="OPTION",
                    ts=f"2025-01-06T09:{30+i:02d}:00",
                ),
            ]
            event = _make_market_event(bars=bars, ts=f"2025-01-06T09:{30+i:02d}:00")
            state = _make_state(current_ts=f"2025-01-06T09:{30+i:02d}:00")
            result = strat.on_step(event, _make_portfolio(), state)

        # Last step should be at a decision interval and produce a BUY
        # from the trend signal (bullish)
        # The trend entry fires at each decision interval (interval=1, so every step)
        # The 16th step should have sufficient price history
        assert len(result) == 1
        assert result[0].side == "BUY"
        assert result[0].strategy_name == "hybrid_intraday"
        assert "Trend signal: bullish" in result[0].rationale

    # 9. test_on_day_open_resets
    def test_on_day_open_resets(self):
        """Rule engine state reset, daily_realized=0, trading_stopped=False."""
        strat = _make_strategy()

        # Dirty up the state
        strat._rule_engine._daily_realized = 150.0
        strat._rule_engine._trading_stopped = True
        strat._trading_stopped = True
        strat._steps_since_decision = 10
        strat._bar_buffer = [_make_market_event()] * 5
        strat._n_trades = 3
        strat._n_wins = 2

        state = _make_state()
        strat.on_day_open("2025-01-07", state)

        assert strat._rule_engine.daily_realized == 0.0
        assert strat._rule_engine.trading_stopped is False
        assert strat._trading_stopped is False
        assert strat._steps_since_decision == 0
        assert len(strat._bar_buffer) == 0
        assert strat._n_trades == 0
        assert strat._n_wins == 0

    # 11. test_flatten_remaining_short_produces_buy
    def test_flatten_remaining_short_produces_buy(self):
        """_flatten_remaining on a SHORT position should produce side=BUY, qty=abs(qty)."""
        strat = _make_strategy()
        pos = PositionInfo(
            contract="O:SPY250106P00590000",
            side="SHORT",
            qty=-2,
            avg_entry=2.00,
            mark_price=1.80,
            unrealized_pnl=40.0,
            dte=0,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[pos])
        intents = strat._flatten_remaining(portfolio, "end of day flatten")

        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].qty == 2
        assert intents[0].contract == pos.contract

    # 10. test_realized_pnl_from_fills
    def test_realized_pnl_from_fills(self):
        """Fill events via state.fills update _daily_realized."""
        strat = _make_strategy()
        mock_llm = MockLLM()
        strat._llm = mock_llm

        contract = "O:SPY250106C00595000"

        # First: record a BUY fill
        buy_fill = _make_fill(contract=contract, side="BUY", qty=2, price=2.00, fees=0.65)
        state1 = _make_state(fills=[buy_fill])
        strat.on_step(_make_market_event(), _make_portfolio(), state1)

        assert strat._rule_engine.daily_realized == 0.0

        # Now: record a SELL fill at profit
        sell_fill = _make_fill(contract=contract, side="SELL", qty=2, price=3.00, fees=1.30)
        state2 = _make_state(fills=[sell_fill])
        strat.on_step(_make_market_event(), _make_portfolio(), state2)

        # (3.00 - 2.00) * 2 * 100 - 1.30 = 198.70
        assert strat._rule_engine.daily_realized == pytest.approx(198.70)
