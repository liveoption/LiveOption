-- 006_runs_error_message.sql
-- Add ``runs.error_message`` so the reconciler / supervisor can record
-- the last terminal error for a run (mirrors the existing
-- ``questions.error_message``).  Required by the worker error
-- surfacing feature: runs that finished ``completed`` but accumulated
-- ``worker.error`` events get reclassified as ``completed_with_errors``
-- and need somewhere durable to stash the final user-visible message
-- without forcing the detail page to join against ``events``.

BEGIN;

ALTER TABLE runs ADD COLUMN error_message TEXT;

INSERT INTO schema_version (version, applied_at, description)
VALUES (6, strftime('%Y-%m-%dT%H:%M:%S.000Z','now'),
        'runs.error_message column for terminal worker error surfacing');

COMMIT;
