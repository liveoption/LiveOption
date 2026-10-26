"""Tests for TradingRunner --web-ingest / --run-id wiring (Task 4)."""
from __future__ import annotations

import tempfile
from pathlib import Path

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
from trading.ingest.event_emitter import EventEmitter, NoopEmitter


class _Stub:
    """Minimal strategy stub registered for these tests."""
    def __init__(self, **kwargs):
        pass
    def on_session_start(self, config): pass
    def on_session_end(self, state): pass
    def on_day_open(self, date, state): pass
    def on_day_close(self, date, state): pass
    def on_step(self, market_event, portfolio, state): return []
    def save_checkpoint(self): return {}
    def load_checkpoint(self, state): pass


STRATEGY_REGISTRY["web_params_stub"] = _Stub


def _make_config(output_dir: str) -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test_run",
            start="2025-01-06",
            end="2025-01-10",
            universe=["SPY"],
            frequency="1m",
        ),
        strategy=StrategyConfig(name="web_params_stub", params={}),
        risk=RiskConfig(flatten_before_close=False),
        execution=ExecutionConfig(),
        data_policy=DataPolicyConfig(),
        evaluation=EvaluationConfig(output_dir=output_dir),
    )


def test_runner_accepts_web_ingest_and_run_id():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, web_ingest=None, run_id="01HTESTRUNIDABCDEFGHIJK123")
        try:
            assert runner.run_id == "01HTESTRUNIDABCDEFGHIJK123"
            assert runner.run_dir == Path(tmp)
            assert runner.events is not None
            assert isinstance(runner.events, EventEmitter)
            # events.jsonl sidecar is always created (spec-aligned)
            assert (Path(tmp) / "events.jsonl").exists()
        finally:
            runner.events.close()


def test_runner_generates_run_id_when_not_provided():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg)
        try:
            # Defaults: runner must have some run_id (from config.experiment or generated)
            assert runner.run_id is not None
            assert isinstance(runner.run_id, str)
            assert len(runner.run_id) > 0
            assert isinstance(runner.events, EventEmitter)
        finally:
            runner.events.close()


def test_runner_strategy_events_wired():
    """strategy.events must reference runner.events, not None."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTRUNIDABCDEFGHIJK999")
        try:
            assert runner.strategy.events is runner.events
        finally:
            runner.events.close()
