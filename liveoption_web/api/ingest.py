"""Loopback-only ingest routes for subprocess → FastAPI traffic.

All three endpoints require the shared ``X-Internal-Secret`` header via
:func:`liveoption_web.api.deps.verify_internal`; the subprocess side
attaches the same secret read from the environment.  Request bodies
follow Spec 5.4.

These routes are intentionally thin: schema validation (pydantic) plus a
delegation to :mod:`liveoption_web.events.ingestor`, which owns DB writes
and pubsub fanout.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.events.ingestor import (
    ingest_decision_record,
    ingest_events,
    ingest_question_chunk,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# /_ingest/events
# ---------------------------------------------------------------------------
class EventIn(BaseModel):
    subprocess_seq: int
    ts: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBatch(BaseModel):
    run_id: str
    events: list[EventIn]


@router.post("/_ingest/events")
async def _ingest_events(
    batch: EventBatch,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict[str, int]:
    settings = request.app.state.settings
    pubsub = request.app.state.pubsub
    return await ingest_events(
        settings.db_path,
        batch.run_id,
        [e.model_dump() for e in batch.events],
        pubsub,
    )


# ---------------------------------------------------------------------------
# /_ingest/decision-record
# ---------------------------------------------------------------------------
class DecisionIn(BaseModel):
    id: str
    seq: int
    trading_day: str
    decision_ts: str
    model_id: str
    provider: str
    prompt_sha256: str
    response_sha256: str
    storage_dir: str
    has_trade: bool
    promoted_checkpoint_id: str | None = None
    cum_pnl_after: float | None = None


class DecisionRecordIn(BaseModel):
    run_id: str
    decision: DecisionIn


@router.post("/_ingest/decision-record")
async def _ingest_decision_record(
    body: DecisionRecordIn,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict[str, Any]:
    settings = request.app.state.settings
    return await ingest_decision_record(
        settings.db_path,
        body.run_id,
        body.decision.model_dump(),
    )


# ---------------------------------------------------------------------------
# /_ingest/question-chunk
# ---------------------------------------------------------------------------
class QuestionChunkIn(BaseModel):
    question_id: str
    seq: int
    delta_text: str
    ts: str


@router.post("/_ingest/question-chunk")
async def _ingest_question_chunk(
    body: QuestionChunkIn,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict[str, Any]:
    settings = request.app.state.settings
    pubsub = request.app.state.pubsub
    return await ingest_question_chunk(
        settings.db_path,
        body.model_dump(),
        pubsub,
    )
