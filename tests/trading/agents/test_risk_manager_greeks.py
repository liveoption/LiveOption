"""Tests for Greeks-aware risk checks in RiskGateAgent."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from trading.agents.risk_manager import RiskGateAgent
from trading.core.config import DataPolicyConfig, RiskConfig
from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
    TradeIntent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(
    contract: str = "O:SPY250106C00595000",
    close: float = 2.3,
    delta: float | None = 0.50,
    vega: float | None = 0.10,
) -> BarData:
    return BarData(
        symbol=contract,
        open=2.0, high=2.5, low=1.8, close=close,
        volume=500, window_start="2025-01-06T10:30:00",
        instrument_type="OPTION",
        stale=False, stale_minutes=0, tradeable=True,
        delta=delta,
        vega=vega,
    )


def _make_market_event(bars: list[BarData] | None = None) -> MarketEvent:
    return MarketEvent(
        event_ts="2025-01-06T10:30:00",
        bars=bars or [_make_bar()],
        bar_history={},
    )


@dataclass
class MockTradingState:
    current_ts: str = "2025-01-06T10:30:00"
    market_event: MarketEvent | None = field(default_factory=_make_market_event)


def _make_portfolio(
    positions: list[PositionInfo] | None = None,
    equity: float = 100_000,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=90_000,
        equity=equity,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        positions=positions or [],
    )


def _make_intent(
    contract: str = "O:SPY250106C00595000",
    side: str = "BUY",
    qty: int = 5,
) -> TradeIntent:
    return TradeIntent(
        symbol="SPY",
        contract=contract,
        side=side,
        qty=qty,
        order_type="MARKET",
    )


def _make_position(
    contract: str = "O:SPY250106C00595000",
    qty: int = 5,
    delta: float | None = 0.50,
    vega: float | None = 0.10,
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side="LONG" if qty > 0 else "SHORT",
        qty=qty,
        avg_entry=2.30,
        mark_price=2.50,
        unrealized_pnl=100.0,
        dte=1,
        stale=False,
        stale_minutes=0,
        delta=delta,
        gamma=0.05,
        vega=vega,
        theta=-0.02,
    )


def _make_gate(
    max_net_delta: float | None = None,
    max_net_vega: float | None = None,
    max_position_per_symbol: int = 10_000,
) -> RiskGateAgent:
    risk_config = RiskConfig(
        max_net_delta=max_net_delta,
        max_net_vega=max_net_vega,
        max_position_per_symbol=max_position_per_symbol,
    )
    return RiskGateAgent(
        risk_config=risk_config,
        data_policy_config=DataPolicyConfig(),
    )


# ---------------------------------------------------------------------------
# Test 1: Delta concentration — REJECT
# ---------------------------------------------------------------------------

class TestDeltaConcentrationReject:
    """test_delta_concentration_reject — net delta too high -> REJECT."""

    def test_reject_when_delta_exceeds_limit(self):
        """Buying 10 contracts with delta=0.50 adds 500 delta. Existing
        position has 5 contracts at delta=0.50 = 250 delta. Total = 750.
        Limit is 500 -> REJECT."""
        gate = _make_gate(max_net_delta=500)

        existing_pos = _make_position(
            contract="O:SPY250106C00594000", qty=5, delta=0.50,
        )
        intent = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=10,
        )

        bar = _make_bar(contract="O:SPY250106C00595000", delta=0.50)
        # Also need a bar for the existing position's contract to avoid
        # stale rejection
        bar_existing = _make_bar(contract="O:SPY250106C00594000", delta=0.50)
        state = MockTradingState(
            market_event=_make_market_event([bar, bar_existing]),
        )

        portfolio = _make_portfolio(positions=[existing_pos])
        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "REJECT"
        assert "delta" in decisions[0].reason.lower()
        assert "delta_concentration" in decisions[0].risk_notes


# ---------------------------------------------------------------------------
# Test 2: Delta concentration — ALLOW
# ---------------------------------------------------------------------------

class TestDeltaConcentrationAllow:
    """test_delta_concentration_allow — within limit -> ALLOW."""

    def test_allow_when_delta_within_limit(self):
        """Buying 2 contracts with delta=0.50 adds 100 delta. No existing
        positions. Total = 100. Limit is 500 -> ALLOW."""
        gate = _make_gate(max_net_delta=500)

        intent = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=2,
        )
        bar = _make_bar(contract="O:SPY250106C00595000", delta=0.50)
        state = MockTradingState(
            market_event=_make_market_event([bar]),
        )

        portfolio = _make_portfolio()
        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "ALLOW"


# ---------------------------------------------------------------------------
# Test 3: Vega concentration — REJECT
# ---------------------------------------------------------------------------

class TestVegaConcentrationReject:
    """test_vega_concentration_reject — vega too high -> REJECT."""

    def test_reject_when_vega_exceeds_limit(self):
        """Buying 20 contracts with vega=0.10 adds 200 vega. Existing
        position has 10 contracts at vega=0.10 = 100 vega. Total = 300.
        Limit is 200 -> REJECT."""
        gate = _make_gate(max_net_vega=200)

        existing_pos = _make_position(
            contract="O:SPY250106C00594000", qty=10, vega=0.10,
        )
        intent = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=20,
        )

        bar = _make_bar(contract="O:SPY250106C00595000", delta=0.50, vega=0.10)
        bar_existing = _make_bar(contract="O:SPY250106C00594000", delta=0.50, vega=0.10)
        state = MockTradingState(
            market_event=_make_market_event([bar, bar_existing]),
        )

        portfolio = _make_portfolio(positions=[existing_pos])
        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "REJECT"
        assert "vega" in decisions[0].reason.lower()
        assert "vega_concentration" in decisions[0].risk_notes


# ---------------------------------------------------------------------------
# Test 4: Greeks check disabled
# ---------------------------------------------------------------------------

class TestGreeksCheckDisabled:
    """test_greeks_check_disabled — max_net_delta=None -> skip check."""

    def test_disabled_delta_check_allows_trade(self):
        """With max_net_delta=None, even huge delta trades pass."""
        gate = _make_gate(max_net_delta=None, max_net_vega=None)

        existing_pos = _make_position(
            contract="O:SPY250106C00594000", qty=100, delta=0.90,
        )
        intent = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=100,
        )

        bar = _make_bar(contract="O:SPY250106C00595000", delta=0.90, vega=0.50)
        bar_existing = _make_bar(contract="O:SPY250106C00594000", delta=0.90, vega=0.50)
        state = MockTradingState(
            market_event=_make_market_event([bar, bar_existing]),
        )

        portfolio = _make_portfolio(positions=[existing_pos])
        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        assert decisions[0].action == "ALLOW"


# ---------------------------------------------------------------------------
# Test 5: Greeks missing from positions — don't crash
# ---------------------------------------------------------------------------

class TestGreeksMissingFromPositions:
    """test_greeks_missing_from_positions — no delta data -> skip check."""

    def test_skip_check_when_no_greeks(self):
        """Positions and bars without delta -> check is skipped, trade allowed."""
        gate = _make_gate(max_net_delta=100)

        # Position with no Greeks
        existing_pos = _make_position(
            contract="O:SPY250106C00594000", qty=50, delta=None, vega=None,
        )
        intent = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=50,
        )

        # Bar with no delta
        bar = _make_bar(contract="O:SPY250106C00595000", delta=None, vega=None)
        bar_existing = _make_bar(contract="O:SPY250106C00594000", delta=None, vega=None)
        state = MockTradingState(
            market_event=_make_market_event([bar, bar_existing]),
        )

        portfolio = _make_portfolio(positions=[existing_pos])
        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        # Should not crash and should allow (check skipped due to no data)
        assert decisions[0].action == "ALLOW"

    def test_partial_greeks_does_not_crash(self):
        """Some positions have delta, some don't — should not crash."""
        gate = _make_gate(max_net_delta=500)

        pos_with = _make_position(
            contract="O:SPY250106C00594000", qty=5, delta=0.50,
        )
        pos_without = _make_position(
            contract="O:SPY250106P00580000", qty=3, delta=None,
        )
        intent = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=2,
        )

        bar = _make_bar(contract="O:SPY250106C00595000", delta=0.50)
        bar1 = _make_bar(contract="O:SPY250106C00594000", delta=0.50)
        bar2 = _make_bar(contract="O:SPY250106P00580000", delta=None)
        state = MockTradingState(
            market_event=_make_market_event([bar, bar1, bar2]),
        )

        portfolio = _make_portfolio(positions=[pos_with, pos_without])
        decisions = gate.evaluate([intent], portfolio, state)

        assert len(decisions) == 1
        # 250 (existing) + 100 (intent) = 350 < 500 -> ALLOW
        assert decisions[0].action == "ALLOW"


# ---------------------------------------------------------------------------
# Test 6: Greeks batch accumulation
# ---------------------------------------------------------------------------

class TestGreeksBatchAccumulation:
    def test_delta_batch_accumulation_rejects_second_order(self):
        gate = _make_gate(max_net_delta=500)
        bars = [
            _make_bar(contract="O:SPY250106C00595000", delta=0.50),
            _make_bar(contract="O:SPY250106C00600000", delta=0.50),
        ]
        state = MockTradingState(market_event=_make_market_event(bars))
        portfolio = _make_portfolio()  # empty positions
        intents = [
            _make_intent(contract="O:SPY250106C00595000", side="BUY", qty=6),
            _make_intent(contract="O:SPY250106C00600000", side="BUY", qty=6),
        ]
        # Each adds 6*0.50*100 = 300. First: 300 < 500 -> ALLOW. Second: 600 > 500 -> REJECT
        decisions = gate.evaluate(intents, portfolio, state)
        assert decisions[0].action == "ALLOW"
        assert decisions[1].action == "REJECT"

    def test_vega_batch_accumulation_rejects_second_order(self):
        gate = _make_gate(max_net_vega=150)
        bars = [
            _make_bar(contract="O:SPY250106C00595000", vega=0.10),
            _make_bar(contract="O:SPY250106C00600000", vega=0.10),
        ]
        state = MockTradingState(market_event=_make_market_event(bars))
        portfolio = _make_portfolio()  # empty positions
        intents = [
            _make_intent(contract="O:SPY250106C00595000", side="BUY", qty=10),
            _make_intent(contract="O:SPY250106C00600000", side="BUY", qty=10),
        ]
        # Each adds 10*0.10*100 = 100. First: 100 < 150 -> ALLOW. Second: 200 > 150 -> REJECT
        decisions = gate.evaluate(intents, portfolio, state)
        assert decisions[0].action == "ALLOW"
        assert decisions[1].action == "REJECT"
