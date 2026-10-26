"""Full pipeline integration tests.

Exercise the entire TradingRunner pipeline end-to-end with mocked PMB and
a MockStrategy.  Four scenarios cover the core intraday and weekly flows:

1. 5 intraday minute steps with a BUY intent on step 3.
2. 3 daily steps with intents only on day 1.
3. EOD flatten window auto-generates SELL intents.
4. Stale instrument gets rejected by risk gate.
"""

from __future__ import annotations

import json
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
from trading.core.registry import STRATEGY_REGISTRY
from trading.core.schemas import (
    MarketEvent,
    PortfolioSnapshot,
    TradeIntent,
)
from trading.core.state import TradingState
from trading.runner import TradingRunner


# ---------------------------------------------------------------------------
# Mock strategy — returns canned intents keyed by step number
# ---------------------------------------------------------------------------


class IntegrationMockStrategy(BaseStrategy):
    """Strategy that returns pre-defined intents at specific steps."""

    def __init__(self, intents_by_step: dict[int, list[TradeIntent]] | None = None, **kwargs):
        self._intents_by_step = intents_by_step or {}
        self._step_counter = 0
        self.observed_steps: list[int] = []
        self.observed_intents: list[list[TradeIntent]] = []

    def on_step(self, market_event, portfolio, state):
        self._step_counter += 1
        self.observed_steps.append(self._step_counter)
        intents = self._intents_by_step.get(self._step_counter, [])
        self.observed_intents.append(intents)
        return intents

    def on_session_start(self, config):
        pass

    def on_session_end(self, state):
        pass

    def on_day_open(self, date, state):
        pass

    def on_day_close(self, date, state):
        pass

    def save_checkpoint(self):
        return {}

    def load_checkpoint(self, state):
        pass


# Register under a unique name for integration tests
STRATEGY_REGISTRY["integration_mock"] = IntegrationMockStrategy


# ---------------------------------------------------------------------------
# Helper — build step results
# ---------------------------------------------------------------------------


def _make_step(
    current_ts: str,
    status: str = "RUNNING",
    next_ts: str | None = None,
    cash: float = 90_000.0,
    equity: float = 100_000.0,
    daily_pnl: float = 0.0,
    daily_pnl_pct: float = 0.0,
    positions: list[dict] | None = None,
    stocks: list[dict] | None = None,
    options: list[dict] | None = None,
    extra_events: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a PMB step result; January market-clock fixtures use EST."""
    if stocks is None:
        stocks = [
            {
                "symbol": "SPY",
                "open": 595.0,
                "high": 596.0,
                "low": 594.0,
                "close": 595.5,
                "volume": 1_000_000,
            }
        ]
    result: dict[str, Any] = {
        "ok": True,
        "clock": {"current_ts": current_ts, "status": status},
        "events": [
            {
                "type": "ACCOUNT_SNAPSHOT",
                "ts": current_ts,
                "payload": {
                    "cash_available": cash,
                    "equity": equity,
                    "daily_pnl": daily_pnl,
                    "daily_pnl_pct": daily_pnl_pct,
                    "positions": positions or [],
                },
            },
            {
                "type": "MARKET_TICK",
                "ts": current_ts,
                "payload": {
                    "stocks": stocks,
                    "options": options or [],
                },
            },
        ],
    }
    if next_ts is not None:
        result["clock"]["next_ts"] = next_ts
    if extra_events:
        result["events"].extend(extra_events)
    return result


def _make_config(
    tmpdir: str,
    frequency: str = "1m",
    start: str = "2025-01-06T09:30:00-05:00",
    end: str = "2025-01-06T09:35:00-05:00",
    flatten_before_close: bool = False,
    max_stale_minutes: int = 10,
    strategy_name: str = "integration_mock",
    strategy_params: dict | None = None,
) -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="integ_test",
            start=start,
            end=end,
            universe=["SPY"],
            frequency=frequency,
        ),
        strategy=StrategyConfig(
            name=strategy_name,
            params=strategy_params or {},
        ),
        risk=RiskConfig(
            flatten_before_close=flatten_before_close,
            flatten_minutes_before_close=5,
            max_exposure_pct=0.5,
            max_daily_loss_pct=0.02,
            circuit_breaker_loss_pct=0.03,
            max_orders_per_day=10,
            max_position_per_symbol=100,
        ),
        execution=ExecutionConfig(),
        data_policy=DataPolicyConfig(max_stale_minutes=max_stale_minutes),
        evaluation=EvaluationConfig(output_dir=tmpdir),
    )


def _setup_runner(config: RunConfig, strategy: IntegrationMockStrategy) -> TradingRunner:
    """Build a TradingRunner with mocked PMB/UPQ and inject the given strategy."""
    runner = TradingRunner(config)

    # Replace the strategy with our mock
    runner.strategy = strategy

    # Replace PMB with a MagicMock (no real HTTP calls)
    mock_pmb = MagicMock()
    mock_pmb.create_account.return_value = "acct_1"
    mock_pmb.create_session.return_value = "sess_1"
    mock_pmb.add_contracts.return_value = {"loaded_contracts": []}
    mock_pmb.place_order.return_value = {"order_id": "ord_1"}
    runner.pmb = mock_pmb

    # ExecutionAgent holds its own reference to the PMB client —
    # we must replace that too so place_order doesn't hit the real client.
    runner.execution._pmb = mock_pmb

    return runner


# ---------------------------------------------------------------------------
# Test 1 — Full intraday pipeline, 5 minute steps, BUY on step 3
# ---------------------------------------------------------------------------


class TestFullIntradayPipeline5Steps:
    """5 minute steps.  Strategy emits a BUY on step 3.

    Verify:
    - MarketDataAgent processes all 5 steps
    - Strategy returns intents only on step 3
    - Risk gate ALLOWs the intent
    - Execution submits the order (via PMB place_order)
    - Fill appears in step 4
    - Audit log has 5 entries
    """

    def test_full_intraday_pipeline_5_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # -- Configure --
            buy_intent = TradeIntent(
                symbol="SPY",
                contract="O:SPY250106C00595000",
                side="BUY",
                qty=2,
                order_type="MARKET",
                rationale="integration test buy",
                strategy_name="integration_mock",
            )
            strategy = IntegrationMockStrategy(intents_by_step={3: [buy_intent]})
            config = _make_config(tmpdir)
            runner = _setup_runner(config, strategy)

            # Steps 1-4 RUNNING, step 5 FINISHED.
            # Step 4 includes fill events for the order placed on step 3.
            fill_events = [
                {
                    "type": "ORDER_EVENT",
                    "ts": "2025-01-06T09:34:00-05:00",
                    "payload": {
                        "order_id": "ord_1",
                        "status": "FILLED",
                        "filled_qty": 2,
                    },
                },
                {
                    "type": "TRADE_EVENT",
                    "ts": "2025-01-06T09:34:00-05:00",
                    "payload": {
                        "trade_id": "trade_1",
                        "order_id": "ord_1",
                        "instrument_id": "OPTION:O:SPY250106C00595000",
                        "side": "BUY",
                        "qty": 2,
                        "price": 2.50,
                        "fees": 1.30,
                    },
                },
            ]

            # Include option bars so the contract is tracked (not stale)
            opt_bars = [{"contract": "O:SPY250106C00595000", "open": 2.0,
                         "high": 2.5, "low": 1.8, "close": 2.3, "volume": 500}]
            runner.pmb.step.side_effect = [
                _make_step("2025-01-06T09:31:00-05:00", options=opt_bars),
                _make_step("2025-01-06T09:32:00-05:00", options=opt_bars),
                _make_step("2025-01-06T09:33:00-05:00", options=opt_bars),
                _make_step("2025-01-06T09:34:00-05:00", options=opt_bars, extra_events=fill_events),
                _make_step("2025-01-06T09:35:00-05:00", status="FINISHED", options=opt_bars),
            ]

            # -- Run --
            with patch("trading.runner.query_option_chain", return_value=[]):
                result = runner.run()

            # -- Assert: 5 steps processed --
            assert result["total_steps"] == 5

            # -- Assert: strategy ran on the 4 RUNNING steps (FINISHED skips strategy) --
            assert len(strategy.observed_steps) == 4

            # -- Assert: intents only on step 3 --
            for i, intents in enumerate(strategy.observed_intents):
                step_num = i + 1
                if step_num == 3:
                    assert len(intents) == 1
                    assert intents[0].side == "BUY"
                else:
                    assert intents == []

            # -- Assert: PMB place_order called exactly once (step 3) --
            assert runner.pmb.place_order.call_count == 1

            # -- Assert: split trade audit records preserve step IDs --
            log_path = tmpdir + "/trades.jsonl"
            with open(log_path) as f:
                entries = [json.loads(line) for line in f]
            assert {entry["step_id"] for entry in entries} >= {3, 4}

            # -- Assert: step 4 has a fill --
            step4_entry = next(entry for entry in entries if entry["step_id"] == 4)
            assert len(step4_entry["fills"]) == 1
            assert step4_entry["fills"][0]["contract"] == "O:SPY250106C00595000"
            assert step4_entry["fills"][0]["qty"] == 2
            assert step4_entry["fills"][0]["price"] == 2.50

            # -- Assert: step 3 has the order submission --
            step3_entry = next(entry for entry in entries if entry["step_id"] == 3)
            assert len(step3_entry["orders"]) == 1
            assert step3_entry["orders"][0]["contract"] == "O:SPY250106C00595000"


# ---------------------------------------------------------------------------
# Test 2 — Weekly pipeline: 3 daily steps, intents only on day 1
# ---------------------------------------------------------------------------


class TestFullWeeklyPipeline3Days:
    """3 daily steps (Mon/Tue/Wed).  Strategy only decides on Monday (step 1).

    Verify:
    - Intents only on day 1 (Monday)
    - Days 2-3 have empty intents
    """

    def test_full_weekly_pipeline_3_days(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buy_intent = TradeIntent(
                symbol="SPY",
                contract="O:SPY250110C00600000",
                side="BUY",
                qty=1,
                order_type="MARKET",
                rationale="weekly rebalance",
                strategy_name="integration_mock",
            )
            strategy = IntegrationMockStrategy(intents_by_step={1: [buy_intent]})
            config = _make_config(
                tmpdir,
                frequency="1d",
                start="2025-01-06",
                end="2025-01-08",
            )
            runner = _setup_runner(config, strategy)

            # 3 daily steps: Mon, Tue, Wed (FINISHED)
            opt_bars = [{"contract": "O:SPY250110C00600000", "open": 3.0,
                         "high": 3.5, "low": 2.8, "close": 3.2, "volume": 300}]
            runner.pmb.step.side_effect = [
                _make_step(
                    "2025-01-06T16:00:00-05:00",
                    next_ts="2025-01-07T16:00:00-05:00",
                    options=opt_bars,
                ),
                _make_step(
                    "2025-01-07T16:00:00-05:00",
                    next_ts="2025-01-08T16:00:00-05:00",
                    options=opt_bars,
                ),
                _make_step("2025-01-08T16:00:00-05:00", status="FINISHED", options=opt_bars),
            ]

            with patch("trading.runner.query_option_chain", return_value=[]):
                result = runner.run()

            assert result["total_steps"] == 3

            # Step 1: has intent.  Step 2: no intents.  Step 3 (FINISHED) skips strategy.
            assert len(strategy.observed_intents) == 2
            assert len(strategy.observed_intents[0]) == 1
            assert strategy.observed_intents[0][0].side == "BUY"
            assert strategy.observed_intents[1] == []

            # place_order should have been called once (for step 1's intent)
            assert runner.pmb.place_order.call_count == 1


# ---------------------------------------------------------------------------
# Test 3 — EOD flatten generates SELL intents
# ---------------------------------------------------------------------------


class TestEodFlattenGeneratesSells:
    """Configure flatten_before_close=True.  Portfolio has an open position.
    Step into the flatten window (15:56 ET, 4 minutes to close).

    Verify:
    - Risk gate auto-generates SELL intents for the position.
    - The SELL is submitted to PMB.
    """

    def test_eod_flatten_generates_sells(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Strategy emits NO intents — flatten is purely risk-gate-driven.
            strategy = IntegrationMockStrategy(intents_by_step={})
            config = _make_config(
                tmpdir,
                flatten_before_close=True,
                start="2025-01-06T09:30:00-05:00",
                end="2025-01-06T16:00:00-05:00",
            )
            runner = _setup_runner(config, strategy)

            existing_position = {
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

            # Step 1: normal bar (09:31), step 2: within flatten window (15:56 ET),
            # step 3: FINISHED.
            runner.pmb.step.side_effect = [
                _make_step("2025-01-06T09:31:00-05:00"),
                _make_step(
                    "2025-01-06T15:56:00-05:00",
                    positions=[existing_position],
                ),
                _make_step("2025-01-06T16:00:00-05:00", status="FINISHED"),
            ]

            with patch("trading.runner.query_option_chain", return_value=[]):
                result = runner.run()

            assert result["total_steps"] == 3

            # Read split risk audit log.
            log_path = tmpdir + "/risk.jsonl"
            with open(log_path) as f:
                entries = [json.loads(line) for line in f]

            # Step 2 should have the flatten-generated risk decision.
            step2 = next(entry for entry in entries if entry["step_id"] == 2)
            assert len(step2["decisions"]) == 1
            decision = step2["decisions"][0]
            assert decision["action"] == "ALLOW"
            assert "eod_flatten" in decision["risk_notes"]
            assert decision["side"] == "SELL"
            assert decision["contract"] == "O:SPY250106C00595000"
            assert decision["qty"] == 5

            # The flatten sell should have been submitted to PMB
            # Step 1: no intents -> no orders.  Step 2: flatten SELL -> 1 order.
            assert runner.pmb.place_order.call_count == 1


# ---------------------------------------------------------------------------
# Test 4 — Stale instrument rejected
# ---------------------------------------------------------------------------


class TestStaleInstrumentRejected:
    """max_stale_minutes=2.  Option bar goes stale for 3 minutes.
    Strategy tries to trade it.  Risk gate rejects with staleness reason.
    """

    def test_stale_instrument_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            contract = "O:SPY250106C00595000"

            # Strategy emits a BUY on step 4 (after the option has been stale
            # for 3 minutes: steps 2, 3, 4 have no option bar).
            buy_intent = TradeIntent(
                symbol="SPY",
                contract=contract,
                side="BUY",
                qty=1,
                order_type="MARKET",
                rationale="buy stale option",
                strategy_name="integration_mock",
            )
            strategy = IntegrationMockStrategy(intents_by_step={4: [buy_intent]})
            config = _make_config(
                tmpdir,
                max_stale_minutes=2,
            )
            runner = _setup_runner(config, strategy)

            # Register the contract with MarketDataAgent so it tracks staleness
            runner.market_data.register_contracts({contract})

            option_bar = {
                "contract": contract,
                "open": 2.50,
                "high": 2.55,
                "low": 2.45,
                "close": 2.52,
                "volume": 500,
                "iv": 0.25,
                "delta": 0.45,
                "gamma": 0.02,
                "theta": -0.05,
            }

            # Step 1: option bar present (fresh).
            # Steps 2-4: option bar MISSING (stale counter increments).
            # Step 4: strategy emits BUY.  Option is stale 3 minutes (>2).
            # Step 5: FINISHED.
            runner.pmb.step.side_effect = [
                _make_step("2025-01-06T09:31:00-05:00", options=[option_bar]),
                _make_step("2025-01-06T09:32:00-05:00"),  # stale_minutes=1
                _make_step("2025-01-06T09:33:00-05:00"),  # stale_minutes=2
                _make_step("2025-01-06T09:34:00-05:00"),  # stale_minutes=3 (exceeds 2)
                _make_step("2025-01-06T09:35:00-05:00", status="FINISHED"),
            ]

            with patch("trading.runner.query_option_chain", return_value=[]):
                result = runner.run()

            assert result["total_steps"] == 5

            # Read split risk audit log.
            log_path = tmpdir + "/risk.jsonl"
            with open(log_path) as f:
                entries = [json.loads(line) for line in f]

            # Step 4 should have a REJECT decision for staleness.
            step4 = next(entry for entry in entries if entry["step_id"] == 4)
            assert len(step4["decisions"]) == 1
            decision = step4["decisions"][0]
            assert decision["action"] == "REJECT"
            assert "stale_data" in decision["risk_notes"]
            assert "stale" in decision["reason"].lower()

            # No orders should have been placed (the intent was rejected)
            assert runner.pmb.place_order.call_count == 0
