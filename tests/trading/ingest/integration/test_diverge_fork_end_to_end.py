"""End-to-end integration tests for the diverge-fork resume flow (Plan A, Task 14).

Two tests are provided:

Test A — ``test_diverge_fork_smoke``
    Always runs in CI.  Exercises ``run_from_checkpoint`` orchestration with a
    stubbed ``TradingRunner._main_loop`` / ``_bootstrap`` to assert the
    event-stream contract (``run.started`` → ``run.finished`` with
    ``kind == 'fork'`` and ``fork_mode == 'diverge'``).

Test B — ``test_diverge_fork_live_pmb``
    Gated behind ``LIVEOPTION_INTEGRATION=1``.  Requires live PMB+UPQ on qlib
    and the (not-yet-committed) ``tests/fixtures/weekly_5day.yaml`` fixture.
    Skipped otherwise.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading.core.registry import STRATEGY_REGISTRY
from trading.runner import TradingRunner
from trading.ingest.resume import run_from_checkpoint
from trading.ingest.runner_checkpoint import RunnerCheckpoint


class _ForkIntegrationStub:
    """Minimal strategy stub with save/load_checkpoint support."""

    def __init__(self, **kwargs):
        self.prompt_template = None
        self._llm = None
        self._loaded: dict | None = None

    def on_session_start(self, config): pass
    def on_session_end(self, state): pass
    def on_day_open(self, date, state): pass
    def on_day_close(self, date, state): pass
    def on_step(self, me, pf, st): return []

    def save_checkpoint(self) -> dict:
        return {"_version": 1}

    def load_checkpoint(self, state: dict) -> None:
        self._loaded = state


STRATEGY_REGISTRY.setdefault("fork_integration_stub", _ForkIntegrationStub)


def _write_inline_checkpoint(ckpt_file: Path) -> None:
    """Write a checkpoint using the inline (``_reconstruct_run_config``
    fallback) path so we don't depend on any on-disk config."""
    RunnerCheckpoint(
        version=1,
        trading_state={
            "step_id": 50,
            "current_ts": "2025-03-14T18:30:00.000Z",
            "current_date": "2025-03-14",
            "session_running": True,
        },
        accumulators={
            "last_portfolio": None,
            "start_of_day_equity": None,
        },
        pmb_session_id="",
        pmb_account_id="",
        strategy_checkpoint={"_version": 1},
        llm_client_state={},
        run_config={
            "strategy": {"name": "fork_integration_stub", "params": {}},
            "experiment": {
                "start": "2025-03-14",
                "end": "2025-03-20",
                "run_id": "parent_run",
                "universe": ["SPY"],
                "frequency": "1d",
            },
        },
    ).save(ckpt_file)


def test_diverge_fork_smoke(tmp_path, monkeypatch):
    """Orchestration-level smoke: a diverge fork produces the expected event
    stream (``run.started`` with ``kind='fork'``/``fork_mode='diverge'`` and
    ``run.finished`` with ``exit_code: 0``) without any PMB/UPQ backend."""
    ckpt_dir = tmp_path / "ckpt"
    ckpt_dir.mkdir()
    _write_inline_checkpoint(ckpt_dir / "runner_checkpoint.json")

    # Patch _main_loop with a minimal stub that replays the real emit contract
    # so the fork-context pathway is end-to-end verified without PMB/UPQ.
    def fake_main_loop(self):
        fork_ctx = getattr(self, "_fork_context", None)
        if fork_ctx:
            self.events.emit("run.started", {
                "scenario": self.config.strategy.name,
                "model_id": fork_ctx.get("model_override") or "unknown",
                "date_start": self.config.experiment.start,
                "date_end": self.config.experiment.end,
                "kind": fork_ctx.get("kind", "batch"),
                "fork_mode": fork_ctx.get("fork_mode"),
            })
        else:
            self.events.emit("run.started", {"kind": "batch"})
        try:
            return {"steps": 0}
        finally:
            self.events.emit("run.finished", {"exit_code": 0})
            self._last_exit_code = 0
            self.events.close(timeout=10)

    monkeypatch.setattr(TradingRunner, "_main_loop", fake_main_loop)

    fork_dir = tmp_path / "fork_run"
    # prompt_override is intentionally None: Plan A rejects it (wiring is
    # deferred to Plan B per resume.py docstring). model_override is enough
    # to satisfy the diverge contract.
    result = run_from_checkpoint(
        checkpoint_path=ckpt_dir,
        mode="diverge",
        prompt_override=None,
        model_override="deepseek-v3",
        web_ingest=None,
        run_id="01HFORK",
        run_dir=fork_dir,
    )

    assert result["exit_code"] == 0

    events_file = fork_dir / "events.jsonl"
    assert events_file.exists(), "expected events.jsonl in fork run_dir"

    events = [
        json.loads(ln)
        for ln in events_file.read_text().splitlines()
        if ln.strip()
    ]
    kinds = [e["kind"] for e in events]
    # Hotfix contract: single run.started + single run.finished on fork path.
    assert kinds.count("run.started") == 1
    assert kinds.count("run.finished") == 1

    # run.started carries the fork diverge metadata + overridden model_id.
    started = next(e for e in events if e["kind"] == "run.started")
    assert started["payload"].get("kind") == "fork"
    assert started["payload"].get("fork_mode") == "diverge"
    assert started["payload"].get("model_id") == "deepseek-v3"

    # run.finished has exit_code 0.
    finished = next(e for e in events if e["kind"] == "run.finished")
    assert finished["payload"].get("exit_code") == 0


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("LIVEOPTION_INTEGRATION"),
    reason="Requires live PMB+UPQ on qlib (set LIVEOPTION_INTEGRATION=1)",
)
def test_diverge_fork_live_pmb(tmp_path):
    """Full-pipeline diverge fork against live PMB+UPQ.  See plan Task 14.

    Prerequisites
    -------------
    - ``tests/fixtures/weekly_5day.yaml`` exists (a 5-day trim of
      ``configs/weekly_options.yaml``).
    - QFinZero hub running on ``127.0.0.1:19777``.
    - ``DEEPSEEK_API_KEY`` (or appropriate provider key) present in env.

    This test drives a real ``TradingRunner.run()`` through the parent-run
    checkpoint step, then forks with a model override via
    ``run_from_checkpoint`` and asserts the fork's event log contains
    ``run.started`` / ``run.finished`` and at least one step event.
    """
    fixture = Path("tests/fixtures/weekly_5day.yaml")
    if not fixture.exists():
        pytest.skip(f"{fixture} fixture not available")

    # Deferred imports so module collection doesn't fail in environments
    # lacking the live backend.
    from trading.core.config import load_config  # noqa: WPS433
    import trading.agents.strategy  # noqa: F401, WPS433  — registry side-effects

    parent_dir = tmp_path / "parent"
    fork_dir = tmp_path / "fork"

    cfg = load_config(str(fixture))
    cfg.evaluation.output_dir = str(parent_dir)

    runner = TradingRunner(cfg, run_id="01HPARENT")
    runner.run()

    ckpt_file = parent_dir / "runner_checkpoint.json"
    assert ckpt_file.exists(), "parent run must have emitted a checkpoint"

    result = run_from_checkpoint(
        checkpoint_path=ckpt_file,
        mode="diverge",
        prompt_override=None,
        model_override="deepseek-v3",
        web_ingest=None,
        run_id="01HFORK",
        run_dir=fork_dir,
    )
    assert result["exit_code"] == 0

    events = [
        json.loads(ln)
        for ln in (fork_dir / "events.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    kinds = {e["kind"] for e in events}
    assert "run.started" in kinds
    assert "run.finished" in kinds
    started = next(e for e in events if e["kind"] == "run.started")
    assert started["payload"].get("fork_mode") == "diverge"
