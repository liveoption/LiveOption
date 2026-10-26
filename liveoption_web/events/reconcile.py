"""Startup reconciliation for the LiveOption Job Server.

The Job Server relies on durable state (SQLite) but runs subprocesses
that may crash, be orphaned, or leave partial artifacts behind when the
server itself restarts.  ``reconcile_on_startup`` is invoked from the
FastAPI lifespan and walks every non-terminal row to bring the database
back in sync with what actually exists on disk / in the process table.

Spec 4.4 removed the previous 7-day cutoff: all non-terminal runs are
scanned, not just recent ones.  The partial index
``idx_runs_active`` (defined in migration 001) keeps this cheap even as
the ``runs`` table grows, since only ``pending``/``running`` rows are
indexed.

Phases (each a separate SQL transaction so a failure in one doesn't
abort the rest):

1. ``_reconcile_runs`` — probe the PID of every non-terminal run; dead
   PIDs get promoted to ``completed``/``failed`` (if ``exit_code.txt`` is
   present) or ``orphaned`` otherwise.
2. ``_reconcile_checkpoints`` — any checkpoint still ``writing`` or
   ``promoting`` after a restart is incomplete by definition: the
   final storage dir (if present) and any ``.tmp`` sibling are scrubbed,
   and the row is deleted so ``promote_decision`` can retry idempotently.
3. ``_reconcile_questions`` — ``streaming`` questions are synthesized
   from ``question_chunks`` if any chunks were persisted, otherwise
   marked ``failed``.
4. ``_backfill_jsonl_events`` — for still-running runs, tail
   ``events.jsonl`` and replay anything missing from the ``events``
   table via ``INSERT OR IGNORE`` (the unique seq index dedupes).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from liveoption_web.config import Settings
from liveoption_web.db.connection import open_db

logger = logging.getLogger(__name__)


def _utc_now_z() -> str:
    """ISO-8601 UTC timestamp matching the DB CHECK constraint.

    CHECK glob pattern is ``????-??-??T??:??:??*Z``; a plain
    ``strftime("%Y-%m-%dT%H:%M:%SZ")`` satisfies it.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid_alive(pid: int) -> bool:
    """True if *pid* refers to a live process.

    ``os.kill(pid, 0)`` is the POSIX idiom: signal 0 performs permission
    and existence checks without delivering a signal.  ``ProcessLookup
    Error`` (no such process) and ``PermissionError`` (process exists
    but belongs to another user) are the two outcomes we care about;
    the former means dead, the latter means alive.
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't own it — still alive.
        return True
    except OSError:
        return False
    return True


async def resolve_terminal_status(
    db, run_id: str, exit_code: int
) -> tuple[str, str | None]:
    """Compute the terminal run status given the worker exit code.

    A successful exit (0) is promoted to ``completed_with_errors`` if any
    ``worker.error`` events with ``severity=error`` were recorded for this
    run.  Returns ``(status, error_message)`` where ``error_message`` is
    the most recent worker.error message (by event id) or ``None``.
    """
    cur = await db.execute(
        "SELECT payload_json FROM events "
        "WHERE run_id=? AND kind='worker.error' "
        "  AND json_extract(payload_json,'$.severity')='error' "
        "ORDER BY id DESC LIMIT 1",
        (run_id,),
    )
    last = await cur.fetchone()
    if exit_code != 0:
        msg = None
        if last:
            try:
                msg = json.loads(last[0]).get("message")
            except Exception:  # noqa: BLE001
                msg = None
        return "failed", msg
    if last:
        try:
            msg = json.loads(last[0]).get("message")
        except Exception:  # noqa: BLE001
            msg = None
        return "completed_with_errors", msg
    return "completed", None


async def _reconcile_runs(settings: Settings) -> None:
    async with open_db(settings.db_path) as db:
        # No 7-day cutoff — spec 4.4 removed it.  The partial index
        # idx_runs_active keeps this cheap.
        cur = await db.execute(
            "SELECT id, pid, storage_dir, status FROM runs "
            "WHERE status IN ('pending','running')"
        )
        rows = await cur.fetchall()
        for row in rows:
            run_id, pid, storage_dir, _status = row
            if pid and _pid_alive(int(pid)):
                # Still running — monitor will reattach elsewhere.
                continue
            exit_code_file = Path(storage_dir) / "exit_code.txt"
            if exit_code_file.exists():
                try:
                    code = int(exit_code_file.read_text().strip())
                except ValueError:
                    logger.warning(
                        "run %s: unparsable exit_code.txt, marking failed", run_id
                    )
                    code = 1
                new_status, err_msg = await resolve_terminal_status(
                    db, run_id, code
                )
                await db.execute(
                    "UPDATE runs SET status=?, exit_code=?, "
                    "error_message=COALESCE(?, error_message), "
                    "status_updated_at=? WHERE id=?",
                    (new_status, code, err_msg, _utc_now_z(), run_id),
                )
            else:
                await db.execute(
                    "UPDATE runs SET status='orphaned', status_updated_at=? "
                    "WHERE id=?",
                    (_utc_now_z(), run_id),
                )
        await db.commit()


async def _reconcile_checkpoints(settings: Settings) -> None:
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT id, storage_dir FROM checkpoints "
            "WHERE status IN ('writing','promoting')"
        )
        rows = await cur.fetchall()
        for ckpt_id, storage_dir in rows:
            p = Path(storage_dir)
            # Spec 3.6: the tmp→final rename is the atomic commit point
            # for a checkpoint promotion.  If we observe ``writing``/
            # ``promoting`` after a restart, the transition was
            # interrupted; the artifact on disk (if any) is presumptively
            # partial/corrupt regardless of whether ``metadata.json``
            # happens to be present (it may have been copied mid-batch).
            # Scrub both the final dir and any ``<storage_dir>.tmp``
            # sibling, then delete the row so ``promote_decision`` can
            # retry idempotently against the same decision_record_id.
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
            tmp_sibling = Path(str(storage_dir) + ".tmp")
            if tmp_sibling.exists():
                shutil.rmtree(tmp_sibling, ignore_errors=True)
            await db.execute(
                "DELETE FROM checkpoints WHERE id=?",
                (ckpt_id,),
            )
        await db.commit()


async def _reconcile_questions(settings: Settings) -> None:
    """Synthesize streaming question state after a restart.

    If any chunks were persisted, concatenate them into ``response_text``
    and mark the question ``completed`` — the client reconnecting via SSE
    will see the synthesized text via the replay path rather than a
    truncated stream.  Otherwise mark it ``failed`` with a descriptive
    message so the UI doesn't silently swallow the interruption.
    """
    async with open_db(settings.db_path) as db:
        cur = await db.execute("SELECT id FROM questions WHERE status='streaming'")
        rows = await cur.fetchall()
        for (qid,) in rows:
            cur2 = await db.execute(
                "SELECT delta_text FROM question_chunks "
                "WHERE question_id=? ORDER BY seq",
                (qid,),
            )
            chunks = await cur2.fetchall()
            if chunks:
                text = "".join(c[0] for c in chunks)
                await db.execute(
                    "UPDATE questions "
                    "SET response_text=?, status='completed', status_updated_at=? "
                    "WHERE id=?",
                    (text, _utc_now_z(), qid),
                )
            else:
                await db.execute(
                    "UPDATE questions "
                    "SET status='failed', error_message=?, status_updated_at=? "
                    "WHERE id=?",
                    (
                        "restart during streaming; no chunks",
                        _utc_now_z(),
                        qid,
                    ),
                )
        await db.commit()


async def _backfill_jsonl_events(settings: Settings) -> None:
    """Replay ``events.jsonl`` into the DB for still-running runs.

    The sidecar may have written to the file after the last DB commit
    before the server died.  ``INSERT OR IGNORE`` + the unique
    ``(run_id, subprocess_seq)`` index deduplicates automatically.
    """
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT id, storage_dir FROM runs WHERE status='running'"
        )
        rows = await cur.fetchall()
        for run_id, storage_dir in rows:
            jsonl = Path(storage_dir) / "events.jsonl"
            if not jsonl.exists():
                logger.warning(
                    "run %s running but no events.jsonl (sidecar may be disabled)",
                    run_id,
                )
                continue
            try:
                content = jsonl.read_text()
            except OSError as e:
                logger.warning("run %s: cannot read events.jsonl: %s", run_id, e)
                continue
            for line in content.splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "run %s: corrupt JSONL line in %s, skipping",
                        run_id,
                        jsonl,
                    )
                    continue
                try:
                    await db.execute(
                        "INSERT OR IGNORE INTO events("
                        "run_id, subprocess_seq, ts, kind, payload_json) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            run_id,
                            ev["subprocess_seq"],
                            ev["ts"],
                            ev["kind"],
                            json.dumps(ev.get("payload", {})),
                        ),
                    )
                except (KeyError, TypeError) as e:
                    logger.warning(
                        "run %s: backfill skipped malformed event: %s", run_id, e
                    )
                except Exception as e:  # noqa: BLE001 - log & continue
                    logger.warning("run %s: backfill INSERT failed: %s", run_id, e)
        await db.commit()


async def reconcile_on_startup(settings: Settings) -> None:
    """Full startup reconcile: runs → checkpoints → questions → JSONL backfill."""
    logger.info("reconcile: starting")
    await _reconcile_runs(settings)
    await _reconcile_checkpoints(settings)
    await _reconcile_questions(settings)
    await _backfill_jsonl_events(settings)
    logger.info("reconcile: done")
