"""Phase C8 integration test: status-flip → run_metrics auto-populate.

Drives `_watch_subprocess` directly with a stub Process to verify the
lifespan hook (added in Phase C6) dispatches `aggregate_for` and writes
a `run_metrics` row when the subprocess exits cleanly.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from liveoption_web.db.migrations import apply_migrations
from liveoption_web.runs.supervisor import _watch_subprocess


class _FakeProc:
    def __init__(self, return_code: int = 0):
        self._rc = return_code
        self.pid = 9999

    async def wait(self) -> int:
        return self._rc


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "db.sqlite"
    asyncio.run(apply_migrations(p))
    return p


def _seed_running_run(db_path: Path, run_id: str, family: str | None) -> Path:
    storage = db_path.parent / "runs" / run_id
    storage.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id,
                                provider, date_start, date_end, status,
                                status_updated_at, created_at, storage_dir,
                                family)
               VALUES(?, 'batch', 'x', 'x.yaml', 'm', 'p',
                      '2025-01-01', '2025-01-02', 'running',
                      '2026-04-25T00:00:00Z', '2026-04-25T00:00:00Z',
                      ?, ?)""",
            (run_id, str(storage), family),
        )
        # Seed one day.completed event so generic_stats returns non-empty.
        conn.execute(
            """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
               VALUES(?, 1, '2026-04-25T00:00:00.000Z', 'day.completed', ?)""",
            (
                run_id,
                json.dumps(
                    {
                        "trading_day": "2025-01-02",
                        "pnl_delta": 100.0,
                        "cum_pnl": 100.0,
                        "portfolio_value": 100100.0,
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return storage


@pytest.mark.asyncio
async def test_watcher_completion_triggers_run_metrics_upsert(
    tmp_path, db_path
):
    run_id = "01HWATCH_OK"
    storage = _seed_running_run(db_path, run_id, family="overlay")
    proc = _FakeProc(return_code=0)
    stdout_fp = open(storage / "stdout.log", "w")

    await _watch_subprocess(db_path, run_id, proc, stdout_fp)

    conn = sqlite3.connect(db_path)
    try:
        status = conn.execute(
            "SELECT status FROM runs WHERE id=?", (run_id,)
        ).fetchone()[0]
        row = conn.execute(
            "SELECT family, generic_json, family_json, behavioral_json,"
            "       evaluator_version FROM run_metrics WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    assert status == "completed"
    assert row is not None
    family, generic_json, family_json, behavioral_json, version = row
    assert family == "overlay"
    g = json.loads(generic_json)
    assert g["n_days"] == 1
    fs = json.loads(family_json)
    assert "overlay_income_total" in fs
    b = json.loads(behavioral_json)
    assert "intent_consistency" in b
    assert "v1-phaseC" in version


@pytest.mark.asyncio
async def test_watcher_failure_also_triggers_evaluator(tmp_path, db_path):
    run_id = "01HWATCH_FAIL"
    storage = _seed_running_run(db_path, run_id, family=None)
    proc = _FakeProc(return_code=1)
    stdout_fp = open(storage / "stdout.log", "w")

    await _watch_subprocess(db_path, run_id, proc, stdout_fp)

    conn = sqlite3.connect(db_path)
    try:
        status = conn.execute(
            "SELECT status FROM runs WHERE id=?", (run_id,)
        ).fetchone()[0]
        row = conn.execute(
            "SELECT family FROM run_metrics WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert status == "failed"
    # Even on failure the run should have a metrics row (null-family).
    assert row is not None and row[0] is None
