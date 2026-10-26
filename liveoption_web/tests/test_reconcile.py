"""Tests for liveoption_web.events.reconcile."""

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
    created_at: str = "2026-01-01T00:00:00Z",
    status_updated_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    """Minimal non-null columns for the ``runs`` table."""
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
        "status_updated_at": status_updated_at,
        "pid": pid,
        "exit_code": None,
        "last_heartbeat_at": None,
        "created_at": created_at,
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


def _prep_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "s")
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))


@pytest.mark.asyncio
async def test_reconcile_marks_dead_pid_as_orphaned(tmp_path, monkeypatch):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HDEAD"
    run_dir.mkdir(parents=True)

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HDEAD",
                storage_dir=run_dir,
                status="running",
                # Very unlikely to be alive; os.kill(pid, 0) will raise.
                pid=2**31 - 2,
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute("SELECT status FROM runs WHERE id='01HDEAD'")
        row = await cur.fetchone()
        assert row[0] == "orphaned"


@pytest.mark.asyncio
async def test_reconcile_reads_exit_code_txt_success(tmp_path, monkeypatch):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HOK"
    run_dir.mkdir(parents=True)
    (run_dir / "exit_code.txt").write_text("0\n")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HOK",
                storage_dir=run_dir,
                status="running",
                pid=2**31 - 3,
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT status, exit_code FROM runs WHERE id='01HOK'"
        )
        row = await cur.fetchone()
        assert row[0] == "completed"
        assert row[1] == 0


@pytest.mark.asyncio
async def test_reconcile_reads_exit_code_txt_failure(tmp_path, monkeypatch):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HFAIL"
    run_dir.mkdir(parents=True)
    (run_dir / "exit_code.txt").write_text("42")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HFAIL",
                storage_dir=run_dir,
                status="running",
                pid=2**31 - 4,
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT status, exit_code FROM runs WHERE id='01HFAIL'"
        )
        row = await cur.fetchone()
        assert row[0] == "failed"
        assert row[1] == 42


@pytest.mark.asyncio
async def test_reconcile_scans_all_non_terminal_not_just_recent(tmp_path, monkeypatch):
    """Regression: spec 4.4 removed 7-day cutoff — old non-terminal runs are still reconciled."""
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    old_dir = tmp_path / "runs" / "01HOLD"
    old_dir.mkdir(parents=True)

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HOLD",
                storage_dir=old_dir,
                status="running",
                pid=2**31 - 5,
                created_at="2025-01-01T00:00:00Z",  # far in the past
                status_updated_at="2025-01-01T00:00:00Z",
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute("SELECT status FROM runs WHERE id='01HOLD'")
        row = await cur.fetchone()
        assert row[0] == "orphaned"


@pytest.mark.asyncio
async def test_reconcile_drops_tmp_checkpoints(tmp_path, monkeypatch):
    """``status='writing'`` checkpoint with a partial final dir gets the
    dir removed and the row deleted so ``promote_decision`` can retry."""
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HRUN"
    run_dir.mkdir(parents=True)
    # Parent run can be in any terminal/non-terminal state; FK requires it exist.
    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HRUN",
                storage_dir=run_dir,
                status="completed",
                pid=None,
            ),
        )
        # decision record is required for checkpoint FK.
        await db.execute(
            """INSERT INTO decision_records(
                 id, run_id, seq, trading_day, decision_ts, model_id, provider,
                 prompt_sha256, response_sha256, storage_dir, has_trade, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "dr1",
                "01HRUN",
                1,
                "2025-01-01",
                "2025-01-01T09:30:00-05:00",
                "m",
                "p",
                "a" * 64,
                "b" * 64,
                str(run_dir / "dr1"),
                0,
                "2026-01-01T00:00:00Z",
            ),
        )

        ckpt_dir = tmp_path / "runs" / "01HRUN" / "ckpt_tmp"
        ckpt_dir.mkdir(parents=True)
        # no metadata.json — simulates a partial write.
        await db.execute(
            """INSERT INTO checkpoints(
                 id, run_id, decision_record_id, storage_dir, fingerprint,
                 portfolio_summary_json, promoted_at, promoted_by, status, status_updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "ck1",
                "01HRUN",
                "dr1",
                str(ckpt_dir),
                "fp",
                None,
                "2026-01-01T00:00:00Z",
                "system",
                "writing",
                "2026-01-01T00:00:00Z",
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    assert not ckpt_dir.exists()
    async with open_db(s.db_path) as db:
        cur = await db.execute("SELECT id FROM checkpoints WHERE id='ck1'")
        assert await cur.fetchone() is None


@pytest.mark.asyncio
async def test_app_lifespan_runs_reconcile_on_startup(tmp_path, monkeypatch):
    """Regression: the FastAPI lifespan must invoke
    ``reconcile_on_startup`` — otherwise restart-recovery is silently
    disabled and orphaned/crashed rows stay non-terminal forever."""
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.main import create_app

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HDEAD2"
    run_dir.mkdir(parents=True)

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HDEAD2",
                storage_dir=run_dir,
                status="running",
                pid=2**31 - 11,
            ),
        )
        await db.commit()

    app = await create_app()
    # Drive ASGI lifespan manually (ASGITransport doesn't auto-fire it).
    received: list[dict] = []

    async def send(msg):
        received.append(msg)

    async def recv_startup():
        return {"type": "lifespan.startup"}

    async def recv_shutdown():
        return {"type": "lifespan.shutdown"}

    recv_queue = [recv_startup, recv_shutdown]

    async def receive():
        return await recv_queue.pop(0)()

    import asyncio

    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    )
    # Wait until startup completes.
    while not any(m["type"] == "lifespan.startup.complete" for m in received):
        await asyncio.sleep(0.01)

    async with open_db(s.db_path) as db:
        cur = await db.execute("SELECT status FROM runs WHERE id='01HDEAD2'")
        row = await cur.fetchone()
        assert row[0] == "orphaned", "reconcile should have fired on lifespan startup"

    # Clean shutdown.
    await task


@pytest.mark.asyncio
async def test_reconcile_drops_writing_row_when_final_dir_absent(tmp_path, monkeypatch):
    """Spec 3.6 state machine: a ``writing`` row whose final dir never
    materialized is deleted outright (plus any ``<storage_dir>.tmp``
    sibling is cleaned) so a retry can re-use the decision_record_id."""
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HRUN2"
    run_dir.mkdir(parents=True)
    ckroot = run_dir / "checkpoints"
    ckroot.mkdir()
    final_dir = ckroot / "01HCK2"  # never created
    tmp_sibling = ckroot / "01HCK2.tmp"
    tmp_sibling.mkdir()
    (tmp_sibling / "record.json").write_text("{}")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HRUN2", storage_dir=run_dir, status="completed", pid=None
            ),
        )
        await db.execute(
            """INSERT INTO decision_records(
                 id, run_id, seq, trading_day, decision_ts, model_id, provider,
                 prompt_sha256, response_sha256, storage_dir, has_trade, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "dr2", "01HRUN2", 1, "2025-01-01",
                "2025-01-01T09:30:00-05:00", "m", "p",
                "a" * 64, "b" * 64, str(run_dir / "dr2"), 0,
                "2026-01-01T00:00:00Z",
            ),
        )
        await db.execute(
            """INSERT INTO checkpoints(
                 id, run_id, decision_record_id, storage_dir, fingerprint,
                 portfolio_summary_json, promoted_at, promoted_by, status,
                 status_updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "01HCK2", "01HRUN2", "dr2", str(final_dir), "fp", None,
                "2026-01-01T00:00:00Z", "alice", "writing",
                "2026-01-01T00:00:00Z",
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    # Tmp sibling cleaned up, row deleted (final dir never existed).
    assert not tmp_sibling.exists()
    async with open_db(s.db_path) as db:
        cur = await db.execute("SELECT id FROM checkpoints WHERE id='01HCK2'")
        assert await cur.fetchone() is None


@pytest.mark.asyncio
async def test_reconcile_deletes_writing_row_with_final_dir_present(
    tmp_path, monkeypatch
):
    """A ``writing`` row whose final dir still exists on disk (even with
    a ``metadata.json``) was interrupted mid-transition — the tmp→final
    rename is the atomic commit point, so any on-disk artifact is
    presumptively partial.  Reconcile must remove the dir AND delete the
    row so ``promote_decision`` can retry against the same
    decision_record_id."""
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HRUN3"
    run_dir.mkdir(parents=True)
    ckpt_dir = run_dir / "ckpt_final"
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "metadata.json").write_text("{}")  # partial but has metadata
    tmp_sibling = Path(str(ckpt_dir) + ".tmp")
    tmp_sibling.mkdir()
    (tmp_sibling / "scratch.bin").write_text("x")

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HRUN3",
                storage_dir=run_dir,
                status="completed",
                pid=None,
            ),
        )
        await db.execute(
            """INSERT INTO decision_records(
                 id, run_id, seq, trading_day, decision_ts, model_id, provider,
                 prompt_sha256, response_sha256, storage_dir, has_trade, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "dr3", "01HRUN3", 1, "2025-01-01",
                "2025-01-01T09:30:00-05:00", "m", "p",
                "a" * 64, "b" * 64, str(run_dir / "dr3"), 0,
                "2026-01-01T00:00:00Z",
            ),
        )
        await db.execute(
            """INSERT INTO checkpoints(
                 id, run_id, decision_record_id, storage_dir, fingerprint,
                 portfolio_summary_json, promoted_at, promoted_by, status,
                 status_updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "ck3", "01HRUN3", "dr3", str(ckpt_dir), "fp", None,
                "2026-01-01T00:00:00Z", "alice", "writing",
                "2026-01-01T00:00:00Z",
            ),
        )
        await db.commit()

    await reconcile_on_startup(s)

    assert not ckpt_dir.exists()
    assert not tmp_sibling.exists()
    async with open_db(s.db_path) as db:
        cur = await db.execute("SELECT id FROM checkpoints WHERE id='ck3'")
        assert await cur.fetchone() is None


@pytest.mark.asyncio
async def test_reconcile_tails_events_jsonl(tmp_path, monkeypatch):
    """events.jsonl lines missing from the DB are backfilled (INSERT OR IGNORE)."""
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    import os

    live_pid = os.getpid()  # alive → run stays 'running' and JSONL backfill runs.
    run_dir = tmp_path / "runs" / "01HLIVE"
    run_dir.mkdir(parents=True)
    jsonl_path = run_dir / "events.jsonl"
    events = [
        {
            "subprocess_seq": 1,
            "ts": "2026-01-01T00:00:01Z",
            "kind": "run.started",
            "payload": {},
        },
        {
            "subprocess_seq": 2,
            "ts": "2026-01-01T00:00:02Z",
            "kind": "heartbeat",
            "payload": {"x": 1},
        },
        "{not valid json",  # corrupt line — must be skipped, not crash.
    ]
    jsonl_path.write_text(
        "\n".join(
            json.dumps(e) if isinstance(e, dict) else e for e in events
        )
    )

    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HLIVE",
                storage_dir=run_dir,
                status="running",
                pid=live_pid,
            ),
        )
        # Seed one pre-existing row; backfill must not duplicate it.
        await db.execute(
            """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            ("01HLIVE", 1, "2026-01-01T00:00:01Z", "run.started", "{}"),
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT subprocess_seq, kind FROM events WHERE run_id='01HLIVE' ORDER BY subprocess_seq"
        )
        rows = await cur.fetchall()
        assert [(r[0], r[1]) for r in rows] == [(1, "run.started"), (2, "heartbeat")]

        # Run should remain 'running' because live pid is alive.
        cur = await db.execute("SELECT status FROM runs WHERE id='01HLIVE'")
        assert (await cur.fetchone())[0] == "running"


@pytest.mark.asyncio
async def test_reconcile_streaming_question_with_chunks(tmp_path, monkeypatch):
    _prep_env(tmp_path, monkeypatch)
    from liveoption_web.config import get_settings
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.events.reconcile import reconcile_on_startup

    s = get_settings()
    await apply_migrations(s.db_path)

    run_dir = tmp_path / "runs" / "01HQ"
    run_dir.mkdir(parents=True)
    async with open_db(s.db_path) as db:
        await _insert_run(
            db,
            **_base_run_cols(
                run_id="01HQ",
                storage_dir=run_dir,
                status="completed",
                pid=None,
            ),
        )
        await db.execute(
            """INSERT INTO decision_records(
                 id, run_id, seq, trading_day, decision_ts, model_id, provider,
                 prompt_sha256, response_sha256, storage_dir, has_trade, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "drQ",
                "01HQ",
                1,
                "2025-01-01",
                "2025-01-01T09:30:00-05:00",
                "m",
                "p",
                "a" * 64,
                "b" * 64,
                str(run_dir / "drQ"),
                0,
                "2026-01-01T00:00:00Z",
            ),
        )
        await db.execute(
            """INSERT INTO checkpoints(
                 id, run_id, decision_record_id, storage_dir, fingerprint,
                 portfolio_summary_json, promoted_at, promoted_by, status, status_updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "ckQ",
                "01HQ",
                "drQ",
                str(run_dir / "ckQ"),
                "fp",
                None,
                "2026-01-01T00:00:00Z",
                "system",
                "active",
                "2026-01-01T00:00:00Z",
            ),
        )
        # Question with chunks → should be synthesized to 'completed'.
        await db.execute(
            """INSERT INTO questions(
                 id, checkpoint_id, asked_by, asked_at, user_prompt,
                 model_id, provider, status, status_updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "q1",
                "ckQ",
                "u",
                "2026-01-01T00:00:00Z",
                "hi",
                "m",
                "p",
                "streaming",
                "2026-01-01T00:00:00Z",
            ),
        )
        # Question without chunks → should become 'failed'.
        await db.execute(
            """INSERT INTO questions(
                 id, checkpoint_id, asked_by, asked_at, user_prompt,
                 model_id, provider, status, status_updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "q2",
                "ckQ",
                "u",
                "2026-01-01T00:00:00Z",
                "hi",
                "m",
                "p",
                "streaming",
                "2026-01-01T00:00:00Z",
            ),
        )
        await db.execute(
            "INSERT INTO question_chunks(question_id, seq, ts, delta_text) VALUES (?,?,?,?)",
            ("q1", 1, "2026-01-01T00:00:00Z", "Hel"),
        )
        await db.execute(
            "INSERT INTO question_chunks(question_id, seq, ts, delta_text) VALUES (?,?,?,?)",
            ("q1", 2, "2026-01-01T00:00:01Z", "lo"),
        )
        await db.commit()

    await reconcile_on_startup(s)

    async with open_db(s.db_path) as db:
        cur = await db.execute(
            "SELECT status, response_text FROM questions WHERE id='q1'"
        )
        row = await cur.fetchone()
        assert row[0] == "completed"
        assert row[1] == "Hello"

        cur = await db.execute(
            "SELECT status, error_message FROM questions WHERE id='q2'"
        )
        row = await cur.fetchone()
        assert row[0] == "failed"
        assert "streaming" in (row[1] or "")
