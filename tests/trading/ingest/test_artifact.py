import json
from pathlib import Path
from datetime import datetime, timezone
from trading.ingest.artifact import write_decision_artifact


def test_artifact_creates_three_files(tmp_path):
    run_dir = tmp_path / "run_01H"
    run_dir.mkdir()
    artifact = write_decision_artifact(
        run_dir=run_dir,
        decision_id="01HDEC",
        seq=0,
        trading_day="2025-03-14",
        decision_ts=datetime(2025, 3, 14, 18, 30, tzinfo=timezone.utc),
        model_id="gpt-4.1",
        provider="openai",
        prompt={"system": "...", "user": "..."},
        response={"raw": "{}", "parsed_trades": [], "reasoning": ""},
        context={"portfolio": {"cash": 100000}, "indicators": {}},
        trades_executed=[],
        market_snapshot={"chain": [], "underlying_quote": 555.0},
    )
    path = Path(artifact["storage_dir"])
    assert (path / "record.json").exists()
    assert (path / "market.json").exists()
    assert (path / "context.json").exists()
    record = json.loads((path / "record.json").read_text())
    assert record["decision_id"] == "01HDEC"
    assert "prompt_sha256" in record
    assert len(record["prompt_sha256"]) == 64
    # verify returned dict fields
    assert artifact["prompt_sha256"] == record["prompt_sha256"]
    assert len(artifact["response_sha256"]) == 64


def test_artifact_is_atomic(tmp_path, monkeypatch):
    """Simulate write failure -> partial files must not remain."""
    import trading.ingest.artifact as mod
    run_dir = tmp_path / "r"
    run_dir.mkdir()

    def fail_rename(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "rename", fail_rename)
    try:
        write_decision_artifact(
            run_dir=run_dir,
            decision_id="01H",
            seq=0,
            trading_day="2026-04-18",
            decision_ts=datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
            model_id="gpt-4.1",
            provider="openai",
            prompt={"system": "...", "user": "..."},
            response={"raw": "{}", "parsed_trades": [], "reasoning": ""},
            context={"portfolio": {"cash": 100000}, "indicators": {}},
            trades_executed=[],
            market_snapshot={"chain": [], "underlying_quote": 555.0},
        )
    except OSError:
        pass
    # No decisions/01H/ dir should be visible
    assert not (run_dir / "decisions" / "01H").exists()
