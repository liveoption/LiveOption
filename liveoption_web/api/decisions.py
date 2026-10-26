"""``/v1/decisions/*`` routes (Task 10).

Decision detail reads the exact immutable artifact written during replay.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.checkpoint.promote import promote_decision
from liveoption_web.db.connection import open_db

router = APIRouter(prefix="/v1", tags=["decisions"])


@router.get("/decisions/{decision_id}")
async def decision_detail(
    decision_id: str, request: Request, actor: Actor = Depends(verify_internal),
) -> dict:
    """Return prompt, model response and proposed trades for one decision."""
    async with open_db(request.app.state.settings.db_path) as db:
        cursor = await db.execute("SELECT * FROM decision_records WHERE id=?", (decision_id,))
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(404, "decision not found")
        columns = [entry[0] for entry in cursor.description]
    decision = dict(zip(columns, row))
    decision["has_trade"] = bool(decision["has_trade"])
    storage = Path(decision["storage_dir"])
    # Only read the fixed artifact names in the decision's own directory.
    if storage.name != decision_id or storage.parent.name != "decisions":
        raise HTTPException(409, "decision artifact path is invalid")

    def read_artifact() -> dict | None:
        record_path = storage / "record.json"
        if not record_path.is_file():
            return None
        if record_path.stat().st_size > 5_000_000:
            raise ValueError("decision artifact is too large")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("decision_id") != decision_id:
            raise ValueError("decision artifact does not match the database row")
        for filename, key in (("context.json", "context"), ("market.json", "market")):
            path = storage / filename
            if path.is_file() and path.stat().st_size <= 5_000_000:
                record[key] = json.loads(path.read_text(encoding="utf-8"))
        return record

    try:
        artifact = await asyncio.to_thread(read_artifact)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "decision artifact is unreadable") from exc
    return {"decision": decision, "artifact_available": artifact is not None, "artifact": artifact}


@router.post("/decisions/{decision_id}/promote", status_code=201)
async def promote(
    decision_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Promote a decision to a checkpoint.

    Returns ``{"id": <checkpoint_id>, "status": "ready"}`` with HTTP
    201 on success.  See :mod:`liveoption_web.checkpoint.promote` for
    the crash-safe artifact + transaction protocol.

    Error shapes (all RFC 7807):

    * 403 — missing or wrong ``X-Internal-Secret`` (from dependency).
    * 404 — ``type=decision-not-found``.
    * 409 — ``type=already-promoted``.
    """
    settings = request.app.state.settings
    try:
        return await promote_decision(
            settings.db_path,
            settings.runs_dir,
            decision_id,
            actor.user_id,
        )
    except HTTPException:
        raise
