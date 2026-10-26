"""Tests for the `capture-checkpoint` CLI subcommand (Task 10b)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from trading.cli import cmd_capture_checkpoint


def test_capture_checkpoint_cli_reconstructs(tmp_path, monkeypatch):
    """CLI writes a reconstructed checkpoint from a synthetic portfolio.jsonl."""
    run_id = "01HFAKE"
    fake_run_dir = tmp_path / "LiveOption" / "runs" / run_id
    fake_run_dir.mkdir(parents=True)
    (fake_run_dir / "portfolio.jsonl").write_text(
        json.dumps({
            "step_id": 42,
            "event_ts": "2026-04-18T14:30:00.000Z",
            "portfolio": {"cash": 100000.0, "equity": 105000.0},
        }) + "\n"
    )

    # Pin Path.home to tmp_path so the CLI resolves our fake run_dir.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    out = tmp_path / "ckpt.json"
    args = SimpleNamespace(
        run_id=run_id, decision_id="01HDEC", output_path=str(out),
    )
    cmd_capture_checkpoint(args)

    assert out.exists()
    data = json.loads(out.read_text())
    # CapturedCheckpoint envelope (Task A6)
    assert data["schema_version"] == "1"
    assert data["run_id"] == run_id
    assert data["decision_id"] == "01HDEC"
    assert "captured_at" in data
    assert data["family_view"] is None
    # Disk-reconstruction path: zero-valued generic summary
    assert data["generic"]["cash"] == 0.0
    assert data["generic"]["positions"] == []
    # Original RunnerCheckpoint payload preserved as opaque runner_state blob
    rs = data["runner_state"]
    assert rs["version"] == 1
    assert rs["accumulators"]["reconstructed"] is True
    assert rs["accumulators"]["last_portfolio"]["equity"] == 105000.0
    assert rs["trading_state"]["step_id"] == 42
    assert rs["strategy_checkpoint"]["_reconstructed"] is True


def test_capture_checkpoint_cli_missing_portfolio_errors(tmp_path, monkeypatch):
    run_id = "01HMISS"
    (tmp_path / "LiveOption" / "runs" / run_id).mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    args = SimpleNamespace(
        run_id=run_id, decision_id="D", output_path=str(tmp_path / "ckpt.json"),
    )
    try:
        cmd_capture_checkpoint(args)
    except SystemExit as e:
        assert "portfolio.jsonl not found" in str(e)
        return
    raise AssertionError("expected SystemExit")


def test_capture_checkpoint_cli_empty_portfolio_errors(tmp_path, monkeypatch):
    run_id = "01HEMPTY"
    d = tmp_path / "LiveOption" / "runs" / run_id
    d.mkdir(parents=True)
    (d / "portfolio.jsonl").write_text("")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    args = SimpleNamespace(
        run_id=run_id, decision_id="D", output_path=str(tmp_path / "ckpt.json"),
    )
    try:
        cmd_capture_checkpoint(args)
    except SystemExit as e:
        assert "empty" in str(e)
        return
    raise AssertionError("expected SystemExit")
