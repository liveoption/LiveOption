"""Tests for Task 12: /v1/checkpoints/:id/questions (ask + stream).

Covers:
- POST /v1/checkpoints/:id/questions      — create + kick off background runner
- GET  /v1/questions/:id/stream            — SSE of question_chunks topic
- Rate limit: max concurrent streaming questions per checkpoint (HTTP 429)
- LLM failure path  — row marked ``failed`` + error_message stored
- Happy path         — chunks persisted, final response_text assembled, status=completed

All LLM interactions are mocked: tests inject a fake runner (no network).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

import pytest

from liveoption_web.db.connection import open_db


HDR = {"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _seed_ready_checkpoint(
    *,
    run_id: str = "01HRUNQQ00000000000000QUES",
    decision_id: str = "01HDECQQ00000000000000QUES",
    ckpt_id: str = "01HCKPQQ00000000000000QUES",
) -> str:
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id, provider,
                                date_start, date_end, status, status_updated_at,
                                created_at, storage_dir)
               VALUES(?, 'base', 'llm_v1', '/tmp/cfg.yaml', 'deepseek-v3',
                      'deepseek', '2026-04-01', '2026-04-10', 'completed',
                      '2026-04-19T00:00:00.000Z', '2026-04-19T00:00:00.000Z',
                      '/tmp/runs/r1')""",
            (run_id,),
        )
        await db.execute(
            """INSERT INTO decision_records(id, run_id, seq, trading_day, decision_ts,
                                            model_id, provider, prompt_sha256, response_sha256,
                                            storage_dir, has_trade, created_at)
               VALUES(?, ?, 1, '2026-04-02',
                      '2026-04-02T15:00:00.000-04:00', 'deepseek-v3', 'deepseek',
                      'abc', 'def', '/tmp/runs/r1/decisions/d1', 1,
                      '2026-04-19T00:00:00.000Z')""",
            (decision_id, run_id),
        )
        await db.execute(
            """INSERT INTO checkpoints(id, run_id, decision_record_id, storage_dir,
                                       fingerprint, promoted_at, promoted_by, status,
                                       status_updated_at)
               VALUES(?, ?, ?, ?, 'fp-1', '2026-04-19T00:00:01.000Z', 'alice',
                      'ready', '2026-04-19T00:00:01.000Z')""",
            (ckpt_id, run_id, decision_id, f"/tmp/runs/r1/checkpoints/{ckpt_id}"),
        )
        await db.commit()
    return ckpt_id


async def _poll_question_status(qid: str, *, want: str, timeout: float = 3.0) -> dict:
    """Poll the questions row until ``status == want`` or timeout."""
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict | None = None
    while asyncio.get_event_loop().time() < deadline:
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT status, response_text, error_message FROM questions WHERE id=?",
                (qid,),
            )
            row = await cur.fetchone()
            if row is not None:
                last = {"status": row[0], "response_text": row[1], "error_message": row[2]}
                if last["status"] == want:
                    return last
        await asyncio.sleep(0.02)
    raise AssertionError(f"question {qid} never reached status={want}; last={last}")


# ---------------------------------------------------------------------------
# Fake LLM streams — injected via app.state to avoid network
# ---------------------------------------------------------------------------
async def _fake_stream_ok(*, prompt: str, model_id: str | None) -> AsyncIterator[str]:
    for tok in ["hello ", "from ", "mock"]:
        yield tok


async def _fake_stream_fail(*, prompt: str, model_id: str | None) -> AsyncIterator[str]:
    # Emit one chunk then blow up.
    yield "partial-"
    raise RuntimeError("mock llm explosion")


def _install_stream(app, stream_fn) -> None:
    app.state.question_llm_stream = stream_fn


# ---------------------------------------------------------------------------
# Auth + validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ask_requires_secret(client):
    r = await client.post("/v1/checkpoints/whatever/questions",
                          json={"user_prompt": "why?"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ask_rejects_missing_checkpoint(client):
    _install_stream(client._transport.app, _fake_stream_ok)
    r = await client.post("/v1/checkpoints/does-not-exist/questions",
                          headers=HDR,
                          json={"user_prompt": "why?"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_ask_rejects_blank_prompt(client):
    _install_stream(client._transport.app, _fake_stream_ok)
    ckpt = await _seed_ready_checkpoint()
    r = await client.post(f"/v1/checkpoints/{ckpt}/questions",
                          headers=HDR,
                          json={"user_prompt": "   "})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ask_creates_question_and_streams(client):
    _install_stream(client._transport.app, _fake_stream_ok)
    ckpt = await _seed_ready_checkpoint()

    r = await client.post(
        f"/v1/checkpoints/{ckpt}/questions",
        headers=HDR,
        json={"user_prompt": "why?", "model_id": "deepseek-v3"},
    )
    assert r.status_code == 202
    body = r.json()
    assert "id" in body
    qid = body["id"]
    assert body["stream_url"] == f"/v1/questions/{qid}/stream"
    assert body["status"] in ("pending", "streaming", "completed")

    final = await _poll_question_status(qid, want="completed", timeout=3.0)
    assert final["response_text"] == "hello from mock"

    # Chunks persisted
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT seq, delta_text FROM question_chunks WHERE question_id=? ORDER BY seq",
            (qid,),
        )
        rows = await cur.fetchall()
    assert [r[1] for r in rows] == ["hello ", "from ", "mock"]
    assert [r[0] for r in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_ask_parent_question_requires_same_checkpoint(client):
    _install_stream(client._transport.app, _fake_stream_ok)
    ckpt = await _seed_ready_checkpoint()

    # Insert a stray question under a DIFFERENT checkpoint id — we'll try
    # to reference it as the parent.
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    async with open_db(db_path) as db:
        # Another checkpoint (reuse same run/decision by making a new decision+ckpt).
        await db.execute(
            """INSERT INTO decision_records(id, run_id, seq, trading_day, decision_ts,
                                            model_id, provider, prompt_sha256, response_sha256,
                                            storage_dir, has_trade, created_at)
               VALUES('01HDECQQ00000000000000OTHR','01HRUNQQ00000000000000QUES',
                      2, '2026-04-03', '2026-04-03T15:00:00.000-04:00',
                      'deepseek-v3', 'deepseek', 'x', 'y',
                      '/tmp/runs/r1/decisions/d2', 0, '2026-04-19T00:00:00.000Z')""",
        )
        await db.execute(
            """INSERT INTO checkpoints(id, run_id, decision_record_id, storage_dir,
                                       fingerprint, promoted_at, promoted_by, status,
                                       status_updated_at)
               VALUES('01HCKPQQ00000000000000OTHR','01HRUNQQ00000000000000QUES',
                      '01HDECQQ00000000000000OTHR',
                      '/tmp/runs/r1/checkpoints/other', 'fp-2',
                      '2026-04-19T00:00:02.000Z','alice','ready',
                      '2026-04-19T00:00:02.000Z')""",
        )
        await db.execute(
            """INSERT INTO questions(id, checkpoint_id, parent_question_id, asked_by,
                                     asked_at, user_prompt, model_id, provider,
                                     status, status_updated_at)
               VALUES('01HQSTQQ00000000000000OTHR','01HCKPQQ00000000000000OTHR', NULL,
                      'bob','2026-04-19T00:00:10.000Z','prev?','deepseek-v3','deepseek',
                      'completed','2026-04-19T00:00:11.000Z')""",
        )
        await db.commit()

    r = await client.post(
        f"/v1/checkpoints/{ckpt}/questions",
        headers=HDR,
        json={
            "user_prompt": "follow up?",
            "parent_question_id": "01HQSTQQ00000000000000OTHR",
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == 400


# ---------------------------------------------------------------------------
# Rate limit: max concurrent streaming questions per checkpoint
# ---------------------------------------------------------------------------
async def _hanging_stream(*, prompt: str, model_id: str | None) -> AsyncIterator[str]:
    # Never yields — holds status=streaming until the test event releases it.
    await asyncio.Event().wait()
    yield "unreachable"  # pragma: no cover


@pytest.mark.asyncio
async def test_rate_limit_concurrent_streaming_questions(client):
    _install_stream(client._transport.app, _hanging_stream)
    ckpt = await _seed_ready_checkpoint()

    # Default cap from settings: max_streaming_questions_per_checkpoint = 3
    for _ in range(3):
        r = await client.post(
            f"/v1/checkpoints/{ckpt}/questions",
            headers=HDR,
            json={"user_prompt": "q"},
        )
        assert r.status_code == 202

    # Give the runner a moment to flip rows into ``streaming`` status.
    await asyncio.sleep(0.05)

    r = await client.post(
        f"/v1/checkpoints/{ckpt}/questions",
        headers=HDR,
        json={"user_prompt": "q"},
    )
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_runner_marks_failed_on_llm_error(client):
    _install_stream(client._transport.app, _fake_stream_fail)
    ckpt = await _seed_ready_checkpoint()

    r = await client.post(
        f"/v1/checkpoints/{ckpt}/questions",
        headers=HDR,
        json={"user_prompt": "why?"},
    )
    assert r.status_code == 202
    qid = r.json()["id"]

    final = await _poll_question_status(qid, want="failed", timeout=3.0)
    assert "mock llm explosion" in (final["error_message"] or "")


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------
async def _collect_stream_chunks(stream, stop_at_kinds: set[str], timeout: float = 3.0) -> list[str]:
    lines: list[str] = []

    async def _inner():
        async for ln in stream.aiter_lines():
            lines.append(ln)
            if ln.startswith("event: ") and ln[7:] in stop_at_kinds:
                # Peek for the blank-line terminator then stop.
                return

    await asyncio.wait_for(_inner(), timeout=timeout)
    return lines


@pytest.mark.asyncio
async def test_stream_replays_and_ends(client):
    _install_stream(client._transport.app, _fake_stream_ok)
    ckpt = await _seed_ready_checkpoint()

    r = await client.post(
        f"/v1/checkpoints/{ckpt}/questions",
        headers=HDR,
        json={"user_prompt": "why?"},
    )
    assert r.status_code == 202
    qid = r.json()["id"]

    # Let runner finish so every chunk is persisted + status=completed.
    await _poll_question_status(qid, want="completed")

    async with client.stream(
        "GET",
        f"/v1/questions/{qid}/stream",
        headers={"X-Internal-Secret": "test-secret"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = await _collect_stream_chunks(resp, stop_at_kinds={"done"})

    joined = "\n".join(lines)
    assert "event: chunk" in joined
    assert "hello " in joined
    assert "event: done" in joined


@pytest.mark.asyncio
async def test_stream_unknown_question_returns_404(client):
    r = await client.get(
        "/v1/questions/does-not-exist/stream",
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert r.status_code == 404
