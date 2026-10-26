"""Tests for LatencyAwareMixin: fast/slow path selection and cost tracking.

5 test cases:
1. test_fast_path_near_close — < 15 min -> fast path, exits losers
2. test_slow_path_early — > 15 min -> slow path
3. test_cost_tracking — step costs recorded correctly
4. test_cost_summary — aggregated metrics correct
5. test_fast_path_no_positions — no positions -> no intents (just record)
"""

from __future__ import annotations

import pytest

from trading.agents.strategy.latency_aware import LatencyAwareMixin, StepCost
from trading.core.schemas import PortfolioSnapshot, PositionInfo


# ---------------------------------------------------------------------------
# Concrete test class using the mixin
# ---------------------------------------------------------------------------

class _LatencyTestable(LatencyAwareMixin):
    """Minimal concrete class that uses the mixin for testing."""

    def __init__(self, fast_threshold_minutes: int = 15):
        self._init_latency_aware(fast_threshold_minutes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_portfolio(
    cash: float = 90_000.0,
    equity: float = 100_000.0,
    positions: list[PositionInfo] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        positions=positions or [],
    )


def _make_losing_position(
    contract: str = "O:SPY250106C00595000",
    avg_entry: float = 3.00,
    mark_price: float = 2.00,  # -33% loss
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side="LONG",
        qty=5,
        avg_entry=avg_entry,
        mark_price=mark_price,
        unrealized_pnl=(mark_price - avg_entry) * 5 * 100,
        dte=0,
        stale=False,
        stale_minutes=0,
    )


def _make_winning_position(
    contract: str = "O:QQQ250106P00500000",
    avg_entry: float = 1.50,
    mark_price: float = 2.00,  # +33% gain
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side="LONG",
        qty=3,
        avg_entry=avg_entry,
        mark_price=mark_price,
        unrealized_pnl=(mark_price - avg_entry) * 3 * 100,
        dte=2,
        stale=False,
        stale_minutes=0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLatencyAwareMixin:

    def test_fast_path_near_close(self):
        """< 15 min to close -> fast path; exits losing positions."""
        obj = _LatencyTestable(fast_threshold_minutes=15)

        # Path selection
        path = obj._choose_path(minutes_to_close=10, has_position=True)
        assert path == "fast"

        # Fast path should emit exit intents for losers
        portfolio = _make_portfolio(positions=[
            _make_losing_position(),
            _make_winning_position(),
        ])
        intents = obj._fast_path(portfolio)

        # Only the losing position should be exited
        assert len(intents) == 1
        assert intents[0].contract == "O:SPY250106C00595000"
        assert intents[0].side == "SELL"
        assert intents[0].qty == 5
        assert "fast_path" in intents[0].rationale

    def test_slow_path_early(self):
        """> 15 min to close -> slow path."""
        obj = _LatencyTestable(fast_threshold_minutes=15)

        path = obj._choose_path(minutes_to_close=120, has_position=True)
        assert path == "slow"

        path = obj._choose_path(minutes_to_close=60, has_position=False)
        assert path == "slow"

    def test_cost_tracking(self):
        """Step costs are recorded with correct values."""
        obj = _LatencyTestable()

        obj._record_cost(
            step_id=1,
            timestamp="2025-01-06T10:00:00",
            path="slow",
            llm_calls=3,
            total_tokens=1500,
            prompt_tokens=1200,
            completion_tokens=300,
            latency_ms=450.5,
            decision="buy",
        )

        assert len(obj._step_costs) == 1
        cost = obj._step_costs[0]
        assert isinstance(cost, StepCost)
        assert cost.step_id == 1
        assert cost.path == "slow"
        assert cost.llm_calls == 3
        assert cost.total_tokens == 1500
        assert cost.prompt_tokens == 1200
        assert cost.completion_tokens == 300
        assert cost.latency_ms == 450.5
        assert cost.decision == "buy"

    def test_cost_summary(self):
        """Aggregated cost metrics are correct."""
        obj = _LatencyTestable()

        # Record a few steps
        obj._record_cost(1, "t1", "fast", 0, 0, 0, 0, 10.0, "hold")
        obj._record_cost(2, "t2", "slow", 3, 1500, 1200, 300, 500.0, "buy")
        obj._record_cost(3, "t3", "slow", 2, 1000, 800, 200, 300.0, "sell")
        obj._record_cost(4, "t4", "fast", 0, 0, 0, 0, 5.0, "exit")

        summary = obj.get_cost_summary()

        assert summary["total_steps"] == 4
        assert summary["fast_path_steps"] == 2
        assert summary["slow_path_steps"] == 2
        assert summary["total_llm_calls"] == 5  # 0 + 3 + 2 + 0
        assert summary["total_tokens"] == 2500  # 0 + 1500 + 1000 + 0
        # avg latency: (10 + 500 + 300 + 5) / 4 = 203.75
        assert abs(summary["avg_latency_ms"] - 203.75) < 0.01
        assert len(summary["cost_per_step"]) == 4

    def test_fast_path_no_positions(self):
        """No positions -> fast path returns empty list."""
        obj = _LatencyTestable(fast_threshold_minutes=15)

        portfolio = _make_portfolio(positions=[])
        intents = obj._fast_path(portfolio)

        assert intents == []

    def test_fast_path_last_five_minutes(self):
        """<= 5 min to close -> always fast path."""
        obj = _LatencyTestable(fast_threshold_minutes=15)

        path = obj._choose_path(minutes_to_close=5, has_position=False)
        assert path == "fast"

        path = obj._choose_path(minutes_to_close=3, has_position=True)
        assert path == "fast"

    def test_cost_summary_empty(self):
        """Empty step costs produce zero-valued summary."""
        obj = _LatencyTestable()
        summary = obj.get_cost_summary()

        assert summary["total_steps"] == 0
        assert summary["fast_path_steps"] == 0
        assert summary["slow_path_steps"] == 0
        assert summary["total_llm_calls"] == 0
        assert summary["total_tokens"] == 0
        assert summary["avg_latency_ms"] == 0.0

    def test_fast_path_short_position(self):
        """Fast path exits a losing SHORT position with BUY side."""
        obj = _LatencyTestable()

        short_pos = PositionInfo(
            contract="O:SPY250106P00590000",
            side="SHORT",
            qty=-3,
            avg_entry=2.00,
            mark_price=1.50,  # -25% loss (short lost value)
            unrealized_pnl=-150.0,
            dte=0,
            stale=False,
            stale_minutes=0,
        )
        # For a short: pnl_pct = (mark - entry) / entry * 100 = (1.5 - 2.0)/2.0 * 100 = -25%
        portfolio = _make_portfolio(positions=[short_pos])
        intents = obj._fast_path(portfolio)

        assert len(intents) == 1
        assert intents[0].side == "BUY"  # close short by buying
        assert intents[0].qty == 3
