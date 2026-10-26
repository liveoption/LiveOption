"""Unit tests for deploy.backfill.backfill_historical."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from deploy.backfill.backfill_historical import (
    HIST_PREFIX,
    derive_decision_id,
    derive_hist_ulid,
    iter_candidate_dirs,
    parse_historical_run,
    run_backfill,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "liveoption_web"
    / "db"
    / "migrations"
    / "001_init.sql"
)


def _apply_migrations(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def _make_run_dir(
    root: Path,
    name: str,
    *,
    days: list[str],
    scenario: str = "earnings_bet",
    model: str = "deepseek-chat",
    provider: str = "deepseek",
    include_config: bool = True,
    include_decisions: bool = False,
) -> Path:
    run_dir = root / name
    run_dir.mkdir(parents=True)

    with (run_dir / "trades.jsonl").open("w") as f:
        for i, day in enumerate(days):
            f.write(
                json.dumps(
                    {
                        "step_id": i,
                        "current_date": day,
                        "event_ts": f"{day}T20:00:00Z",
                        "fills": [{"dummy": True}],
                    }
                )
                + "\n"
            )
    (run_dir / "portfolio.jsonl").write_text("")

    if include_config:
        yaml_text = (
            f"scenario: {scenario}\n"
            f"llm:\n  model_id: {model}\n  provider: {provider}\n"
            f"date_start: {days[0]}\n"
            f"date_end: {days[-1]}\n"
            f"universe:\n  - AAPL\n  - MSFT\n"
        )
        (run_dir / "config_frozen.yaml").write_text(yaml_text)

    if include_decisions:
        decisions_dir = run_dir / "decisions"
        decisions_dir.mkdir()
        for seq, day in enumerate(days, start=1):
            d = decisions_dir / f"dec_{seq:03d}"
            d.mkdir()
            (d / "record.json").write_text(
                json.dumps(
                    {
                        "decision_id": f"dec_{seq:03d}",
                        "seq": seq,
                        "trading_day": day,
                        "decision_ts": f"{day}T21:00:00.000Z",
                        "model_id": model,
                        "provider": provider,
                        "prompt_sha256": "a" * 64,
                        "response_sha256": "b" * 64,
                        "trades_executed": [{"ticker": "AAPL"}],
                    }
                )
            )
    return run_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_derive_hist_ulid_is_deterministic_and_prefixed():
    u1 = derive_hist_ulid("/tmp/runs/alpha")
    u2 = derive_hist_ulid("/tmp/runs/alpha")
    u3 = derive_hist_ulid("/tmp/runs/beta")
    assert u1 == u2
    assert u1 != u3
    assert u1.startswith(HIST_PREFIX)
    assert len(u1) == len(HIST_PREFIX) + 21


def test_parse_historical_run_extracts_fields(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        "run1",
        days=["2025-01-02", "2025-01-03", "2025-01-06"],
    )
    hist = parse_historical_run(rd)
    assert hist is not None
    assert hist.scenario == "earnings_bet"
    assert hist.model_id == "deepseek-chat"
    assert hist.provider == "deepseek"
    assert hist.date_start == "2025-01-02"
    assert hist.date_end == "2025-01-06"
    assert "AAPL" in (hist.universe_json or "")
    assert hist.days_completed == ["2025-01-02", "2025-01-03", "2025-01-06"]
    assert hist.decisions == []


def test_parse_historical_run_tolerates_missing_config(tmp_path):
    rd = _make_run_dir(
        tmp_path, "run1", days=["2025-02-01"], include_config=False
    )
    hist = parse_historical_run(rd)
    assert hist is not None
    # Falls back to directory name + unknown model.
    assert hist.date_start == "2025-02-01"
    assert hist.date_end == "2025-02-01"
    assert hist.model_id == "unknown"


def test_parse_historical_run_skips_empty_dir(tmp_path):
    (tmp_path / "not_a_run").mkdir()
    assert parse_historical_run(tmp_path / "not_a_run") is None


def test_parse_decisions_from_artifacts(tmp_path):
    rd = _make_run_dir(
        tmp_path,
        "run1",
        days=["2025-03-10", "2025-03-11"],
        include_decisions=True,
    )
    hist = parse_historical_run(rd)
    assert hist is not None
    assert len(hist.decisions) == 2
    d0 = hist.decisions[0]
    assert d0.seq == 1
    assert d0.trading_day == "2025-03-10"
    # Z must be rewritten to +00:00 for the schema CHECK.
    assert d0.decision_ts.endswith("+00:00")
    assert d0.has_trade is True


def test_run_backfill_inserts_rows(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    _make_run_dir(results, "run1", days=["2025-01-02", "2025-01-03"])
    _make_run_dir(
        results,
        "run2",
        days=["2025-02-04"],
        scenario="overlay",
        include_decisions=True,
    )

    db_path = tmp_path / "liveoption.db"
    _apply_migrations(db_path)

    stats = run_backfill(source_dir=results, db_path=db_path)
    assert stats["inserted"] == 2
    assert stats["already_present"] == 0

    conn = sqlite3.connect(str(db_path))
    try:
        run_ids = [
            r[0]
            for r in conn.execute("SELECT id FROM runs ORDER BY id").fetchall()
        ]
        assert all(rid.startswith(HIST_PREFIX) for rid in run_ids)

        # Decision from run2 should be present.
        dec_count = conn.execute(
            "SELECT COUNT(*) FROM decision_records"
        ).fetchone()[0]
        assert dec_count == 1

        # Events: run.started + day.completed(s) + run.finished per run.
        # run1 has 2 days -> 4 events; run2 has 1 day -> 3 events; total 7.
        ev_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert ev_count == 7

        kinds = {
            r[0]
            for r in conn.execute("SELECT DISTINCT kind FROM events").fetchall()
        }
        assert kinds == {"run.started", "day.completed", "run.finished"}
    finally:
        conn.close()

    # Audit file must exist and have one line per inserted run.
    audit = db_path.with_name("backfill_audit.jsonl")
    assert audit.exists()
    lines = [json.loads(ln) for ln in audit.read_text().splitlines() if ln]
    assert len(lines) == 2
    assert {ln["run_id"] for ln in lines} == set(run_ids)


def test_run_backfill_is_idempotent(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    _make_run_dir(results, "run1", days=["2025-01-02"])

    db_path = tmp_path / "liveoption.db"
    _apply_migrations(db_path)

    first = run_backfill(source_dir=results, db_path=db_path)
    second = run_backfill(source_dir=results, db_path=db_path)
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["already_present"] == 1

    conn = sqlite3.connect(str(db_path))
    try:
        n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert n == 1
    finally:
        conn.close()

    # Audit log is written only on fresh inserts, so second run adds
    # nothing.
    audit = db_path.with_name("backfill_audit.jsonl")
    assert len(audit.read_text().splitlines()) == 1


def test_run_backfill_respects_limit(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    for i in range(3):
        _make_run_dir(results, f"run{i}", days=[f"2025-01-0{i+1}"])

    db_path = tmp_path / "liveoption.db"
    _apply_migrations(db_path)

    stats = run_backfill(source_dir=results, db_path=db_path, limit=2)
    assert stats["inserted"] == 2

    conn = sqlite3.connect(str(db_path))
    try:
        n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_run_backfill_since_filter(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    _make_run_dir(results, "old", days=["2024-06-01"])
    _make_run_dir(results, "new", days=["2025-02-02"])

    db_path = tmp_path / "liveoption.db"
    _apply_migrations(db_path)

    stats = run_backfill(
        source_dir=results, db_path=db_path, since="2025-01-01"
    )
    assert stats["inserted"] == 1
    assert stats["skipped"] == 1


def test_run_backfill_dry_run_makes_no_changes(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    _make_run_dir(results, "run1", days=["2025-01-02"])

    db_path = tmp_path / "liveoption.db"
    _apply_migrations(db_path)

    stats = run_backfill(source_dir=results, db_path=db_path, dry_run=True)
    assert stats["inserted"] == 1  # would-insert count

    conn = sqlite3.connect(str(db_path))
    try:
        n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert n == 0
    finally:
        conn.close()
    audit = db_path.with_name("backfill_audit.jsonl")
    assert not audit.exists()


def test_iter_candidate_dirs_walks_two_levels(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    # level-1 run
    _make_run_dir(root, "run_top", days=["2025-01-02"])
    # level-2 run
    bucket = root / "bucket"
    bucket.mkdir()
    _make_run_dir(bucket, "run_nested", days=["2025-01-03"])

    names = {p.name for p in iter_candidate_dirs(root)}
    assert names == {"run_top", "run_nested"}


def test_decision_id_is_stable_and_unique():
    a1 = derive_decision_id("HIST_ABC", 1)
    a2 = derive_decision_id("HIST_ABC", 1)
    a3 = derive_decision_id("HIST_ABC", 2)
    assert a1 == a2 and a1 != a3
    assert a1.startswith("HDEC_")


def test_missing_source_dir_raises(tmp_path):
    db_path = tmp_path / "liveoption.db"
    _apply_migrations(db_path)
    with pytest.raises(FileNotFoundError):
        run_backfill(source_dir=tmp_path / "does_not_exist", db_path=db_path)


def test_missing_schema_raises(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    _make_run_dir(results, "run1", days=["2025-01-02"])
    # Fresh empty DB without migrations applied.
    db_path = tmp_path / "empty.db"
    sqlite3.connect(str(db_path)).close()
    with pytest.raises(RuntimeError, match="missing the 'runs' table"):
        run_backfill(source_dir=results, db_path=db_path)
