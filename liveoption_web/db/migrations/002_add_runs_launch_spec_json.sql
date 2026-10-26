-- Migration 002: runs.launch_spec_json
-- Merged spec snapshot (JSON) recorded at batch-launch time so that run
-- history survives subsequent catalog edits. NULL for fork runs.

ALTER TABLE runs ADD COLUMN launch_spec_json TEXT;

INSERT INTO schema_version (version, applied_at, description)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%S.000Z','now'), 'runs.launch_spec_json');
