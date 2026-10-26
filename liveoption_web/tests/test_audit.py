"""Tests for /v1/audit endpoints (Task 14)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from liveoption_web.db.connection import open_db


async def _seed_audit(db_path: Path, rows: list[tuple]) -> None:
    """Insert rows into audit_log.

    Each row: (ts, actor_user_id, actor_email, action, target_type,
    target_id, payload_json).
    """
    async with open_db(db_path) as db:
        await db.executemany(
            """INSERT INTO audit_log(ts, actor_user_id, actor_email, action,
                                     target_type, target_id, payload_json)
               VALUES(?,?,?,?,?,?,?)""",
            rows,
        )
        await db.commit()


@pytest.mark.asyncio
async def test_audit_requires_internal_secret(client):
    r = await client.get("/v1/audit/targets/checkpoint/ckpt_x")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_audit_returns_matching_rows_newest_first(client):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    await _seed_audit(
        db_path,
        [
            (
                "2026-04-18T10:00:00Z",
                "alice",
                "alice@example.com",
                "checkpoint.promote",
                "checkpoint",
                "ckpt_1",
                '{"decision_id": "d1"}',
            ),
            (
                "2026-04-18T11:00:00Z",
                "bob",
                "bob@example.com",
                "checkpoint.label",
                "checkpoint",
                "ckpt_1",
                '{"label": "baseline"}',
            ),
            (
                "2026-04-18T12:00:00Z",
                "carol",
                None,
                "checkpoint.promote",
                "checkpoint",
                "ckpt_other",
                None,
            ),
        ],
    )

    r = await client.get(
        "/v1/audit/targets/checkpoint/ckpt_1",
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    items = body["items"]
    assert len(items) == 2
    # Newest first: ts=11:00 before ts=10:00.
    assert items[0]["action"] == "checkpoint.label"
    assert items[0]["actor_user_id"] == "bob"
    assert items[0]["actor_email"] == "bob@example.com"
    assert items[0]["target_type"] == "checkpoint"
    assert items[0]["target_id"] == "ckpt_1"
    assert items[0]["payload"] == {"label": "baseline"}
    assert items[1]["action"] == "checkpoint.promote"
    assert items[1]["payload"] == {"decision_id": "d1"}


@pytest.mark.asyncio
async def test_audit_unknown_target_returns_empty(client):
    r = await client.get(
        "/v1/audit/targets/run/missing",
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.asyncio
async def test_audit_null_payload_serialized_as_none(client):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    await _seed_audit(
        db_path,
        [
            (
                "2026-04-18T09:00:00Z",
                None,
                None,
                "run.fork",
                "run",
                "run_z",
                None,
            ),
        ],
    )
    r = await client.get(
        "/v1/audit/targets/run/run_z",
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["payload"] is None
    assert items[0]["actor_user_id"] is None
    assert items[0]["actor_email"] is None
