"""Integration tests for the worker.error → ``completed_with_errors`` promotion.

Covers spec §3.2:

* exit_code=0 with ≥1 ``severity=error`` worker.error events →
  ``completed_with_errors`` + ``error_message`` populated from the most
  recent error.
* exit_code=0 with only ``severity=warning`` events → ``completed``
  unchanged.
* exit_code != 0 → ``failed``; ``error_message`` is taken from the last
  worker.error if any exist (preserves the current ``failed`` semantics
  while making the message visible to the detail header).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _base_run_cols(
    *,
    run_id: str,
    storage_dir: Path,
    status: str,
    pid: int | None,
) -> dict:
    return {
        "id": run_id,
        "kind": "baseline",
        "parent_run_id": None,
        "parent_checkpoint_id": None,
        "scenario": "test",
        "config_path": "cfg.yaml",
        "model_id": "m",
        "provider": "p",
        "universe_json": None,
        "date_start": "2025-01-01",
        "date_end": "2025-01-02",
        "status": status,
        "status_updated_at": "2026-01-01T00:00:00Z",
        "pid": pid,
        "exit_code": None,
        "last_heartbeat_at": None,
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": None,
        "title": None,
        "notes": None,
        "is_featured": 0,
        "storage_dir": str(storage_dir),
        "fork_mode": None,
        "fork_prompt_override": None,
        "fork_model_override": None,
    }


async def _insert_run(db, **cols) -> None:
    keys = list(cols.keys())
    placeholders = ",".join(["?"] * len(keys))
    await db.execute(
        f"INSERT INTO runs ({','.join(keys)}) VALUES ({placeholders})",
        tuple(cols[k] for k in keys),
    )


async def _insert_worker_error(
    db,
    *,
    run_id: str,
    seq: int,
    ts: str,
    severity: str,
    message: str,
    category: str = "auth",
) -> None:
    payload = {
        "severity": severity,
        "category": category,
        "message": message,
        "count": seq,
        "first_ts": ts,
        "last_ts": ts,
    }
    await db.execute(
        "INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json) "
        "VALUES (?, ?, ?, 'worker.error', ?)",
        (run_id, seq, ts, json.dumps(payload)),
    )


def _prep_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "s")
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))


@pytest.mark.asyncio
async def test_exit_zero_with_error_events_promotes_to_completed_with_errors(
    tmp_path, monkeypatch
):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HERR"
    run_dir.mkdir(parents=True)
    (run_dir / "exit_code.txt").write_text("0")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HERR",
                storage_dir=run_dir,
                status="running",
                pid=2**31 - 21,
            ),
        )
        await _insert_worker_error(
            db,
            run_id="01HERR",
            seq=1,
            ts="2026-01-01T00:00:01Z",
            severity="error",
            message="Qwen API returned 401",
        )
        await _insert_worker_error(
            db,
            run_id="01HERR",
            seq=2,
            ts="2026-01-01T00:00:02Z",
            severity="error",
            message="Qwen API returned 401 (count=10)",
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT status, exit_code, error_message FROM runs WHERE id='01HERR'"
        )
        row = await cur.fetchone()
        assert row[0] == "completed_with_errors"
        assert row[1] == 0
        # Most recent event wins (max id == seq=2).
        assert row[2] == "Qwen API returned 401 (count=10)"


@pytest.mark.asyncio
async def test_exit_zero_with_only_warnings_stays_completed(tmp_path, monkeypatch):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HWARN"
    run_dir.mkdir(parents=True)
    (run_dir / "exit_code.txt").write_text("0")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HWARN",
                storage_dir=run_dir,
                status="running",
                pid=2**31 - 22,
            ),
        )
        await _insert_worker_error(
            db,
            run_id="01HWARN",
            seq=1,
            ts="2026-01-01T00:00:01Z",
            severity="warning",
            message="parse warning",
            category="parse",
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT status, exit_code, error_message FROM runs WHERE id='01HWARN'"
        )
        row = await cur.fetchone()
        assert row[0] == "completed"
        assert row[1] == 0
        assert row[2] is None


@pytest.mark.asyncio
async def test_exit_nonzero_failed_populates_error_message_from_last_event(
    tmp_path, monkeypatch
):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HFAIL2"
    run_dir.mkdir(parents=True)
    (run_dir / "exit_code.txt").write_text("1")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HFAIL2",
                storage_dir=run_dir,
                status="running",
                pid=2**31 - 23,
            ),
        )
        await _insert_worker_error(
            db,
            run_id="01HFAIL2",
            seq=1,
            ts="2026-01-01T00:00:01Z",
            severity="error",
            message="write_artifact raised",
            category="artifact_write",
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT status, exit_code, error_message FROM runs WHERE id='01HFAIL2'"
        )
        row = await cur.fetchone()
        assert row[0] == "failed"
        assert row[1] == 1
        assert row[2] == "write_artifact raised"


@pytest.mark.asyncio
async def test_exit_nonzero_no_events_leaves_error_message_null(
    tmp_path, monkeypatch
):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HFAIL3"
    run_dir.mkdir(parents=True)
    (run_dir / "exit_code.txt").write_text("9")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HFAIL3",
                storage_dir=run_dir,
                status="running",
                pid=2**31 - 24,
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT status, exit_code, error_message FROM runs WHERE id='01HFAIL3'"
        )
        row = await cur.fetchone()
        assert row[0] == "failed"
        assert row[1] == 9
        assert row[2] is None
