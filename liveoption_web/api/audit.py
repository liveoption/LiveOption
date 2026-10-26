"""Audit log read API.

``GET /v1/audit/targets/{type}/{id}`` returns the append-only audit trail
for a single target (e.g. a checkpoint, run, decision) in reverse-
chronological order.  The endpoint is gated on the shared internal
secret via :func:`liveoption_web.api.deps.verify_internal`.

Rows live in the ``audit_log`` table (see migration ``001_init.sql``).
``payload_json`` is stored as a TEXT blob; this handler decodes it into
a JSON object for the response so clients don't double-parse.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.config import Settings, get_settings
from liveoption_web.db.connection import open_db

router = APIRouter()


def _decode_payload(raw: str | None) -> object | None:
    """Decode ``payload_json`` text into a JSON-compatible value.

    Returns ``None`` for NULL rows; falls back to the raw string when
    the column contains non-JSON text (defensive — the writer should
    always emit valid JSON, but we never want a single malformed row
    to break the entire listing).
    """
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


@router.get("/audit/targets/{target_type}/{target_id}")
async def list_audit_entries(
    target_type: str,
    target_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return audit_log rows for a given target, newest first."""

    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            """SELECT id, ts, actor_user_id, actor_email, action,
                      target_type, target_id, payload_json
               FROM audit_log
               WHERE target_type = ? AND target_id = ?
               ORDER BY ts DESC, id DESC""",
            (target_type, target_id),
        )
        rows = await cur.fetchall()

    items = [
        {
            "id": row[0],
            "ts": row[1],
            "actor_user_id": row[2],
            "actor_email": row[3],
            "action": row[4],
            "target_type": row[5],
            "target_id": row[6],
            "payload": _decode_payload(row[7]),
        }
        for row in rows
    ]
    return {"items": items}
