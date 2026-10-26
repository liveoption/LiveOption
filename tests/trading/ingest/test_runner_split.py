"""Tests for TradingRunner.run() split into _bootstrap / _main_loop (Task 11)."""
from __future__ import annotations

import tempfile
from unittest.mock import patch

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
from trading.runner import TradingRunner


class _Stub:
    def __init__(self, **kwargs):
        pass
    def on_session_start(self, config): pass
    def on_session_end(self, state): pass
    def on_day_open(self, date, state): pass
    def on_day_close(self, date, state): pass
    def on_step(self, me, pf, st): return []
    def save_checkpoint(self) -> dict: return {}
    def load_checkpoint(self, state: dict) -> None: pass


STRATEGY_REGISTRY["runner_split_stub"] = _Stub


def _make_config(output_dir: str) -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test_split",
            start="2025-01-06",
            end="2025-01-10",
            universe=["SPY"],
            frequency="1m",
        ),
        strategy=StrategyConfig(name="runner_split_stub", params={}),
        risk=RiskConfig(flatten_before_close=False),
        execution=ExecutionConfig(),
        data_policy=DataPolicyConfig(),
        evaluation=EvaluationConfig(output_dir=output_dir),
    )


def test_runner_has_split_methods():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTRUNIDSPLIT1234567")
        try:
            assert hasattr(runner, "_bootstrap")
            assert hasattr(runner, "_main_loop")
            assert hasattr(runner, "_restore_from_checkpoint")
            assert callable(runner._bootstrap)
            assert callable(runner._main_loop)
        finally:
            runner.events.close()


def test_run_calls_bootstrap_then_main_loop():
    """run() must delegate to _bootstrap() then _main_loop() in that order."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTRUNIDSPLIT7654321")
        order = []
        try:
            with patch.object(
                TradingRunner, "_bootstrap",
                lambda self: order.append("bootstrap"),
            ), patch.object(
                TradingRunner, "_main_loop",
                lambda self: order.append("main_loop") or {"run_id": "x", "total_steps": 0},
            ):
                result = runner.run()
            assert order == ["bootstrap", "main_loop"]
            assert result == {"run_id": "x", "total_steps": 0}
        finally:
            runner.events.close()


def test_restore_from_checkpoint_marks_bootstrapped():
    """Task 12: _restore_from_checkpoint rehydrates state and flips the
    _bootstrapped guard so subsequent _bootstrap() calls are no-ops."""
    from trading.ingest.runner_checkpoint import RunnerCheckpoint

    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTRUNIDSPLITCKPT999")
        try:
            ckpt = RunnerCheckpoint(
                version=1,
                trading_state={
                    "step_id": 7,
                    "current_ts": "2025-03-14T18:30:00.000Z",
                    "current_date": "2025-03-14",
                    "session_running": True,
                },
                accumulators={"last_portfolio": None, "start_of_day_equity": None},
                pmb_session_id="",
                pmb_account_id="",
                strategy_checkpoint={},
                llm_client_state={},
                run_config={},
            )
            runner._restore_from_checkpoint(ckpt)
            assert runner._bootstrapped is True
            assert runner._state.step_id == 7
        finally:
            runner.events.close()
