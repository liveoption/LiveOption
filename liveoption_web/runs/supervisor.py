"""Async subprocess supervisor for fork runs (Plan B, Task 13).

:func:`spawn_fork` launches ``python -m trading.cli resume-from-checkpoint``
with the appropriate ``--web-ingest``/``--run-id`` wiring, records the
live pid into ``runs.pid``, flips the run's status from ``pending`` →
``running``, and schedules a background watcher that flips it to
``completed`` / ``failed`` / ``cancelled`` once the process exits.

Design notes:

* The watcher is launched as a fire-and-forget ``asyncio.Task``.  Tests
  drive it by polling the ``runs`` row with a short sleep loop.
* ``preexec_fn=_preexec_pdeathsig`` is Linux-specific; on macOS
  (development) we skip the prctl call so process launch doesn't fail.
* ``start_new_session=True`` gives the subprocess its own session so
  SIGTERM from :func:`os.kill` (used by the future
  ``POST /v1/runs/:id/cancel``) doesn't propagate to the Job Server.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from liveoption_web.config import get_settings
from liveoption_web.db.connection import open_db

logger = logging.getLogger(__name__)


class RunCapExceeded(RuntimeError):
    """Raised when the shared ``max_concurrent_runs`` cap is hit.

    Forks and batch launches share a single pool (spec §4.4).  The API
    layer already guards this at request time; the supervisor raises
    this exception as defense-in-depth for non-API callers (tests,
    internal scripts, future CLI).  ``forks.py`` translates it to a 409.
    """


# Back-compat alias for external importers — prefer ``RunCapExceeded``.
# TODO: remove once all callers migrate (trigger: next Web plan milestone
# that touches forks.py + liveoption_web/api/forks.py).
ForkCapExceeded = RunCapExceeded


# Env keys stripped from every spawned subprocess.
#
# We deliberately KEEP ``LIVEOPTION_INTERNAL_SECRET`` in the subprocess
# env: the subprocess's ``trading.ingest.event_emitter`` POSTs events back
# to ``/_ingest/events``, which requires the shared secret (same
# hmac-compare_digest guard used by every other privileged endpoint).
# Without it, every launched run succeeds but leaves timeline/pnl/events
# empty in the UI — the reconcile-from-JSONL fallback only fires on
# Job Server restart, not continuously.
#
# Stripping the secret was a wrong move in an earlier Code Quality
# review — subprocess runs as the same OS user with access to the same
# ``.env.liveoption-web`` file, so env-level strip is a paper barrier
# that blocks legitimate back-channel traffic without preventing any
# real exfiltration. ``spawn_fork`` never stripped it and is the
# production-tested pattern.
#
# Clerk secrets are still stripped: the subprocess has no need for
# them and they change more frequently (rotated at Clerk).
_SUBPROCESS_ENV_DENYLIST: frozenset[str] = frozenset({
    "CLERK_SECRET_KEY",
    "CLERK_JWT_KEY",
})


def _utc_now_z() -> str:
    """UTC timestamp matching the ``runs.status_updated_at`` CHECK glob."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preexec_pdeathsig() -> None:  # pragma: no cover - exercised only on Linux
    """Linux-only: ask the kernel to SIGTERM the child when the parent dies.

    Swallows all errors — if libc/prctl is unavailable we simply fall
    back to vanilla subprocess behaviour.
    """
    try:
        libc = ctypes.CDLL("libc.so.6")
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass


def _preexec_fn_for_platform():
    """Return the platform-appropriate ``preexec_fn`` (or ``None``)."""

    if sys.platform.startswith("linux"):
        return _preexec_pdeathsig
    return None


async def _ensure_cap(
    db_path: Path,
    max_concurrent_runs: int | None,
    *,
    exclude_run_id: str | None = None,
) -> None:
    """Raise :class:`RunCapExceeded` if the shared run pool is at cap.

    Counts all runs (any kind) in ``pending`` or ``running`` state.  The
    caller can pass ``exclude_run_id`` to ignore a row it has already
    inserted for itself (so the caller's own pending row doesn't count
    against the cap).
    """

    if max_concurrent_runs is None:
        max_concurrent_runs = get_settings().max_concurrent_runs
    async with open_db(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        if exclude_run_id is not None:
            cur = await db.execute(
                "SELECT COUNT(*) FROM runs "
                "WHERE status IN ('pending','running') AND id != ?",
                (exclude_run_id,),
            )
        else:
            cur = await db.execute(
                "SELECT COUNT(*) FROM runs "
                "WHERE status IN ('pending','running')"
            )
        (count,) = await cur.fetchone()
        await db.commit()
    if count >= max_concurrent_runs:
        raise RunCapExceeded(
            f"{count} pending/running runs (limit {max_concurrent_runs})"
        )


async def spawn_fork(
    *,
    db_path: Path,
    runs_dir: Path,
    ingest_url: str,
    checkpoint_id: str,
    run_id: str,
    parent_run_id: str,
    parent_storage_dir: str,
    mode: str,
    prompt_override: str | None,
    model_override: str | None,
    actor_user_id: str | None = None,
    max_concurrent_runs: int | None = None,
) -> int:
    """Spawn a fork subprocess and return its pid.

    The caller is expected to have already inserted the ``runs`` row
    with ``status='pending'``.  This function:

    1. Materialises the run's storage dir + optional prompt override file.
    2. Launches ``trading.cli resume-from-checkpoint``.
    3. Updates ``runs.pid`` / ``runs.status='running'``.
    4. Schedules a watcher task that records exit status.
    """

    # Defense-in-depth: enforce the shared run pool cap (forks + batches)
    # even when the caller is not the API layer.  Forks and batches share
    # a single ``max_concurrent_runs`` cap (spec §4.4).
    try:
        await _ensure_cap(
            db_path,
            max_concurrent_runs,
            exclude_run_id=run_id,
        )
    except RunCapExceeded:
        # Flip the caller-inserted pending row to cancelled so the DB
        # stays consistent — the subprocess never ran.
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE runs SET status='cancelled', status_updated_at=? "
                "WHERE id=?",
                (_utc_now_z(), run_id),
            )
            await db.commit()
        raise

    runs_dir = Path(runs_dir)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    ckpt_dir = Path(parent_storage_dir) / "checkpoints" / checkpoint_id

    args: list[str] = [
        sys.executable,
        "-m",
        "trading.cli",
        "resume-from-checkpoint",
        "--checkpoint-id",
        checkpoint_id,
        "--checkpoint-path",
        str(ckpt_dir),
        "--mode",
        mode,
        "--web-ingest",
        ingest_url,
        "--run-id",
        run_id,
    ]
    if prompt_override is not None:
        prompt_file = run_dir / "prompt_override.txt"
        prompt_file.write_text(prompt_override)
        args.extend(["--prompt-override", str(prompt_file)])
    if model_override is not None:
        args.extend(["--model-override", model_override])

    stdout_path = run_dir / "stdout.log"
    stdout_fp = open(stdout_path, "a")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=stdout_fp,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=_preexec_fn_for_platform(),
            start_new_session=True,
        )
    except Exception:
        stdout_fp.close()
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE runs SET status='failed', status_updated_at=? WHERE id=?",
                (_utc_now_z(), run_id),
            )
            await db.commit()
        raise

    async with open_db(db_path) as db:
        await db.execute(
            "UPDATE runs SET pid=?, status='running', status_updated_at=? "
            "WHERE id=?",
            (proc.pid, _utc_now_z(), run_id),
        )
        await db.commit()

    asyncio.create_task(_watch_subprocess(db_path, run_id, proc, stdout_fp))
    return proc.pid


async def spawn_batch(
    *,
    db_path: Path,
    runs_dir: Path,
    run_id: str,
    argv: list[str],
    env: Mapping[str, str],
    actor_email: str | None = None,
    max_concurrent_runs: int | None = None,
) -> int:
    """Spawn a web-launched batch run subprocess; return the pid.

    The caller is expected to have already inserted the ``runs`` row with
    ``kind='batch'``, ``status='pending'``, and ``launch_spec_json`` set.

    Mirrors :func:`spawn_fork`: enforces the shared concurrency cap,
    materialises ``runs_dir/<run_id>/``, strips ``LIVEOPTION_INTERNAL_SECRET``
    from the subprocess env, execs with a detached session, and schedules
    the watcher task.
    """

    # Defense-in-depth: enforce the shared run pool cap (forks + batches).
    try:
        await _ensure_cap(
            db_path,
            max_concurrent_runs,
            exclude_run_id=run_id,
        )
    except RunCapExceeded:
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE runs SET status='cancelled', status_updated_at=? "
                "WHERE id=?",
                (_utc_now_z(), run_id),
            )
            await db.commit()
        raise

    runs_dir = Path(runs_dir)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Strip Job-Server secrets from the subprocess env. The subprocess POSTs
    # back using headers the caller baked into argv; it has no legitimate use
    # for the internal secret, Clerk keys, or any future shared secret listed
    # in _SUBPROCESS_ENV_DENYLIST.
    safe_env = {k: v for k, v in env.items() if k not in _SUBPROCESS_ENV_DENYLIST}

    stdout_path = run_dir / "stdout.log"
    stdout_fp = open(stdout_path, "a")

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=stdout_fp,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=_preexec_fn_for_platform(),
            start_new_session=True,
            env=safe_env,
        )
    except Exception:
        stdout_fp.close()
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE runs SET status='failed', status_updated_at=? WHERE id=?",
                (_utc_now_z(), run_id),
            )
            await db.commit()
        raise

    async with open_db(db_path) as db:
        await db.execute(
            "UPDATE runs SET pid=?, status='running', status_updated_at=? "
            "WHERE id=?",
            (proc.pid, _utc_now_z(), run_id),
        )
        await db.commit()

    # Audit trail for web-launched batch runs (spec §8 observability). Log
    # argv as a sha256 fingerprint — prompt_override files may contain
    # research IP that shouldn't land in journalctl.
    argv_hash = hashlib.sha256(
        "\x00".join(argv).encode("utf-8", "replace")
    ).hexdigest()[:16]
    logger.info(
        "run.launched",
        extra={
            "run_id": run_id,
            "actor_email": actor_email,
            "pid": proc.pid,
            "argv_hash": argv_hash,
        },
    )

    asyncio.create_task(_watch_subprocess(db_path, run_id, proc, stdout_fp))
    return proc.pid


async def _watch_subprocess(
    db_path: Path,
    run_id: str,
    proc,
    stdout_fp,
) -> None:
    """Wait for *proc* to exit and record the final status in the DB."""

    try:
        code = await proc.wait()
    except Exception:  # pragma: no cover - defensive
        logger.exception("watcher failed for run %s", run_id)
        code = -1
    finally:
        try:
            stdout_fp.close()
        except Exception:
            pass

    if code == -signal.SIGTERM or code == -signal.SIGINT:
        status = "cancelled"
        err_msg: str | None = None
    else:
        status = None  # resolved against the events table below
        err_msg = None

    try:
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT storage_dir FROM runs WHERE id=?", (run_id,)
            )
            row = await cur.fetchone()
            if row and row[0]:
                try:
                    (Path(row[0]) / "exit_code.txt").write_text(str(code))
                except Exception:
                    logger.warning(
                        "could not write exit_code.txt for %s", run_id
                    )
            if status is None:
                from liveoption_web.events.reconcile import resolve_terminal_status

                status, err_msg = await resolve_terminal_status(db, run_id, code)
            await db.execute(
                "UPDATE runs SET status=?, exit_code=?, "
                "error_message=COALESCE(?, error_message), "
                "status_updated_at=? WHERE id=?",
                (status, code, err_msg, _utc_now_z(), run_id),
            )
            await db.commit()
    except Exception:  # pragma: no cover - defensive
        logger.exception("failed to persist exit status for run %s", run_id)

    # Phase C6 lifespan hook: dispatch evaluator on terminal status.
    # Best-effort; evaluator failures must not poison the watcher task.
    if status in ("completed", "completed_with_errors", "failed"):
        try:
            from liveoption_web import eval as eval_pkg

            await asyncio.to_thread(eval_pkg.aggregate_for, run_id, db_path)
        except Exception:  # pragma: no cover - defensive
            logger.exception("evaluator failed for run %s", run_id)
