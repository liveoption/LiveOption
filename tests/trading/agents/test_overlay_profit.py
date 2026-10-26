"""Tests for OverlayProfitStrategy (overlay_profit) — multi-agent LLM pipeline version."""

import pytest
from datetime import date, timedelta

from trading.agents.strategy.overlay import (
    OverlayProfitStrategy,
    compute_covered_call_capacity,
    _parse_opra_right,
)
from trading.core.registry import STRATEGY_REGISTRY
from trading.core.schemas import (
    PortfolioSnapshot,
    PositionInfo,
    TradeIntent,
)


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


def _stock(qty: int = 10_000, price: float = 138.0) -> PositionInfo:
    return PositionInfo(
        contract="NVDA", side="LONG", qty=qty,
        avg_entry=price, mark_price=price, unrealized_pnl=0.0,
        dte=0, stale=False, stale_minutes=0,
    )


def _short_call(contract: str, qty: int, delta: float = 0.25, mark: float = 2.0) -> PositionInfo:
    return PositionInfo(
        contract=contract, side="SHORT", qty=-qty,
        avg_entry=mark + 0.3, mark_price=mark, unrealized_pnl=0.0,
        dte=14, stale=False, stale_minutes=0, delta=delta,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_registered(self):
        assert "overlay_profit" in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY["overlay_profit"] is OverlayProfitStrategy


# ---------------------------------------------------------------------------
# Covered call capacity
# ---------------------------------------------------------------------------

class TestCoveredCallCapacity:
    def _make_strategy(self) -> OverlayProfitStrategy:
        s = object.__new__(OverlayProfitStrategy)
        s._underlying = "NVDA"
        return s

    def test_capacity_no_positions(self):
        port = _make_portfolio(positions=[_stock(10_000)])
        long_shares, existing, available = compute_covered_call_capacity(port, "NVDA")
        assert long_shares == 10_000
        assert existing == 0
        assert available == 100  # 10000 / 100

    def test_capacity_with_existing_short_calls(self):
        port = _make_portfolio(positions=[
            _stock(10_000),
            _short_call("O:NVDA250221C00145000", qty=20),
        ])
        long_shares, existing, available = compute_covered_call_capacity(port, "NVDA")
        assert long_shares == 10_000
        assert existing == 20
        assert available == 80

    def test_capacity_fully_covered(self):
        port = _make_portfolio(positions=[
            _stock(10_000),
            _short_call("O:NVDA250221C00145000", qty=100),
        ])
        _, _, available = compute_covered_call_capacity(port, "NVDA")
        assert available == 0

    def test_capacity_no_shares(self):
        port = _make_portfolio(positions=[])
        long_shares, _, available = compute_covered_call_capacity(port, "NVDA")
        assert long_shares == 0
        assert available == 0


# ---------------------------------------------------------------------------
# Assignment risk check
# ---------------------------------------------------------------------------

class TestAssignmentRisk:
    def _make_strategy(self) -> OverlayProfitStrategy:
        s = object.__new__(OverlayProfitStrategy)
        s._underlying = "NVDA"
        return s

    def test_closes_deep_itm_short_call(self):
        strat = self._make_strategy()
        port = _make_portfolio(positions=[
            _stock(10_000),
            _short_call("O:NVDA250221C00130000", qty=50, delta=0.82),
        ])
        intents = strat._check_assignment_risk(port)
        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].qty == 50

    def test_no_close_below_delta_threshold(self):
        strat = self._make_strategy()
        port = _make_portfolio(positions=[
            _stock(10_000),
            _short_call("O:NVDA250221C00145000", qty=30, delta=0.35),
        ])
        intents = strat._check_assignment_risk(port)
        assert intents == []

    def test_no_close_for_long_calls(self):
        strat = self._make_strategy()
        port = _make_portfolio(positions=[
            _stock(10_000),
            PositionInfo(
                contract="O:NVDA250221C00145000", side="LONG", qty=20,
                avg_entry=2.0, mark_price=10.0, unrealized_pnl=160_000.0,
                dte=5, stale=False, stale_minutes=0, delta=0.85,
            ),
        ])
        intents = strat._check_assignment_risk(port)
        assert intents == []


# ---------------------------------------------------------------------------
# Share deficit check
# ---------------------------------------------------------------------------

class TestShareDeficit:
    def _make_strategy(self, stock_qty: int = 10_000) -> OverlayProfitStrategy:
        s = object.__new__(OverlayProfitStrategy)
        s._underlying = "NVDA"
        s._stock_qty = stock_qty
        return s

    def test_rebuys_missing_shares(self):
        strat = self._make_strategy(10_000)
        port = _make_portfolio(positions=[_stock(8_000)])  # 2000 missing
        intents = strat._check_share_deficit(port)
        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].contract == "NVDA"
        assert intents[0].qty == 2_000

    def test_no_rebuy_when_sufficient(self):
        strat = self._make_strategy(10_000)
        port = _make_portfolio(positions=[_stock(10_000)])
        intents = strat._check_share_deficit(port)
        assert intents == []

    def test_no_rebuy_when_no_target(self):
        strat = self._make_strategy(0)
        port = _make_portfolio(positions=[])
        intents = strat._check_share_deficit(port)
        assert intents == []


# ---------------------------------------------------------------------------
# Expiry: short call ITM closed 2 days early
# ---------------------------------------------------------------------------

class TestExpiryCloseOrders:
    def _make_strategy(self) -> OverlayProfitStrategy:
        s = object.__new__(OverlayProfitStrategy)
        s._underlying = "NVDA"
        s._stock_qty = 10_000
        return s

    def test_closes_short_itm_call_on_expiry_day(self):
        """Short ITM call on actual expiry day → forced buy-to-close (backstop)."""
        strat = self._make_strategy()
        today = date(2025, 2, 21).isoformat()  # actual expiry day
        port = _make_portfolio(positions=[
            _stock(10_000, 150.0),
            _short_call("O:NVDA250221C00130000", qty=20, delta=0.85),
        ])
        intents = strat._generate_expiry_close_orders(today, port, "overlay_profit")
        assert any(i.side == "BUY" and "C00130000" in i.contract for i in intents)

    def test_near_expiry_warning_not_forced_close(self):
        """2 days before expiry: warning injected into context, not a forced close."""
        strat = self._make_strategy()
        today = (date(2025, 2, 21) - timedelta(days=2)).isoformat()
        port = _make_portfolio(positions=[
            _stock(10_000, 150.0),
            _short_call("O:NVDA250221C00130000", qty=20, delta=0.85),
        ])
        intents = strat._generate_expiry_close_orders(today, port, "overlay_profit")
        assert not any("C00130000" in i.contract for i in intents)
        warnings = strat._collect_near_expiry_warnings(today, port)
        assert any("C00130000" in w for w in warnings)

    def test_no_close_for_short_otm_call(self):
        strat = self._make_strategy()
        today = (date(2025, 2, 21) - timedelta(days=2)).isoformat()
        port = _make_portfolio(positions=[
            _stock(10_000, 100.0),  # stock well below strike
            _short_call("O:NVDA250221C00155000", qty=20, delta=0.05, mark=0.02),
        ])
        intents = strat._generate_expiry_close_orders(today, port, "overlay_profit")
        buy_intents = [i for i in intents if i.side == "BUY" and "C00155000" in i.contract]
        assert len(buy_intents) == 0
