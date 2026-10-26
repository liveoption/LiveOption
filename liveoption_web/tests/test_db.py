"""Tests for liveoption_web.db — migrations, WAL PRAGMA, pydantic models."""

from __future__ import annotations

import aiosqlite
import pytest


async def test_migrations_create_all_tables(tmp_path):
    from liveoption_web.db.migrations import apply_migrations

    db_path = tmp_path / "test.db"
    await apply_migrations(db_path)

    async with aiosqlite.connect(str(db_path)) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        rows = await cur.fetchall()
        tables = {r[0] for r in rows}

    assert tables >= {
        "runs",
        "decision_records",
        "checkpoints",
        "questions",
        "question_chunks",
        "events",
        "audit_log",
        "schema_version",
    }


async def test_migrations_idempotent(tmp_path):
    from liveoption_web.db.migrations import MIGRATIONS_DIR, apply_migrations

    db_path = tmp_path / "test.db"
    await apply_migrations(db_path)
    await apply_migrations(db_path)  # re-apply must be no-op

    expected_versions = sorted(
        int(path.name.split("_", 1)[0])
        for path in MIGRATIONS_DIR.glob("*.sql")
        if path.name.split("_", 1)[0].isdigit()
    )

    async with aiosqlite.connect(str(db_path)) as db:
        cur = await db.execute("SELECT version FROM schema_version ORDER BY version")
        rows = await cur.fetchall()
        assert [row[0] for row in rows] == expected_versions


async def test_wal_mode_enabled(tmp_path):
    from liveoption_web.db.connection import open_db
    from liveoption_web.db.migrations import apply_migrations

    db_path = tmp_path / "test.db"
    await apply_migrations(db_path)

    async with open_db(db_path) as db:
        cur = await db.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
        assert row[0].lower() == "wal"

        cur = await db.execute("PRAGMA foreign_keys")
        row = await cur.fetchone()
        assert row[0] == 1


async def test_partial_index_runs_active_present(tmp_path):
    """Confirm the partial index survived the migration (spec 3.3)."""
    from liveoption_web.db.migrations import apply_migrations

    db_path = tmp_path / "test.db"
    await apply_migrations(db_path)

    async with aiosqlite.connect(str(db_path)) as db:
        cur = await db.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_runs_active'"
        )
        row = await cur.fetchone()

    assert row is not None, "idx_runs_active partial index missing"
    assert "WHERE" in row[1].upper()
    assert "pending" in row[1] and "running" in row[1]


async def test_timestamp_check_rejects_bad_format(tmp_path):
    """Spec 3.5: system timestamps must match GLOB '????-??-??T??:??:??*Z'."""
    from liveoption_web.db.migrations import apply_migrations
    from liveoption_web.db.connection import open_db

    db_path = tmp_path / "test.db"
    await apply_migrations(db_path)

    async with open_db(db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                """
                INSERT INTO runs (
                    id, kind, scenario, config_path, model_id, provider,
                    date_start, date_end, status, status_updated_at,
                    created_at, storage_dir
                ) VALUES (
                    'r1', 'batch', 'weekly_llm', 'configs/x.yaml', 'm', 'p',
                    '2026-04-18', '2026-04-19', 'pending',
                    'not-a-timestamp',
                    '2026-04-18T00:00:00.000Z',
                    '/tmp/run'
                )
                """
            )
            await db.commit()


async def test_run_row_model_roundtrip():
    """Smoke test: pydantic RunRow accepts a minimal valid payload."""
    from liveoption_web.db.models import RunRow

    row = RunRow(
        id="01HABCDEF",
        kind="batch",
        scenario="weekly_llm",
        config_path="configs/weekly_llm.yaml",
        model_id="gpt-5",
        provider="openai",
        date_start="2026-01-01",
        date_end="2026-01-05",
        status="pending",
        status_updated_at="2026-04-18T00:00:00.000Z",
        created_at="2026-04-18T00:00:00.000Z",
        storage_dir="/data/runs/01HABCDEF",
    )
    assert row.kind == "batch"
    assert row.is_featured is False
    assert row.fork_mode is None


async def test_all_row_models_importable():
    """Ensure every table has a corresponding pydantic model exported."""
    from liveoption_web.db import models

    for name in (
        "RunRow",
        "DecisionRecordRow",
        "CheckpointRow",
        "QuestionRow",
        "QuestionChunkRow",
        "EventRow",
        "AuditLogRow",
        "SchemaVersionRow",
    ):
        assert hasattr(models, name), f"missing model {name}"
