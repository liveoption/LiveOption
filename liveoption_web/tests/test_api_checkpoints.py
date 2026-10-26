"""Tests for /v1/checkpoints/* routes (Task 11).

Covers:
- GET /v1/checkpoints/:id            — detail
- GET /v1/runs/:id/checkpoints        — list by run
- GET /v1/checkpoints/:id/questions   — question list for a checkpoint

All three endpoints require the internal secret header and return
RFC 7807 problem responses on missing rows.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from liveoption_web.config import get_settings
from liveoption_web.db.connection import open_db


HDR = {"X-Internal-Secret": "test-secret"}


async def _seed(
    *,
    run_id: str = "01HRUN000000000000000000RUN",
    decision_id: str = "01HDEC000000000000000000DEC",
    ckpt_id: str = "01HCKP000000000000000000CKP",
    storage_dir: str = "/tmp/runs/r1/checkpoints/c1",
    status: str = "ready",
    portfolio_summary: dict | None = None,
    label: str | None = None,
) -> None:
    """Insert a run + decision_record + checkpoint for the given ids."""
    settings = get_settings()
    async with open_db(settings.db_path) as db:
        await db.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id, provider,
                                date_start, date_end, status, status_updated_at,
                                created_at, storage_dir)
               VALUES (?, 'base', 'llm_v1', '/tmp/cfg.yaml', 'deepseek-v3',
                       'deepseek', '2026-04-01', '2026-04-10', 'completed',
                       '2026-04-19T00:00:00Z', '2026-04-18T00:00:00Z', '/tmp/runs/r1')""",
            (run_id,),
        )
        await db.execute(
            """INSERT INTO decision_records(id, run_id, seq, trading_day, decision_ts,
                                            model_id, provider, prompt_sha256, response_sha256,
                                            storage_dir, has_trade, created_at)
               VALUES (?, ?, 1, '2026-04-02',
                       '2026-04-02T15:00:00-04:00', 'deepseek-v3', 'deepseek',
                       'abc', 'def', '/tmp/runs/r1/decisions/d1', 1,
                       '2026-04-18T00:00:00Z')""",
            (decision_id, run_id),
        )
        await db.execute(
            """INSERT INTO checkpoints(id, run_id, decision_record_id, storage_dir,
                                       fingerprint, portfolio_summary_json, promoted_at,
                                       promoted_by, status, status_updated_at, label)
               VALUES (?, ?, ?, ?, 'fp-1', ?, '2026-04-18T00:00:01Z',
                       'alice', ?, '2026-04-18T00:00:01Z', ?)""",
            (
                ckpt_id,
                run_id,
                decision_id,
                storage_dir,
                json.dumps(portfolio_summary) if portfolio_summary else None,
                status,
                label,
            ),
        )
        await db.commit()


async def _seed_question(
    *,
    qid: str,
    checkpoint_id: str,
    parent_question_id: str | None = None,
    status: str = "completed",
    response_text: str | None = "answer",
) -> None:
    settings = get_settings()
    async with open_db(settings.db_path) as db:
        await db.execute(
            """INSERT INTO questions(id, checkpoint_id, parent_question_id, asked_by,
                                     asked_at, user_prompt, model_id, provider,
                                     response_text, status, status_updated_at)
               VALUES (?, ?, ?, 'alice', '2026-04-18T01:00:00Z', 'why?',
                       'deepseek-v3', 'deepseek', ?, ?, '2026-04-18T01:00:05Z')""",
            (qid, checkpoint_id, parent_question_id, response_text, status),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_get_checkpoint_requires_secret(client):
    r = await client.get("/v1/checkpoints/anything")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_checkpoint_not_found(client):
    r = await client.get("/v1/checkpoints/does-not-exist", headers=HDR)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 404


@pytest.mark.asyncio
async def test_get_checkpoint_detail(client):
    await _seed(portfolio_summary={"nav": 100000.0, "positions": []}, label="post-trade")
    r = await client.get(
        "/v1/checkpoints/01HCKP000000000000000000CKP", headers=HDR
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "01HCKP000000000000000000CKP"
    assert body["run_id"] == "01HRUN000000000000000000RUN"
    assert body["decision_record_id"] == "01HDEC000000000000000000DEC"
    assert body["status"] == "ready"
    assert body["label"] == "post-trade"
    assert body["storage_dir"].endswith("/c1")
    assert body["portfolio_summary"] == {"nav": 100000.0, "positions": []}
    assert body["fingerprint"] == "fp-1"
    assert body["promoted_by"] == "alice"


@pytest.mark.asyncio
async def test_get_checkpoint_detail_null_summary(client):
    await _seed(portfolio_summary=None)
    r = await client.get(
        "/v1/checkpoints/01HCKP000000000000000000CKP", headers=HDR
    )
    assert r.status_code == 200
    assert r.json()["portfolio_summary"] is None


@pytest.mark.asyncio
async def test_list_checkpoints_by_run_empty(client):
    r = await client.get(
        "/v1/runs/01HRUN000000000000000000XXX/checkpoints", headers=HDR
    )
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.asyncio
async def test_list_checkpoints_by_run(client):
    await _seed(
        ckpt_id="01HCKP0000000000000000000A1",
        decision_id="01HDEC0000000000000000000D1",
    )
    # Second decision + checkpoint on same run.
    settings = get_settings()
    async with open_db(settings.db_path) as db:
        await db.execute(
            """INSERT INTO decision_records(id, run_id, seq, trading_day, decision_ts,
                                            model_id, provider, prompt_sha256, response_sha256,
                                            storage_dir, has_trade, created_at)
               VALUES ('01HDEC0000000000000000000D2',
                       '01HRUN000000000000000000RUN', 2, '2026-04-03',
                       '2026-04-03T15:00:00-04:00', 'deepseek-v3', 'deepseek',
                       'ghi', 'jkl', '/tmp/runs/r1/decisions/d2', 0,
                       '2026-04-18T00:10:00Z')""",
        )
        await db.execute(
            """INSERT INTO checkpoints(id, run_id, decision_record_id, storage_dir,
                                       fingerprint, promoted_at, promoted_by, status,
                                       status_updated_at)
               VALUES ('01HCKP0000000000000000000A2',
                       '01HRUN000000000000000000RUN',
                       '01HDEC0000000000000000000D2',
                       '/tmp/runs/r1/checkpoints/c2', 'fp-2',
                       '2026-04-18T00:10:01Z', 'bob', 'ready',
                       '2026-04-18T00:10:01Z')""",
        )
        await db.commit()

    r = await client.get(
        "/v1/runs/01HRUN000000000000000000RUN/checkpoints", headers=HDR
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    ids = [i["id"] for i in items]
    assert "01HCKP0000000000000000000A1" in ids
    assert "01HCKP0000000000000000000A2" in ids


@pytest.mark.asyncio
async def test_list_checkpoint_questions_empty(client):
    await _seed()
    r = await client.get(
        "/v1/checkpoints/01HCKP000000000000000000CKP/questions", headers=HDR
    )
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.asyncio
async def test_list_checkpoint_questions(client):
    await _seed()
    await _seed_question(qid="01HQST00000000000000000001Q", checkpoint_id="01HCKP000000000000000000CKP")
    await _seed_question(
        qid="01HQST00000000000000000002Q",
        checkpoint_id="01HCKP000000000000000000CKP",
        parent_question_id="01HQST00000000000000000001Q",
    )

    r = await client.get(
        "/v1/checkpoints/01HCKP000000000000000000CKP/questions", headers=HDR
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    q1 = next(q for q in items if q["id"] == "01HQST00000000000000000001Q")
    assert q1["parent_question_id"] is None
    assert q1["status"] == "completed"
    assert q1["user_prompt"] == "why?"
    q2 = next(q for q in items if q["id"] == "01HQST00000000000000000002Q")
    assert q2["parent_question_id"] == "01HQST00000000000000000001Q"


@pytest.mark.asyncio
async def test_list_questions_unknown_checkpoint_returns_404(client):
    r = await client.get(
        "/v1/checkpoints/does-not-exist/questions", headers=HDR
    )
    assert r.status_code == 404
