"""Tests for WeeklyLLMStrategy migration."""

import json
from unittest.mock import MagicMock, patch

import pytest

from trading.agents.strategy.weekly_llm import WeeklyLLMStrategy, get_rebalance_dates
from trading.core.config import (
    DataPolicyConfig,
    ExperimentConfig,
    RunConfig,
    StrategyConfig,
)
from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
    TradeIntent,
)
from trading.core.state import TradingState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(start="2025-01-06", end="2025-01-31", universe=None):
    """Build a minimal RunConfig for testing."""
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test",
            start=start,
            end=end,
            universe=universe or ["SPY"],
            frequency="1d",
        ),
        strategy=StrategyConfig(name="weekly_llm", params={}),
    )


def _make_state(current_ts: str, config=None) -> TradingState:
    """Build a TradingState with the given timestamp."""
    from trading.core.time_contract import utc_from_iso
    cfg = config or _make_config()
    ts_iso = current_ts if current_ts.endswith("Z") or "+" in current_ts else current_ts + "+00:00"
    ts_dt = utc_from_iso(ts_iso)
    return TradingState(
        config=cfg,
        run_id="test_run",
        episode_id="ep_1",
        current_ts=ts_dt,
        current_date=current_ts[:10],
    )


def _make_portfolio(cash=100_000.0, equity=100_000.0, positions=None):
    """Build a minimal PortfolioSnapshot."""
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        positions=positions or [],
    )


def _make_market_event(ts="2025-01-06T09:31:00"):
    """Build a minimal MarketEvent."""
    bar = BarData(
        symbol="SPY", open=595.0, high=596.0, low=594.0, close=595.5,
        volume=1_000_000, window_start=ts,
    )
    return MarketEvent(event_ts=ts, bars=[bar], bar_history={})


MOCK_LLM_RESPONSE = json.dumps([
    {
        "action": "buy",
        "instrument_type": "option",
        "contract": "O:SPY250120C00595000",
        "qty": 5,
        "order_type": "MARKET",
        "rationale": "Bullish momentum on SPY",
    },
])


def _create_strategy(
    rebalance_frequency="weekly",
    rebalance_weekday=0,
    llm_provider="mock",
):
    """Create and session-start a WeeklyLLMStrategy with mock dependencies."""
    strategy = WeeklyLLMStrategy(
        rebalance_frequency=rebalance_frequency,
        rebalance_weekday=rebalance_weekday,
        llm_provider=llm_provider,
        llm_model="mock-model",
        dte_min=7,
        dte_max=60,
        news_lookback_days=7,
        upq_base_url="http://localhost:19350",
    )
    config = _make_config(start="2025-01-06", end="2025-01-31")
    strategy.on_session_start(config)
    return strategy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetRebalanceDates:
    def test_weekly_mondays(self):
        dates = get_rebalance_dates("2025-01-06", "2025-01-31", "weekly", 0)
        # Jan 2025: Mondays are 6, 13, 20, 27
        assert dates == ["2025-01-06", "2025-01-13", "2025-01-20", "2025-01-27"]

    def test_daily_weekdays(self):
        dates = get_rebalance_dates("2025-01-06", "2025-01-10", "daily", 0)
        # Mon-Fri: 6,7,8,9,10
        assert len(dates) == 5
        assert "2025-01-11" not in dates  # Saturday

    def test_weekly_strategy_handles_datetime_start(self):
        """Pass datetime strings (with time component) — should not error."""
        dates = get_rebalance_dates(
            "2025-01-06T09:30:00", "2025-01-31T16:00:00", "weekly", 0
        )
        # Should still produce the same Mondays as date-only strings
        assert dates == ["2025-01-06", "2025-01-13", "2025-01-20", "2025-01-27"]

    def test_daily_handles_datetime_start(self):
        """Daily frequency also works with datetime strings."""
        dates = get_rebalance_dates(
            "2025-01-06T09:30:00", "2025-01-10T16:00:00", "daily", 0
        )
        assert len(dates) == 5


class TestWeeklyLLMStrategy:

    def test_returns_empty_on_non_rebalance_day(self):
        """Monday strategy, current date is Tuesday -> []."""
        strategy = _create_strategy(rebalance_frequency="weekly", rebalance_weekday=0)
        # 2025-01-07 is a Tuesday
        state = _make_state("2025-01-07T09:31:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-07T09:31:00")

        result = strategy.on_step(event, portfolio, state)
        assert result == []

    def test_returns_intents_on_rebalance_day(self):
        """Monday strategy, current date is Monday -> LLM called, intents returned."""
        strategy = _create_strategy(rebalance_frequency="weekly", rebalance_weekday=0)

        # Mock the LLM to return a known response
        mock_llm = MagicMock()
        mock_llm.decide.return_value = [
            {
                "action": "buy",
                "instrument_type": "option",
                "contract": "O:SPY250120C00595000",
                "qty": 5,
                "order_type": "MARKET",
                "rationale": "Bullish momentum",
            },
        ]
        strategy._llm = mock_llm

        # 2025-01-06 is a Monday
        state = _make_state("2025-01-06T09:31:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-06T09:31:00")

        with patch("trading.tools.upq_tools.query_option_chain", return_value=[]):
            result = strategy.on_step(event, portfolio, state)

        assert len(result) == 1
        intent = result[0]
        assert isinstance(intent, TradeIntent)
        assert intent.contract == "O:SPY250120C00595000"
        assert intent.side == "BUY"
        assert intent.qty == 5
        assert intent.order_type == "MARKET"
        assert intent.symbol == "SPY"
        assert intent.strategy_name == "weekly_llm"
        assert intent.rationale == "Bullish momentum"

    def test_daily_frequency_decides_every_weekday(self):
        """rebalance_frequency='daily' -> decides Mon-Fri."""
        strategy = _create_strategy(rebalance_frequency="daily")

        mock_llm = MagicMock()
        mock_llm.decide.return_value = [
            {
                "action": "buy",
                "instrument_type": "option",
                "contract": "O:SPY250120C00595000",
                "qty": 1,
                "order_type": "MARKET",
            },
        ]
        strategy._llm = mock_llm

        portfolio = _make_portfolio()

        weekday_dates = [
            "2025-01-06",  # Mon
            "2025-01-07",  # Tue
            "2025-01-08",  # Wed
            "2025-01-09",  # Thu
            "2025-01-10",  # Fri
        ]

        with patch("trading.tools.upq_tools.query_option_chain", return_value=[]):
            for d in weekday_dates:
                ts = f"{d}T09:31:00"
                state = _make_state(ts)
                event = _make_market_event(ts)
                result = strategy.on_step(event, portfolio, state)
                assert len(result) == 1, f"Expected intent on {d} but got none"

        # Should have been called 5 times (Mon-Fri)
        assert mock_llm.decide.call_count == 5

    def test_no_duplicate_decisions_same_day(self):
        """Two on_step calls same day -> only first triggers LLM."""
        strategy = _create_strategy(rebalance_frequency="weekly", rebalance_weekday=0)

        mock_llm = MagicMock()
        mock_llm.decide.return_value = [
            {
                "action": "buy",
                "instrument_type": "option",
                "contract": "O:SPY250120C00595000",
                "qty": 1,
                "order_type": "MARKET",
            },
        ]
        strategy._llm = mock_llm

        # 2025-01-06 is a Monday
        state = _make_state("2025-01-06T09:31:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-06T09:31:00")

        with patch("trading.tools.upq_tools.query_option_chain", return_value=[]):
            result1 = strategy.on_step(event, portfolio, state)
            result2 = strategy.on_step(event, portfolio, state)

        assert len(result1) == 1
        assert result2 == []
        assert mock_llm.decide.call_count == 1

    def test_chain_query_called_on_decision(self):
        """Verify UPQ chain query is called before the LLM call."""
        strategy = _create_strategy(rebalance_frequency="weekly", rebalance_weekday=0)

        mock_llm = MagicMock()
        mock_llm.decide.return_value = []
        strategy._llm = mock_llm

        # 2025-01-06 is a Monday
        state = _make_state("2025-01-06T09:31:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-06T09:31:00")

        with patch("trading.tools.upq_tools.query_option_chain", return_value=[]) as mock_chain:
            strategy.on_step(event, portfolio, state)

        # Called for each ticker (SPY) x each right (C, P) = 2 calls
        assert mock_chain.call_count == 2
        # Verify it was called with the right parameters
        call_args = mock_chain.call_args_list[0]
        assert call_args.kwargs["underlying"] == "SPY"
        assert call_args.kwargs["trade_date"] == "2025-01-06"

    def test_llm_failure_returns_empty(self):
        """LLM raises -> returns [], no crash."""
        strategy = _create_strategy(rebalance_frequency="weekly", rebalance_weekday=0)

        mock_llm = MagicMock()
        mock_llm.decide.side_effect = RuntimeError("API timeout")
        strategy._llm = mock_llm

        # 2025-01-06 is a Monday
        state = _make_state("2025-01-06T09:31:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-06T09:31:00")

        with patch("trading.tools.upq_tools.query_option_chain", return_value=[]):
            result = strategy.on_step(event, portfolio, state)

        assert result == []


class TestActionsToIntents:
    """Test the static action-to-intent conversion."""

    def test_buy_action(self):
        actions = [{
            "action": "buy",
            "contract": "O:SPY250120C00595000",
            "qty": 5,
            "order_type": "MARKET",
            "rationale": "Bull call",
        }]
        intents = WeeklyLLMStrategy._actions_to_intents(actions)
        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].symbol == "SPY"

    def test_sell_action(self):
        actions = [{
            "action": "sell",
            "contract": "O:AAPL250620P00200000",
            "qty": 3,
            "order_type": "LIMIT",
            "limit_price": 2.50,
            "rationale": "Close position",
        }]
        intents = WeeklyLLMStrategy._actions_to_intents(actions)
        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert intents[0].symbol == "AAPL"
        assert intents[0].limit_price == 2.50

    def test_spread_group_preserved(self):
        actions = [
            {
                "action": "buy",
                "contract": "O:SPY250120C00595000",
                "qty": 5,
                "order_type": "MARKET",
                "spread_group": "A",
            },
            {
                "action": "sell",
                "contract": "O:SPY250120C00600000",
                "qty": 5,
                "order_type": "MARKET",
                "spread_group": "A",
            },
        ]
        intents = WeeklyLLMStrategy._actions_to_intents(actions)
        assert len(intents) == 2
        assert all(i.spread_group == "A" for i in intents)
        assert intents[0].side == "BUY"
        assert intents[1].side == "SELL"


class TestPortfolioToAccount:
    """Tests for WeeklyLLMStrategy._portfolio_to_account serialisation."""

    def test_preserves_signed_short_qty(self):
        """SHORT positions must preserve negative qty in the prompt dict.

        PositionInfo.qty is SIGNED: negative for SHORT.  The serialisation
        method must NOT double-negate it (i.e. must not do ``-pos.qty`` for
        SHORT positions).
        """
        strategy = _create_strategy()
        short_pos = PositionInfo(
            contract="O:SPY250120P00580000",
            side="SHORT",
            qty=-3,
            avg_entry=1.50,
            mark_price=1.20,
            unrealized_pnl=90.0,
            dte=14,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[short_pos])
        account = strategy._portfolio_to_account(portfolio)
        assert account["positions"][0]["qty"] == -3, (
            "SHORT qty=-3 must remain -3 (not be double-negated to +3)"
        )

    def test_preserves_positive_long_qty(self):
        """LONG positions must preserve their positive qty unchanged."""
        strategy = _create_strategy()
        long_pos = PositionInfo(
            contract="O:SPY250120C00595000",
            side="LONG",
            qty=5,
            avg_entry=2.00,
            mark_price=2.50,
            unrealized_pnl=250.0,
            dte=14,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[long_pos])
        account = strategy._portfolio_to_account(portfolio)
        assert account["positions"][0]["qty"] == 5
