"""End-to-end test of apply_migrations against an empty DB.

Catches the failure mode where a migration script forgets to record itself
in the schema_version table, which would cause re-execution on next startup
and crash with table-exists / duplicate-column errors.
"""
import sqlite3

import pytest

from liveoption_web.db.migrations import MIGRATIONS_DIR, apply_migrations


def _migration_versions():
    return sorted(
        int(path.name.split("_", 1)[0])
        for path in MIGRATIONS_DIR.glob("*.sql")
        if path.name.split("_", 1)[0].isdigit()
    )


async def test_apply_migrations_records_all_versions(tmp_path):
    db = tmp_path / "test.db"
    await apply_migrations(db)

    conn = sqlite3.connect(db)
    rows = list(conn.execute("SELECT version FROM schema_version ORDER BY version"))
    versions = [r[0] for r in rows]
    assert versions == _migration_versions(), versions


async def test_apply_migrations_is_idempotent(tmp_path):
    """Second invocation must be a no-op — re-running 003 would crash because
    run_metrics already exists; re-running 004 would crash because the family
    column already exists."""
    db = tmp_path / "test.db"
    await apply_migrations(db)
    await apply_migrations(db)  # must not raise

    conn = sqlite3.connect(db)
    rows = list(conn.execute("SELECT version FROM schema_version ORDER BY version"))
    versions = [r[0] for r in rows]
    # Idempotent: same version set, no duplicates.
    assert versions == _migration_versions(), versions


async def test_apply_migrations_creates_runs_metrics_and_family(tmp_path):
    """Smoke: after a fresh apply, the runs table has family but not
    metrics_json, and run_metrics exists."""
    db = tmp_path / "test.db"
    await apply_migrations(db)

    conn = sqlite3.connect(db)
    runs_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert "family" in runs_cols
    assert "metrics_json" not in runs_cols

    rm_cols = {row[1] for row in conn.execute("PRAGMA table_info(run_metrics)")}
    assert {"run_id", "generic_json", "behavioral_json", "evaluator_version"} <= rm_cols
