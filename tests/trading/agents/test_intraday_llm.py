"""Tests for IntradayLLMStrategy and intraday prompt builder.

12 test cases covering:
- Prompt generation (6 tests)
- Strategy behaviour (6 tests)
"""

from __future__ import annotations

import json
from dataclasses import field as dataclass_field
from unittest.mock import MagicMock, patch

import pytest

from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
    TradeIntent,
)
from trading.core.config import (
    ExperimentConfig,
    RunConfig,
    StrategyConfig,
)
from trading.prompts.intraday import (
    build_intraday_system_prompt,
    build_intraday_user_prompt,
    parse_intraday_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str = "SPY",
    o: float = 595.0,
    h: float = 596.0,
    l: float = 594.0,
    c: float = 595.5,
    v: int = 1_000_000,
    ts: str = "2025-01-06T10:00:00",
    instrument_type: str = "STOCK",
    **kwargs,
) -> BarData:
    return BarData(
        symbol=symbol,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        window_start=ts,
        instrument_type=instrument_type,
        **kwargs,
    )


def _make_market_event(
    bars: list[BarData] | None = None,
    ts: str = "2025-01-06T10:00:00",
) -> MarketEvent:
    bars = bars or [_make_bar(ts=ts)]
    return MarketEvent(event_ts=ts, bars=bars, bar_history={})


def _make_portfolio(
    cash: float = 90_000.0,
    equity: float = 100_000.0,
    daily_pnl: float = 500.0,
    daily_pnl_pct: float = 0.5,
    positions: list[PositionInfo] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        positions=positions or [],
    )


def _make_state(
    current_ts: str = "2025-01-06T10:00:00",
    current_date: str = "2025-01-06",
):
    """Create a minimal TradingState-like mock."""
    from trading.core.time_contract import utc_from_iso
    state = MagicMock()
    ts_iso = current_ts if current_ts.endswith("Z") or "+" in current_ts else current_ts + "+00:00"
    state.current_ts = utc_from_iso(ts_iso)
    state.current_date = current_date
    state.errors = []
    return state


def _make_config(universe: list[str] | None = None) -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test",
            start="2025-01-06",
            end="2025-01-10",
            universe=universe or ["SPY", "QQQ"],
        ),
        strategy=StrategyConfig(name="intraday_llm"),
    )


class MockLLMClient:
    """A mock LLM client that returns a predefined JSON response."""

    def __init__(self, response: list[dict] | None = None, raise_on_call: bool = False):
        self._response = response if response is not None else []
        self._raise_on_call = raise_on_call
        self.call_count = 0

    def decide(self, system_prompt: str, user_prompt: str) -> list[dict]:
        self.call_count += 1
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        if self._raise_on_call:
            raise RuntimeError("Mock LLM error")
        return self._response


# ---------------------------------------------------------------------------
# Prompt tests (6)
# ---------------------------------------------------------------------------

class TestIntradayPrompts:
    """Tests for the intraday prompt builder functions."""

    def test_system_prompt_contains_universe(self):
        """Universe tickers appear in the system prompt."""
        prompt = build_intraday_system_prompt(
            universe=["SPY", "QQQ"],
            flatten_before_close=False,
        )
        assert "SPY" in prompt
        assert "QQQ" in prompt

    def test_system_prompt_flatten_message(self):
        """When flatten is enabled, the flatten time appears in the prompt."""
        prompt = build_intraday_system_prompt(
            universe=["SPY"],
            flatten_before_close=True,
            flatten_time="15:55",
        )
        assert "15:55" in prompt
        assert "close all positions" in prompt.lower() or "MUST close" in prompt

        # When disabled, no flatten clause
        prompt_no_flatten = build_intraday_system_prompt(
            universe=["SPY"],
            flatten_before_close=False,
        )
        assert "15:55" not in prompt_no_flatten

    def test_user_prompt_has_time_and_minutes_to_close(self):
        """User prompt contains the current time and minutes-to-close."""
        portfolio = {
            "cash": 90000,
            "equity": 100000,
            "daily_pnl": 500,
            "daily_pnl_pct": 0.5,
            "positions": [],
        }
        prompt = build_intraday_user_prompt(
            current_time="10:30",
            minutes_to_close=330,
            portfolio=portfolio,
            price_windows=[],
            chain_sample=[],
        )
        assert "10:30" in prompt
        assert "330" in prompt

    def test_user_prompt_shows_positions_with_stale_flag(self):
        """Positions display [LIVE] and [STALE Xmin] tags correctly."""
        portfolio = {
            "cash": 90000,
            "equity": 100000,
            "daily_pnl": 0,
            "daily_pnl_pct": 0,
            "positions": [
                {
                    "contract": "O:SPY250106C00595000",
                    "side": "LONG",
                    "qty": 5,
                    "avg_entry": 2.30,
                    "mark_price": 2.85,
                    "unrealized_pnl": 275.0,
                    "dte": 0,
                    "stale": False,
                    "stale_minutes": 0,
                },
                {
                    "contract": "O:QQQ250110P00500000",
                    "side": "LONG",
                    "qty": 3,
                    "avg_entry": 1.10,
                    "mark_price": 0.95,
                    "unrealized_pnl": -45.0,
                    "dte": 4,
                    "stale": True,
                    "stale_minutes": 3,
                },
            ],
        }
        prompt = build_intraday_user_prompt(
            current_time="10:00",
            minutes_to_close=360,
            portfolio=portfolio,
            price_windows=[],
            chain_sample=[],
        )
        assert "[LIVE]" in prompt
        assert "[STALE 3min]" in prompt
        assert "O:SPY250106C00595000" in prompt
        assert "O:QQQ250110P00500000" in prompt

    def test_user_prompt_shows_price_action_windows(self):
        """Aggregated N-minute OHLCV windows appear in the prompt."""
        price_windows = [
            {
                "window_idx": 1,
                "time": "10:15",
                "SPY_close": 595.20,
                "SPY_change_pct": 0.15,
                "SPY_volume": 1_200_000,
            }
        ]
        portfolio = {
            "cash": 90000, "equity": 100000,
            "daily_pnl": 0, "daily_pnl_pct": 0, "positions": [],
        }
        prompt = build_intraday_user_prompt(
            current_time="10:15",
            minutes_to_close=345,
            portfolio=portfolio,
            price_windows=price_windows,
            chain_sample=[],
            tickers=["SPY"],
        )
        assert "595.20" in prompt
        assert "1.2M" in prompt
        assert "10:15" in prompt

    def test_user_prompt_shows_available_options(self):
        """Available options with DTE, delta, IV, staleness appear in prompt."""
        chain_sample = [
            {
                "contract": "O:SPY250106C00596000",
                "dte": 0,
                "delta": 0.48,
                "iv": 0.182,
                "ask": 1.20,
                "stale": False,
                "stale_minutes": 0,
            },
            {
                "contract": "O:SPY250106P00594000",
                "dte": 0,
                "delta": -0.42,
                "iv": 0.191,
                "ask": 0.95,
                "stale": True,
                "stale_minutes": 5,
            },
        ]
        portfolio = {
            "cash": 90000, "equity": 100000,
            "daily_pnl": 0, "daily_pnl_pct": 0, "positions": [],
        }
        prompt = build_intraday_user_prompt(
            current_time="10:30",
            minutes_to_close=330,
            portfolio=portfolio,
            price_windows=[],
            chain_sample=chain_sample,
        )
        assert "O:SPY250106C00596000" in prompt
        assert "O:SPY250106P00594000" in prompt
        assert "delta=0.48" in prompt
        assert "delta=-0.42" in prompt
        assert "[LIVE]" in prompt
        assert "[STALE 5min]" in prompt
        assert "18.2%" in prompt  # IV formatted as percentage


# ---------------------------------------------------------------------------
# Parse response tests
# ---------------------------------------------------------------------------

class TestParseResponse:
    """Tests for parse_intraday_response."""

    def test_parse_valid_json(self):
        raw = json.dumps([
            {"action": "buy", "contract": "O:SPY250106C00595000", "qty": 5,
             "order_type": "MARKET", "rationale": "bullish"},
        ])
        result = parse_intraday_response(raw)
        assert len(result) == 1
        assert result[0]["action"] == "buy"

    def test_parse_empty_array(self):
        assert parse_intraday_response("[]") == []

    def test_parse_wrapped_in_code_fence(self):
        raw = '```json\n[{"action": "buy", "contract": "O:SPY250106C00595000", "qty": 2}]\n```'
        result = parse_intraday_response(raw)
        assert len(result) == 1

    def test_parse_invalid_json(self):
        assert parse_intraday_response("not json at all") == []

    def test_parse_filters_invalid_actions(self):
        raw = json.dumps([
            {"action": "buy", "contract": "O:SPY250106C00595000", "qty": 5},
            {"action": "invalid", "contract": "X", "qty": 1},
        ])
        result = parse_intraday_response(raw)
        assert len(result) == 1  # only the valid one


# ---------------------------------------------------------------------------
# Strategy tests (6)
# ---------------------------------------------------------------------------

class TestIntradayLLMStrategy:
    """Tests for the IntradayLLMStrategy class."""

    def _make_strategy(self, interval: int = 5, **kwargs):
        """Create a strategy instance with a mock LLM client."""
        from trading.agents.strategy.intraday_llm import IntradayLLMStrategy

        strat = IntradayLLMStrategy(
            decision_interval_minutes=interval,
            llm_provider="mock",
            **kwargs,
        )
        # Initialize universe directly (bypasses on_session_start LLM init)
        strat._universe = ["SPY"]
        return strat

    def test_buffers_bars_between_decisions(self):
        """Steps fewer than interval return empty list."""
        strat = self._make_strategy(interval=5)
        strat._llm = MockLLMClient()

        portfolio = _make_portfolio()
        state = _make_state()

        for i in range(4):
            result = strat.on_step(
                _make_market_event(ts=f"2025-01-06T10:{i:02d}:00"),
                portfolio,
                state,
            )
            assert result == [], f"step {i} should return empty"

        # Bar buffer should have 4 events
        assert len(strat._bar_buffer) == 4
        assert strat._steps_since_decision == 4

    def test_decides_at_interval(self):
        """After decision_interval_minutes steps, LLM is called."""
        mock_llm = MockLLMClient(response=[
            {"action": "buy", "contract": "O:SPY250106C00595000", "qty": 2,
             "order_type": "MARKET", "rationale": "test"},
        ])
        strat = self._make_strategy(interval=3)
        strat._llm = mock_llm

        portfolio = _make_portfolio()
        state = _make_state()

        # Steps 1 and 2: no decision
        for i in range(2):
            result = strat.on_step(
                _make_market_event(ts=f"2025-01-06T10:{i:02d}:00"),
                portfolio,
                state,
            )
            assert result == []

        # Step 3: decision point
        result = strat.on_step(
            _make_market_event(ts="2025-01-06T10:02:00"),
            portfolio,
            state,
        )

        assert mock_llm.call_count == 1
        assert len(result) == 1
        assert isinstance(result[0], TradeIntent)
        assert result[0].contract == "O:SPY250106C00595000"
        assert result[0].side == "BUY"
        assert result[0].qty == 2

    def test_custom_context_reaches_model_and_stock_order_is_rejected(self):
        llm = MockLLMClient(response=[
            {"action": "buy", "contract": "SPY", "qty": 1},
        ])
        strat = self._make_strategy(interval=1, prompt_override="Prefer cash when spreads widen.")
        strat._llm = llm
        result = strat.on_step(_make_market_event(), _make_portfolio(), _make_state())
        assert "Prefer cash when spreads widen." in llm.user_prompt
        assert "OPTIONS ONLY" in llm.system_prompt
        assert result == []

    def test_option_chain_includes_opra_strike_for_prompt_compression(self):
        strat = self._make_strategy()
        event = _make_market_event(bars=[
            _make_bar(symbol="O:SPY250106C00595000", instrument_type="OPTION"),
        ])
        chain = strat._build_chain_sample(event, "2025-01-06")
        assert chain[0]["strike"] == 595.0
        assert chain[0]["dte"] == 0

    def test_decision_counter_resets_after_decision(self):
        """After a decision, the step counter resets to 0."""
        mock_llm = MockLLMClient(response=[])
        strat = self._make_strategy(interval=3)
        strat._llm = mock_llm

        portfolio = _make_portfolio()
        state = _make_state()

        # Run through one full interval
        for i in range(3):
            strat.on_step(
                _make_market_event(ts=f"2025-01-06T10:{i:02d}:00"),
                portfolio,
                state,
            )

        assert strat._steps_since_decision == 0
        assert mock_llm.call_count == 1

        # Next steps should start counting again from 0
        strat.on_step(
            _make_market_event(ts="2025-01-06T10:03:00"),
            portfolio,
            state,
        )
        assert strat._steps_since_decision == 1
        assert mock_llm.call_count == 1  # not called again yet

    def test_on_day_open_resets_buffer(self):
        """on_day_open clears the bar buffer and resets the counter."""
        strat = self._make_strategy(interval=5)
        strat._llm = MockLLMClient()

        portfolio = _make_portfolio()
        state = _make_state()

        # Accumulate some bars
        for i in range(3):
            strat.on_step(
                _make_market_event(ts=f"2025-01-06T10:{i:02d}:00"),
                portfolio,
                state,
            )
        assert len(strat._bar_buffer) == 3
        assert strat._steps_since_decision == 3

        # Day open resets everything
        strat.on_day_open("2025-01-07", state)

        assert len(strat._bar_buffer) == 0
        assert strat._steps_since_decision == 0

    def test_llm_error_returns_empty(self):
        """LLM failure returns [] and logs error, no crash."""
        mock_llm = MockLLMClient(raise_on_call=True)
        strat = self._make_strategy(interval=2)
        strat._llm = mock_llm

        portfolio = _make_portfolio()
        state = _make_state()

        # Step through to decision point
        strat.on_step(
            _make_market_event(ts="2025-01-06T10:00:00"),
            portfolio,
            state,
        )
        # Patch time.sleep to avoid waiting in tests
        with patch("trading.agents.strategy.intraday_llm.time.sleep"):
            result = strat.on_step(
                _make_market_event(ts="2025-01-06T10:01:00"),
                portfolio,
                state,
            )

        assert result == []
        # Both retry attempts should have been made
        assert mock_llm.call_count == 2
        assert len(state.errors) == 1
        assert "LLM call failed" in state.errors[0]

    def test_aggregate_window_correct(self):
        """N bars are correctly aggregated into OHLCV summary."""
        strat = self._make_strategy(interval=3)

        bars = [
            _make_bar(o=100.0, h=102.0, l=99.0, c=101.0, v=1000),
            _make_bar(o=101.0, h=104.0, l=100.0, c=103.0, v=2000),
            _make_bar(o=103.0, h=105.0, l=101.0, c=104.0, v=1500),
        ]

        result = strat._aggregate_window(bars)

        assert result["open"] == 100.0     # first bar's open
        assert result["high"] == 105.0     # max of all highs
        assert result["low"] == 99.0       # min of all lows
        assert result["close"] == 104.0    # last bar's close
        assert result["volume"] == 4500    # sum of volumes

    def test_aggregate_window_empty(self):
        """Empty bar list returns zero OHLCV."""
        strat = self._make_strategy()
        result = strat._aggregate_window([])
        assert result == {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}


# ---------------------------------------------------------------------------
# Strategy integration: on_session_start
# ---------------------------------------------------------------------------

class TestIntradaySessionStart:
    """Test on_session_start initialisation."""

    def test_on_session_start_sets_universe(self):
        """on_session_start picks up universe from config."""
        from trading.agents.strategy.intraday_llm import IntradayLLMStrategy

        strat = IntradayLLMStrategy(
            decision_interval_minutes=15,
            llm_provider="mock",
        )
        config = _make_config(universe=["SPY", "QQQ", "AAPL"])

        with patch("trading.tools.llm_client.LLMClient", autospec=True):
            strat.on_session_start(config)

        assert strat._universe == ["SPY", "QQQ", "AAPL"]

    def test_chain_sample_tickers_override(self):
        """chain_sample_tickers overrides the experiment universe."""
        from trading.agents.strategy.intraday_llm import IntradayLLMStrategy

        strat = IntradayLLMStrategy(
            decision_interval_minutes=15,
            llm_provider="mock",
            chain_sample_tickers=["TSLA"],
        )
        config = _make_config(universe=["SPY", "QQQ"])

        with patch("trading.tools.llm_client.LLMClient", autospec=True):
            strat.on_session_start(config)

        assert strat._universe == ["TSLA"]


# ---------------------------------------------------------------------------
# Timezone conversion tests (Issue 5)
# ---------------------------------------------------------------------------

class TestMinutesToCloseTimezone:
    """Issue 5: UTC timestamps must be converted to ET before HH:MM extraction."""

    def test_utc_timestamp_converted_to_et(self):
        """A UTC timestamp at 14:30+00:00 = 09:30 ET -> 390 min to close."""
        from trading.agents.strategy.intraday_llm import IntradayLLMStrategy
        result = IntradayLLMStrategy._compute_minutes_to_close(
            "2025-01-06T14:30:00+00:00"
        )
        assert result == 390  # 16:00 - 09:30 = 6.5 hours = 390 min

    def test_et_naive_timestamp_assumed_et(self):
        """A naive (no timezone) timestamp is assumed ET."""
        from trading.agents.strategy.intraday_llm import IntradayLLMStrategy
        result = IntradayLLMStrategy._compute_minutes_to_close(
            "2025-01-06T09:30:00"
        )
        assert result == 390

    def test_utc_timestamp_near_close(self):
        """UTC 20:55+00:00 = 15:55 ET -> 5 min to close."""
        from trading.agents.strategy.intraday_llm import IntradayLLMStrategy
        result = IntradayLLMStrategy._compute_minutes_to_close(
            "2025-01-06T20:55:00+00:00"
        )
        assert result == 5

    def test_utc_past_close_returns_zero(self):
        """UTC 21:30+00:00 = 16:30 ET -> past close -> 0."""
        from trading.agents.strategy.intraday_llm import IntradayLLMStrategy
        result = IntradayLLMStrategy._compute_minutes_to_close(
            "2025-01-06T21:30:00+00:00"
        )
        assert result == 0

    def test_hybrid_utc_timestamp_converted(self):
        """Same fix applies to HybridIntradayStrategy."""
        from trading.agents.strategy.hybrid_intraday import HybridIntradayStrategy
        result = HybridIntradayStrategy._compute_minutes_to_close(
            "2025-01-06T14:30:00+00:00"
        )
        assert result == 390


def test_custom_daily_decides_once_on_each_daily_bar_with_own_prompts():
    from types import SimpleNamespace
    from liveoption_web.runs.protocol import RunSpecV1, materialize
    from trading.agents.strategy.intraday_llm import CustomDailyLLMStrategy

    config = materialize(RunSpecV1(mode="custom", cadence="daily", model_id="mock"), {},
                         SimpleNamespace(api_model="mock"), SimpleNamespace(provider="mock"), "daily-test")
    strategy = CustomDailyLLMStrategy(**config.strategy.params)
    with patch("trading.tools.llm_client.LLMClient") as client:
        client.return_value.decide.return_value = []
        strategy.on_session_start(config)
        for day in ("2025-01-06", "2025-01-07"):
            state = _make_state(current_ts=f"{day}T21:00:00", current_date=day)
            event = _make_market_event(ts=f"{day}T21:00:00")
            state.config = config
            state.market_event = event
            strategy.on_day_open(day, state)
            assert strategy.on_step(event, _make_portfolio(), state) == []
        assert client.return_value.decide.call_count == 2
        system, user = client.return_value.decide.call_args.args
        assert "one decision per trading day" in system
        assert "Date: 2025-01-07" in user
        assert "Minutes to close" not in user
        assert not strategy._flatten_before_close
