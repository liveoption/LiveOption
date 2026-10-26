"""Crash-safe checkpoint promotion (Spec Section 3.6).

Promotion protocol:

1. Load the ``decision_records`` row (404 if missing) and short-circuit
   with HTTP 409 when a checkpoint already exists for this decision.
2. Copy the decision artifact directory
   (``<runs_dir>/<run_id>/decisions/<decision_id>``) into a ``.tmp``
   sibling under ``<runs_dir>/<run_id>/checkpoints/``.
3. Invoke ``python -m trading.cli capture-checkpoint`` to serialize the
   runner state into ``runner_checkpoint.json`` inside the tmp dir.
   (Tests monkeypatch this call.)
4. Write ``metadata.json``, fsync every file and the tmp dir, then
   ``os.rename`` to the final path and fsync the parent dir.
5. Commit the logical promotion in a **single SQLite transaction** that
   INSERTs the ``checkpoints`` row with ``status='ready'``, UPDATEs
   ``decision_records.promoted_checkpoint_id`` for the back-link, and
   writes an ``audit_log`` entry for ``checkpoint.promote``.

Crash semantics:

* Crash before step 5 → artifact may exist on disk but no DB row points
  at it; the next promotion produces a fresh checkpoint id and the
  orphan directory is a harmless leftover cleaned by startup reconcile
  (Spec 3.6 final sentence).
* Crash during step 5 → SQLite transaction rollback keeps the pre-state
  intact; retry is safe and yields 409 only if the transaction actually
  committed.
* The ``UNIQUE(decision_record_id)`` constraint on ``checkpoints``
  (migration 001) is a belt-and-suspenders safety net that also makes
  retries idempotent at the database layer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from liveoption_web.db.connection import open_db
from trading.checkpoint.captured import CapturedCheckpoint
from trading.ingest.ulid import new_ulid


def _utc_now_z() -> str:
    """Return ``YYYY-MM-DDTHH:MM:SS.mmmZ`` — matches the schema CHECK."""
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def _fsync_tree(root: Path) -> None:
    """fsync every regular file under ``root`` then fsync ``root`` itself."""
    for f in root.rglob("*"):
        if f.is_file():
            fd = os.open(f, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


async def promote_decision(
    db_path: Path,
    runs_dir: Path,
    decision_id: str,
    actor_user_id: str | None,
) -> dict:
    """Promote ``decision_id`` into a checkpoint and return ``{id, status}``.

    Raises
    ------
    HTTPException(404)
        The decision does not exist.
    HTTPException(409)
        The decision has already been promoted.
    """
    # ------------------------------------------------------------------
    # 1. Look up the decision + idempotency check (Spec 3.6 state
    #    machine: 'writing' → 'ready').
    # ------------------------------------------------------------------
    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT run_id, storage_dir FROM decision_records WHERE id=?",
            (decision_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "type": "decision-not-found",
                    "title": "Decision not found",
                    "status": 404,
                    "detail": f"decision_id={decision_id!r}",
                },
            )
        run_id, decision_storage_dir = row

        # Phase C: capture run.family for portfolio_summary embedding.
        cur = await db.execute(
            "SELECT family FROM runs WHERE id=?", (run_id,)
        )
        family_row = await cur.fetchone()
        run_family = family_row[0] if family_row else None

        cur = await db.execute(
            "SELECT id, status, storage_dir FROM checkpoints "
            "WHERE decision_record_id=?",
            (decision_id,),
        )
        existing = await cur.fetchone()
        if existing is not None:
            existing_id, existing_status, existing_storage = existing
            # Spec 3.6 idempotency:
            # * ``ready``  → already promoted, 409
            # * ``writing`` with artifact on disk → prior promote is
            #   mid-flight or left a partial dir; 409 to avoid clobber
            # * ``writing`` with no artifact on disk → prior crash before
            #   any files were written; retry-write by dropping the row
            # * anything else (``failed`` etc.) → 409 (terminal state)
            retry_ok = False
            if existing_status == "writing":
                art = Path(existing_storage)
                tmp_sibling = Path(str(existing_storage) + ".tmp")
                if not art.exists() and not tmp_sibling.exists():
                    # Safe to retry: remove the stale writing row and
                    # proceed as a fresh promotion.
                    await db.execute(
                        "DELETE FROM checkpoints WHERE id=?",
                        (existing_id,),
                    )
                    await db.commit()
                    retry_ok = True
            if not retry_ok:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "type": "already-promoted",
                        "title": "Decision already promoted",
                        "status": 409,
                        "detail": f"checkpoint_id={existing_id}",
                    },
                )

    # ------------------------------------------------------------------
    # 2. INSERT the ``writing`` row BEFORE any filesystem work so that a
    #    crash between now and the final rename leaves reconcile a
    #    breadcrumb to clean up.
    # ------------------------------------------------------------------
    ckpt_id = new_ulid()
    checkpoints_root = runs_dir / run_id / "checkpoints"
    checkpoints_root.mkdir(parents=True, exist_ok=True)
    final_dir = checkpoints_root / ckpt_id
    tmp_dir = checkpoints_root / f"{ckpt_id}.tmp"

    src = Path(decision_storage_dir)
    if not src.is_dir():
        # Fall back to the canonical layout if the stored path is stale.
        src = runs_dir / run_id / "decisions" / decision_id
        if not src.is_dir():
            raise HTTPException(
                status_code=500,
                detail={
                    "type": "decision-artifact-missing",
                    "title": "Decision artifact missing on disk",
                    "status": 500,
                    "detail": str(src),
                },
            )

    writing_ts = _utc_now_z()
    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO checkpoints(
                    id, run_id, decision_record_id, storage_dir,
                    fingerprint, portfolio_summary_json,
                    promoted_at, promoted_by, status,
                    status_updated_at, label)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ckpt_id,
                run_id,
                decision_id,
                str(final_dir),
                f"sha256:decision:{decision_id}",
                None,
                writing_ts,
                actor_user_id or "unknown",
                "writing",
                writing_ts,
                None,
            ),
        )
        await db.commit()

    try:
        # Copy decision artifact → tmp (atomic via final rename below).
        shutil.copytree(src, tmp_dir)

        # Capture runner state (tests stub this out).
        runner_ckpt = tmp_dir / "runner_checkpoint.json"
        subprocess.check_call(
            [
                "python",
                "-m",
                "trading.cli",
                "capture-checkpoint",
                "--run-id",
                run_id,
                "--decision-id",
                decision_id,
                "--output-path",
                str(runner_ckpt),
            ],
            timeout=30,
        )

        # Parse CapturedCheckpoint envelope from runner_checkpoint.json.
        # Tolerate stub / pre-Phase-A artifacts: if the file isn't a
        # CapturedCheckpoint, fall back to {family, generic={}, family_view=null}.
        portfolio_summary: dict = {
            "family": run_family,
            "generic": {},
            "family_view": None,
        }
        try:
            captured = CapturedCheckpoint.model_validate_json(
                runner_ckpt.read_text(encoding="utf-8")
            )
            portfolio_summary = {
                "family": run_family,
                "generic": captured.generic.model_dump(),
                "family_view": (
                    captured.family_view.model_dump()
                    if captured.family_view is not None
                    else None
                ),
            }
        except (ValidationError, ValueError, OSError):
            # Older / stub checkpoints — keep the placeholder summary so
            # the row can still flip to 'ready'. family_view=null per
            # spec §4.2.1 schema-version tolerance.
            pass

        promoted_at = _utc_now_z()
        (tmp_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "checkpoint_id": ckpt_id,
                    "run_id": run_id,
                    "decision_id": decision_id,
                    "promoted_at": promoted_at,
                },
                sort_keys=True,
            )
        )

        # fsync everything in the tmp tree, then atomic rename, then
        # fsync the parent dir so the rename is durable.
        _fsync_tree(tmp_dir)
        os.rename(tmp_dir, final_dir)
        _fsync_dir(checkpoints_root)
    except BaseException:
        # Best-effort cleanup of the partial tmp dir.  If ``final_dir``
        # was already created we leave it alone — reconcile will decide.
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    # ------------------------------------------------------------------
    # 3. Single DB transaction: UPDATE checkpoint status 'writing' →
    #    'ready', UPDATE decision back-link, INSERT audit row.  Any
    #    failure rolls back atomically and the row stays 'writing'
    #    (reconcile will clean it up on next startup).
    # ------------------------------------------------------------------
    ts = _utc_now_z()
    try:
        async with open_db(db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "UPDATE checkpoints SET status='ready', "
                    "status_updated_at=?, promoted_at=?, "
                    "portfolio_summary_json=? WHERE id=?",
                    (
                        ts,
                        ts,
                        json.dumps(portfolio_summary, sort_keys=True),
                        ckpt_id,
                    ),
                )
                await db.execute(
                    "UPDATE decision_records SET promoted_checkpoint_id=? WHERE id=?",
                    (ckpt_id, decision_id),
                )
                await db.execute(
                    """INSERT INTO audit_log(
                            ts, actor_user_id, action, target_type,
                            target_id, payload_json)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        ts,
                        actor_user_id,
                        "checkpoint.promote",
                        "checkpoint",
                        ckpt_id,
                        json.dumps({"decision_id": decision_id}),
                    ),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    except Exception:
        # DB commit failed — the finalized artifact is now an orphan
        # directory and the row remains 'writing'.  Reconcile on startup
        # cleans both up (Spec 3.6).
        raise

    return {"id": ckpt_id, "status": "ready"}
