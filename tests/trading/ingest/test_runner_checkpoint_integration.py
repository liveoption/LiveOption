"""Unit tests for TradingRunner.capture_checkpoint (Task 10b)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from trading.core.schemas import PortfolioSnapshot
from trading.core.state import TradingState
from trading.runner import TradingRunner
from trading.ingest.runner_checkpoint import RunnerCheckpoint


class _StubStrategy:
    def save_checkpoint(self):
        return {"_version": 1, "hello": "world"}


def _make_runner_with_state() -> TradingRunner:
    r = TradingRunner.__new__(TradingRunner)  # bypass __init__

    # Minimal config stub with .strategy.name and .experiment.{start,end,run_id}
    r.config = SimpleNamespace(
        strategy=SimpleNamespace(name="stub_strategy"),
        experiment=SimpleNamespace(start="2025-01-06", end="2025-01-10", run_id="R1"),
    )

    ts = datetime(2026, 4, 18, 14, 30, 0, tzinfo=timezone.utc)
    # TradingState requires config, run_id, episode_id
    r._state = TradingState(
        config=r.config, run_id="R1", episode_id="R1",
        step_id=42, current_ts=ts, current_date="2026-04-18",
        session_running=True, is_new_day=False,
    )

    r._last_portfolio = PortfolioSnapshot(
        cash=100000.0, equity=105000.0,
        daily_pnl=0.0, daily_pnl_pct=0.0, positions=[],
    )
    r._start_of_day_equity = 100000.0

    r.pmb = SimpleNamespace(session_id="sess-1", account_id="acct-1")
    r.strategy = _StubStrategy()
    return r


def test_capture_checkpoint_builds_runner_checkpoint():
    r = _make_runner_with_state()
    ckpt = r.capture_checkpoint()

    assert isinstance(ckpt, RunnerCheckpoint)
    assert ckpt.version == 1
    assert ckpt.pmb_session_id == "sess-1"
    assert ckpt.pmb_account_id == "acct-1"
    assert ckpt.strategy_checkpoint == {"_version": 1, "hello": "world"}
    assert ckpt.trading_state["step_id"] == 42
    assert ckpt.trading_state["current_date"] == "2026-04-18"
    # ISO-Z timestamp
    assert ckpt.trading_state["current_ts"].endswith("Z")
    assert ckpt.accumulators["start_of_day_equity"] == 100000.0
    assert ckpt.accumulators["last_portfolio"]["equity"] == 105000.0
    assert ckpt.run_config["strategy"]["name"] == "stub_strategy"
    assert ckpt.run_config["experiment"]["start"] == "2025-01-06"


def test_capture_checkpoint_roundtrips_via_save_load(tmp_path):
    r = _make_runner_with_state()
    ckpt = r.capture_checkpoint()
    p = tmp_path / "ckpt.json"
    ckpt.save(p)
    loaded = RunnerCheckpoint.load(p)
    assert loaded.version == 1
    assert loaded.pmb_session_id == "sess-1"
    assert loaded.trading_state["step_id"] == 42


def test_capture_checkpoint_handles_missing_last_portfolio():
    r = _make_runner_with_state()
    r._last_portfolio = None
    ckpt = r.capture_checkpoint()
    assert ckpt.accumulators["last_portfolio"] is None


def test_capture_checkpoint_propagates_strategy_not_implemented():
    r = _make_runner_with_state()

    class _NoCheckpoint:
        def save_checkpoint(self):
            raise NotImplementedError("no checkpoint")

    r.strategy = _NoCheckpoint()
    try:
        r.capture_checkpoint()
    except NotImplementedError:
        return
    raise AssertionError("expected NotImplementedError")
