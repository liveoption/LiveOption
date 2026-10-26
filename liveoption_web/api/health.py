"""Public health endpoint.

``GET /healthz`` reports server liveness and performs a lightweight DB
reachability check by reading the current ``schema_version``.  It is
deliberately unauthenticated so local watchdogs and process supervisors
can poll it without the internal secret.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from liveoption_web.config import Settings, get_settings
from liveoption_web.db.connection import open_db

router = APIRouter()


@router.get("/healthz")
async def healthz(settings: Settings = Depends(get_settings)) -> dict:
    """Return ``{status, db, version}`` reflecting DB reachability."""
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        )
        row = await cur.fetchone()
        version = int(row[0]) if row and row[0] is not None else 0
    return {"status": "ok", "db": "reachable", "version": version}
