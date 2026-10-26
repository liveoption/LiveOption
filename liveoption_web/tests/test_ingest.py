"""Tests for Task 6 ingest endpoints.

Covers ``/_ingest/events``, ``/_ingest/decision-record`` and
``/_ingest/question-chunk``.  Each endpoint requires the shared
``X-Internal-Secret`` header (verified by the auth tests separately) and
must be idempotent against duplicate submissions keyed on the natural
uniqueness constraints declared in migration 001.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from liveoption_web.db.connection import open_db


async def _seed_run(run_id: str) -> None:
    """Insert a minimal row into ``runs`` so FK constraints are satisfied."""
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    async with open_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO runs(id, kind, scenario, config_path, model_id, provider,
                             date_start, date_end, status, status_updated_at,
                             created_at, storage_dir)
            VALUES(?, 'batch', 'weekly_llm', 'x.yaml', 'gpt-4', 'openai',
                   '2025-01-01', '2025-01-31', 'running',
                   '2026-04-18T00:00:00.000Z', '2026-04-18T00:00:00.000Z', '/tmp/run')
            """,
            (run_id,),
        )
        await db.commit()


async def _seed_checkpoint(run_id: str, decision_id: str, question_id: str) -> None:
    """Seed a run + decision_record + checkpoint + question for chunk tests."""
    await _seed_run(run_id)
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    async with open_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO decision_records(id, run_id, seq, trading_day, decision_ts,
                                         model_id, provider, prompt_sha256,
                                         response_sha256, storage_dir, has_trade,
                                         created_at)
            VALUES(?, ?, 1, '2025-03-14', '2025-03-14T14:30:00-04:00',
                   'gpt-4', 'openai', 'a'*64, 'b'*64, 'decisions/x/', 1,
                   '2026-04-18T00:00:00.000Z')
            """,
            (decision_id, run_id),
        )
        await db.execute(
            """
            INSERT INTO checkpoints(id, run_id, decision_record_id, storage_dir,
                                    fingerprint, promoted_at, promoted_by, status,
                                    status_updated_at)
            VALUES('01HCKPT', ?, ?, 'ckpts/x/', 'fp', '2026-04-18T00:00:00.000Z',
                   'user', 'ready', '2026-04-18T00:00:00.000Z')
            """,
            (run_id, decision_id),
        )
        await db.execute(
            """
            INSERT INTO questions(id, checkpoint_id, asked_by, asked_at, user_prompt,
                                  model_id, provider, status, status_updated_at)
            VALUES(?, '01HCKPT', 'user', '2026-04-18T00:00:00.000Z', 'why?',
                   'gpt-4', 'openai', 'streaming', '2026-04-18T00:00:00.000Z')
            """,
            (question_id,),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_ingest_events_requires_secret(client):
    r = await client.post(
        "/_ingest/events",
        json={"run_id": "X", "events": []},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ingest_events_batch(client):
    await _seed_run("01HRUN")

    r = await client.post(
        "/_ingest/events",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "run_id": "01HRUN",
            "events": [
                {
                    "subprocess_seq": 1,
                    "ts": "2026-04-18T00:00:01.000Z",
                    "kind": "run.started",
                    "payload": {},
                },
                {
                    "subprocess_seq": 2,
                    "ts": "2026-04-18T00:00:02.000Z",
                    "kind": "decision.started",
                    "payload": {"decision_id": "01HDEC"},
                },
            ],
        },
    )
    assert r.status_code == 200
    assert r.json() == {"accepted": 2, "duplicates": 0}


@pytest.mark.asyncio
async def test_ingest_duplicates_idempotent(client):
    await _seed_run("01HRUN2")

    ev = {
        "subprocess_seq": 1,
        "ts": "2026-04-18T00:00:01.000Z",
        "kind": "x",
        "payload": {},
    }
    body = {"run_id": "01HRUN2", "events": [ev]}

    r1 = await client.post(
        "/_ingest/events",
        headers={"X-Internal-Secret": "test-secret"},
        json=body,
    )
    assert r1.json() == {"accepted": 1, "duplicates": 0}

    r2 = await client.post(
        "/_ingest/events",
        headers={"X-Internal-Secret": "test-secret"},
        json=body,
    )
    assert r2.json() == {"accepted": 0, "duplicates": 1}


@pytest.mark.asyncio
async def test_ingest_events_publishes_to_pubsub(client):
    """Events written to DB must also fan out on the run's pubsub topic."""
    await _seed_run("01HRUNPUB")

    # Subscribe before the publish so the queued message is captured.
    # app.state is reachable through the ASGI transport.
    app = client._transport.app  # type: ignore[attr-defined]
    q = app.state.pubsub.subscribe("01HRUNPUB")

    r = await client.post(
        "/_ingest/events",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "run_id": "01HRUNPUB",
            "events": [
                {
                    "subprocess_seq": 10,
                    "ts": "2026-04-18T00:00:10.000Z",
                    "kind": "tick",
                    "payload": {"v": 1},
                }
            ],
        },
    )
    assert r.status_code == 200
    msg = q.get_nowait()
    assert msg["kind"] == "tick"
    assert msg["payload"] == {"v": 1}
    assert msg["ts"] == "2026-04-18T00:00:10.000Z"
    assert "id" in msg


@pytest.mark.asyncio
async def test_ingest_decision_record(client):
    await _seed_run("01HRUNDR")
    body = {
        "run_id": "01HRUNDR",
        "decision": {
            "id": "01HDEC1",
            "seq": 12,
            "trading_day": "2025-03-14",
            "decision_ts": "2025-03-14T14:30:00-04:00",
            "model_id": "gpt-4",
            "provider": "openai",
            "prompt_sha256": "a" * 64,
            "response_sha256": "b" * 64,
            "storage_dir": "decisions/01HDEC1/",
            "has_trade": True,
            "cum_pnl_after": -3400.0,
        },
    }
    r = await client.post(
        "/_ingest/decision-record",
        headers={"X-Internal-Secret": "test-secret"},
        json=body,
    )
    assert r.status_code == 200
    assert r.json().get("duplicate") in (False, None) or r.json() == {"ok": True}

    # Duplicate post (same (run_id, seq)) must respond 200 duplicate=True
    r2 = await client.post(
        "/_ingest/decision-record",
        headers={"X-Internal-Secret": "test-secret"},
        json=body,
    )
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True


@pytest.mark.asyncio
async def test_ingest_question_chunk(client):
    await _seed_checkpoint("01HRUNQ", "01HDECQ", "01HQ")

    app = client._transport.app  # type: ignore[attr-defined]
    q = app.state.pubsub.subscribe("question:01HQ")

    r = await client.post(
        "/_ingest/question-chunk",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "question_id": "01HQ",
            "seq": 15,
            "delta_text": "because VIX spiked",
            "ts": "2026-04-18T00:00:15.000Z",
        },
    )
    assert r.status_code == 200

    msg = q.get_nowait()
    assert msg["seq"] == 15
    assert msg["delta_text"] == "because VIX spiked"
