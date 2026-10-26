"""Tests for trading event schemas."""

import pytest

from trading.core.schemas import (
    BarData,
    FillEvent,
    MarketEvent,
    OrderRequest,
    PortfolioSnapshot,
    PositionInfo,
    RiskDecision,
    TradeIntent,
)


def test_bar_data_defaults():
    bar = BarData(
        symbol="AAPL",
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=1000,
        window_start="2025-01-06T09:31:00",
    )
    assert bar.instrument_type == "STOCK"
    assert bar.stale is False
    assert bar.stale_minutes == 0
    assert bar.tradeable is True
    assert bar.iv is None


def test_bar_data_option_with_greeks():
    bar = BarData(
        symbol="O:SPY250106C00595000",
        open=2.0,
        high=2.5,
        low=1.8,
        close=2.3,
        volume=500,
        window_start="2025-01-06T10:00:00",
        instrument_type="OPTION",
        iv=0.18,
        delta=0.48,
        gamma=0.05,
        theta=-0.03,
    )
    assert bar.instrument_type == "OPTION"
    assert bar.delta == 0.48


def test_bar_data_stale():
    bar = BarData(
        symbol="O:SPY250106C00595000",
        open=2.0,
        high=2.5,
        low=1.8,
        close=2.3,
        volume=500,
        window_start="2025-01-06T10:00:00",
        instrument_type="OPTION",
        stale=True,
        stale_minutes=5,
        tradeable=True,
    )
    assert bar.stale is True
    assert bar.stale_minutes == 5


def test_market_event():
    bar = BarData(
        symbol="AAPL",
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=1000,
        window_start="2025-01-06T09:31:00",
    )
    event = MarketEvent(event_ts="2025-01-06T09:31:00", bars=[bar], bar_history={})
    assert len(event.bars) == 1
    assert event.bar_history == {}


def test_portfolio_snapshot():
    pos = PositionInfo(
        contract="O:SPY250106C00595000",
        side="LONG",
        qty=5,
        avg_entry=2.30,
        mark_price=2.85,
        unrealized_pnl=275.0,
        dte=0,
        stale=False,
        stale_minutes=0,
    )
    snap = PortfolioSnapshot(
        cash=90000,
        equity=100000,
        daily_pnl=500,
        daily_pnl_pct=0.5,
        positions=[pos],
    )
    assert snap.positions[0].contract == "O:SPY250106C00595000"


def test_trade_intent():
    intent = TradeIntent(
        symbol="SPY",
        contract="O:SPY250106C00595000",
        side="BUY",
        qty=5,
        order_type="MARKET",
        rationale="Bull call",
        strategy_name="intraday_llm",
    )
    assert intent.spread_group is None
    assert intent.confidence == 0.0


def test_trade_intent_spread():
    intent = TradeIntent(
        symbol="SPY",
        contract="O:SPY250106C00595000",
        side="BUY",
        qty=5,
        order_type="MARKET",
        spread_group="A",
        rationale="Long leg",
    )
    assert intent.spread_group == "A"


def test_risk_decision():
    intent = TradeIntent(
        symbol="SPY",
        contract="O:SPY250106C00595000",
        side="BUY",
        qty=5,
        order_type="MARKET",
    )
    decision = RiskDecision(
        original_intent=intent,
        action="ALLOW",
        reason="All checks passed",
        risk_notes=["exposure ok"],
        exposure_after=0.05,
        loss_after=0.001,
    )
    assert decision.action == "ALLOW"
    assert decision.adjusted_qty is None


def test_risk_decision_reject():
    intent = TradeIntent(
        symbol="SPY",
        contract="O:SPY250106C00595000",
        side="BUY",
        qty=5,
        order_type="MARKET",
    )
    decision = RiskDecision(
        original_intent=intent,
        action="REJECT",
        reason="Max exposure exceeded",
        risk_notes=["exposure 0.6 > 0.5"],
    )
    assert decision.action == "REJECT"


def test_fill_event():
    fill = FillEvent(
        trade_id="trd_abc123",
        order_id="ord_def456",
        contract="O:SPY250106C00595000",
        side="BUY",
        qty=5,
        price=2.30,
        fees=3.25,
        fill_ts="2025-01-06T09:32:00",
    )
    assert fill.fees == 3.25


def test_order_request():
    order = OrderRequest(
        order_id="ord_001",
        contract="O:SPY250106C00595000",
        side="BUY",
        qty=5,
        order_type="MARKET",
    )
    assert order.spread_id is None
    assert order.limit_price is None


def test_bar_data_model_dump():
    """Pydantic v2 models support model_dump() for serialization."""
    bar = BarData(
        symbol="AAPL",
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=1000,
        window_start="2025-01-06T09:31:00",
    )
    data = bar.model_dump()
    assert data["symbol"] == "AAPL"
    assert data["instrument_type"] == "STOCK"


def test_risk_decision_with_list_default():
    """Verify risk_notes defaults to empty list (mutable default via Field)."""
    intent = TradeIntent(
        symbol="SPY",
        contract="O:SPY250106C00595000",
        side="BUY",
        qty=1,
        order_type="MARKET",
    )
    d1 = RiskDecision(original_intent=intent, action="ALLOW")
    d2 = RiskDecision(original_intent=intent, action="ALLOW")
    # Ensure separate list instances (no shared mutable default)
    d1.risk_notes.append("test")
    assert d2.risk_notes == []
