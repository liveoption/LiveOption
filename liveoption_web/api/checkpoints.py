"""/v1/checkpoints/* routes (Plan B, Task 11).

Endpoints (all gated by :func:`verify_internal`):

- ``GET /v1/checkpoints/{id}``               — single checkpoint detail
- ``GET /v1/runs/{run_id}/checkpoints``       — list checkpoints for a run
- ``GET /v1/checkpoints/{id}/questions``      — list questions asked against
  the checkpoint, ordered by ``asked_at``

The detail endpoint returns the parsed ``portfolio_summary_json`` under
``portfolio_summary``; larger artifacts are available under
``storage_dir`` (fetched lazily by the frontend per spec 7.3.3).
"""

from __future__ import annotations

import json

import aiosqlite
from fastapi import APIRouter, Depends, Request

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.api.problem import problem_response
from liveoption_web.db.connection import open_db

router = APIRouter(tags=["checkpoints"])


def _checkpoint_row_to_dict(row) -> dict:
    summary_raw = row["portfolio_summary_json"]
    try:
        summary = json.loads(summary_raw) if summary_raw else None
    except (TypeError, ValueError):
        # Preserve malformed payloads as raw text rather than 500ing.
        summary = summary_raw
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "decision_record_id": row["decision_record_id"],
        "storage_dir": row["storage_dir"],
        "fingerprint": row["fingerprint"],
        "portfolio_summary": summary,
        "promoted_at": row["promoted_at"],
        "promoted_by": row["promoted_by"],
        "status": row["status"],
        "status_updated_at": row["status_updated_at"],
        "label": row["label"],
    }


def _question_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "checkpoint_id": row["checkpoint_id"],
        "parent_question_id": row["parent_question_id"],
        "asked_by": row["asked_by"],
        "asked_at": row["asked_at"],
        "user_prompt": row["user_prompt"],
        "model_id": row["model_id"],
        "provider": row["provider"],
        "response_text": row["response_text"],
        "response_tokens": row["response_tokens"],
        "latency_ms": row["latency_ms"],
        "cost_usd": row["cost_usd"],
        "status": row["status"],
        "status_updated_at": row["status_updated_at"],
        "error_message": row["error_message"],
    }


@router.get("/checkpoints/{checkpoint_id}")
async def get_checkpoint(
    checkpoint_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Return a single checkpoint row with parsed portfolio summary."""
    settings = request.app.state.settings
    async with open_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, run_id, decision_record_id, storage_dir, fingerprint,
                      portfolio_summary_json, promoted_at, promoted_by, status,
                      status_updated_at, label
               FROM checkpoints WHERE id = ?""",
            (checkpoint_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise problem_response(
            404,
            "checkpoint-not-found",
            "Checkpoint not found",
            detail=f"No checkpoint with id={checkpoint_id}",
        )
    return _checkpoint_row_to_dict(row)


@router.get("/runs/{run_id}/checkpoints")
async def list_run_checkpoints(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """List checkpoints for a run, ordered by promotion time."""
    settings = request.app.state.settings
    async with open_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, run_id, decision_record_id, storage_dir, fingerprint,
                      portfolio_summary_json, promoted_at, promoted_by, status,
                      status_updated_at, label
               FROM checkpoints
               WHERE run_id = ?
               ORDER BY promoted_at ASC, id ASC""",
            (run_id,),
        )
        rows = await cur.fetchall()
    return {"items": [_checkpoint_row_to_dict(r) for r in rows]}


@router.get("/checkpoints/{checkpoint_id}/questions")
async def list_checkpoint_questions(
    checkpoint_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """List questions attached to a checkpoint, oldest first.

    Returns 404 when the checkpoint itself does not exist so the client
    can distinguish "empty" (200 with []) from "bad id".
    """
    settings = request.app.state.settings
    async with open_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT 1 FROM checkpoints WHERE id = ?", (checkpoint_id,)
        )
        if await cur.fetchone() is None:
            raise problem_response(
                404,
                "checkpoint-not-found",
                "Checkpoint not found",
                detail=f"No checkpoint with id={checkpoint_id}",
            )
        cur = await db.execute(
            """SELECT id, checkpoint_id, parent_question_id, asked_by, asked_at,
                      user_prompt, model_id, provider, response_text,
                      response_tokens, latency_ms, cost_usd, status,
                      status_updated_at, error_message
               FROM questions
               WHERE checkpoint_id = ?
               ORDER BY asked_at ASC, id ASC""",
            (checkpoint_id,),
        )
        rows = await cur.fetchall()
    return {"items": [_question_row_to_dict(r) for r in rows]}
