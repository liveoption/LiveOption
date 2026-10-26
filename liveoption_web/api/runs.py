"""HTTP routes for ``/v1/runs`` — list, detail, timeline, pnl, patch, children.

This module is deliberately thin: all database work lives in
:mod:`liveoption_web.runs.service`, and each endpoint is an orchestrator
that resolves ``db_path`` from ``app.state.settings``, calls the
service, and shapes the response.

Later tasks add more endpoints to this router:

* Task 9 — ``GET /v1/runs/{id}/events`` (SSE streaming)
* Task 15 — ``GET /v1/runs/{a}/compare/{b}`` (diverge comparison)

To keep those additions low-friction, route handlers are grouped by
concern and the Pydantic request models live next to the handler that
uses them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import asyncio

from liveoption_web import eval as eval_pkg
from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.api.launch import LaunchRunRequest, handle_launch_run
from liveoption_web.api.sse import generate_run_sse
from liveoption_web.runs import service

router = APIRouter(tags=["runs"])


def _db_path(request: Request):
    """Resolve the SQLite path from application settings."""

    return request.app.state.settings.db_path


def _run_not_found(run_id: str) -> HTTPException:
    """RFC 7807-shaped 404 for a missing run."""

    return HTTPException(
        status_code=404,
        detail={
            "type": "run-not-found",
            "title": "Run not found",
            "status": 404,
            "detail": f"No run with id {run_id!r}",
        },
    )


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_runs(
    request: Request,
    scenario: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    created_by: str | None = None,
    parent_run_id: str | None = None,
    featured: bool | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Paginated list of runs with optional filters."""

    return await service.list_runs(
        _db_path(request),
        scenario=scenario,
        kind=kind,
        status=status,
        created_by=created_by,
        parent_run_id=parent_run_id,
        featured=featured,
        cursor=cursor,
        limit=limit,
    )


@router.post("/runs", status_code=202)
async def create_run(
    request: Request,
    body: LaunchRunRequest,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Launch a scenario-catalog batch run. See :mod:`liveoption_web.api.launch`."""

    return await handle_launch_run(request, body, actor)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Return a single run or RFC 7807 404."""

    row = await service.get_run(_db_path(request), run_id)
    if row is None:
        raise _run_not_found(run_id)
    return row


@router.get("/runs/{run_id}/metrics")
async def get_run_metrics(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Return parsed ``run_metrics`` row (Phase B5).

    Returns RFC 7807 404 with type ``metrics-not-found`` if the run has
    no ``run_metrics`` row yet (still running, or evaluators not landed).
    """

    db_path = _db_path(request)
    metrics = await service.get_run_metrics(db_path, run_id)
    if metrics is None:
        raise HTTPException(
            status_code=404,
            detail={
                "type": "metrics-not-found",
                "title": "Run metrics not available",
                "status": 404,
                "detail": (
                    f"No run_metrics row for run_id={run_id!r}. Either the run "
                    "is not yet completed, or evaluators have not landed (Phase C)."
                ),
            },
        )
    # Phase C6: synchronous recompute on evaluator-version mismatch.
    current = eval_pkg.current_evaluator_version(metrics.get("family"))
    if metrics.get("evaluator_version") != current:
        recomputed = await asyncio.to_thread(
            eval_pkg.aggregate_for, run_id, db_path
        )
        if recomputed is not None:
            metrics = recomputed
    return metrics


@router.post("/runs/{run_id}/metrics:recompute")
async def recompute_run_metrics(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Force-recompute the run's metrics row (Phase C6).

    Returns the freshly persisted row, or 404 if the run does not exist.
    """

    db_path = _db_path(request)
    existing = await service.get_run(db_path, run_id)
    if existing is None:
        raise _run_not_found(run_id)
    result = await asyncio.to_thread(eval_pkg.aggregate_for, run_id, db_path)
    if result is None:
        raise _run_not_found(run_id)
    return result


# ---------------------------------------------------------------------------
# Patch — editable UI fields only
# ---------------------------------------------------------------------------


class RunPatch(BaseModel):
    """Subset of ``runs`` columns that operators may edit post-hoc."""

    title: str | None = None
    notes: str | None = None
    is_featured: bool | None = None


@router.patch("/runs/{run_id}")
async def patch_run(
    run_id: str,
    patch: RunPatch,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Partial update of ``title`` / ``notes`` / ``is_featured``."""

    ok = await service.patch_run(
        _db_path(request),
        run_id,
        title=patch.title,
        notes=patch.notes,
        is_featured=patch.is_featured,
    )
    if not ok:
        raise _run_not_found(run_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Timeline / PnL / Children
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/timeline")
async def timeline(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Return decision_records for the run ordered by ``seq``."""

    existing = await service.get_run(_db_path(request), run_id)
    if existing is None:
        raise _run_not_found(run_id)
    items = await service.get_timeline(_db_path(request), run_id)
    return {"items": items}


@router.get("/runs/{run_id}/pnl")
async def pnl(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Return aggregated ``day.completed`` payloads as a PnL series."""

    existing = await service.get_run(_db_path(request), run_id)
    if existing is None:
        raise _run_not_found(run_id)
    series = await service.get_pnl_series(_db_path(request), run_id)
    return {"items": series}


@router.get("/runs/{run_id}/children")
async def children(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Return runs whose ``parent_run_id`` equals ``run_id``."""

    existing = await service.get_run(_db_path(request), run_id)
    if existing is None:
        raise _run_not_found(run_id)
    items = await service.list_children(_db_path(request), run_id)
    return {"items": items}


# ---------------------------------------------------------------------------
# Task 15 — Compare endpoint
# ---------------------------------------------------------------------------


@router.get("/runs/{a}/compare/{b}")
async def compare_runs(
    a: str,
    b: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Return a side-by-side comparison of runs ``a`` and ``b``.

    The payload shape is geared toward a diff UI:

    * ``a``/``b`` — each side's ``runs`` row (with parsed ``metrics``).
    * ``days`` — ``day.completed`` payloads aligned by ``trading_day``;
      missing sides surface as ``None``.
    * ``decisions`` — decision records aligned by ``seq``; missing sides
      surface as ``None``.

    Returns RFC 7807 404 if *either* run id is unknown.
    """

    db = _db_path(request)
    row_a = await service.get_run(db, a)
    if row_a is None:
        raise _run_not_found(a)
    row_b = await service.get_run(db, b)
    if row_b is None:
        raise _run_not_found(b)

    # Phase C7: same-family enforcement (spec §7.1). Cross-family
    # compare is intentionally not supported — payload semantics differ
    # too much to render side-by-side meaningfully.
    fam_a = row_a.get("family")
    fam_b = row_b.get("family")
    if fam_a != fam_b:
        raise HTTPException(
            status_code=400,
            detail={
                "type": "cross-family-compare-not-supported",
                "title": "Cross-family compare not supported",
                "status": 400,
                "detail": (
                    f"run a family={fam_a!r} != run b family={fam_b!r}; "
                    "compare requires same family or both null."
                ),
            },
        )

    # Attach generic metrics (parsed from run_metrics.generic_json) for
    # diff display.  Falls back to None when the run has no run_metrics
    # row yet (still running or evaluators haven't landed).
    metrics_a = await service.get_run_metrics(db, a)
    metrics_b = await service.get_run_metrics(db, b)
    row_a["metrics"] = metrics_a["generic"] if metrics_a else None
    row_b["metrics"] = metrics_b["generic"] if metrics_b else None

    pnl_a = await service.get_pnl_series(db, a)
    pnl_b = await service.get_pnl_series(db, b)
    timeline_a = await service.get_timeline(db, a)
    timeline_b = await service.get_timeline(db, b)

    # Align PnL points by trading_day.
    by_day_a = {p["trading_day"]: p for p in pnl_a if p.get("trading_day")}
    by_day_b = {p["trading_day"]: p for p in pnl_b if p.get("trading_day")}
    all_days = sorted(set(by_day_a) | set(by_day_b))
    days = [
        {
            "trading_day": day,
            "a": by_day_a.get(day),
            "b": by_day_b.get(day),
        }
        for day in all_days
    ]

    # Align decision records by seq.
    by_seq_a = {d["seq"]: d for d in timeline_a}
    by_seq_b = {d["seq"]: d for d in timeline_b}
    all_seqs = sorted(set(by_seq_a) | set(by_seq_b))
    decisions = [
        {
            "seq": seq,
            "a": by_seq_a.get(seq),
            "b": by_seq_b.get(seq),
        }
        for seq in all_seqs
    ]

    return {
        "a": row_a,
        "b": row_b,
        "days": days,
        "decisions": decisions,
    }


# ---------------------------------------------------------------------------
# Task 9 — SSE stream of run events
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
    last_event_id: int = Header(default=0, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Stream persisted + live events for ``run_id`` as SSE.

    Honors the ``Last-Event-ID`` header for resumable clients: rows with
    ``id > last_event_id`` are replayed from SQLite, then the handler
    switches to the in-process pubsub for live fanout.  Heartbeat frames
    are emitted during idle periods so proxies don't close the socket.
    """
    return StreamingResponse(
        generate_run_sse(
            request.app.state.settings.db_path,
            request.app.state.pubsub,
            run_id,
            last_event_id,
            request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Task 1 — REST cursor-paginated event history
# ---------------------------------------------------------------------------


class EventHistoryItem(BaseModel):
    """Single event row returned by the history endpoint."""

    id: int
    kind: str
    payload: dict[str, Any]
    occurred_at: str


class EventHistoryPage(BaseModel):
    """Cursor-paginated page of events for a run.

    ``events`` is newest-first by ``events.id`` desc. ``total`` counts every
    event for ``run_id`` independent of pagination. ``truncated`` is True iff
    older events exist beyond this page; ``next_cursor`` is non-null iff
    truncated and equals ``min(events.id)`` (the last/oldest event's id).
    """

    events: list[EventHistoryItem]
    total: int
    truncated: bool
    next_cursor: int | None


@router.get("/runs/{run_id}/events/history", response_model=EventHistoryPage)
async def events_history(
    run_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
    limit: int = Query(default=500),
    cursor: int | None = Query(default=None),
) -> EventHistoryPage:
    """REST cursor-paginated event history (newest-first) for a completed run.

    Coexists with the SSE handler at ``/v1/runs/{run_id}/events``. Used by
    the unified run-detail view to populate the events panel after a run
    finishes.
    """
    db_path = _db_path(request)
    if not await service.run_exists(db_path, run_id):
        raise _run_not_found(run_id)
    result = await service.get_run_events_history(db_path, run_id, limit, cursor)
    return EventHistoryPage(**result)
