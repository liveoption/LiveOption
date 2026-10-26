"""Tests for TradingState dataclass."""

from datetime import datetime

from trading.core.time_contract import UTC_TZ, utc_from_iso
from trading.core.config import (
    ExperimentConfig,
    RunConfig,
    StrategyConfig,
)
from trading.core.schemas import (
    FillEvent,
    MarketEvent,
    OrderRequest,
    PortfolioSnapshot,
    RiskDecision,
    TradeIntent,
)
from trading.core.state import TradingState


def _make_config() -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test", start="2025-01-06", end="2025-01-10", universe=["SPY"]
        ),
        strategy=StrategyConfig(name="test"),
    )


def test_trading_state_defaults():
    cfg = _make_config()
    state = TradingState(config=cfg, run_id="test_run", episode_id="ep_001")
    assert state.step_id == 0
    # Default tz-aware UTC datetime (epoch placeholder) per time contract.
    assert isinstance(state.current_ts, datetime)
    assert state.current_ts.tzinfo is not None
    assert state.current_date == ""
    assert state.session_running is True
    assert state.raw_step_result == {}
    assert state.is_new_day is False
    assert state.portfolio is None
    assert state.market_event is None
    assert state.fills == []
    assert state.intents == []
    assert state.risk_decisions == []
    assert state.orders == []
    assert state.errors == []


def test_trading_state_mutable():
    """TradingState fields can be mutated (it's a dataclass, not frozen)."""
    cfg = _make_config()
    state = TradingState(config=cfg, run_id="test_run", episode_id="ep_001")
    state.step_id = 5
    state.current_ts = utc_from_iso("2025-01-06T14:31:00+00:00")
    state.current_date = "2025-01-06"
    state.is_new_day = True
    state.errors.append("test error")

    assert state.step_id == 5
    assert state.current_ts == datetime(2025, 1, 6, 14, 31, 0, tzinfo=UTC_TZ)
    assert state.is_new_day is True
    assert state.errors == ["test error"]


def test_trading_state_list_fields_independent():
    """Each TradingState instance has its own list fields (no shared refs)."""
    cfg = _make_config()
    s1 = TradingState(config=cfg, run_id="r1", episode_id="e1")
    s2 = TradingState(config=cfg, run_id="r2", episode_id="e2")
    s1.errors.append("only in s1")
    assert s2.errors == []


def test_trading_state_accepts_schema_objects():
    """TradingState can hold schema objects in its typed fields."""
    cfg = _make_config()
    state = TradingState(config=cfg, run_id="test", episode_id="ep")

    intent = TradeIntent(
        contract="O:SPY250106C00595000", side="BUY", qty=5, order_type="MARKET"
    )
    state.intents = [intent]
    assert len(state.intents) == 1
    assert state.intents[0].contract == "O:SPY250106C00595000"
