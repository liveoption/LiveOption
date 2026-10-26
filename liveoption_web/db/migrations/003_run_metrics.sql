-- 003_run_metrics.sql
-- Retire runs.metrics_json into a dedicated run_metrics table.
-- Per Spec §5.3: single source of truth for generic + family + behavioral metrics.
-- IRREVERSIBLE: the ALTER TABLE DROP COLUMN at the end cannot be rolled back
-- without restoring the DB from a pre-migration backup.

BEGIN;

CREATE TABLE run_metrics (
  run_id            TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
  family            TEXT,
  generic_json      TEXT NOT NULL,
  family_json       TEXT,
  behavioral_json   TEXT NOT NULL,
  computed_at       TEXT NOT NULL
    CHECK(computed_at GLOB '????-??-??T??:??:??*Z'),
  evaluator_version TEXT NOT NULL
);

INSERT INTO run_metrics (run_id, family, generic_json, family_json, behavioral_json,
                         computed_at, evaluator_version)
SELECT
  id,
  NULL,
  COALESCE(metrics_json, '{}'),
  NULL,
  '{}',
  created_at,
  'v0-migrated'
FROM runs
WHERE metrics_json IS NOT NULL;

ALTER TABLE runs DROP COLUMN metrics_json;

INSERT INTO schema_version (version, applied_at, description)
VALUES (3, strftime('%Y-%m-%dT%H:%M:%S.000Z','now'), 'run_metrics table; collapse runs.metrics_json');

COMMIT;
