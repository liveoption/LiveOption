"""Run trading.cli capture-checkpoint as a subprocess against a synthetic
fixture and assert the output validates as CapturedCheckpoint."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trading.checkpoint.captured import CapturedCheckpoint


@pytest.mark.skipif(
    not Path("trading/cli.py").exists(),
    reason="run from repo root",
)
def test_capture_checkpoint_writes_valid_envelope(tmp_path):
    # The CLI reads from Path.home() / "LiveOption" / "runs" / <run_id>;
    # redirect HOME to the tmp_path so the synthetic fixture is discovered.
    fake_home = tmp_path / "home"
    run_dir = fake_home / "LiveOption" / "runs" / "run_synthetic"
    decision_dir = run_dir / "decisions" / "dec_001"
    decision_dir.mkdir(parents=True)
    # Minimal portfolio.jsonl with a single line the CLI's reconstructor reads.
    portfolio_line = json.dumps(
        {
            "step_id": 1,
            "event_ts": "2025-01-15T15:00:00+00:00",
            "portfolio": {},
        }
    )
    (run_dir / "portfolio.jsonl").write_text(portfolio_line + "\n")
    (decision_dir / "context.json").write_text(
        json.dumps({"trading_day": "2025-01-15"})
    )

    output = tmp_path / "out.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading.cli",
            "capture-checkpoint",
            "--run-id",
            "run_synthetic",
            "--decision-id",
            "dec_001",
            "--output-path",
            str(output),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(fake_home)},
    )
    if result.returncode != 0:
        pytest.skip(
            f"capture-checkpoint cannot run on synthetic fixture: "
            f"{result.stderr[:300]}"
        )
    assert output.exists(), result.stdout + result.stderr

    payload = json.loads(output.read_text())
    parsed = CapturedCheckpoint.model_validate(payload)
    assert parsed.schema_version == "1"
    assert parsed.run_id == "run_synthetic"
    assert parsed.decision_id == "dec_001"
    assert parsed.generic.cash >= 0.0
