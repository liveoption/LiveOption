"""Tests for individual migrations beyond the initial schema.

Complements ``test_db.py`` (which covers migration 001 + idempotency) with
per-migration checks that verify the incremental DDL landed as expected.
"""

from __future__ import annotations

import aiosqlite


async def test_runs_has_launch_spec_json_column(tmp_path):
    """Migration 002: runs.launch_spec_json — nullable TEXT (JSON) column.

    Populated by POST /v1/runs for kind=batch; holds the merged spec snapshot
    so run history survives catalog edits. Left NULL for fork runs.
    """
    from liveoption_web.db.migrations import apply_migrations

    db_path = tmp_path / "test.db"
    await apply_migrations(db_path)

    async with aiosqlite.connect(str(db_path)) as db:
        cur = await db.execute("PRAGMA table_info(runs)")
        rows = await cur.fetchall()

    cols = {row[1]: row for row in rows}
    assert "launch_spec_json" in cols, (
        f"launch_spec_json column missing from runs; got: {sorted(cols)}"
    )
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    col = cols["launch_spec_json"]
    assert col[2].upper() == "TEXT", f"expected TEXT, got {col[2]!r}"
    assert col[3] == 0, "launch_spec_json must be nullable (notnull=0)"
    assert col[5] == 0, "launch_spec_json must not be part of primary key"


async def test_runs_launch_spec_json_idempotent(tmp_path):
    """Running apply_migrations twice must not fail on 002."""
    from liveoption_web.db.migrations import apply_migrations

    db_path = tmp_path / "test.db"
    await apply_migrations(db_path)
    await apply_migrations(db_path)  # re-apply must be no-op

    async with aiosqlite.connect(str(db_path)) as db:
        cur = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cur.fetchone()
        assert row[0] >= 2, f"expected schema_version >= 2, got {row[0]}"

        cur = await db.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version = 2"
        )
        row = await cur.fetchone()
        assert row[0] == 1, "migration 002 should be recorded exactly once"
