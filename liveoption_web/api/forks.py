"""``POST /v1/checkpoints/:id/forks`` — create a fork run (Plan B, Task 13).

Phase 1 only supports ``fork_mode='diverge'`` and requires at least one
of ``prompt_override`` / ``model_override``; pure-replay forks are
deferred.  The endpoint:

1. Validates the target checkpoint exists and has ``status='ready'``.
2. Enforces the shared run-pool cap
   (``settings.max_concurrent_runs``) across forks + batches.
3. Inserts a ``kind='fork'`` row in ``runs`` with ``status='pending'``
   and writes an ``audit_log`` entry.
4. Delegates to :func:`liveoption_web.runs.supervisor.spawn_fork` to
   launch the subprocess; the supervisor flips ``status`` to
   ``running`` and watches for exit.

The response is ``202 Accepted`` carrying the new ``run_id`` and the
SSE URL that the UI can immediately subscribe to.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, model_validator

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.api.problem import problem_response
from liveoption_web.db.connection import open_db
from liveoption_web.runs.supervisor import RunCapExceeded, spawn_fork
from trading.ingest.ulid import new_ulid

router = APIRouter(tags=["forks"])


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ForkCreate(BaseModel):
    """Body for ``POST /v1/checkpoints/:id/forks``.

    ``mode`` is currently restricted to ``"diverge"`` via ``Literal``;
    adding ``"replay"`` in Phase 2 will be a one-line widening.
    """

    mode: Literal["diverge"] = "diverge"
    prompt_override: str | None = None
    model_override: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def _require_override(self):
        if self.prompt_override is None and self.model_override is None:
            raise ValueError(
                "diverge mode requires at least one of prompt_override "
                "or model_override"
            )
        return self


@router.post("/checkpoints/{checkpoint_id}/forks", status_code=202)
async def create_fork(
    checkpoint_id: str,
    body: ForkCreate,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Create a fork run + spawn its supervisor subprocess."""

    settings = request.app.state.settings

    async with open_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT run_id, status FROM checkpoints WHERE id=?",
            (checkpoint_id,),
        )
        ckpt = await cur.fetchone()
        if ckpt is None:
            raise problem_response(
                404,
                "checkpoint-not-found",
                "Checkpoint not found",
                detail=f"No checkpoint with id={checkpoint_id}",
            )
        if ckpt["status"] != "ready":
            raise problem_response(
                400,
                "checkpoint-not-ready",
                "Checkpoint not ready",
                detail=f"Checkpoint status={ckpt['status']!r}",
            )
        parent_run_id = ckpt["run_id"]

        cur = await db.execute(
            "SELECT COUNT(*) FROM runs "
            "WHERE status IN ('pending','running')",
        )
        (running_count,) = await cur.fetchone()
        if running_count >= settings.max_concurrent_runs:
            raise problem_response(
                409,
                "fork-rate-limit",
                "Too many concurrent runs",
                detail=(
                    f"{running_count} pending/running runs "
                    f"(limit {settings.max_concurrent_runs})"
                ),
            )

        cur = await db.execute(
            "SELECT scenario, model_id, provider, date_start, date_end, "
            "config_path, universe_json, storage_dir FROM runs WHERE id=?",
            (parent_run_id,),
        )
        parent = await cur.fetchone()
        if parent is None:  # pragma: no cover - FK should guarantee this
            raise problem_response(
                500,
                "parent-run-missing",
                "Parent run missing",
                detail=f"parent_run_id={parent_run_id}",
            )

        run_id = new_ulid()
        now = _utc_now_z()
        storage_dir = str(settings.runs_dir / run_id)
        resolved_model = body.model_override or parent["model_id"]

        await db.execute(
            """INSERT INTO runs(id, kind, parent_run_id, parent_checkpoint_id,
                                scenario, config_path, model_id, provider,
                                universe_json, date_start, date_end, status,
                                status_updated_at, created_at, created_by,
                                title, storage_dir, fork_mode,
                                fork_prompt_override, fork_model_override)
               VALUES (?, 'fork', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                       ?, ?, ?, ?, ?, 'diverge', ?, ?)""",
            (
                run_id,
                parent_run_id,
                checkpoint_id,
                parent["scenario"],
                parent["config_path"],
                resolved_model,
                parent["provider"],
                parent["universe_json"],
                parent["date_start"],
                parent["date_end"],
                now,
                now,
                actor.user_id,
                body.title,
                storage_dir,
                body.prompt_override,
                body.model_override,
            ),
        )
        await db.execute(
            """INSERT INTO audit_log(ts, actor_user_id, actor_email, action,
                                     target_type, target_id, payload_json)
               VALUES (?, ?, ?, 'fork.create', 'run', ?, ?)""",
            (
                now,
                actor.user_id,
                actor.email,
                run_id,
                json.dumps(
                    {
                        "checkpoint_id": checkpoint_id,
                        "mode": "diverge",
                        "prompt_override": body.prompt_override is not None,
                        "model_override": body.model_override,
                    }
                ),
            ),
        )
        await db.commit()

        parent_storage_dir = parent["storage_dir"]

    try:
        await spawn_fork(
            db_path=settings.db_path,
            runs_dir=settings.runs_dir,
            ingest_url=settings.ingest_url,
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            parent_run_id=parent_run_id,
            parent_storage_dir=parent_storage_dir,
            mode="diverge",
            prompt_override=body.prompt_override,
            model_override=body.model_override,
            actor_user_id=actor.user_id,
            max_concurrent_runs=settings.max_concurrent_runs,
        )
    except RunCapExceeded as exc:
        # Defense-in-depth: another caller (non-API) raced us past the
        # cap between our earlier check and spawn.
        raise problem_response(
            409,
            "fork-rate-limit",
            "Too many concurrent runs",
            detail=str(exc),
        )
    except Exception as exc:
        # spawn_fork already flipped status to 'failed' on error; surface
        # a 500 so the client knows the POST didn't actually launch work.
        raise problem_response(
            500,
            "fork-spawn-failed",
            "Failed to spawn fork subprocess",
            detail=str(exc),
        )

    return {
        "run_id": run_id,
        "status": "pending",
        "events_url": f"/v1/runs/{run_id}/events",
    }
