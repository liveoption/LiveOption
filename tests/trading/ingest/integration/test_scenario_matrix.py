"""Scenario matrix validation (Plan A Task 15).

Two layers:

- Layer 1 (always runs in CI): static source-inspection checks that each Phase-1
  strategy still contains the emit sites the web event contract depends on. If a
  refactor removes an emit by accident, these tests fail fast.

- Layer 2 (gated by LIVEOPTION_INTEGRATION env var): parametrized end-to-end run
  against live PMB+UPQ that exercises 5 scenario fixtures. Skipped by default;
  each case also skips cleanly when its fixture file is not present.

Also includes a lightweight direct sanity test of write_decision_artifact.
"""
from __future__ import annotations

import inspect
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Layer 1 — source-inspection emit site checks
# ---------------------------------------------------------------------------

def _src(cls) -> str:
    return inspect.getsource(cls)


def _has_emit(src: str, kind: str) -> bool:
    """True if source emits `kind` via self.emit("kind", ...) or 'kind'."""
    return (f'self.emit("{kind}"' in src) or (f"self.emit('{kind}'" in src)


def _has_phase(src: str, phase_name: str) -> bool:
    """True if source mentions a phase.completed entry for phase_name."""
    return (f'"phase": "{phase_name}"' in src) or (f"'phase': '{phase_name}'" in src)


def test_weekly_llm_emits_decision_kinds():
    from trading.agents.strategy.weekly_llm import WeeklyLLMStrategy

    src = _src(WeeklyLLMStrategy)
    assert _has_emit(src, "decision.started"), "weekly_llm missing decision.started"
    assert _has_emit(src, "decision.completed"), "weekly_llm missing decision.completed"
    assert _has_emit(src, "decision.skipped"), "weekly_llm missing decision.skipped"


def test_paper_aligned_emits_decision_and_phases():
    from trading.agents.strategy.paper_aligned import PaperAlignedStrategy

    src = _src(PaperAlignedStrategy)
    assert _has_emit(src, "decision.started")
    assert _has_emit(src, "decision.completed")
    assert _has_emit(src, "decision.skipped")
    assert _has_emit(src, "phase.completed")
    for phase in ("analysis", "trader", "reflection"):
        assert _has_phase(src, phase), f"paper_aligned missing phase={phase}"


def test_overlay_profit_emits_decision_and_phases():
    from trading.agents.strategy.overlay import OverlayProfitStrategy

    # OverlayProfitStrategy inherits from _OverlayBase; gather both sources.
    mod_src = inspect.getsource(inspect.getmodule(OverlayProfitStrategy))
    assert _has_emit(mod_src, "decision.started")
    assert _has_emit(mod_src, "decision.completed")
    assert _has_emit(mod_src, "decision.skipped")
    assert _has_emit(mod_src, "phase.completed")
    for phase in ("analyzer", "executor"):
        assert _has_phase(mod_src, phase), f"overlay missing phase={phase}"


def test_overlay_hedge_emits_decision_and_phases():
    from trading.agents.strategy.overlay import OverlayHedgeStrategy

    mod_src = inspect.getsource(inspect.getmodule(OverlayHedgeStrategy))
    # Both hedge + profit share _OverlayBase emits; just re-assert here to guard
    # against someone stripping emits from only one subclass path.
    assert _has_emit(mod_src, "decision.started")
    assert _has_emit(mod_src, "decision.completed")
    assert _has_emit(mod_src, "phase.completed")


def test_earnings_bet_emits_decision_and_phases():
    from trading.agents.strategy.earnings_bet import EarningsBetStrategy

    src = _src(EarningsBetStrategy)
    assert _has_emit(src, "decision.started")
    assert _has_emit(src, "decision.completed")
    assert _has_emit(src, "phase.completed")
    for phase in ("pre_analyzer", "pre_executor", "post_analyzer", "post_executor"):
        assert _has_phase(src, phase), f"earnings_bet missing phase={phase}"


# ---------------------------------------------------------------------------
# Layer 2 — live scenario matrix (skipped by default)
# ---------------------------------------------------------------------------

SCENARIOS = [
    ("weekly_llm", "tests/fixtures/weekly_5day.yaml"),
    ("overlay_profit", "tests/fixtures/overlay_profit_5day.yaml"),
    ("overlay_hedge", "tests/fixtures/overlay_hedge_5day.yaml"),
    ("earnings_bet", "tests/fixtures/earnings_single_event.yaml"),
    ("paper_aligned", "tests/fixtures/paper_aligned_3day.yaml"),
]


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("LIVEOPTION_INTEGRATION"),
    reason="Requires live PMB+UPQ on qlib + fixture configs (set LIVEOPTION_INTEGRATION=1)",
)
@pytest.mark.parametrize("name,cfg_path", SCENARIOS)
def test_scenario_produces_expected_events_live(tmp_path, name, cfg_path):
    """Run each scenario end-to-end and assert events.jsonl + artifact layout."""
    cfg = Path(cfg_path)
    if not cfg.exists():
        pytest.skip(f"{cfg_path} fixture not available")

    # Lazy imports — these paths only need to resolve when integration runs.
    from trading.core.runner import TradingRunner  # type: ignore

    run_dir = tmp_path / f"run_{name}"
    run_dir.mkdir()

    runner = TradingRunner.from_config(str(cfg), run_dir=str(run_dir))  # type: ignore[attr-defined]
    runner.run()

    events_file = run_dir / "events.jsonl"
    assert events_file.exists(), f"{name}: events.jsonl missing"

    kinds = []
    with events_file.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            kinds.append(json.loads(line).get("kind"))

    assert "decision.started" in kinds, f"{name}: no decision.started emitted"
    assert any(k in ("decision.completed", "decision.skipped") for k in kinds), (
        f"{name}: neither decision.completed nor decision.skipped emitted"
    )

    decisions_root = run_dir / "decisions"
    if decisions_root.exists():
        for dec_dir in decisions_root.iterdir():
            if not dec_dir.is_dir():
                continue
            for fname in ("record.json", "market.json", "context.json"):
                assert (dec_dir / fname).exists(), (
                    f"{name}: decision {dec_dir.name} missing {fname}"
                )


# ---------------------------------------------------------------------------
# Artifact writer sanity (direct call, independent of strategies)
# ---------------------------------------------------------------------------

def test_artifact_writer_produces_three_files(tmp_path):
    from trading.ingest.artifact import write_decision_artifact

    run_dir = tmp_path / "run_01H"
    run_dir.mkdir()

    artifact = write_decision_artifact(
        run_dir=run_dir,
        decision_id="01HDEC",
        seq=0,
        trading_day="2026-04-18",
        decision_ts=datetime(2026, 4, 18, 18, 30, tzinfo=timezone.utc),
        model_id="test-model",
        provider="test",
        prompt={"system": "", "user": ""},
        response={"raw": "", "parsed_trades": [], "reasoning": ""},
        context={"portfolio": {}, "indicators": {}},
        trades_executed=[],
        market_snapshot={"chain": [], "underlying_quote": 0.0},
    )
    path = Path(artifact["storage_dir"])
    for name in ("record.json", "market.json", "context.json"):
        assert (path / name).exists(), f"missing {name} in {path}"
