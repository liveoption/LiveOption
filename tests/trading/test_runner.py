"""Tests for TradingRunner — pipeline orchestration.

Uses unittest.mock extensively to avoid real HTTP calls.  A MockStrategy
returns predefined intents so we can verify pipeline ordering and
lifecycle hooks.
"""

from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trading.agents.strategy.base import BaseStrategy
from trading.core.config import (
    DataPolicyConfig,
    EvaluationConfig,
    ExecutionConfig,
    ExperimentConfig,
    RiskConfig,
    RunConfig,
    StrategyConfig,
)
from trading.core.registry import STRATEGY_REGISTRY, register_strategy
from trading.core.schemas import (
    MarketEvent,
    PortfolioSnapshot,
    TradeIntent,
)
from trading.core.state import TradingState
from trading.runner import TradingRunner


# ---------------------------------------------------------------------------
# Helpers — mock strategy that records lifecycle calls
# ---------------------------------------------------------------------------


class MockStrategy(BaseStrategy):
    """Strategy that records lifecycle calls and returns canned intents."""

    def __init__(self, intents_per_step: dict[int, list] | None = None, **kwargs):
        self.calls: list[str] = []
        self._intents_per_step = intents_per_step or {}
        self._step_counter = 0

    def on_session_start(self, config):
        self.calls.append("on_session_start")

    def on_session_end(self, state):
        self.calls.append("on_session_end")

    def on_day_open(self, date, state):
        self.calls.append(f"on_day_open:{date}")

    def on_day_close(self, date, state):
        self.calls.append(f"on_day_close:{date}")

    def on_step(self, market_event, portfolio, state):
        self._step_counter += 1
        self.calls.append(f"on_step:{self._step_counter}")
        return self._intents_per_step.get(self._step_counter, [])

    def save_checkpoint(self):
        return {}

    def load_checkpoint(self, state):
        pass


# Register mock strategy for tests
STRATEGY_REGISTRY["mock_test"] = MockStrategy


def _make_config(tmpdir: str, frequency: str = "1m") -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test_run",
            start="2025-01-06",
            end="2025-01-10",
            universe=["SPY"],
            frequency=frequency,
        ),
        strategy=StrategyConfig(name="mock_test", params={}),
        risk=RiskConfig(flatten_before_close=False),
        execution=ExecutionConfig(),
        data_policy=DataPolicyConfig(),
        evaluation=EvaluationConfig(output_dir=tmpdir),
    )


def _make_step_result(
    current_ts: str = "2025-01-06T09:31:00-05:00",
    status: str = "RUNNING",
    next_ts: str | None = None,
    positions: list | None = None,
) -> dict:
    """Build a PMB step result; January market-clock fixtures use EST."""
    result: dict[str, Any] = {
        "ok": True,
        "clock": {
            "current_ts": current_ts,
            "status": status,
        },
        "events": [
            {
                "type": "ACCOUNT_SNAPSHOT",
                "ts": current_ts,
                "payload": {
                    "cash_available": 90000.0,
                    "equity": 100000.0,
                    "daily_pnl": 0.0,
                    "daily_pnl_pct": 0.0,
                    "positions": positions or [],
                },
            },
            {
                "type": "MARKET_TICK",
                "ts": current_ts,
                "payload": {
                    "stocks": [
                        {
                            "symbol": "SPY",
                            "open": 595.0,
                            "high": 596.0,
                            "low": 594.0,
                            "close": 595.5,
                            "volume": 1000000,
                        }
                    ],
                    "options": [],
                },
            },
        ],
    }
    if next_ts is not None:
        result["clock"]["next_ts"] = next_ts
    return result


def _make_finished_step(current_ts: str = "2025-01-10T16:00:00-05:00") -> dict:
    """Build a FINISHED step result."""
    return _make_step_result(current_ts=current_ts, status="FINISHED")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunnerCreatesSession:
    def test_runner_creates_session_with_correct_frequency(self):
        """Verify PMB session is created with the config frequency."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir, frequency="1m")
            runner = TradingRunner(config)

            # Mock all PMB calls
            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}
            runner.pmb.step.return_value = _make_finished_step()

            runner.run()

            # Verify create_session called with frequency="1m"
            runner.pmb.create_session.assert_called_once()
            call_kwargs = runner.pmb.create_session.call_args
            assert call_kwargs.kwargs.get("frequency") == "1m" or (
                call_kwargs.args and "1m" in str(call_kwargs)
            )


class TestRunnerPipelineOrder:
    def test_runner_pipeline_order(self):
        """Verify agents are called in the correct pipeline order per step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            call_order: list[str] = []

            # Mock PMB
            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            # Two steps: one RUNNING, one FINISHED
            runner.pmb.step.side_effect = [
                _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING"),
                _make_finished_step("2025-01-06T09:32:00-05:00"),
            ]

            # Wrap agents to track call order
            orig_md = runner.market_data.process_step
            orig_ef = runner.execution.extract_fills
            orig_so = runner.execution.submit_orders

            def track_market_data(step_result):
                call_order.append("market_data")
                return orig_md(step_result)

            def track_extract_fills(step_result):
                call_order.append("extract_fills")
                return orig_ef(step_result)

            def track_submit_orders(decisions, state):
                call_order.append("submit_orders")
                return orig_so(decisions, state)

            runner.market_data.process_step = track_market_data
            runner.execution.extract_fills = track_extract_fills
            runner.execution.submit_orders = track_submit_orders

            # Wrap strategy to track
            orig_on_step = runner.strategy.on_step

            def track_strategy(me, p, s):
                call_order.append("strategy")
                return orig_on_step(me, p, s)

            runner.strategy.on_step = track_strategy

            # Wrap risk gate
            orig_eval = runner.risk_gate.evaluate

            def track_risk(intents, portfolio, state):
                call_order.append("risk_gate")
                return orig_eval(intents, portfolio, state)

            runner.risk_gate.evaluate = track_risk

            runner.run()

            # Each step should have: market_data -> extract_fills -> strategy -> risk_gate -> submit_orders
            # We have 2 steps (one RUNNING, one FINISHED where session_running becomes False)
            # Actually both steps execute the pipeline body, since session_running is set
            # from the step result _after_ calling step.
            # Step 1: RUNNING step processes fully.
            # Step 2: FINISHED step - session_running=False is set, but the pipeline body
            # executes for that step too (loop condition checked at top).
            # So we should see the pattern twice.
            assert call_order.count("market_data") >= 1
            assert call_order.count("strategy") >= 1

            # Verify ordering within a single step
            first_md = call_order.index("market_data")
            first_ef = call_order.index("extract_fills")
            first_strat = call_order.index("strategy")
            first_risk = call_order.index("risk_gate")
            first_submit = call_order.index("submit_orders")

            assert first_md < first_strat
            assert first_ef < first_strat
            assert first_strat < first_risk
            assert first_risk < first_submit


class TestRunnerDayBoundary:
    def test_runner_day_boundary_detection(self):
        """is_new_day should be True when date changes between steps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            observed_new_days: list[bool] = []

            # 3 steps: day 1, day 1, day 2 (new_day), then FINISHED
            runner.pmb.step.side_effect = [
                _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING"),
                _make_step_result("2025-01-06T15:59:00-05:00", "RUNNING",
                                  next_ts="2025-01-07T09:31:00-05:00"),
                _make_step_result("2025-01-07T09:31:00-05:00", "RUNNING"),
                _make_finished_step("2025-01-07T09:32:00-05:00"),
            ]

            # Track is_new_day through the strategy
            orig_on_step = runner.strategy.on_step

            def capture_new_day(me, p, state):
                observed_new_days.append(state.is_new_day)
                return orig_on_step(me, p, state)

            runner.strategy.on_step = capture_new_day

            runner.run()

            # Step 1: prev_date=None -> is_new_day=True (first day counts)
            # Step 2: same day -> is_new_day=False
            # Step 3: date changed -> is_new_day=True
            # Step 4: FINISHED -> early break, no on_step called
            assert len(observed_new_days) == 3
            assert observed_new_days[0] is True
            assert observed_new_days[1] is False
            assert observed_new_days[2] is True

    def test_runner_calls_on_day_open(self):
        """strategy.on_day_open should be called at day boundary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            runner.pmb.step.side_effect = [
                _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING"),
                _make_step_result("2025-01-07T09:31:00-05:00", "RUNNING"),
                _make_finished_step("2025-01-07T09:32:00-05:00"),
            ]

            runner.run()

            mock_strat = runner.strategy
            assert "on_day_open:2025-01-07" in mock_strat.calls

    def test_runner_calls_on_day_close(self):
        """strategy.on_day_close should be called at last bar of day."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            # Step 1 has next_ts on a different day -> last bar of day
            runner.pmb.step.side_effect = [
                _make_step_result(
                    "2025-01-06T15:59:00-05:00",
                    "RUNNING",
                    next_ts="2025-01-07T09:31:00-05:00",
                ),
                _make_finished_step("2025-01-07T09:31:00-05:00"),
            ]

            runner.run()

            mock_strat = runner.strategy
            assert "on_day_close:2025-01-06" in mock_strat.calls


class TestRunnerChainRefresh:
    def test_runner_chain_refresh_at_day_open(self):
        """Chain refresh should be triggered when a new day is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            runner.pmb.step.side_effect = [
                _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING"),
                _make_step_result("2025-01-07T09:31:00-05:00", "RUNNING"),
                _make_finished_step("2025-01-07T09:32:00-05:00"),
            ]

            # Patch the UPQ chain query to track calls
            with patch("trading.runner.query_option_chain", return_value=[]) as mock_chain:
                runner.run()

                # Minute sessions prefetch at creation, then register at
                # initial refresh and refresh again on the next day.
                # Each pass queries SPY calls and puts.
                assert mock_chain.call_count == 6

    def test_chain_refresh_clears_old_contracts(self):
        """After chain refresh, old contracts should be unregistered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"

            # Initial refresh loads contract A; day-2 refresh loads contract B
            old_contract = "O:SPY250106C00595000"
            new_contract = "O:SPY250107C00600000"
            runner.pmb.add_contracts.side_effect = [
                {"ok": True, "loaded": 1, "loaded_contracts": [old_contract]},
                {"ok": True, "loaded": 1, "loaded_contracts": [new_contract]},
            ]

            runner.pmb.step.side_effect = [
                _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING"),
                _make_step_result("2025-01-07T09:31:00-05:00", "RUNNING"),
                _make_finished_step("2025-01-07T09:32:00-05:00"),
            ]

            with patch("trading.runner.query_option_chain") as mock_chain:
                # Return a contract that will pass delta filter
                mock_chain.return_value = [
                    {"ticker": old_contract, "delta": 0.40, "volume": 100},
                ]

                # Override for second call to return new contract
                def chain_side_effect(**kwargs):
                    if kwargs.get("trade_date") == "2025-01-07":
                        return [{"ticker": new_contract, "delta": 0.40, "volume": 100}]
                    return [{"ticker": old_contract, "delta": 0.40, "volume": 100}]

                mock_chain.side_effect = chain_side_effect

                runner.run()

            # After the day-2 refresh, old_contract should NOT be tracked
            assert old_contract not in runner.market_data._tracked_contracts
            assert old_contract not in runner.market_data._instrument_state
            # new_contract SHOULD be tracked
            assert new_contract in runner.market_data._tracked_contracts


class TestRunnerSessionEnd:
    def test_runner_stops_when_session_finished(self):
        """Loop should exit when PMB clock status is FINISHED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            runner.pmb.step.side_effect = [
                _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING"),
                _make_step_result("2025-01-06T09:32:00-05:00", "RUNNING"),
                _make_finished_step("2025-01-06T09:33:00-05:00"),
            ]

            result = runner.run()

            # Should have processed 3 steps (the FINISHED one still runs through
            # the loop body but session_running becomes False)
            assert result["total_steps"] == 3
            assert runner.pmb.step.call_count == 3


class TestFinishedStepNoStrategyExecution:
    def test_finished_step_no_strategy_execution(self):
        """FINISHED step should NOT call strategy.on_step or submit_orders."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            on_step_count = 0

            # Two steps: one RUNNING (calls on_step), one FINISHED (should NOT)
            runner.pmb.step.side_effect = [
                _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING"),
                _make_finished_step("2025-01-06T09:32:00-05:00"),
            ]

            orig_on_step = runner.strategy.on_step

            def counting_on_step(me, p, s):
                nonlocal on_step_count
                on_step_count += 1
                return orig_on_step(me, p, s)

            runner.strategy.on_step = counting_on_step

            # Track risk gate calls
            risk_call_count = 0
            orig_eval = runner.risk_gate.evaluate

            def counting_eval(intents, portfolio, state):
                nonlocal risk_call_count
                risk_call_count += 1
                return orig_eval(intents, portfolio, state)

            runner.risk_gate.evaluate = counting_eval

            runner.run()

            # strategy.on_step should only be called once (for the RUNNING step)
            assert on_step_count == 1
            # risk_gate.evaluate should only be called once (for the RUNNING step)
            assert risk_call_count == 1


class TestRunnerPortfolioExtraction:
    def test_runner_portfolio_extracted_from_snapshot(self):
        """Portfolio should be parsed from ACCOUNT_SNAPSHOT event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            positions = [
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
                }
            ]

            captured_portfolios: list[PortfolioSnapshot] = []

            runner.pmb.step.side_effect = [
                _make_step_result(
                    "2025-01-06T09:31:00-05:00", "RUNNING", positions=positions
                ),
                _make_finished_step("2025-01-06T09:32:00-05:00"),
            ]

            orig_on_step = runner.strategy.on_step

            def capture_portfolio(me, portfolio, state):
                captured_portfolios.append(state.portfolio)
                return orig_on_step(me, portfolio, state)

            runner.strategy.on_step = capture_portfolio

            runner.run()

            # First step should have portfolio with positions
            assert len(captured_portfolios) >= 1
            p = captured_portfolios[0]
            assert p.cash == 90000.0
            assert p.equity == 100000.0
            assert len(p.positions) == 1
            assert p.positions[0].contract == "O:SPY250106C00595000"
            assert p.positions[0].qty == 5

    def test_runner_portfolio_from_pmb_field_names(self):
        """Portfolio should be parsed from PMB's native field names.

        PMB Position uses instrument_id/avg_price/qty (signed);
        _extract_portfolio must map these to contract/avg_entry/side.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            # Positions using PMB native field names
            pmb_positions = [
                {
                    "instrument_id": "OPTION:O:SPY250106C00595000",
                    "type": "OPTION",
                    "qty": 5,
                    "avg_price": 2.30,
                    "mark_price": 2.85,
                    "unrealized_pnl": 275.0,
                    "multiplier": 100,
                },
                {
                    "instrument_id": "OPTION:O:SPY250106P00580000",
                    "type": "OPTION",
                    "qty": -3,
                    "avg_price": 1.50,
                    "mark_price": 1.20,
                    "unrealized_pnl": 90.0,
                    "multiplier": 100,
                },
            ]

            captured_portfolios: list[PortfolioSnapshot] = []

            runner.pmb.step.side_effect = [
                _make_step_result(
                    "2025-01-06T09:31:00-05:00", "RUNNING", positions=pmb_positions
                ),
                _make_finished_step("2025-01-06T09:32:00-05:00"),
            ]

            orig_on_step = runner.strategy.on_step

            def capture_portfolio(me, portfolio, state):
                captured_portfolios.append(state.portfolio)
                return orig_on_step(me, portfolio, state)

            runner.strategy.on_step = capture_portfolio

            runner.run()

            assert len(captured_portfolios) >= 1
            p = captured_portfolios[0]
            assert len(p.positions) == 2

            # Long position from PMB instrument_id
            long_pos = next(
                pos for pos in p.positions
                if "C00595000" in pos.contract
            )
            assert long_pos.contract == "O:SPY250106C00595000"
            assert long_pos.side == "LONG"
            assert long_pos.qty == 5
            assert long_pos.avg_entry == 2.30

            # Short position (negative qty -> side=SHORT)
            short_pos = next(
                pos for pos in p.positions
                if "P00580000" in pos.contract
            )
            assert short_pos.contract == "O:SPY250106P00580000"
            assert short_pos.side == "SHORT"
            assert short_pos.qty == -3
            assert short_pos.avg_entry == 1.50


class TestRunnerDailyPnlComputation:
    """Issue 3: daily_pnl should be computed from start-of-day equity."""

    def test_daily_pnl_computed_from_equity_delta(self):
        """daily_pnl and daily_pnl_pct are computed when PMB doesn't provide them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            runner = TradingRunner(config)

            runner.pmb = MagicMock()
            runner.pmb.create_account.return_value = "acct_1"
            runner.pmb.create_session.return_value = "sess_1"
            runner.pmb.add_contracts.return_value = {"loaded_contracts": []}

            # First step: equity = 100000 (start of day)
            # Second step: equity = 99000 (loss of $1000 = -1%)
            step1 = _make_step_result("2025-01-06T09:31:00-05:00", "RUNNING")
            step2 = {
                "ok": True,
                "clock": {
                    "current_ts": "2025-01-06T09:32:00-05:00",
                    "status": "RUNNING",
                },
                "events": [
                    {
                        "type": "ACCOUNT_SNAPSHOT",
                        "ts": "2025-01-06T09:32:00-05:00",
                        "payload": {
                            "cash_available": 89000.0,
                            "equity": 99000.0,
                            # Note: no daily_pnl or daily_pnl_pct from PMB
                            "positions": [],
                        },
                    },
                    {
                        "type": "MARKET_TICK",
                        "ts": "2025-01-06T09:32:00-05:00",
                        "payload": {"stocks": [], "options": []},
                    },
                ],
            }
            step_fin = _make_finished_step("2025-01-06T09:33:00-05:00")

            runner.pmb.step.side_effect = [step1, step2, step_fin]

            captured_portfolios: list[PortfolioSnapshot] = []
            orig_on_step = runner.strategy.on_step

            def capture_portfolio(me, portfolio, state):
                captured_portfolios.append(state.portfolio)
                return orig_on_step(me, portfolio, state)

            runner.strategy.on_step = capture_portfolio

            runner.run()

            # First step establishes start_of_day equity = 100000
            # Second step: equity=99000, daily_pnl = 99000-100000 = -1000
            assert len(captured_portfolios) >= 2
            p2 = captured_portfolios[1]
            assert p2.daily_pnl == pytest.approx(-1000.0)
            assert p2.daily_pnl_pct == pytest.approx(-0.01)
