"""Ask-a-question API (Plan B Task 12).

Endpoints:

* ``POST /v1/checkpoints/{id}/questions`` — queue a follow-up question
  for a ``ready`` checkpoint. Returns HTTP 202 with the new question id
  and the SSE ``stream_url``; the LLM call runs as a background task
  (``asyncio.create_task``).
* ``GET  /v1/questions/{id}/stream`` — SSE feed of ``question_chunks``
  for a given question. Historical rows are replayed first (resumable
  via a ``Last-Event-ID`` header that carries the last seen ``seq``),
  then live deltas published on pubsub topic ``question:<id>``. A final
  ``event: done`` (or ``failed``) frame closes the stream.

Concurrency / rate-limit:

* ``settings.max_streaming_questions_per_checkpoint`` — concurrent
  in-flight questions per checkpoint. Over the cap → HTTP 429.
* ``settings.rate_limit_llm_per_min`` — per-actor rolling-minute cap on
  question creations. Over the cap → HTTP 429.

LLM injection:

The router looks up the async stream function at ``app.state.question_llm_stream``.
Tests install a fake stream there; production wiring installs a real
``trading.tools.llm_client``-backed adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import aiosqlite
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.api.problem import problem_response
from liveoption_web.db.connection import open_db
from liveoption_web.events.pubsub import Pubsub
from liveoption_web.llm.question_runner import run_question
from trading.ingest.ulid import new_ulid

logger = logging.getLogger(__name__)

router = APIRouter(tags=["questions"])

#: Seconds of pubsub silence before a heartbeat frame is emitted on the
#: streaming endpoint. Matches the value used by the run-events SSE
#: endpoint, but kept local so this router has no cross-file coupling.
HEARTBEAT_INTERVAL_SECS = 15.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc_now_z() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def _sse_frame(event_id: int | None, kind: str, payload_json: str) -> str:
    """Format one SSE record (see :rfc:`8895` / HTML SSE spec).

    ``payload_json`` must already be a JSON-encoded string. Kept local
    to this router so the question-stream endpoint doesn't depend on
    other task files.
    """
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {kind}")
    lines.append(f"data: {payload_json}")
    lines.append("")  # blank line terminates the frame
    return "\n".join(lines) + "\n"


_ACTIVE_STATUSES = ("pending", "streaming")


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class AskQuestion(BaseModel):
    """POST body for creating a follow-up question.

    Validation intentionally strips whitespace so a blank prompt (all
    spaces) is rejected at the 422 boundary rather than spending an LLM
    call.
    """

    user_prompt: str = Field(..., min_length=1, max_length=8000)
    model_id: str | None = None
    parent_question_id: str | None = None

    @field_validator("user_prompt")
    @classmethod
    def _strip_non_empty(cls, v: str) -> str:
        v2 = v.strip()
        if not v2:
            raise ValueError("user_prompt must be non-empty")
        return v2


# ---------------------------------------------------------------------------
# POST /v1/checkpoints/{id}/questions
# ---------------------------------------------------------------------------
@router.post("/checkpoints/{checkpoint_id}/questions", status_code=202)
async def ask_question(
    checkpoint_id: str,
    body: AskQuestion,
    request: Request,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Create a question row and spawn the LLM background task.

    Returns ``{id, stream_url, status, asked_at}``. Callers should
    subscribe to ``stream_url`` immediately to receive live chunks —
    historical chunks are included on reconnect via replay.
    """
    settings = request.app.state.settings
    pubsub: Pubsub = request.app.state.pubsub

    async with open_db(settings.db_path) as db:
        db.row_factory = aiosqlite.Row

        # -------- Checkpoint must exist + be ready --------
        cur = await db.execute(
            "SELECT status FROM checkpoints WHERE id=?", (checkpoint_id,)
        )
        ckpt = await cur.fetchone()
        if ckpt is None:
            raise problem_response(
                404, "checkpoint-not-found",
                "Checkpoint not found",
                detail=f"No checkpoint with id={checkpoint_id}",
            )
        if ckpt["status"] != "ready":
            raise problem_response(
                409, "checkpoint-not-ready",
                "Checkpoint is not ready for questions",
                detail=f"status={ckpt['status']!r}",
            )

        # -------- Parent question, if any, must live on same checkpoint --------
        if body.parent_question_id is not None:
            cur = await db.execute(
                "SELECT checkpoint_id FROM questions WHERE id=?",
                (body.parent_question_id,),
            )
            parent = await cur.fetchone()
            if parent is None:
                raise problem_response(
                    400, "parent-question-not-found",
                    "parent_question_id does not exist",
                )
            if parent["checkpoint_id"] != checkpoint_id:
                raise problem_response(
                    400, "parent-question-mismatch",
                    "parent_question_id belongs to a different checkpoint",
                )

        # -------- Per-checkpoint concurrency cap --------
        cur = await db.execute(
            "SELECT COUNT(*) FROM questions WHERE checkpoint_id=? "
            "AND status IN ('pending','streaming')",
            (checkpoint_id,),
        )
        active = (await cur.fetchone())[0]
        if active >= settings.max_streaming_questions_per_checkpoint:
            raise problem_response(
                429, "question-rate-limit",
                "Too many concurrent streaming questions for this checkpoint",
                detail=(
                    f"{active} in-flight; cap = "
                    f"{settings.max_streaming_questions_per_checkpoint}"
                ),
            )

        # -------- Per-actor rolling-minute cap --------
        if actor.user_id:
            # Asked-at is ISO-8601 UTC; lexicographic comparison against a
            # ``now - 60s`` string is correct.
            now = datetime.now(timezone.utc)
            cutoff = now.timestamp() - 60.0
            cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
            cutoff_str = (
                cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
                + f".{cutoff_dt.microsecond // 1000:03d}Z"
            )
            cur = await db.execute(
                "SELECT COUNT(*) FROM questions WHERE asked_by=? AND asked_at>=?",
                (actor.user_id, cutoff_str),
            )
            recent = (await cur.fetchone())[0]
            if recent >= settings.rate_limit_llm_per_min:
                raise problem_response(
                    429, "llm-rate-limit",
                    "LLM per-minute rate limit exceeded",
                    detail=(
                        f"{recent} questions in last 60s; cap = "
                        f"{settings.rate_limit_llm_per_min}"
                    ),
                )

        # -------- Create row --------
        qid = new_ulid()
        asked_at = _utc_now_z()
        await db.execute(
            """INSERT INTO questions(id, checkpoint_id, parent_question_id, asked_by,
                                     asked_at, user_prompt, model_id, provider,
                                     status, status_updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                qid,
                checkpoint_id,
                body.parent_question_id,
                actor.user_id or "unknown",
                asked_at,
                body.user_prompt,
                body.model_id or "",
                # Provider is filled in by the runner once the LLM binding
                # resolves; we store empty string to satisfy NOT NULL.
                "",
                asked_at,
            ),
        )
        await db.commit()

    # Spawn background LLM task. Use app.state so tests can inject a
    # no-network fake.
    stream_fn = getattr(request.app.state, "question_llm_stream", None)
    asyncio.create_task(
        run_question(
            db_path=settings.db_path,
            pubsub=pubsub,
            question_id=qid,
            checkpoint_id=checkpoint_id,
            user_prompt=body.user_prompt,
            model_id=body.model_id,
            parent_question_id=body.parent_question_id,
            stream_fn=stream_fn,
        )
    )

    return {
        "id": qid,
        "stream_url": f"/v1/questions/{qid}/stream",
        "status": "pending",
        "asked_at": asked_at,
    }


# ---------------------------------------------------------------------------
# GET /v1/questions/{id}/stream
# ---------------------------------------------------------------------------
async def _generate_question_sse(
    db_path, pubsub: Pubsub, question_id: str, last_seq: int
) -> AsyncIterator[str]:
    """Yield SSE frames for ``question_id``.

    Steps mirror :func:`liveoption_web.api.sse.generate_run_sse` but use
    ``seq`` (per-question) instead of the global ``events.id`` and close
    the stream after emitting a terminal ``done``/``failed`` frame.
    """
    queue = pubsub.subscribe(f"question:{question_id}")
    try:
        # Historical snapshot.
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM question_chunks WHERE question_id=?",
                (question_id,),
            )
            row = await cur.fetchone()
            floor_seq = row[0] if row else 0

            cur = await db.execute(
                "SELECT seq, ts, delta_text FROM question_chunks "
                "WHERE question_id=? AND seq>? AND seq<=? ORDER BY seq",
                (question_id, last_seq, floor_seq),
            )
            rows = await cur.fetchall()

        for seq, ts, delta_text in rows:
            yield _sse_frame(
                seq, "chunk",
                json.dumps({"seq": seq, "ts": ts, "delta_text": delta_text}),
            )

        # If the question is already terminal, emit done/failed now.
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT status, error_message, response_text FROM questions WHERE id=?",
                (question_id,),
            )
            qrow = await cur.fetchone()
        if qrow is not None:
            status, error_message, response_text = qrow
            if status == "completed":
                yield _sse_frame(
                    None, "done",
                    json.dumps({"response_text": response_text or ""}),
                )
                return
            if status == "failed":
                yield _sse_frame(
                    None, "failed",
                    json.dumps({"error": error_message or ""}),
                )
                return

        # Live fanout until we observe a terminal marker.
        while True:
            try:
                ev = await asyncio.wait_for(
                    queue.get(), timeout=HEARTBEAT_INTERVAL_SECS
                )
            except asyncio.TimeoutError:
                yield _sse_frame(None, "heartbeat", "{}")
                continue

            if ev.get("done"):
                yield _sse_frame(None, "done", json.dumps(ev))
                return
            if ev.get("failed"):
                yield _sse_frame(None, "failed", json.dumps(ev))
                return

            seq = ev.get("seq", 0)
            if seq <= floor_seq:
                continue
            yield _sse_frame(seq, "chunk", json.dumps(ev))
    finally:
        pubsub.unsubscribe(f"question:{question_id}", queue)


@router.get("/questions/{question_id}/stream")
async def stream_question(
    question_id: str,
    request: Request,
    actor: Actor = Depends(verify_internal),
    last_event_id: int = Header(default=0, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE endpoint for a question's assembled chunk stream.

    Returns HTTP 404 (problem+json) if the question is not known —
    streaming a non-existent resource is a client error, not an empty
    success.
    """
    settings = request.app.state.settings
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT 1 FROM questions WHERE id=?", (question_id,)
        )
        if await cur.fetchone() is None:
            raise problem_response(
                404, "question-not-found",
                "Question not found",
                detail=f"No question with id={question_id}",
            )

    return StreamingResponse(
        _generate_question_sse(
            settings.db_path,
            request.app.state.pubsub,
            question_id,
            last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
