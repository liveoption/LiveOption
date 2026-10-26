"""Tests for RiskGateAgent — hard risk gate for trade intents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from trading.agents.risk_manager import RiskGateAgent
from trading.core.config import DataPolicyConfig, RiskConfig
from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
    RiskDecision,
    TradeIntent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_market_event(contract: str = "O:SPY250106C00595000") -> MarketEvent:
    """Create a MarketEvent with a fresh (non-stale) bar for the given contract."""
    return MarketEvent(
        event_ts="2025-01-06T10:30:00",
        bars=[
            BarData(
                symbol=contract,
                open=2.0, high=2.5, low=1.8, close=2.3,
                volume=500, window_start="2025-01-06T10:30:00",
                instrument_type="OPTION",
                stale=False, stale_minutes=0, tradeable=True,
            ),
        ],
        bar_history={},
    )


@dataclass
class MockTradingState:
    """Minimal stand-in for TradingState with only the fields RiskGateAgent needs."""

    current_ts: str = "2025-01-06T10:30:00"
    market_event: MarketEvent | None = field(default_factory=_default_market_event)


def _make_portfolio(
    cash: float = 90_000,
    equity: float = 100_000,
    daily_pnl: float = 0.0,
    daily_pnl_pct: float = 0.0,
    positions: list[PositionInfo] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        positions=positions or [],
    )


def _make_intent(
    contract: str = "O:SPY250106C00595000",
    symbol: str = "SPY",
    side: str = "BUY",
    qty: int = 5,
    order_type: str = "MARKET",
    limit_price: float | None = None,
) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        contract=contract,
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
    )


def _make_position(
    contract: str = "O:SPY250106C00595000",
    side: str = "LONG",
    qty: int = 5,
    avg_entry: float = 2.30,
    mark_price: float = 2.50,
    unrealized_pnl: float = 100.0,
    dte: int = 0,
    stale: bool = False,
    stale_minutes: int = 0,
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side=side,
        qty=qty,
        avg_entry=avg_entry,
        mark_price=mark_price,
        unrealized_pnl=unrealized_pnl,
        dte=dte,
        stale=stale,
        stale_minutes=stale_minutes,
    )


def _make_gate(
    risk_config: RiskConfig | None = None,
    data_policy: DataPolicyConfig | None = None,
) -> RiskGateAgent:
    return RiskGateAgent(
        risk_config=risk_config or RiskConfig(),
        data_policy_config=data_policy or DataPolicyConfig(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAllowNormalTrade:
    """test_allow_normal_trade — within all limits → action='ALLOW'."""

    def test_allow_normal_trade(self):
        gate = _make_gate()
        intent = _make_intent(qty=2, limit_price=2.50)
        portfolio = _make_portfolio()
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "ALLOW"
        assert decisions[0].original_intent is intent


class TestRejectMaxExposure:
    """test_reject_max_exposure — position value / equity > max_exposure_pct → REJECT."""

    def test_reject_max_exposure(self):
        # Very low exposure limit so even 1 contract exceeds it.
        # max_exposure_pct=0.001 on $100K equity → max value = $100.
        # 1 contract at $10 → notional = 1*10*100 = $1,000 → way over.
        config = RiskConfig(max_exposure_pct=0.001)
        gate = _make_gate(risk_config=config)

        intent = _make_intent(qty=5, limit_price=10.0)
        portfolio = _make_portfolio(equity=100_000)
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "REJECT"
        assert "exposure" in decisions[0].reason.lower()


class TestRejectDailyLossLimit:
    """test_reject_daily_loss_limit — daily_pnl < -max_daily_loss_pct → reject opening."""

    def test_reject_daily_loss_limit(self):
        config = RiskConfig(max_daily_loss_pct=0.02)
        gate = _make_gate(risk_config=config)

        intent = _make_intent(side="BUY", qty=5)
        # daily_pnl_pct = -3% which exceeds -2% limit
        portfolio = _make_portfolio(daily_pnl=-3000, daily_pnl_pct=-0.03)
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "REJECT"
        assert "daily loss" in decisions[0].reason.lower()


class TestAllowClosingTradeDuringLoss:
    """test_allow_closing_trade_during_loss — loss exceeded but closing → ALLOW."""

    def test_allow_closing_trade_during_loss(self):
        config = RiskConfig(max_daily_loss_pct=0.02)
        gate = _make_gate(risk_config=config)

        pos = _make_position(contract="O:SPY250106C00595000", qty=5)
        # Sell intent closes existing position
        intent = _make_intent(
            contract="O:SPY250106C00595000", side="SELL", qty=5
        )
        portfolio = _make_portfolio(
            daily_pnl=-3000, daily_pnl_pct=-0.03, positions=[pos]
        )
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "ALLOW"


class TestCircuitBreakerTriggers:
    """test_circuit_breaker_triggers — loss > circuit_breaker_loss_pct → reject ALL."""

    def test_circuit_breaker_triggers(self):
        config = RiskConfig(circuit_breaker_loss_pct=0.03)
        gate = _make_gate(risk_config=config)

        intents = [
            _make_intent(side="BUY", qty=2),
            _make_intent(side="SELL", qty=3, contract="O:SPY250106P00580000"),
        ]
        # daily_pnl_pct = -4% which exceeds -3% circuit breaker
        portfolio = _make_portfolio(daily_pnl=-4000, daily_pnl_pct=-0.04)
        state = MockTradingState()

        decisions = gate.evaluate(intents, portfolio, state)

        assert len(decisions) == 2
        assert all(d.action == "REJECT" for d in decisions)
        assert all("circuit breaker" in d.reason.lower() for d in decisions)


class TestCircuitBreakerResetsOnNewDay:
    """test_circuit_breaker_resets_on_new_day — on_new_day() clears breaker."""

    def test_circuit_breaker_resets_on_new_day(self):
        config = RiskConfig(circuit_breaker_loss_pct=0.03)
        gate = _make_gate(risk_config=config)

        # Trigger circuit breaker
        intent = _make_intent(qty=1)
        bad_portfolio = _make_portfolio(daily_pnl=-4000, daily_pnl_pct=-0.04)
        state = MockTradingState()
        decisions = gate.evaluate([intent], bad_portfolio, state)
        assert decisions[0].action == "REJECT"

        # Reset for new day
        gate.on_new_day()

        # Now a fresh portfolio — should allow
        fresh_portfolio = _make_portfolio(daily_pnl=0, daily_pnl_pct=0.0)
        decisions = gate.evaluate([intent], fresh_portfolio, state)
        assert decisions[0].action == "ALLOW"


class TestRejectStaleInstrument:
    """test_reject_stale_instrument — stale_minutes > max_stale_minutes → REJECT."""

    def test_reject_stale_instrument(self):
        data_policy = DataPolicyConfig(max_stale_minutes=5)
        gate = _make_gate(data_policy=data_policy)

        intent = _make_intent(contract="O:SPY250106C00595000", qty=2)
        portfolio = _make_portfolio()

        # Market event shows the contract is stale for 8 minutes
        stale_bar = BarData(
            symbol="O:SPY250106C00595000",
            open=2.0,
            high=2.5,
            low=1.8,
            close=2.3,
            volume=0,
            window_start="2025-01-06T10:30:00",
            instrument_type="OPTION",
            stale=True,
            stale_minutes=8,
            tradeable=False,
        )
        market_event = MarketEvent(
            event_ts="2025-01-06T10:30:00",
            bars=[stale_bar],
            bar_history={},
        )
        state = MockTradingState(market_event=market_event)

        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "REJECT"
        assert "stale" in decisions[0].reason.lower()


class TestFlattenBeforeClose:
    """test_flatten_before_close — within flatten window → auto SELL all positions."""

    def test_flatten_before_close(self):
        config = RiskConfig(
            flatten_before_close=True,
            flatten_minutes_before_close=5,
        )
        gate = _make_gate(risk_config=config)

        pos1 = _make_position(contract="O:SPY250106C00595000", qty=5)
        pos2 = _make_position(contract="O:SPY250106P00580000", qty=3)
        portfolio = _make_portfolio(positions=[pos1, pos2])

        # 3 minutes before close (15:57 ET)
        state = MockTradingState(current_ts="2025-01-06T15:57:00")

        # Even though we pass BUY intents, flatten should override
        intent = _make_intent(side="BUY", qty=10)
        decisions = gate.evaluate([intent], portfolio, state)

        # Should get 2 SELL decisions (one per position), not 1 BUY
        assert len(decisions) == 2
        assert all(d.action == "ALLOW" for d in decisions)
        assert all(d.original_intent.side == "SELL" for d in decisions)
        assert {d.original_intent.contract for d in decisions} == {
            "O:SPY250106C00595000",
            "O:SPY250106P00580000",
        }
        assert {d.original_intent.qty for d in decisions} == {5, 3}
        assert all("eod_flatten" in d.risk_notes for d in decisions)


class TestNoFlattenWhenDisabled:
    """test_no_flatten_when_disabled — flatten_before_close=False → no auto SELL."""

    def test_no_flatten_when_disabled(self):
        config = RiskConfig(
            flatten_before_close=False,
            flatten_minutes_before_close=5,
        )
        gate = _make_gate(risk_config=config)

        pos = _make_position(contract="O:SPY250106C00595000", qty=5)
        portfolio = _make_portfolio(positions=[pos])

        # Same time (3 min before close) but flatten disabled
        state = MockTradingState(current_ts="2025-01-06T15:57:00")
        intent = _make_intent(side="BUY", qty=2, limit_price=2.50)
        decisions = gate.evaluate([intent], portfolio, state)

        # Should evaluate the BUY intent normally, NOT generate flatten sells
        assert len(decisions) == 1
        assert decisions[0].original_intent.side == "BUY"


class TestAdjustQty:
    """test_adjust_qty — partial allow when exposure limit partially allows."""

    def test_adjust_qty(self):
        # Set a specific exposure limit
        config = RiskConfig(max_exposure_pct=0.10)
        gate = _make_gate(risk_config=config)

        # Existing position: 5 contracts at $2.50 mark → value = 5*2.50*100 = $1,250
        pos = _make_position(contract="O:SPY250106C00595000", qty=5, mark_price=2.50)
        portfolio = _make_portfolio(equity=100_000, positions=[pos])

        # Try to buy 100 more at $2.50 → would add $25,000 → total $26,250 → 26.25% > 10%
        # But max allowed value = 10% * 100,000 = $10,000. Remaining = $10,000 - $1,250 = $8,750
        # Max qty = 8,750 / (2.50 * 100) = 35
        intent = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=100, limit_price=2.50
        )
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "ADJUST"
        assert decisions[0].adjusted_qty == 35
        assert decisions[0].adjusted_qty < intent.qty


class TestOrderFrequencyLimit:
    """test_order_frequency_limit — exceed max_orders_per_day → REJECT excess."""

    def test_order_frequency_limit(self):
        config = RiskConfig(max_orders_per_day=3)
        gate = _make_gate(risk_config=config)

        portfolio = _make_portfolio()
        state = MockTradingState()

        # Submit 5 intents — first 3 should be allowed, last 2 rejected
        intents = [_make_intent(qty=1, limit_price=2.0) for _ in range(5)]
        decisions = gate.evaluate(intents, portfolio, state)

        assert len(decisions) == 5
        allowed = [d for d in decisions if d.action == "ALLOW"]
        rejected = [d for d in decisions if d.action == "REJECT"]
        assert len(allowed) == 3
        assert len(rejected) == 2
        assert all("frequency" in d.reason.lower() for d in rejected)


class TestBatchIntentsCumulativeExposure:
    """test_batch_intents_cumulative_exposure — two BUY 4 with max_position=5 → first ALLOW, second REJECT."""

    def test_batch_intents_cumulative_exposure(self):
        config = RiskConfig(max_position_per_symbol=5)
        gate = _make_gate(risk_config=config)

        # Two BUY 4 intents for the same symbol — total would be 8, exceeds 5
        intent1 = _make_intent(qty=4, limit_price=2.0)
        intent2 = _make_intent(qty=4, limit_price=2.0)
        portfolio = _make_portfolio()
        state = MockTradingState()

        decisions = gate.evaluate([intent1, intent2], portfolio, state)

        assert len(decisions) == 2
        # First BUY 4 fits (0+4=4 <= 5)
        assert decisions[0].action == "ALLOW"
        # Second BUY 4 would bring total to 8 (4+4=8 > 5) — must be REJECT
        assert decisions[1].action == "REJECT"
        assert "max position" in decisions[1].reason.lower()


class TestFlattenShortPositionUsesBuy:
    """test_flatten_short_position_uses_buy — short position → flatten generates BUY intent."""

    def test_flatten_short_position_uses_buy(self):
        config = RiskConfig(
            flatten_before_close=True,
            flatten_minutes_before_close=5,
        )
        gate = _make_gate(risk_config=config)

        # One long position and one short position
        long_pos = _make_position(contract="O:SPY250106C00595000", side="LONG", qty=3)
        short_pos = _make_position(contract="O:SPY250106P00580000", side="SHORT", qty=-2)
        portfolio = _make_portfolio(positions=[long_pos, short_pos])

        # 3 minutes before close (15:57 ET)
        state = MockTradingState(current_ts="2025-01-06T15:57:00")

        intent = _make_intent(side="BUY", qty=10)
        decisions = gate.evaluate([intent], portfolio, state)

        # Should get 2 decisions (one SELL for long, one BUY for short)
        assert len(decisions) == 2
        assert all(d.action == "ALLOW" for d in decisions)
        assert all("eod_flatten" in d.risk_notes for d in decisions)

        # Find the decision for the long position — should be SELL
        long_decision = next(
            d for d in decisions if d.original_intent.contract == "O:SPY250106C00595000"
        )
        assert long_decision.original_intent.side == "SELL"
        assert long_decision.original_intent.qty == 3

        # Find the decision for the short position — should be BUY
        short_decision = next(
            d for d in decisions if d.original_intent.contract == "O:SPY250106P00580000"
        )
        assert short_decision.original_intent.side == "BUY"
        assert short_decision.original_intent.qty == 2


class TestOrdersTodayResetsOnNewDay:
    """test_orders_today_resets_on_new_day — verify the renamed _orders_today field resets."""

    def test_orders_today_resets_on_new_day(self):
        config = RiskConfig(max_orders_per_day=2)
        gate = _make_gate(risk_config=config)

        portfolio = _make_portfolio()
        state = MockTradingState()

        # Submit 2 intents — both should be allowed (hits the limit)
        intents = [_make_intent(qty=1, limit_price=2.0) for _ in range(2)]
        decisions = gate.evaluate(intents, portfolio, state)
        assert all(d.action == "ALLOW" for d in decisions)

        # Now a 3rd intent should be rejected (limit reached)
        extra = [_make_intent(qty=1, limit_price=2.0)]
        decisions = gate.evaluate(extra, portfolio, state)
        assert decisions[0].action == "REJECT"
        assert "frequency" in decisions[0].reason.lower()

        # Reset for new day
        gate.on_new_day()

        # After reset, the counter is back to 0 — should allow again
        decisions = gate.evaluate(extra, portfolio, state)
        assert decisions[0].action == "ALLOW"

        # Verify the internal field name is _orders_today (not _orders_this_interval)
        assert hasattr(gate, "_orders_today")
        assert not hasattr(gate, "_orders_this_interval")


class TestSellExposureCalculation:
    """Issue 2: SELL to open a short should increase (not decrease) exposure."""

    def test_sell_to_open_short_increases_exposure(self):
        """A SELL with no existing position opens a short, increasing exposure."""
        # allow_naked_short_options=True so the naked-short gate is bypassed; this
        # test specifically validates exposure calculation for a short open.
        config = RiskConfig(max_exposure_pct=0.50, allow_naked_short_options=True)
        gate = _make_gate(risk_config=config)

        # No positions — selling 10 contracts at $5 mark → $5,000 notional
        # On $100K equity → 5% exposure
        intent = _make_intent(
            contract="O:SPY250106P00580000",
            side="SELL",
            qty=10,
            limit_price=5.0,
        )
        portfolio = _make_portfolio(equity=100_000, positions=[])
        state = MockTradingState(
            market_event=MarketEvent(
                event_ts="2025-01-06T10:30:00",
                bars=[
                    BarData(
                        symbol="O:SPY250106P00580000",
                        open=5.0, high=5.5, low=4.5, close=5.0,
                        volume=500, window_start="2025-01-06T10:30:00",
                        instrument_type="OPTION",
                        stale=False, stale_minutes=0, tradeable=True,
                    ),
                ],
                bar_history={},
            )
        )

        decisions = gate.evaluate([intent], portfolio, state)
        assert len(decisions) == 1
        # Exposure should be > 0 (not clipped to 0 as in the old code)
        assert decisions[0].exposure_after > 0

    def test_sell_to_close_long_reduces_exposure(self):
        """A SELL that closes an existing long position reduces exposure."""
        config = RiskConfig(max_exposure_pct=0.50)
        gate = _make_gate(risk_config=config)

        # Existing long: 10 contracts at $5 → $5,000 notional → 5% exposure
        pos = _make_position(
            contract="O:SPY250106C00595000",
            side="LONG", qty=10, mark_price=5.0,
        )
        # SELL 10 to close — exposure should drop to 0
        intent = _make_intent(
            contract="O:SPY250106C00595000",
            side="SELL",
            qty=10,
            limit_price=5.0,
        )
        portfolio = _make_portfolio(equity=100_000, positions=[pos])
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)
        assert len(decisions) == 1
        assert decisions[0].action == "ALLOW"
        assert decisions[0].exposure_after == pytest.approx(0.0, abs=0.001)

    def test_sell_to_open_short_rejected_when_exceeds_exposure(self):
        """SELL to open short beyond max_exposure_pct is rejected."""
        # allow_naked_short_options=True so the naked-short gate is bypassed; this
        # test specifically validates exposure rejection for a large short open.
        config = RiskConfig(max_exposure_pct=0.01, allow_naked_short_options=True)  # very low: 1%
        gate = _make_gate(risk_config=config)

        # SELL 10 contracts at $5 → $5,000 notional → 5% > 1% limit
        intent = _make_intent(
            contract="O:SPY250106P00580000",
            side="SELL",
            qty=10,
            limit_price=5.0,
        )
        portfolio = _make_portfolio(equity=100_000, positions=[])
        state = MockTradingState(
            market_event=MarketEvent(
                event_ts="2025-01-06T10:30:00",
                bars=[
                    BarData(
                        symbol="O:SPY250106P00580000",
                        open=5.0, high=5.5, low=4.5, close=5.0,
                        volume=500, window_start="2025-01-06T10:30:00",
                        instrument_type="OPTION",
                        stale=False, stale_minutes=0, tradeable=True,
                    ),
                ],
                bar_history={},
            )
        )

        decisions = gate.evaluate([intent], portfolio, state)
        assert len(decisions) == 1
        # Should be REJECT or ADJUST since exposure exceeds limit
        assert decisions[0].action in ("REJECT", "ADJUST")


class TestDeRiskingSellAllowedWhenOverExposure:
    """Bug fix: sells that reduce exposure should be allowed even when
    the portfolio is already above the exposure limit."""

    def test_sell_half_of_long_allowed_when_over_limit(self):
        """Over exposure limit (200%), selling half reduces to 100% — should ALLOW."""
        config = RiskConfig(max_exposure_pct=0.50)  # 50% limit
        gate = _make_gate(risk_config=config)

        # Existing long: 20 contracts at $10 → $20,000 notional → 200% exposure on $10K
        pos = _make_position(
            contract="O:SPY250106C00595000",
            side="LONG", qty=20, mark_price=10.0,
        )
        # SELL 10 to reduce — exposure drops from 200% to 100%
        intent = _make_intent(
            contract="O:SPY250106C00595000",
            side="SELL", qty=10, limit_price=10.0,
        )
        portfolio = _make_portfolio(equity=10_000, positions=[pos])
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)
        assert len(decisions) == 1
        # Even though 100% > 50% limit, this is a de-risk → ALLOW
        assert decisions[0].action == "ALLOW"
        assert "exposure_over_limit_but_reducing" in decisions[0].risk_notes

    def test_buy_still_rejected_when_over_limit(self):
        """Over exposure limit, buying more should still be REJECT."""
        config = RiskConfig(max_exposure_pct=0.50)  # 50% limit
        gate = _make_gate(risk_config=config)

        pos = _make_position(
            contract="O:SPY250106C00595000",
            side="LONG", qty=20, mark_price=10.0,
        )
        # BUY more — increases exposure
        intent = _make_intent(
            contract="O:SPY250106C00595000",
            side="BUY", qty=5, limit_price=10.0,
        )
        portfolio = _make_portfolio(equity=10_000, positions=[pos])
        state = MockTradingState()

        decisions = gate.evaluate([intent], portfolio, state)
        assert len(decisions) == 1
        assert decisions[0].action in ("REJECT", "ADJUST")


# ---------------------------------------------------------------------------
# Stock vs Option handling tests
# ---------------------------------------------------------------------------


class TestIsOption:
    """C1: _is_option correctly distinguishes OPRA option contracts from stocks."""

    def test_opra_contract_is_option(self):
        assert RiskGateAgent._is_option("O:TSM250620C00170000") is True

    def test_plain_ticker_is_not_option(self):
        assert RiskGateAgent._is_option("TSM") is False

    def test_multi_char_ticker_is_not_option(self):
        assert RiskGateAgent._is_option("BRKB") is False

    def test_empty_string_is_not_option(self):
        assert RiskGateAgent._is_option("") is False


class TestStockPositionExposureUses1xMultiplier:
    """C2: Stock positions use a 1x multiplier (not 100x) in exposure calc."""

    def test_stock_position_exposure_uses_1x_multiplier(self):
        gate = _make_gate()

        # Stock position: contract="TSM", qty=100, mark_price=170.0, side="LONG"
        pos = _make_position(
            contract="TSM",
            side="LONG",
            qty=100,
            mark_price=170.0,
        )
        portfolio = _make_portfolio(equity=100_000, positions=[pos])

        # Expected: 100 * 170 * 1 / 100_000 = 0.17 (17%)
        exposure = gate._calc_current_exposure(portfolio)
        assert exposure == pytest.approx(0.17, abs=0.001)
        # Must NOT be 17.0 (which would indicate 100x multiplier)
        assert exposure < 1.0


class TestOptionPositionExposureUses100xMultiplier:
    """C3: Option positions use a 100x multiplier in exposure calc."""

    def test_option_position_exposure_uses_100x_multiplier(self):
        gate = _make_gate()

        # Option position: contract="O:SPY250106C00595000", qty=5, mark_price=2.50
        pos = _make_position(
            contract="O:SPY250106C00595000",
            side="LONG",
            qty=5,
            mark_price=2.50,
        )
        portfolio = _make_portfolio(equity=100_000, positions=[pos])

        # Expected: 5 * 2.50 * 100 / 100_000 = 0.0125 (1.25%)
        exposure = gate._calc_current_exposure(portfolio)
        assert exposure == pytest.approx(0.0125, abs=0.0001)


class TestMixedStockAndOptionExposure:
    """C4: Portfolio with both stock and option positions sums exposure correctly."""

    def test_mixed_stock_and_option_exposure(self):
        gate = _make_gate()

        stock_pos = _make_position(
            contract="TSM",
            side="LONG",
            qty=100,
            mark_price=170.0,
        )
        option_pos = _make_position(
            contract="O:SPY250106C00595000",
            side="LONG",
            qty=5,
            mark_price=2.50,
        )
        portfolio = _make_portfolio(equity=100_000, positions=[stock_pos, option_pos])

        # Stock: 100 * 170 * 1 = 17_000
        # Option: 5 * 2.50 * 100 = 1_250
        # Total: 18_250 / 100_000 = 0.1825
        exposure = gate._calc_current_exposure(portfolio)
        assert exposure == pytest.approx(0.1825, abs=0.0001)


class TestIsClosingTradeForStock:
    """C5: _is_closing_trade works for stock positions (non-OPRA contracts)."""

    def test_sell_closing_long_stock(self):
        gate = _make_gate()

        pos = _make_position(contract="TSM", side="LONG", qty=100, mark_price=170.0)
        portfolio = _make_portfolio(positions=[pos])

        # SELL intent for "TSM" should be closing
        sell_intent = _make_intent(contract="TSM", symbol="TSM", side="SELL", qty=100)
        assert gate._is_closing_trade(sell_intent, portfolio) is True

    def test_sell_different_stock_is_not_closing(self):
        gate = _make_gate()

        pos = _make_position(contract="TSM", side="LONG", qty=100, mark_price=170.0)
        portfolio = _make_portfolio(positions=[pos])

        # SELL intent for "AAPL" should NOT be closing (no AAPL position)
        sell_intent = _make_intent(contract="AAPL", symbol="AAPL", side="SELL", qty=50)
        assert gate._is_closing_trade(sell_intent, portfolio) is False


class TestExtractUnderlyingStockContract:
    """C6: _extract_underlying returns the ticker for both stocks and OPRA contracts."""

    def test_plain_stock_returns_itself(self):
        gate = _make_gate()
        assert gate._extract_underlying("TSM") == "TSM"

    def test_opra_option_returns_underlying(self):
        gate = _make_gate()
        assert gate._extract_underlying("O:TSM250620C00170000") == "TSM"

    def test_opra_multi_char_ticker(self):
        gate = _make_gate()
        assert gate._extract_underlying("O:BRKB250620C00400000") == "BRKB"


class TestUnknownContractRejection:
    """Contracts with no market data (untracked) must be rejected by the risk gate."""

    def test_untracked_contract_rejected(self):
        """An intent for a contract not in market_event.bars must be REJECT."""
        data_policy = DataPolicyConfig(max_stale_minutes=5)
        gate = _make_gate(data_policy=data_policy)

        # State has a bar for SPY but NOT for AAPL option
        spy_bar = BarData(
            symbol="O:SPY250106C00595000",
            open=2.0, high=2.5, low=1.8, close=2.3,
            volume=500, window_start="2025-01-06T10:30:00",
            instrument_type="OPTION",
            stale=False, stale_minutes=0, tradeable=True,
        )
        market_event = MarketEvent(
            event_ts="2025-01-06T10:30:00",
            bars=[spy_bar],
            bar_history={},
        )
        state = MockTradingState(market_event=market_event)
        portfolio = _make_portfolio()

        # Intent is for a fake/unknown AAPL option contract — NOT in market bars
        aapl_intent = _make_intent(
            contract="O:AAPL250106C00200000",
            symbol="AAPL",
            side="BUY",
            qty=2,
        )

        decisions = gate.evaluate([aapl_intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "REJECT"
        assert "no market data" in decisions[0].reason.lower() or "stale" in decisions[0].reason.lower()


# ---------------------------------------------------------------------------
# Naked short option tests
# ---------------------------------------------------------------------------


def _make_put_market_event(contract: str = "O:SPY250106P00580000") -> MarketEvent:
    """MarketEvent with a fresh bar for a put contract."""
    return MarketEvent(
        event_ts="2025-01-06T10:30:00",
        bars=[
            BarData(
                symbol=contract,
                open=3.0, high=3.5, low=2.8, close=3.1,
                volume=200, window_start="2025-01-06T10:30:00",
                instrument_type="OPTION",
                stale=False, stale_minutes=0, tradeable=True,
            ),
        ],
        bar_history={},
    )


def _make_call_market_event(contract: str = "O:SPY250106C00595000") -> MarketEvent:
    """MarketEvent with a fresh bar for a call contract."""
    return MarketEvent(
        event_ts="2025-01-06T10:30:00",
        bars=[
            BarData(
                symbol=contract,
                open=2.0, high=2.5, low=1.8, close=2.3,
                volume=500, window_start="2025-01-06T10:30:00",
                instrument_type="OPTION",
                stale=False, stale_minutes=0, tradeable=True,
            ),
        ],
        bar_history={},
    )


class TestUndefinedRiskShorts:
    """Structural rejection of naked short options."""

    def test_naked_short_put_rejected(self):
        """SELL put with no covering position → REJECT."""
        gate = _make_gate()  # allow_naked_short_options=False by default
        intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106P00580000",
            side="SELL",
            qty=5,
            order_type="MARKET",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=_make_put_market_event())

        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "REJECT"
        reason = decisions[0].reason.lower()
        assert "naked" in reason or "undefined" in reason or "undefined-risk" in reason
        assert "naked_short_option" in decisions[0].risk_notes

    def test_naked_short_call_rejected(self):
        """SELL call with no covering position → REJECT."""
        gate = _make_gate()
        intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106C00595000",
            side="SELL",
            qty=5,
            order_type="MARKET",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=_make_call_market_event())

        decisions = gate.evaluate([intent], portfolio, state)

        assert decisions[0].action == "REJECT"
        assert "naked_short_option" in decisions[0].risk_notes

    def test_sell_to_close_long_allowed(self):
        """SELL to close existing LONG option → ALLOW (closing trade)."""
        gate = _make_gate()
        contract = "O:SPY250106C00595000"
        pos = _make_position(contract=contract, side="LONG", qty=5)
        intent = TradeIntent(
            symbol="SPY",
            contract=contract,
            side="SELL",
            qty=5,
            order_type="MARKET",
        )
        portfolio = _make_portfolio(positions=[pos])
        state = MockTradingState(market_event=_make_call_market_event(contract))

        decisions = gate.evaluate([intent], portfolio, state)

        assert decisions[0].action == "ALLOW"

    def test_covered_call_allowed(self):
        """SELL call covered by LONG stock → ALLOW (not rejected as naked short)."""
        # Use max_exposure_pct=2.0 so the stock position doesn't trip the exposure check.
        # We only want to verify the naked-short gate passes for a covered call.
        config = RiskConfig(max_exposure_pct=2.0)
        gate = _make_gate(risk_config=config)
        # SPY stock position: 1000 shares covers 10 call contracts (10 * 100 = 1000)
        stock_pos = _make_position(
            contract="SPY", side="LONG", qty=1000, mark_price=595.0
        )
        intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106C00595000",
            side="SELL",
            qty=5,  # 5 * 100 = 500 shares needed; 1000 >= 500 → covered
            order_type="MARKET",
        )
        portfolio = _make_portfolio(
            equity=600_000, positions=[stock_pos]
        )
        state = MockTradingState(market_event=_make_call_market_event())

        decisions = gate.evaluate([intent], portfolio, state)

        # Covered call: not naked, should not be rejected by the naked short gate
        assert decisions[0].action == "ALLOW"
        assert "naked_short_option" not in decisions[0].risk_notes

    def test_covered_call_insufficient_stock_rejected(self):
        """SELL call where stock holdings < qty*100 → REJECT as naked short."""
        gate = _make_gate()
        # Only 100 shares, but selling 5 contracts needs 500 shares
        stock_pos = _make_position(
            contract="SPY", side="LONG", qty=100, mark_price=595.0
        )
        intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106C00595000",
            side="SELL",
            qty=5,
            order_type="MARKET",
        )
        portfolio = _make_portfolio(equity=600_000, positions=[stock_pos])
        state = MockTradingState(market_event=_make_call_market_event())

        decisions = gate.evaluate([intent], portfolio, state)

        assert decisions[0].action == "REJECT"
        assert "naked_short_option" in decisions[0].risk_notes

    def test_defined_risk_spread_allowed(self):
        """SELL option in a spread_group with a BUY leg → ALLOW (defined-risk spread)."""
        gate = _make_gate()
        contract_sell = "O:SPY250106P00580000"
        contract_buy = "O:SPY250106P00570000"
        market_event = MarketEvent(
            event_ts="2025-01-06T10:30:00",
            bars=[
                BarData(
                    symbol=contract_sell,
                    open=3.0, high=3.5, low=2.8, close=3.1,
                    volume=200, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
                BarData(
                    symbol=contract_buy,
                    open=1.5, high=2.0, low=1.2, close=1.6,
                    volume=150, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
            ],
            bar_history={},
        )
        sell_intent = TradeIntent(
            symbol="SPY",
            contract=contract_sell,
            side="SELL",
            qty=5,
            order_type="MARKET",
            spread_group="bear_put_spread_1",
        )
        buy_intent = TradeIntent(
            symbol="SPY",
            contract=contract_buy,
            side="BUY",
            qty=5,
            order_type="MARKET",
            spread_group="bear_put_spread_1",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=market_event)

        decisions = gate.evaluate([sell_intent, buy_intent], portfolio, state)

        # Both legs should be allowed (spread defines risk)
        sell_decision = next(
            d for d in decisions if d.original_intent.side == "SELL"
        )
        assert sell_decision.action == "ALLOW"

    def test_cross_type_spread_rejected(self):
        """SELL call + BUY put in same spread_group is NOT defined-risk."""
        gate = _make_gate()
        contract_sell = "O:SPY250106C00600000"  # call
        contract_buy = "O:SPY250106P00580000"   # put — different type!
        market_event = MarketEvent(
            event_ts="2025-01-06T10:30:00",
            bars=[
                BarData(
                    symbol=contract_sell,
                    open=3.0, high=3.5, low=2.8, close=3.1,
                    volume=200, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
                BarData(
                    symbol=contract_buy,
                    open=1.5, high=2.0, low=1.2, close=1.6,
                    volume=150, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
            ],
            bar_history={},
        )
        sell_intent = TradeIntent(
            symbol="SPY",
            contract=contract_sell,
            side="SELL",
            qty=5,
            order_type="MARKET",
            spread_group="fake_spread",
        )
        buy_intent = TradeIntent(
            symbol="SPY",
            contract=contract_buy,
            side="BUY",
            qty=5,
            order_type="MARKET",
            spread_group="fake_spread",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=market_event)

        decisions = gate.evaluate([sell_intent, buy_intent], portfolio, state)
        sell_decision = next(
            d for d in decisions if d.original_intent.side == "SELL"
        )
        # SELL call + BUY put is NOT a vertical spread — should be REJECTED
        assert sell_decision.action == "REJECT"
        assert "naked_short_option" in sell_decision.risk_notes

    def test_different_expiry_spread_rejected(self):
        """Spread legs with different expiry dates are NOT defined-risk."""
        gate = _make_gate()
        contract_sell = "O:SPY250106P00580000"  # Jan 6 expiry
        contract_buy = "O:SPY250113P00570000"   # Jan 13 expiry — different!
        market_event = MarketEvent(
            event_ts="2025-01-06T10:30:00",
            bars=[
                BarData(
                    symbol=contract_sell,
                    open=3.0, high=3.5, low=2.8, close=3.1,
                    volume=200, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
                BarData(
                    symbol=contract_buy,
                    open=1.5, high=2.0, low=1.2, close=1.6,
                    volume=150, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
            ],
            bar_history={},
        )
        sell_intent = TradeIntent(
            symbol="SPY",
            contract=contract_sell,
            side="SELL",
            qty=5,
            order_type="MARKET",
            spread_group="calendar_spread",
        )
        buy_intent = TradeIntent(
            symbol="SPY",
            contract=contract_buy,
            side="BUY",
            qty=5,
            order_type="MARKET",
            spread_group="calendar_spread",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=market_event)

        decisions = gate.evaluate([sell_intent, buy_intent], portfolio, state)
        sell_decision = next(
            d for d in decisions if d.original_intent.side == "SELL"
        )
        assert sell_decision.action == "REJECT"
        assert "naked_short_option" in sell_decision.risk_notes

    def test_mismatched_qty_spread_rejected(self):
        """Spread legs with different quantities are NOT defined-risk."""
        gate = _make_gate()
        contract_sell = "O:SPY250106P00580000"
        contract_buy = "O:SPY250106P00570000"
        market_event = MarketEvent(
            event_ts="2025-01-06T10:30:00",
            bars=[
                BarData(
                    symbol=contract_sell,
                    open=3.0, high=3.5, low=2.8, close=3.1,
                    volume=200, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
                BarData(
                    symbol=contract_buy,
                    open=1.5, high=2.0, low=1.2, close=1.6,
                    volume=150, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
            ],
            bar_history={},
        )
        sell_intent = TradeIntent(
            symbol="SPY",
            contract=contract_sell,
            side="SELL",
            qty=10,  # different from buy
            order_type="MARKET",
            spread_group="ratio_spread",
        )
        buy_intent = TradeIntent(
            symbol="SPY",
            contract=contract_buy,
            side="BUY",
            qty=5,  # different from sell
            order_type="MARKET",
            spread_group="ratio_spread",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=market_event)

        decisions = gate.evaluate([sell_intent, buy_intent], portfolio, state)
        sell_decision = next(
            d for d in decisions if d.original_intent.side == "SELL"
        )
        assert sell_decision.action == "REJECT"
        assert "naked_short_option" in sell_decision.risk_notes

    def test_naked_short_allowed_when_flag_enabled(self):
        """When allow_naked_short_options=True, naked SELL is not rejected by this check."""
        config = RiskConfig(allow_naked_short_options=True)
        gate = _make_gate(risk_config=config)
        intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106P00580000",
            side="SELL",
            qty=5,
            order_type="MARKET",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=_make_put_market_event())

        decisions = gate.evaluate([intent], portfolio, state)

        # With the flag enabled, the naked short check is bypassed.
        # It may still be rejected by other checks (exposure etc.) but NOT for nakedness.
        assert "naked_short_option" not in decisions[0].risk_notes

    def test_buy_option_not_affected(self):
        """BUY option is never rejected by the naked short check."""
        gate = _make_gate()
        intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106P00580000",
            side="BUY",
            qty=5,
            order_type="MARKET",
        )
        portfolio = _make_portfolio(positions=[])
        state = MockTradingState(market_event=_make_put_market_event())

        decisions = gate.evaluate([intent], portfolio, state)

        assert "naked_short_option" not in decisions[0].risk_notes
        assert decisions[0].action == "ALLOW"


class TestSpreadExposureFixup:
    """Spread SELL leg should not double-count exposure (bug fix)."""

    def _make_spread_market_event(self) -> MarketEvent:
        return MarketEvent(
            event_ts="2025-01-06T10:30:00",
            bars=[
                BarData(
                    symbol="O:SPY250106C00590000",  # buy (long) leg
                    open=3.0, high=3.5, low=2.8, close=3.0,
                    volume=200, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
                BarData(
                    symbol="O:SPY250106C00600000",  # sell (short) leg
                    open=1.5, high=2.0, low=1.2, close=1.5,
                    volume=150, window_start="2025-01-06T10:30:00",
                    instrument_type="OPTION",
                    stale=False, stale_minutes=0, tradeable=True,
                ),
            ],
            bar_history={},
        )

    def test_spread_short_leg_excluded_from_exposure(self):
        """SELL leg of a defined-risk spread does not add to exposure calc.

        Without the fix: both BUY (@3.0) and SELL (@1.5) legs each add their
        absolute notional → (3.0 + 1.5) * 5 * 100 = $2,250 on $100k = 2.25%.
        With the fix: only BUY leg counts → 3.0 * 5 * 100 = $1,500 = 1.5%.
        """
        # Set max_exposure_pct to 2% — would reject the spread before the fix
        gate = _make_gate(risk_config=RiskConfig(max_exposure_pct=0.02))
        buy_intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106C00590000",
            side="BUY",
            qty=5,
            order_type="MARKET",
            spread_group="bull_call_1",
        )
        sell_intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106C00600000",
            side="SELL",
            qty=5,
            order_type="MARKET",
            spread_group="bull_call_1",
        )
        portfolio = _make_portfolio(equity=100_000, positions=[])
        state = MockTradingState(market_event=self._make_spread_market_event())

        decisions = gate.evaluate([buy_intent, sell_intent], portfolio, state)

        sell_dec = next(d for d in decisions if d.original_intent.side == "SELL")
        buy_dec = next(d for d in decisions if d.original_intent.side == "BUY")
        # With fix: SELL leg not double-counted — both legs should pass 2% limit
        assert buy_dec.action == "ALLOW", f"BUY leg rejected: {buy_dec.reason}"
        assert sell_dec.action == "ALLOW", f"SELL leg rejected: {sell_dec.reason}"

    def test_spread_counts_as_one_order(self):
        """A 2-leg spread should consume only 1 slot from max_orders_per_day."""
        gate = _make_gate(risk_config=RiskConfig(max_orders_per_day=1))
        buy_intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106C00590000",
            side="BUY",
            qty=1,
            order_type="MARKET",
            spread_group="bull_call_1",
        )
        sell_intent = TradeIntent(
            symbol="SPY",
            contract="O:SPY250106C00600000",
            side="SELL",
            qty=1,
            order_type="MARKET",
            spread_group="bull_call_1",
        )
        portfolio = _make_portfolio(equity=100_000, positions=[])
        state = MockTradingState(market_event=self._make_spread_market_event())

        decisions = gate.evaluate([buy_intent, sell_intent], portfolio, state)

        # Both legs should be allowed — the spread uses only 1 of max_orders_per_day=1
        assert all(d.action == "ALLOW" for d in decisions), \
            [(d.original_intent.side, d.action, d.reason) for d in decisions]


class TestIsCall:
    """Unit tests for the _is_call static method."""

    def test_call_contract_returns_true(self):
        assert RiskGateAgent._is_call("O:SPY250106C00595000") is True

    def test_put_contract_returns_false(self):
        assert RiskGateAgent._is_call("O:SPY250106P00580000") is False

    def test_stock_contract_returns_false(self):
        assert RiskGateAgent._is_call("SPY") is False

    def test_short_contract_returns_false(self):
        assert RiskGateAgent._is_call("O:ABC") is False


class TestIsCoveredCall:
    """Unit tests for the _is_covered_call helper."""

    def test_covered_call_exact_coverage(self):
        """Exactly enough stock (qty * 100 shares) → covered."""
        gate = _make_gate()
        stock_pos = _make_position(contract="SPY", side="LONG", qty=500, mark_price=595.0)
        portfolio = _make_portfolio(positions=[stock_pos])
        intent = TradeIntent(
            symbol="SPY", contract="O:SPY250106C00595000",
            side="SELL", qty=5, order_type="MARKET",
        )
        assert gate._is_covered_call(intent, portfolio) is True

    def test_covered_call_insufficient_stock(self):
        """Less stock than needed → not covered."""
        gate = _make_gate()
        stock_pos = _make_position(contract="SPY", side="LONG", qty=400, mark_price=595.0)
        portfolio = _make_portfolio(positions=[stock_pos])
        intent = TradeIntent(
            symbol="SPY", contract="O:SPY250106C00595000",
            side="SELL", qty=5, order_type="MARKET",
        )
        assert gate._is_covered_call(intent, portfolio) is False

    def test_put_is_not_covered_call(self):
        """A put is never a covered call."""
        gate = _make_gate()
        stock_pos = _make_position(contract="SPY", side="LONG", qty=10000, mark_price=595.0)
        portfolio = _make_portfolio(positions=[stock_pos])
        intent = TradeIntent(
            symbol="SPY", contract="O:SPY250106P00580000",
            side="SELL", qty=5, order_type="MARKET",
        )
        assert gate._is_covered_call(intent, portfolio) is False

    def test_short_stock_not_covered(self):
        """SHORT stock position does not cover a call."""
        gate = _make_gate()
        short_stock_pos = _make_position(contract="SPY", side="SHORT", qty=-500, mark_price=595.0)
        portfolio = _make_portfolio(positions=[short_stock_pos])
        intent = TradeIntent(
            symbol="SPY", contract="O:SPY250106C00595000",
            side="SELL", qty=5, order_type="MARKET",
        )
        assert gate._is_covered_call(intent, portfolio) is False
