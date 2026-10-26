"""Tests for trading.ingest.resume.run_from_checkpoint (Task 12).

These tests exercise the orchestration wrapper (checkpoint load, override
application, event bookends) without spinning up PMB.  The real engine
loop is patched to a no-op so we can verify the surrounding glue.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from trading.core.registry import STRATEGY_REGISTRY
from trading.runner import TradingRunner
from trading.ingest.resume import run_from_checkpoint


class _ResumeStub:
    """Minimal strategy stub with save/load_checkpoint support."""

    def __init__(self, **kwargs):
        self.prompt_template = None
        self._llm = None
        self._loaded = None

    def on_session_start(self, config): pass
    def on_session_end(self, state): pass
    def on_day_open(self, date, state): pass
    def on_day_close(self, date, state): pass
    def on_step(self, me, pf, st): return []

    def save_checkpoint(self) -> dict:
        return {"_version": 1}

    def load_checkpoint(self, state: dict) -> None:
        self._loaded = state


STRATEGY_REGISTRY["resume_stub"] = _ResumeStub


def _write_ckpt(path: Path, *, strategy_name: str = "resume_stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "trading_state": {
                    "step_id": 50,
                    "current_ts": "2025-03-14T18:30:00.000Z",
                    "current_date": "2025-03-14",
                    "session_running": True,
                },
                "accumulators": {
                    "last_portfolio": None,
                    "start_of_day_equity": None,
                },
                "pmb_session_id": "",
                "pmb_account_id": "",
                "strategy_checkpoint": {"_version": 1},
                "llm_client_state": {},
                "run_config": {
                    "strategy": {"name": strategy_name, "params": {}},
                    "experiment": {
                        "start": "2025-03-14",
                        "end": "2025-03-20",
                        "run_id": "parent_run",
                        "universe": ["SPY"],
                        "frequency": "1d",
                    },
                },
            }
        )
    )


def test_run_from_checkpoint_diverge_completes(tmp_path, monkeypatch):
    """End-to-end orchestration: checkpoint loaded, overrides applied,
    _fork_context set, _main_loop invoked.  run.started/run.finished
    are emitted BY _main_loop (single source of truth) — the
    monkeypatched stub here replays the real emit sequence so the
    fork-context handling is end-to-end verified."""
    ckpt_dir = tmp_path / "ckpt"
    _write_ckpt(ckpt_dir / "runner_checkpoint.json")

    def fake_main_loop(self):
        # Mimic _main_loop's real emit contract: read _fork_context and
        # emit bookends around a no-op body.  This covers the hotfix that
        # moved run.started/run.finished out of resume.py and into the
        # runner so fork paths don't emit twice.
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
            return {}
        finally:
            self.events.emit("run.finished", {"exit_code": 0})
            self._last_exit_code = 0
            self.events.close(timeout=10)

    monkeypatch.setattr(TradingRunner, "_main_loop", fake_main_loop)

    run_dir = tmp_path / "fork_run"
    result = run_from_checkpoint(
        checkpoint_path=ckpt_dir,
        mode="diverge",
        prompt_override=None,
        model_override="deepseek-v3",
        web_ingest=None,
        run_id="01HFORK",
        run_dir=run_dir,
    )

    assert result["exit_code"] == 0
    events_file = run_dir / "events.jsonl"
    assert events_file.exists()
    lines = [json.loads(ln) for ln in events_file.read_text().splitlines() if ln]
    kinds = [ev["kind"] for ev in lines]
    # Single run.started + single run.finished (hotfix contract)
    assert kinds.count("run.started") == 1
    assert kinds.count("run.finished") == 1
    started = next(ev for ev in lines if ev["kind"] == "run.started")
    assert started["payload"]["kind"] == "fork"
    assert started["payload"]["fork_mode"] == "diverge"
    assert started["payload"]["model_id"] == "deepseek-v3"


def test_run_from_checkpoint_rejects_prompt_override(tmp_path):
    """Phase 1: prompt_override wiring is Plan B scope — reject up front
    rather than silently no-op (Codex review hotfix)."""
    ckpt_dir = tmp_path / "ckpt"
    _write_ckpt(ckpt_dir / "runner_checkpoint.json")
    with pytest.raises(NotImplementedError, match="prompt_override"):
        run_from_checkpoint(
            checkpoint_path=ckpt_dir,
            mode="diverge",
            prompt_override="some-prompt",
            model_override=None,
            web_ingest=None,
            run_id="01H",
            run_dir=tmp_path / "fork",
        )


def test_run_from_checkpoint_diverge_requires_override(tmp_path):
    """diverge mode must reject calls with no prompt or model override."""
    with pytest.raises(ValueError, match="override"):
        run_from_checkpoint(
            checkpoint_path=tmp_path,
            mode="diverge",
            prompt_override=None,
            model_override=None,
            web_ingest=None,
            run_id="01H",
            run_dir=tmp_path,
        )


def test_run_from_checkpoint_rejects_replay(tmp_path):
    """replay mode is deferred to Phase 2."""
    with pytest.raises(ValueError, match="replay.*Phase 2"):
        run_from_checkpoint(
            checkpoint_path=tmp_path,
            mode="replay",
            prompt_override=None,
            model_override=None,
            web_ingest=None,
            run_id="01H",
            run_dir=tmp_path,
        )


def test_resume_cli_help_lists_flags():
    """Smoke test: CLI --help exposes the new subcommand's flags."""
    out = subprocess.check_output(
        [sys.executable, "-m", "trading.cli", "resume-from-checkpoint", "--help"],
        text=True,
    )
    assert "--checkpoint-id" in out
    assert "--mode" in out
    assert "--prompt-override" in out
    assert "--model-override" in out
    assert "--run-id" in out
