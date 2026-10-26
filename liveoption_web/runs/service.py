"""Read/write services backing the ``/v1/runs`` HTTP surface.

These functions are intentionally framework-agnostic: each takes a
``db_path`` plus plain keyword arguments and returns dicts (or
``None``).  The API layer (``liveoption_web.api.runs``) handles
HTTP-specific concerns — auth, status codes, response shaping.

Design notes:

* Queries use ``row_factory`` to yield dict rows so callers don't
  depend on column ordering.
* Filter clauses are built by appending to a shared ``params`` list so
  SQL is always parameterised (never interpolated).
* Pagination uses monotonically-decreasing ``runs.id`` (ULID/ksuid-like
  lexicographic ordering) as a keyset cursor.  This avoids ``OFFSET``
  cost drift as tables grow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from liveoption_web.db.connection import open_db


# ---------------------------------------------------------------------------
# Runs — list / detail / patch / children
# ---------------------------------------------------------------------------


async def list_runs(
    db_path: Path,
    *,
    scenario: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    created_by: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    featured: Optional[bool] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Return ``{"items": [...], "next_cursor": <id|None>}``.

    Results are keyset-paginated by descending ``runs.id``.  The
    ``cursor`` argument, when provided, is the last ``id`` from the
    previous page; rows with ``id < cursor`` are returned.
    """

    clauses: list[str] = []
    params: list[Any] = []
    if scenario is not None:
        clauses.append("scenario = ?")
        params.append(scenario)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if created_by is not None:
        clauses.append("created_by = ?")
        params.append(created_by)
    if parent_run_id is not None:
        clauses.append("parent_run_id = ?")
        params.append(parent_run_id)
    if featured:
        clauses.append("is_featured = 1")
    if cursor is not None:
        clauses.append("id < ?")
        params.append(cursor)

    where = " AND ".join(clauses) or "1=1"
    params.append(limit + 1)  # fetch one extra to detect "has more"
    sql = f"SELECT * FROM runs WHERE {where} ORDER BY id DESC LIMIT ?"

    async with open_db(db_path) as db:
        db.row_factory = _row_factory
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()

    items = [_run_row_to_dict(r) for r in rows[:limit]]
    next_cursor = items[-1]["id"] if len(rows) > limit else None
    return {"items": items, "next_cursor": next_cursor}


async def get_run(db_path: Path, run_id: str) -> Optional[dict]:
    """Return a single run dict or ``None`` if not found."""

    async with open_db(db_path) as db:
        db.row_factory = _row_factory
        cur = await db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
    return _run_row_to_dict(row) if row else None


async def patch_run(
    db_path: Path,
    run_id: str,
    *,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    is_featured: Optional[bool] = None,
) -> bool:
    """Apply an UPDATE to the given run; returns ``True`` if the row exists.

    ``None`` values are skipped (partial update semantics).  When every
    field is ``None`` the function simply verifies existence.
    """

    sets: list[str] = []
    params: list[Any] = []
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if is_featured is not None:
        sets.append("is_featured = ?")
        params.append(1 if is_featured else 0)

    async with open_db(db_path) as db:
        # Existence check first — lets the caller return 404 without
        # silently swallowing a no-op UPDATE.
        cur = await db.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,))
        if await cur.fetchone() is None:
            return False
        if sets:
            params.append(run_id)
            sql = f"UPDATE runs SET {', '.join(sets)} WHERE id = ?"
            await db.execute(sql, params)
            await db.commit()
    return True


async def get_run_metrics(db_path: Path, run_id: str) -> Optional[dict]:
    """Return parsed ``run_metrics`` row for ``run_id`` or ``None``.

    Parses the JSON columns into native Python objects.  ``family_json``
    is optional in the schema and surfaces as ``family_specific=None``
    when absent.
    """

    async with open_db(db_path) as db:
        db.row_factory = _row_factory
        cur = await db.execute(
            """
            SELECT run_id, family, generic_json, family_json,
                   behavioral_json, computed_at, evaluator_version
              FROM run_metrics
             WHERE run_id = ?
            """,
            (run_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    family_specific = (
        json.loads(row["family_json"]) if row["family_json"] else None
    )
    return {
        "run_id": row["run_id"],
        "family": row["family"],
        "generic": json.loads(row["generic_json"]),
        "family_specific": family_specific,
        "behavioral": json.loads(row["behavioral_json"]),
        "computed_at": row["computed_at"],
        "evaluator_version": row["evaluator_version"],
    }


async def list_children(db_path: Path, run_id: str) -> list[dict]:
    """Return all runs whose ``parent_run_id`` is ``run_id`` (newest first)."""

    async with open_db(db_path) as db:
        db.row_factory = _row_factory
        cur = await db.execute(
            "SELECT * FROM runs WHERE parent_run_id = ? ORDER BY id DESC",
            (run_id,),
        )
        rows = await cur.fetchall()
    return [_run_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Decision records — timeline
# ---------------------------------------------------------------------------


async def get_timeline(db_path: Path, run_id: str) -> list[dict]:
    """Return decision_records for ``run_id`` ordered by ``seq``."""

    async with open_db(db_path) as db:
        db.row_factory = _row_factory
        cur = await db.execute(
            "SELECT * FROM decision_records WHERE run_id = ? ORDER BY seq ASC",
            (run_id,),
        )
        rows = await cur.fetchall()
    for r in rows:
        # Integer-boolean coercion to mirror the pydantic model.
        r["has_trade"] = bool(r["has_trade"])
    return rows


# ---------------------------------------------------------------------------
# Events — PnL series
# ---------------------------------------------------------------------------


async def get_pnl_series(db_path: Path, run_id: str) -> list[dict]:
    """Aggregate ``day.completed`` events into a PnL time series.

    Each point is ``{"trading_day", "cum_pnl", "portfolio_value"}``.
    The actual keys of interest live inside ``events.payload_json``;
    unknown/missing fields are surfaced as ``None`` so the UI can still
    render a partial series.
    """

    async with open_db(db_path) as db:
        db.row_factory = _row_factory
        cur = await db.execute(
            """
            SELECT payload_json
              FROM events
             WHERE run_id = ? AND kind = 'day.completed'
             ORDER BY id ASC
            """,
            (run_id,),
        )
        rows = await cur.fetchall()

    points: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        points.append(
            {
                "trading_day": payload.get("trading_day"),
                "cum_pnl": payload.get("cum_pnl"),
                "portfolio_value": payload.get("portfolio_value"),
            }
        )
    return points


# ---------------------------------------------------------------------------
# Event history
# ---------------------------------------------------------------------------


async def run_exists(db_path: Path, run_id: str) -> bool:
    """Return True iff a run with id ``run_id`` exists."""
    async with open_db(db_path) as db:
        cur = await db.execute("SELECT 1 FROM runs WHERE id = ? LIMIT 1", (run_id,))
        row = await cur.fetchone()
        return row is not None


def _safe_payload(raw: str | None) -> dict:
    """Best-effort parse: corrupt or null payload becomes empty dict.

    Keeps a single bad row from 500-ing the whole page. The events table
    column is NOT NULL but the type system can't enforce well-formed JSON.
    """
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

async def get_run_events_history(
    db_path: Path,
    run_id: str,
    limit: int,
    cursor: int | None,
) -> dict:
    """Cursor-paginated event history for ``run_id`` (newest-first).

    Uses the +1 fetch trick: requests ``limit + 1`` rows, drops the extra
    one (if present) to flag truncation cheaply without a second query
    answering "is there more?". Total count is a separate query so callers
    can show ``N of total`` mid-pagination.

    ``limit`` is clamped to ``[1, 2000]``.
    Returns the EventHistoryPage shape as a plain dict; the route handler
    wraps it in the Pydantic model so this module stays framework-agnostic.
    """
    bounded = max(1, min(limit, 2000))

    if cursor is None:
        sql = (
            "SELECT id, kind, payload_json, ts FROM events "
            "WHERE run_id = ? ORDER BY id DESC LIMIT ?"
        )
        params: tuple = (run_id, bounded + 1)
    else:
        sql = (
            "SELECT id, kind, payload_json, ts FROM events "
            "WHERE run_id = ? AND id < ? ORDER BY id DESC LIMIT ?"
        )
        params = (run_id, cursor, bounded + 1)

    async with open_db(db_path) as db:
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        cur = await db.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
        )
        total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

    truncated = len(rows) > bounded
    if truncated:
        rows = rows[:bounded]

    events = [
        {
            "id": row[0],
            "kind": row[1],
            "payload": _safe_payload(row[2]),
            "occurred_at": row[3],
        }
        for row in rows
    ]
    next_cursor = events[-1]["id"] if (truncated and events) else None
    return {
        "events": events,
        "total": total,
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


def _row_factory(cursor, row):
    """Convert a sqlite row into a plain dict keyed by column name."""

    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


def _run_row_to_dict(row: Optional[dict]) -> Optional[dict]:
    """Post-process a raw ``runs`` row for API consumption.

    * Coerces the ``is_featured`` integer to a real bool.
    * Parses the ``universe_json`` blob into a nested structure under
      ``universe``, dropping the raw string so the payload stays tidy.

    Note: ``metrics_json`` was retired in migration 002 (Phase B). Metrics
    now live in the dedicated ``run_metrics`` table and will be served via
    ``GET /v1/runs/{id}/metrics`` (Phase B5+).
    """

    if row is None:
        return None
    d = dict(row)
    if "is_featured" in d:
        d["is_featured"] = bool(d["is_featured"])
    if d.get("universe_json"):
        try:
            d["universe"] = json.loads(d["universe_json"])
        except (TypeError, ValueError):
            d["universe"] = None
        d.pop("universe_json", None)
    else:
        d.pop("universe_json", None)
        d.setdefault("universe", None)
    try:
        snapshot = json.loads(d.get("launch_spec_json") or "{}")
        effective = snapshot.get("effective_config") or {}
        spec = snapshot.get("spec") or {}
        d["replay_frequency"] = effective.get("experiment", {}).get("frequency") or ("1m" if spec.get("decision_time_et") else None)
    except (TypeError, ValueError, AttributeError):
        d["replay_frequency"] = None
    return d
