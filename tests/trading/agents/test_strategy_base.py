"""Tests for BaseStrategy abstract interface and lifecycle hooks."""

import pytest

from trading.agents.strategy.base import BaseStrategy
from trading.core.registry import STRATEGY_REGISTRY, register_strategy
from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    TradeIntent,
)
from trading.core.state import TradingState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market_event() -> MarketEvent:
    bar = BarData(
        symbol="SPY", open=595.0, high=596.0, low=594.0, close=595.5,
        volume=100_000, window_start="2025-01-06T09:31:00",
    )
    return MarketEvent(event_ts="2025-01-06T09:31:00", bars=[bar], bar_history={})


def _make_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=100_000, equity=100_000, daily_pnl=0.0,
        daily_pnl_pct=0.0, positions=[],
    )


def _make_state() -> TradingState:
    from trading.core.config import (
        ExperimentConfig,
        RunConfig,
        StrategyConfig,
    )

    config = RunConfig(
        experiment=ExperimentConfig(
            run_id="test", start="2025-01-06", end="2025-01-10",
            universe=["SPY"],
        ),
        strategy=StrategyConfig(name="mock", params={}),
    )
    return TradingState(config=config, run_id="test", episode_id="ep0")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_base_strategy_is_abstract():
    """Cannot instantiate BaseStrategy directly — on_step is abstract."""
    with pytest.raises(TypeError):
        BaseStrategy()


def test_concrete_strategy_on_step():
    """A subclass that implements on_step returns intents correctly."""

    class SimpleStrategy(BaseStrategy):
        def on_step(self, market_event, portfolio, state):
            return [
                TradeIntent(
                    symbol="SPY",
                    contract="O:SPY250106C00595000",
                    side="BUY",
                    qty=1,
                    order_type="MARKET",
                    rationale="test intent",
                    strategy_name="simple",
                )
            ]

        def save_checkpoint(self):
            return {}

        def load_checkpoint(self, state):
            pass

    strat = SimpleStrategy()
    intents = strat.on_step(_make_market_event(), _make_portfolio(), _make_state())
    assert len(intents) == 1
    assert intents[0].contract == "O:SPY250106C00595000"
    assert intents[0].side == "BUY"


def test_on_day_open_default_noop():
    """Default on_day_open does nothing and raises no error."""

    class Minimal(BaseStrategy):
        def on_step(self, market_event, portfolio, state):
            return []

        def save_checkpoint(self):
            return {}

        def load_checkpoint(self, state):
            pass

    strat = Minimal()
    # Should not raise
    strat.on_day_open("2025-01-06", _make_state())


def test_on_day_close_default_noop():
    """Default on_day_close does nothing and raises no error."""

    class Minimal(BaseStrategy):
        def on_step(self, market_event, portfolio, state):
            return []

        def save_checkpoint(self):
            return {}

        def load_checkpoint(self, state):
            pass

    strat = Minimal()
    # Should not raise
    strat.on_day_close("2025-01-06", _make_state())


def test_on_session_start_default_noop():
    """Default on_session_start does nothing and raises no error."""

    class Minimal(BaseStrategy):
        def on_step(self, market_event, portfolio, state):
            return []

        def save_checkpoint(self):
            return {}

        def load_checkpoint(self, state):
            pass

    strat = Minimal()
    state = _make_state()
    # Should not raise
    strat.on_session_start(state.config)


def test_strategy_registered_via_decorator():
    """@register_strategy('mock') class appears in STRATEGY_REGISTRY."""

    @register_strategy("mock_test_strategy")
    class MockStrategy(BaseStrategy):
        def __init__(self, greeting="hello"):
            self.greeting = greeting

        def on_step(self, market_event, portfolio, state):
            return []

        def save_checkpoint(self):
            return {}

        def load_checkpoint(self, state):
            pass

    try:
        assert "mock_test_strategy" in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY["mock_test_strategy"] is MockStrategy
        # Verify get_strategy can instantiate it
        from trading.core.registry import get_strategy
        instance = get_strategy("mock_test_strategy", {"greeting": "world"})
        assert instance.greeting == "world"
    finally:
        # Cleanup to avoid polluting the global registry
        STRATEGY_REGISTRY.pop("mock_test_strategy", None)
