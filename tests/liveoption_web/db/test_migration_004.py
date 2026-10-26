import shutil
import sqlite3
from pathlib import Path

import pytest

FIXTURE = Path("tests/liveoption_web/db/fixtures/pre_migration.db")
MIGRATION_002_LAUNCH_SPEC = Path("liveoption_web/db/migrations/002_add_runs_launch_spec_json.sql")
MIGRATION_003 = Path("liveoption_web/db/migrations/003_run_metrics.sql")
MIGRATION_004 = Path("liveoption_web/db/migrations/004_runs_family.sql")


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
    if not MIGRATION_002_LAUNCH_SPEC.exists():
        pytest.skip("002_add_runs_launch_spec_json.sql not yet merged from main")
    _apply(target, MIGRATION_002_LAUNCH_SPEC)
    _apply(target, MIGRATION_003)
    # Insert an intraday-scenario row before 004 so backfill has a target.
    conn = sqlite3.connect(target)
    conn.execute(
        "INSERT INTO runs(id,kind,scenario,config_path,model_id,provider,"
        "date_start,date_end,status,status_updated_at,created_at,storage_dir) "
        "VALUES ('run_intraday','batch','intraday_spy_2025_q1','c.yaml','m','p',"
        "'2025-01-01','2025-01-02','completed','2025-01-02T16:00:00Z','2025-01-01T09:00:00Z','/tmp/i')"
    )
    conn.commit()
    conn.close()
    _apply(target, MIGRATION_004)
    return target


def test_family_column_added(migrated_db):
    conn = sqlite3.connect(migrated_db)
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(runs)")}
    assert "family" in cols


def test_family_check_constraint(migrated_db):
    conn = sqlite3.connect(migrated_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO runs(id,kind,scenario,config_path,model_id,provider,"
            "date_start,date_end,status,status_updated_at,created_at,storage_dir,family) "
            "VALUES ('run_bad','batch','x','c.yaml','m','p','2025-01-01','2025-01-02',"
            "'completed','2025-01-02T16:00:00Z','2025-01-01T09:00:00Z','/tmp/x','bogus')"
        )
        conn.commit()


def test_intraday_scenario_backfilled(migrated_db):
    conn = sqlite3.connect(migrated_db)
    row = conn.execute("SELECT family FROM runs WHERE id='run_intraday'").fetchone()
    assert row[0] == "intraday"


def test_other_scenarios_remain_null(migrated_db):
    conn = sqlite3.connect(migrated_db)
    row = conn.execute("SELECT family FROM runs WHERE id='run_a'").fetchone()
    assert row[0] is None


def test_family_index_created(migrated_db):
    conn = sqlite3.connect(migrated_db)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(runs)")}
    assert any("family" in idx for idx in indexes)
