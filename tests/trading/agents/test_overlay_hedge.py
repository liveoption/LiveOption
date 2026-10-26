"""Tests for OverlayHedgeStrategy (overlay_hedge) — multi-agent LLM pipeline version."""

import pytest
from datetime import date, timedelta

from trading.agents.strategy.overlay import (
    OverlayHedgeStrategy,
    _parse_opra_right,
    _parse_opra_expiry,
)
from trading.core.registry import STRATEGY_REGISTRY
from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
    TradeIntent,
)
from trading.core.state import TradingState
from trading.core.config import ExperimentConfig, RunConfig, StrategyConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_portfolio(positions: list | None = None, cash: float = 197_635.0) -> PortfolioSnapshot:
    equity = cash + sum(p.qty * p.mark_price for p in (positions or []))
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        positions=positions or [],
    )


def _make_state(current_ts: str = "2025-01-06T00:00:00+00:00") -> TradingState:
    config = RunConfig(
        experiment=ExperimentConfig(
            run_id="test_overlay_hedge", start="2025-01-02", end="2025-12-31",
            universe=["AMD"],
        ),
        strategy=StrategyConfig(name="overlay_hedge", params={}),
    )
    state = TradingState(config=config, run_id="test", episode_id="ep0")
    state.current_ts = current_ts
    return state


def _make_put_position(contract: str, qty: int, mark: float, delta: float | None = None) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side="LONG" if qty > 0 else "SHORT",
        qty=qty,
        avg_entry=mark + 0.5,
        mark_price=mark,
        unrealized_pnl=0.0,
        dte=10,
        stale=False,
        stale_minutes=0,
        delta=delta,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_registered_in_global_registry(self):
        assert "overlay_hedge" in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY["overlay_hedge"] is OverlayHedgeStrategy

    def test_max_limits(self):
        assert OverlayHedgeStrategy.MAX_LONG_PUTS == 100
        assert OverlayHedgeStrategy.MAX_PER_TRADE == 50


# ---------------------------------------------------------------------------
# Short put risk check
# ---------------------------------------------------------------------------

class TestShortPutRisk:
    def _make_strategy(self) -> OverlayHedgeStrategy:
        s = object.__new__(OverlayHedgeStrategy)
        s._underlying = "AMD"
        return s

    def test_closes_deep_itm_short_put(self):
        strat = self._make_strategy()
        port = _make_portfolio(positions=[
            _make_put_position("O:AMD251121P00220000", qty=-100, mark=14.28, delta=-0.92),
        ])
        intents = strat._check_short_put_risk(port)
        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].qty == 100
        assert intents[0].contract == "O:AMD251121P00220000"

    def test_no_close_when_delta_below_threshold(self):
        strat = self._make_strategy()
        port = _make_portfolio(positions=[
            _make_put_position("O:AMD251121P00220000", qty=-100, mark=2.5, delta=-0.35),
        ])
        intents = strat._check_short_put_risk(port)
        assert intents == []

    def test_no_close_for_long_puts(self):
        strat = self._make_strategy()
        port = _make_portfolio(positions=[
            _make_put_position("O:AMD251121P00260000", qty=150, mark=54.43, delta=-0.96),
        ])
        intents = strat._check_short_put_risk(port)
        assert intents == []

    def test_no_close_for_calls(self):
        strat = self._make_strategy()
        port = _make_portfolio(positions=[
            _make_put_position("O:AMD251121C00200000", qty=-50, mark=10.0, delta=0.85),
        ])
        intents = strat._check_short_put_risk(port)
        assert intents == []


# ---------------------------------------------------------------------------
# Expiry close: 2-day early close for short ITM
# ---------------------------------------------------------------------------

class TestExpiryCloseOrders:
    def _make_strategy(self) -> OverlayHedgeStrategy:
        s = object.__new__(OverlayHedgeStrategy)
        s._underlying = "AMD"
        s._stock_qty = 10_000
        return s

    def _stock_pos(self, price: float = 203.0) -> PositionInfo:
        return PositionInfo(
            contract="AMD", side="LONG", qty=10_000,
            avg_entry=200.0, mark_price=price, unrealized_pnl=0.0,
            dte=0, stale=False, stale_minutes=0,
        )

    def test_closes_short_itm_put_on_expiry_day(self):
        """Short ITM put on actual expiry day must be bought to close (backstop)."""
        strat = self._make_strategy()
        today = date(2025, 11, 21).isoformat()  # expiry day itself
        port = _make_portfolio(positions=[
            self._stock_pos(203.0),
            _make_put_position("O:AMD251121P00220000", qty=-100, mark=14.28, delta=-0.92),
        ])
        intents = strat._generate_expiry_close_orders(today, port, "overlay_hedge")
        assert any(i.side == "BUY" and "P00220000" in i.contract for i in intents)

    def test_no_close_2_days_before_expiry_short_itm(self):
        """2 days before expiry: NO forced close — LLM context warning instead."""
        strat = self._make_strategy()
        today = (date(2025, 11, 21) - timedelta(days=2)).isoformat()
        port = _make_portfolio(positions=[
            self._stock_pos(203.0),
            _make_put_position("O:AMD251121P00220000", qty=-100, mark=14.28, delta=-0.92),
        ])
        intents = strat._generate_expiry_close_orders(today, port, "overlay_hedge")
        # No forced close — but near_expiry_warnings should fire
        assert not any(i.side == "BUY" and "P00220000" in i.contract for i in intents)
        warnings = strat._collect_near_expiry_warnings(today, port)
        assert any("P00220000" in w for w in warnings)

    def test_no_close_for_short_otm_put(self):
        """Short OTM put expiring in 2 days should NOT be closed (let expire worthless)."""
        strat = self._make_strategy()
        today = (date(2025, 11, 21) - timedelta(days=2)).isoformat()
        port = _make_portfolio(positions=[
            self._stock_pos(230.0),
            _make_put_position("O:AMD251121P00220000", qty=-100, mark=0.05, delta=-0.05),
        ])
        intents = strat._generate_expiry_close_orders(today, port, "overlay_hedge")
        # OTM short → let expire, no intent
        assert not any(i.side == "BUY" and "P00220000" in i.contract for i in intents)

    def test_sells_long_itm_put_on_expiry(self):
        """Long ITM put on expiry day should be sold to capture intrinsic."""
        strat = self._make_strategy()
        expiry = date(2025, 11, 21)
        today = expiry.isoformat()
        port = _make_portfolio(positions=[
            self._stock_pos(203.0),
            _make_put_position("O:AMD251121P00260000", qty=150, mark=54.43, delta=-0.96),
        ])
        intents = strat._generate_expiry_close_orders(today, port, "overlay_hedge")
        assert any(i.side == "SELL" and "P00260000" in i.contract for i in intents)


# ---------------------------------------------------------------------------
# Hedge capacity enforcement
# ---------------------------------------------------------------------------

class TestEnforceHedgeCapacity:
    def _make_strategy(self) -> OverlayHedgeStrategy:
        s = object.__new__(OverlayHedgeStrategy)
        s._underlying = "AMD"
        return s

    def _make_buy_put(self, contract: str, qty: int, sg: str | None = None) -> TradeIntent:
        return TradeIntent(
            symbol="AMD", contract=contract, side="BUY", qty=qty,
            order_type="MARKET", strategy_name="overlay_hedge",
            spread_group=sg,
        )

    def _make_sell_put(self, contract: str, qty: int, sg: str | None = None) -> TradeIntent:
        return TradeIntent(
            symbol="AMD", contract=contract, side="SELL", qty=qty,
            order_type="MARKET", strategy_name="overlay_hedge",
            spread_group=sg,
        )

    def test_caps_buy_puts_to_per_trade_cap(self):
        strat = self._make_strategy()
        intents = [self._make_buy_put("O:AMD251205P00220000", qty=275)]
        result = strat._enforce_hedge_capacity(intents, remaining_puts=100, per_trade_cap=50)
        assert len(result) == 1
        assert result[0].qty == 50

    def test_syncs_spread_sell_leg_to_capped_buy(self):
        strat = self._make_strategy()
        intents = [
            self._make_buy_put("O:AMD251205P00220000", qty=275, sg="A"),
            self._make_sell_put("O:AMD251205P00210000", qty=275, sg="A"),
        ]
        result = strat._enforce_hedge_capacity(intents, remaining_puts=100, per_trade_cap=50)
        buy_qty = next(i.qty for i in result if i.side == "BUY")
        sell_qty = next(i.qty for i in result if i.side == "SELL")
        assert buy_qty == 50
        assert sell_qty == 50

    def test_blocks_when_remaining_puts_zero(self):
        strat = self._make_strategy()
        intents = [self._make_buy_put("O:AMD251205P00220000", qty=50)]
        result = strat._enforce_hedge_capacity(intents, remaining_puts=0, per_trade_cap=50)
        assert len(result) == 0

    def test_respects_remaining_puts_below_per_trade_cap(self):
        strat = self._make_strategy()
        intents = [self._make_buy_put("O:AMD251205P00220000", qty=50)]
        result = strat._enforce_hedge_capacity(intents, remaining_puts=20, per_trade_cap=50)
        assert result[0].qty == 20
