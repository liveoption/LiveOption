-- 004_runs_family.sql
-- Add runs.family column for paper-aligned scenario family enum.
-- Backfill from scenario field where the mapping is unambiguous.

BEGIN;

ALTER TABLE runs ADD COLUMN family TEXT
  CHECK(family IS NULL OR family IN ('overlay','earnings','intraday'));

UPDATE runs SET family = 'intraday' WHERE scenario = 'intraday_spy_2025_q1';
-- baseline_2025_q1_*, dryrun_smoke remain NULL by design (null-family).
-- overlay_*, earnings_* scenarios don't exist yet (Phase C); no rows to backfill.

CREATE INDEX idx_runs_family_created ON runs(family, created_at);

INSERT INTO schema_version (version, applied_at, description)
VALUES (4, strftime('%Y-%m-%dT%H:%M:%S.000Z','now'), 'runs.family column with paper-aligned scenario backfill');

COMMIT;
