"""Tests for the EarningsBetStrategy."""

import json
from unittest.mock import MagicMock, patch

import pytest

from trading.agents.strategy.earnings_bet import (
    EarningsBetStrategy,
    EventWindow,
    _compute_trading_days,
    build_event_windows,
)
from trading.core.config import (
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


def _make_config(start="2025-01-01", end="2025-12-31", universe=None):
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test_earnings",
            start=start,
            end=end,
            universe=universe or ["AAPL", "NVDA"],
            frequency="1d",
        ),
        strategy=StrategyConfig(name="earnings_bet", params={}),
    )


def _make_state(current_ts: str, config=None) -> TradingState:
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
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        positions=positions or [],
    )


def _make_market_event(ts="2025-01-06T09:31:00"):
    bar = BarData(
        symbol="AAPL",
        open=195.0,
        high=196.0,
        low=194.0,
        close=195.5,
        volume=1_000_000,
        window_start=ts,
    )
    return MarketEvent(event_ts=ts, bars=[bar], bar_history={})


MOCK_EARNINGS_EVENTS = [
    {
        "ticker": "AAPL",
        "upq_ticker": "AAPL",
        "date": "2025-01-30",
        "time": "16:30:00",
        "fiscal_period": "Q1",
        "fiscal_year": "2025",
        "estimated_eps": 2.35,
        "actual_eps": 2.40,
        "eps_surprise_percent": 2.13,
        "actual_revenue": 124_000_000_000,
        "estimated_revenue": 122_000_000_000,
        "is_bmo": False,
        "importance": 5,
    },
]

MOCK_ANALYZER_RESPONSE = json.dumps({
    "prediction": {
        "direction": "bullish",
        "expected_eps_surprise": "beat",
        "expected_move_pct": 5.0,
        "confidence": "medium",
        "thesis": "Strong momentum and historical beat pattern.",
    },
    "target_instruments": [
        "BUY CALL K=195-205 DTE=7-14 QTY=5",
    ],
})

MOCK_EXECUTOR_RESPONSE = json.dumps([
    {
        "action": "buy",
        "instrument_type": "option",
        "contract": "O:AAPL250214C00200000",
        "qty": 5,
        "order_type": "MARKET",
        "rationale": "Bullish earnings play",
    }
])

MOCK_POST_ANALYZER_RESPONSE = json.dumps({
    "assessment": {
        "direction_correct": True,
        "current_pnl_view": "profitable",
        "post_earnings_trend": "continuing",
        "iv_environment": "crushed",
        "decision": "exit",
        "reasoning": "Take profits after strong earnings beat.",
    },
    "target_instruments": [
        "CLOSE LONG O:AAPL250214C00200000 QTY=5",
    ],
    "stop": False,
})

MOCK_POST_EXECUTOR_RESPONSE = json.dumps([
    {
        "action": "sell",
        "instrument_type": "option",
        "contract": "O:AAPL250214C00200000",
        "qty": 5,
        "order_type": "MARKET",
        "rationale": "Take profits after strong earnings beat",
    }
])

MOCK_HOLD_ANALYZER_RESPONSE = json.dumps({
    "prediction": {
        "direction": "neutral",
        "expected_eps_surprise": "inline",
        "expected_move_pct": 0.5,
        "confidence": "low",
        "thesis": "No clear direction.",
    },
    "target_instruments": [],
})


def _create_strategy(ssh_host=None, llm_provider="mock"):
    """Create an EarningsBetStrategy with mock dependencies."""
    strategy = EarningsBetStrategy(
        llm_provider=llm_provider,
        llm_model="mock-model",
        max_position_cost=5000,
        max_contracts=10,
        auto_close_at_t2=True,
        dte_min=7,
        dte_max=45,
        upq_base_url="http://localhost:19350",
        # Deliberately dead port: tests must stay hermetic even when a live
        # ESP service is reachable at the real default endpoint.
        npp_base_url="http://127.0.0.1:1",
        ssh_host=ssh_host,
    )
    return strategy


# ---------------------------------------------------------------------------
# Trading day computation
# ---------------------------------------------------------------------------


class TestComputeTradingDays:
    def test_t_minus_2_skips_weekend(self):
        # 2025-01-30 is Thursday; T-2 = Tuesday Jan 28
        assert _compute_trading_days("2025-01-30", -2) == "2025-01-28"

    def test_t_plus_2_skips_weekend(self):
        # 2025-01-30 is Thursday; T+2 = Monday Feb 3
        assert _compute_trading_days("2025-01-30", 2) == "2025-02-03"

    def test_t_minus_2_across_weekend(self):
        # 2025-02-03 is Monday; T-2 = Thursday Jan 30
        assert _compute_trading_days("2025-02-03", -2) == "2025-01-30"

    def test_t_plus_1(self):
        # 2025-01-30 is Thursday; T+1 = Friday Jan 31
        assert _compute_trading_days("2025-01-30", 1) == "2025-01-31"


# ---------------------------------------------------------------------------
# Event window tests
# ---------------------------------------------------------------------------


class TestEventWindow:
    def test_build_event_windows(self):
        windows = build_event_windows(MOCK_EARNINGS_EVENTS)
        assert len(windows) == 1
        w = windows[0]
        assert w.ticker == "AAPL"
        assert w.earnings_date == "2025-01-30"
        assert w.is_bmo is False
        assert w.t_minus_2 == "2025-01-28"  # Tuesday
        assert w.t_minus_1 == "2025-01-29"  # Wednesday
        assert w.t_plus_1 == "2025-01-31"  # Friday
        assert w.t_plus_2 == "2025-02-03"  # Monday (skip weekend)

    def test_contains_date(self):
        windows = build_event_windows(MOCK_EARNINGS_EVENTS)
        w = windows[0]
        assert w.contains_date("2025-01-28") is True  # T-2
        assert w.contains_date("2025-01-30") is True  # T
        assert w.contains_date("2025-02-03") is True  # T+2
        assert w.contains_date("2025-01-27") is False  # before window
        assert w.contains_date("2025-02-04") is False  # after window

    def test_phase_pre_for_amc(self):
        """AMC event: T is skipped because results arrive after market close."""
        windows = build_event_windows(MOCK_EARNINGS_EVENTS)
        w = windows[0]
        assert w.get_phase_for_date("2025-01-28") == "pre"  # T-2
        assert w.get_phase_for_date("2025-01-29") == "pre"  # T-1
        assert w.get_phase_for_date("2025-01-30") == "skip"

    def test_phase_post_for_amc(self):
        """AMC event: T+1 and T+2 are post-earnings."""
        windows = build_event_windows(MOCK_EARNINGS_EVENTS)
        w = windows[0]
        assert w.get_phase_for_date("2025-01-31") == "post"  # T+1
        assert w.get_phase_for_date("2025-02-03") == "post"  # T+2

    def test_phase_bmo(self):
        """BMO event: earnings day itself is post-earnings."""
        bmo_event = [
            {
                "ticker": "JPM",
                "upq_ticker": "JPM",
                "date": "2025-01-14",
                "time": "07:00:00",
                "is_bmo": True,
                "fiscal_period": "Q4",
                "fiscal_year": "2024",
                "estimated_eps": 4.0,
                "actual_eps": 4.2,
                "eps_surprise_percent": 5.0,
                "actual_revenue": None,
                "estimated_revenue": None,
                "importance": 5,
            }
        ]
        windows = build_event_windows(bmo_event)
        w = windows[0]
        assert w.get_phase_for_date(w.t_minus_2) == "pre"
        assert w.get_phase_for_date(w.t_minus_1) == "pre"
        # BMO: earnings day is post (results known at open)
        assert w.get_phase_for_date("2025-01-14") == "post"

    def test_days_to_earnings(self):
        windows = build_event_windows(MOCK_EARNINGS_EVENTS)
        w = windows[0]
        assert w.days_to_earnings("2025-01-28") == 2
        assert w.days_to_earnings("2025-01-30") == 0

    def test_outside_phase(self):
        windows = build_event_windows(MOCK_EARNINGS_EVENTS)
        w = windows[0]
        assert w.get_phase_for_date("2025-01-20") == "outside"
        assert w.get_phase_for_date("2025-02-10") == "outside"


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------


class TestEarningsBetStrategy:
    def test_returns_empty_outside_window(self):
        """No event window active => no intents."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        # Manually set windows with a specific date range
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        # Date outside any window
        state = _make_state("2025-01-15T09:31:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-15T09:31:00")

        result = strategy.on_step(event, portfolio, state)
        assert result == []

    def test_pre_earnings_entry(self):
        """T-2 within window => analyzer + executor called for entry."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        # Mock LLM: first call = analyzer, second call = executor
        mock_llm = MagicMock()
        mock_llm.call_raw.side_effect = [MOCK_ANALYZER_RESPONSE, MOCK_EXECUTOR_RESPONSE]
        strategy._llm = mock_llm

        # T-2 is 2025-01-28
        state = _make_state("2025-01-28T20:35:00+00:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-28T20:35:00+00:00")

        with patch(
            "trading.tools.upq_tools.query_option_chain", return_value=[]
        ):
            result = strategy.on_step(event, portfolio, state)

        assert len(result) == 1
        intent = result[0]
        assert isinstance(intent, TradeIntent)
        assert intent.contract == "O:AAPL250214C00200000"
        assert intent.side == "BUY"
        assert intent.qty == 5
        assert intent.strategy_name == "earnings_bet"
        assert mock_llm.call_raw.call_count == 2  # analyzer + executor

    def test_post_earnings_management(self):
        """After earnings, strategy manages positions via analyzer-executor."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        # Simulate: entry was made, now in post-earnings
        w = strategy._event_windows[0]
        w.entry_decided = True
        w.positions_opened = True
        w.phase = "post"

        # Create portfolio with open position
        position = PositionInfo(
            contract="O:AAPL250214C00200000",
            side="LONG",
            qty=5,
            avg_entry=3.50,
            mark_price=5.00,
            unrealized_pnl=750.0,
            dte=17,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[position])

        mock_llm = MagicMock()
        # First call = post analyzer, second call = executor
        mock_llm.call_raw.side_effect = [MOCK_POST_ANALYZER_RESPONSE, MOCK_POST_EXECUTOR_RESPONSE]
        strategy._llm = mock_llm

        # T+1 = 2025-01-31
        state = _make_state("2025-01-31T09:31:00")
        event = _make_market_event("2025-01-31T09:31:00")

        with patch(
            "trading.tools.upq_tools.query_option_chain", return_value=[]
        ):
            result = strategy.on_step(event, portfolio, state)

        assert len(result) == 1
        assert result[0].side == "SELL"
        assert result[0].qty == 5
        assert mock_llm.call_raw.call_count == 2  # analyzer + executor

    def test_auto_close_at_t2(self):
        """Positions are force-closed at T+2."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        w = strategy._event_windows[0]
        w.entry_decided = True
        w.positions_opened = True
        w.phase = "post"

        position = PositionInfo(
            contract="O:AAPL250214C00200000",
            side="LONG",
            qty=5,
            avg_entry=3.50,
            mark_price=2.00,
            unrealized_pnl=-750.0,
            dte=11,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[position])

        # T+2 = 2025-02-03
        state = _make_state("2025-02-03T09:31:00")
        event = _make_market_event("2025-02-03T09:31:00")

        result = strategy.on_step(event, portfolio, state)

        assert len(result) == 1
        assert result[0].side == "SELL"  # close long position
        assert result[0].qty == 5
        assert "auto-close" in result[0].rationale.lower()
        assert w.phase == "closed"

    def test_skip_event_on_hold(self):
        """Analyzer returns empty targets at T-2 => event is marked as decided, no positions opened."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        mock_llm = MagicMock()
        # Both analyzer calls (initial + retry) return empty targets
        mock_llm.call_raw.return_value = MOCK_HOLD_ANALYZER_RESPONSE
        strategy._llm = mock_llm

        # T-2 = 2025-01-28
        state = _make_state("2025-01-28T20:35:00+00:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-28T20:35:00+00:00")

        with patch(
            "trading.tools.upq_tools.query_option_chain", return_value=[]
        ):
            result = strategy.on_step(event, portfolio, state)

        assert result == []
        w = strategy._event_windows[0]
        assert w.entry_decided is True
        assert w.positions_opened is False

    def test_no_duplicate_decisions_same_day(self):
        """Two on_step calls same day => only first triggers LLM."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        mock_llm = MagicMock()
        # First call = analyzer, second call = executor (only on first on_step)
        mock_llm.call_raw.side_effect = [MOCK_ANALYZER_RESPONSE, MOCK_EXECUTOR_RESPONSE]
        strategy._llm = mock_llm

        state = _make_state("2025-01-28T20:35:00+00:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-28T20:35:00+00:00")

        with patch(
            "trading.tools.upq_tools.query_option_chain", return_value=[]
        ):
            result1 = strategy.on_step(event, portfolio, state)
            result2 = strategy.on_step(event, portfolio, state)

        assert len(result1) == 1
        assert result2 == []
        assert mock_llm.call_raw.call_count == 2  # analyzer + executor (only first on_step)

    def test_force_close_short_position(self):
        """Short positions generate BUY close orders at T+2."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        w = strategy._event_windows[0]
        w.entry_decided = True
        w.positions_opened = True
        w.phase = "post"

        position = PositionInfo(
            contract="O:AAPL250214P00190000",
            side="SHORT",
            qty=3,
            avg_entry=2.00,
            mark_price=1.50,
            unrealized_pnl=150.0,
            dte=11,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[position])

        state = _make_state("2025-02-03T09:31:00")
        event = _make_market_event("2025-02-03T09:31:00")

        result = strategy.on_step(event, portfolio, state)
        assert len(result) == 1
        assert result[0].side == "BUY"  # close short position
        assert result[0].qty == 3

    def test_no_post_management_if_no_positions(self):
        """Post-earnings with no positions => close window."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        w = strategy._event_windows[0]
        w.entry_decided = True
        w.positions_opened = True
        w.phase = "post"

        portfolio = _make_portfolio()  # no positions

        state = _make_state("2025-01-31T09:31:00")
        event = _make_market_event("2025-01-31T09:31:00")

        with patch(
            "trading.tools.upq_tools.query_option_chain", return_value=[]
        ):
            result = strategy.on_step(event, portfolio, state)

        assert result == []
        assert w.phase == "closed"

    def test_earnings_calendar_loading(self):
        """Strategy loads earnings calendar on session start."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)

        # With ssh_host=None, no events are loaded
        assert strategy._event_windows == []
        assert strategy._universe == ["AAPL", "NVDA"]

    def test_close_orphan_short_stock_produces_buy(self):
        """_close_orphan_stock_positions on a SHORT stock should generate BUY, qty=100."""
        strategy = _create_strategy()
        config = _make_config(universe=["AAPL", "NVDA"])
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        # SHORT stock position (e.g., assigned from short call exercise)
        short_stock = PositionInfo(
            contract="AAPL",
            side="SHORT",
            qty=-100,
            avg_entry=195.0,
            mark_price=192.0,
            unrealized_pnl=300.0,
            dte=0,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[short_stock])

        intents = strategy._close_orphan_stock_positions(portfolio)

        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].qty == 100
        assert intents[0].contract == "AAPL"

    def test_llm_failure_returns_empty(self):
        """LLM failure => empty intents, no crash."""
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        strategy._event_windows = build_event_windows(MOCK_EARNINGS_EVENTS)

        mock_llm = MagicMock()
        mock_llm.call_raw.side_effect = RuntimeError("API timeout")
        strategy._llm = mock_llm

        state = _make_state("2025-01-28T09:31:00")
        portfolio = _make_portfolio()
        event = _make_market_event("2025-01-28T09:31:00")

        with patch(
            "trading.tools.upq_tools.query_option_chain", return_value=[]
        ):
            result = strategy.on_step(event, portfolio, state)

        assert result == []

    def test_portfolio_to_account_preserves_signed_short_qty(self):
        """SHORT positions must preserve negative qty in the prompt dict.

        PositionInfo.qty is SIGNED: negative for SHORT.  The serialisation
        method must NOT double-negate it (i.e. must not do ``-pos.qty`` for
        SHORT positions).
        """
        strategy = _create_strategy()
        config = _make_config()
        strategy.on_session_start(config)
        short_pos = PositionInfo(
            contract="O:AAPL250214P00190000",
            side="SHORT",
            qty=-3,
            avg_entry=2.00,
            mark_price=1.50,
            unrealized_pnl=150.0,
            dte=11,
            stale=False,
            stale_minutes=0,
        )
        portfolio = _make_portfolio(positions=[short_pos])
        account = strategy._portfolio_to_account(portfolio)
        assert account["positions"][0]["qty"] == -3, (
            "SHORT qty=-3 must remain -3 (not be double-negated to +3)"
        )
