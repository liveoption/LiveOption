# Historical Run Backfill — Operator Runbook

`deploy/backfill/backfill_historical.py` imports **pre-Plan-B batch
runs** (directories under `~/LiveOption/results/` that only have
`trades.jsonl` / `portfolio.jsonl`, and optionally `config_frozen.yaml`
+ a `decisions/` dir from Plan-A) into the new `liveoption.db` SQLite
schema so they appear in the Web UI.

## Prerequisites

1. `liveoption.db` already exists and has migration `001_init.sql`
   applied (the script aborts if the `runs` table is missing).
2. `pyyaml` and `tqdm` are installed in the target venv
   (`~/LiveOption/.venv/bin/python -m pip show pyyaml tqdm`).
3. Nothing else is writing to the DB while the backfill runs. It is
   safe to run alongside the Job Server because each run is a single
   transaction with `INSERT OR IGNORE`, but during the first bulk
   import we recommend stopping the web service for cleanliness.

## Dry-run (always do this first)

```bash
cd ~/LiveOption
.venv/bin/python -m deploy.backfill.backfill_historical \
    --source-dir ~/LiveOption/results \
    --db-path   ~/LiveOption/web/liveoption.db \
    --dry-run -v
```

The dry-run walks the results tree and logs what **would** be inserted
without opening a write transaction. Inspect the output for
`skip <dir>: cannot determine date range` style warnings and decide
whether to ignore them or fix the source dir.

## Full import

```bash
.venv/bin/python -m deploy.backfill.backfill_historical \
    --source-dir ~/LiveOption/results \
    --db-path   ~/LiveOption/web/liveoption.db \
    -v
```

Useful flags:

- `--limit N` — stop after inserting N new runs (good for progressive
  chunking on large histories).
- `--since YYYY-MM-DD` — ignore runs whose `date_end` is older than
  this. Handy when you only care about recent experiments.
- `--audit-path /some/where.jsonl` — override the default audit path
  (`<db_path dir>/backfill_audit.jsonl`).

Every inserted run is appended to `backfill_audit.jsonl` as a single
JSON object with `run_id`, `source_dir`, `scenario`, `n_decisions` and
`n_days`. This is the canonical rollback manifest.

## Idempotency

The run id is a deterministic hash of the source directory path:
`HIST_<sha256(source_dir)[:21]>`. Re-running the script therefore
re-visits every directory but all `INSERT OR IGNORE` statements become
no-ops for rows that already exist. The audit log will **not** grow
on a second run (writes happen only when `INSERT … RETURNING rowcount`
reports a new row).

## Rollback

All backfilled runs share the `HIST_` id prefix, so reversal is a
single query:

```sql
-- Order matters: FK-dependent tables first.
DELETE FROM events           WHERE run_id LIKE 'HIST\_%' ESCAPE '\';
DELETE FROM decision_records WHERE run_id LIKE 'HIST\_%' ESCAPE '\';
DELETE FROM runs             WHERE id     LIKE 'HIST\_%' ESCAPE '\';
```

For a surgical rollback, read the ULIDs from the audit file:

```bash
jq -r '.run_id' backfill_audit.jsonl \
  | xargs -I{} sqlite3 liveoption.db \
     "DELETE FROM events WHERE run_id='{}'; \
      DELETE FROM decision_records WHERE run_id='{}'; \
      DELETE FROM runs WHERE id='{}';"
```

After rollback, rotate the audit file (`mv backfill_audit.jsonl
backfill_audit.jsonl.reverted.$(date +%s)`) so a subsequent retry
starts with a clean ledger.

## Troubleshooting

**`RuntimeError: target DB is missing the 'runs' table`**
 Apply migrations first: `sqlite3 liveoption.db < liveoption_web/db/migrations/001_init.sql`.

**`skip <dir>: no trades.jsonl or portfolio.jsonl`**
 The directory is not a completed run (setup scratch, failed launch,
 grid-search summary dir). Expected; the script skips it quietly.

**`skip <dir>: cannot determine date range`**
 Config is missing or unparseable and `trades.jsonl` yielded no
 `current_date`. Either re-materialize `config_frozen.yaml` with the
 correct `date_start`/`date_end`, or drop the run.

**`sqlite3.IntegrityError: CHECK constraint failed`**
 Almost always means `config_frozen.yaml` contains a malformed
 `date_start`/`date_end` (not `YYYY-MM-DD`). Fix the file and re-run —
 the partial transaction is rolled back so no orphans are left.

**`schema mismatch` after a DB migration**
 Backfill writes only the columns present in migration 001. If a
 future migration adds `NOT NULL` columns without defaults, this
 script must be updated before re-running on the new schema.
