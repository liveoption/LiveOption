-- liveoption_web initial schema (migration 001)
-- Matches Spec 3.3 (2026-04-18-web-interface-design.md).
-- Timestamp CHECK constraints per Spec 3.5: GLOB '????-??-??T??:??:??*Z'.

-- ---------------------------------------------------------------------------
-- runs
-- ---------------------------------------------------------------------------
CREATE TABLE runs (
  id                   TEXT PRIMARY KEY,
  kind                 TEXT NOT NULL,
  parent_run_id        TEXT,
  parent_checkpoint_id TEXT,
  scenario             TEXT NOT NULL,
  config_path          TEXT NOT NULL,
  model_id             TEXT NOT NULL,
  provider             TEXT NOT NULL,
  universe_json        TEXT,
  date_start           TEXT NOT NULL,
  date_end             TEXT NOT NULL,
  status               TEXT NOT NULL,
  status_updated_at    TEXT NOT NULL
    CHECK(status_updated_at GLOB '????-??-??T??:??:??*Z'),
  pid                  INTEGER,
  exit_code            INTEGER,
  last_heartbeat_at    TEXT
    CHECK(last_heartbeat_at IS NULL OR last_heartbeat_at GLOB '????-??-??T??:??:??*Z'),
  created_at           TEXT NOT NULL
    CHECK(created_at GLOB '????-??-??T??:??:??*Z'),
  created_by           TEXT,
  title                TEXT,
  notes                TEXT,
  is_featured          INTEGER NOT NULL DEFAULT 0,
  storage_dir          TEXT NOT NULL,
  fork_mode            TEXT,
  fork_prompt_override TEXT,
  fork_model_override  TEXT,
  metrics_json         TEXT
);
CREATE INDEX idx_runs_scenario_status ON runs(scenario, status);
CREATE INDEX idx_runs_parent ON runs(parent_run_id);
CREATE INDEX idx_runs_created_at ON runs(created_at);
CREATE INDEX idx_runs_active ON runs(status_updated_at DESC)
  WHERE status IN ('pending','running');

-- ---------------------------------------------------------------------------
-- decision_records
-- ---------------------------------------------------------------------------
CREATE TABLE decision_records (
  id                     TEXT PRIMARY KEY,
  run_id                 TEXT NOT NULL REFERENCES runs(id),
  seq                    INTEGER NOT NULL,
  trading_day            TEXT NOT NULL
    CHECK(trading_day GLOB '????-??-??'),
  decision_ts            TEXT NOT NULL
    CHECK(decision_ts GLOB '????-??-??T??:??:??*[-+]??:??'),
  model_id               TEXT NOT NULL,
  provider               TEXT NOT NULL,
  prompt_sha256          TEXT NOT NULL,
  response_sha256        TEXT NOT NULL,
  storage_dir            TEXT NOT NULL,
  has_trade              INTEGER NOT NULL,
  promoted_checkpoint_id TEXT,
  cum_pnl_after          REAL,
  created_at             TEXT NOT NULL
    CHECK(created_at GLOB '????-??-??T??:??:??*Z'),
  UNIQUE (run_id, seq)
);
CREATE INDEX idx_dr_run_day ON decision_records(run_id, trading_day);
CREATE INDEX idx_dr_promoted ON decision_records(promoted_checkpoint_id);

-- ---------------------------------------------------------------------------
-- checkpoints
-- ---------------------------------------------------------------------------
CREATE TABLE checkpoints (
  id                     TEXT PRIMARY KEY,
  run_id                 TEXT NOT NULL REFERENCES runs(id),
  decision_record_id     TEXT NOT NULL REFERENCES decision_records(id),
  storage_dir            TEXT NOT NULL,
  fingerprint            TEXT NOT NULL,
  portfolio_summary_json TEXT,
  promoted_at            TEXT NOT NULL
    CHECK(promoted_at GLOB '????-??-??T??:??:??*Z'),
  promoted_by            TEXT NOT NULL,
  status                 TEXT NOT NULL,
  status_updated_at      TEXT NOT NULL
    CHECK(status_updated_at GLOB '????-??-??T??:??:??*Z'),
  label                  TEXT,
  UNIQUE (decision_record_id)
);
CREATE INDEX idx_ckpt_run ON checkpoints(run_id);

-- ---------------------------------------------------------------------------
-- questions
-- ---------------------------------------------------------------------------
CREATE TABLE questions (
  id                 TEXT PRIMARY KEY,
  checkpoint_id      TEXT NOT NULL REFERENCES checkpoints(id),
  parent_question_id TEXT REFERENCES questions(id),
  asked_by           TEXT NOT NULL,
  asked_at           TEXT NOT NULL
    CHECK(asked_at GLOB '????-??-??T??:??:??*Z'),
  user_prompt        TEXT NOT NULL,
  model_id           TEXT NOT NULL,
  provider           TEXT NOT NULL,
  response_text      TEXT,
  response_tokens    INTEGER,
  latency_ms         INTEGER,
  cost_usd           REAL,
  status             TEXT NOT NULL,
  status_updated_at  TEXT NOT NULL
    CHECK(status_updated_at GLOB '????-??-??T??:??:??*Z'),
  error_message      TEXT
);
CREATE INDEX idx_q_checkpoint ON questions(checkpoint_id);
CREATE INDEX idx_q_parent ON questions(parent_question_id);

-- ---------------------------------------------------------------------------
-- question_chunks (audit-friendly streaming)
-- ---------------------------------------------------------------------------
CREATE TABLE question_chunks (
  question_id TEXT NOT NULL REFERENCES questions(id),
  seq         INTEGER NOT NULL,
  ts          TEXT NOT NULL
    CHECK(ts GLOB '????-??-??T??:??:??*Z'),
  delta_text  TEXT NOT NULL,
  PRIMARY KEY (question_id, seq)
);

-- ---------------------------------------------------------------------------
-- events (SSE source of truth)
-- ---------------------------------------------------------------------------
CREATE TABLE events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         TEXT NOT NULL REFERENCES runs(id),
  subprocess_seq INTEGER NOT NULL,
  ts             TEXT NOT NULL
    CHECK(ts GLOB '????-??-??T??:??:??*Z'),
  kind           TEXT NOT NULL,
  payload_json   TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_events_seq ON events(run_id, subprocess_seq);
CREATE INDEX idx_events_run_id ON events(run_id, id);

-- ---------------------------------------------------------------------------
-- audit_log (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL
    CHECK(ts GLOB '????-??-??T??:??:??*Z'),
  actor_user_id TEXT,
  actor_email   TEXT,
  action        TEXT NOT NULL,
  target_type   TEXT NOT NULL,
  target_id     TEXT NOT NULL,
  payload_json  TEXT
);
CREATE INDEX idx_audit_target ON audit_log(target_type, target_id);
CREATE INDEX idx_audit_actor_ts ON audit_log(actor_user_id, ts);

-- ---------------------------------------------------------------------------
-- schema_version bookkeeping
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL,
  description TEXT
);

INSERT INTO schema_version (version, applied_at, description)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%S.000Z','now'), 'initial schema');
