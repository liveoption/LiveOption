import shutil
import sqlite3
from pathlib import Path

import pytest

FIXTURE = Path("tests/liveoption_web/db/fixtures/pre_migration.db")
MIGRATION_002_LAUNCH_SPEC = Path("liveoption_web/db/migrations/002_add_runs_launch_spec_json.sql")
MIGRATION_003 = Path("liveoption_web/db/migrations/003_run_metrics.sql")


def _apply(db_path: Path, sql_path: Path) -> None:
    sql = sql_path.read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def migrated_db(tmp_path):
    target = tmp_path / "test.db"
    shutil.copy(FIXTURE, target)
    if MIGRATION_002_LAUNCH_SPEC.exists():
        _apply(target, MIGRATION_002_LAUNCH_SPEC)
    else:
        pytest.skip("002_add_runs_launch_spec_json.sql not yet merged from main")
    _apply(target, MIGRATION_003)
    return target


def test_run_metrics_table_created(migrated_db):
    conn = sqlite3.connect(migrated_db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(run_metrics)")}
    assert {"run_id", "family", "generic_json", "family_json", "behavioral_json", "computed_at", "evaluator_version"} <= cols


def test_runs_metrics_json_dropped(migrated_db):
    conn = sqlite3.connect(migrated_db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert "metrics_json" not in cols


def test_completed_run_with_metrics_migrated(migrated_db):
    conn = sqlite3.connect(migrated_db)
    row = conn.execute(
        "SELECT generic_json, family, family_json, behavioral_json, evaluator_version "
        "FROM run_metrics WHERE run_id = 'run_a'"
    ).fetchone()
    assert row is not None
    generic_json, family, family_json, behavioral_json, ev_version = row
    assert "ar" in generic_json
    assert family is None
    assert family_json is None
    assert behavioral_json == "{}"
    assert ev_version == "v0-migrated"


def test_completed_run_without_metrics_skipped(migrated_db):
    conn = sqlite3.connect(migrated_db)
    row = conn.execute("SELECT 1 FROM run_metrics WHERE run_id = 'run_b'").fetchone()
    assert row is None


def test_running_run_skipped(migrated_db):
    conn = sqlite3.connect(migrated_db)
    row = conn.execute("SELECT 1 FROM run_metrics WHERE run_id = 'run_c'").fetchone()
    assert row is None


def test_run_metrics_pk_and_fk(migrated_db):
    conn = sqlite3.connect(migrated_db)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO run_metrics(run_id,family,generic_json,family_json,behavioral_json,computed_at,evaluator_version) "
            "VALUES ('run_a',NULL,'{}',NULL,'{}','2025-01-15T00:00:00Z','v1')"
        )
        conn.commit()
