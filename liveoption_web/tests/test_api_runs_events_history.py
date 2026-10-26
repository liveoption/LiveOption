"""Tests for ``GET /v1/runs/{run_id}/events/history`` (REST history endpoint)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from liveoption_web.db.connection import open_db

HDRS = {"X-Internal-Secret": "test-secret"}


async def _insert_run(db_path, run_id: str, status: str = "completed") -> None:
    async with open_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO runs (
                id, kind, scenario, config_path, model_id, provider,
                date_start, date_end, status, status_updated_at,
                created_at, storage_dir
            ) VALUES (
                ?, 'batch', 'weekly_llm', 'x.yaml', 'gpt-5', 'openai',
                '2026-01-01', '2026-01-05', ?,
                '2026-04-18T00:00:00.000Z', '2026-04-18T00:00:00.000Z',
                '/tmp/run'
            )
            """,
            (run_id, status),
        )
        await db.commit()


async def _insert_events(db_path, run_id: str, count: int) -> None:
    async with open_db(db_path) as db:
        for i in range(1, count + 1):
            await db.execute(
                """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
                   VALUES(?, ?, ?, ?, ?)""",
                (
                    run_id,
                    i,
                    f"2026-04-20T00:00:{i:02d}.000Z",
                    "day.completed" if i % 2 == 0 else "decision.started",
                    json.dumps({"seq": i}),
                ),
            )
        await db.commit()


@pytest.mark.asyncio
async def test_returns_empty_list_for_run_with_no_events(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r1")

    resp = await client.get("/v1/runs/r1/events/history", headers=HDRS)

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"events": [], "total": 0, "truncated": False, "next_cursor": None}


@pytest.mark.asyncio
async def test_returns_404_when_run_missing(client):
    resp = await client.get("/v1/runs/does-not-exist/events/history", headers=HDRS)

    assert resp.status_code == 404
    body = resp.json()
    assert body["type"] == "run-not-found"
    assert body["status"] == 404


@pytest.mark.asyncio
async def test_returns_events_newest_first(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r2")
    await _insert_events(db, "r2", 5)

    resp = await client.get("/v1/runs/r2/events/history", headers=HDRS)

    body = resp.json()
    assert [e["payload"]["seq"] for e in body["events"]] == [5, 4, 3, 2, 1]
    assert body["total"] == 5
    assert body["truncated"] is False
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_payload_json_parsed_to_dict(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r3")
    await _insert_events(db, "r3", 1)

    resp = await client.get("/v1/runs/r3/events/history", headers=HDRS)

    body = resp.json()
    assert body["events"][0]["payload"] == {"seq": 1}
    assert body["events"][0]["kind"] == "decision.started"
    assert body["events"][0]["occurred_at"].endswith("Z")


@pytest.mark.asyncio
async def test_other_runs_events_not_included(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r4")
    await _insert_run(db, "r5")
    await _insert_events(db, "r4", 3)
    await _insert_events(db, "r5", 7)

    resp = await client.get("/v1/runs/r4/events/history", headers=HDRS)

    body = resp.json()
    assert body["total"] == 3
    assert len(body["events"]) == 3


@pytest.mark.asyncio
async def test_malformed_payload_does_not_crash_page(client):
    """A single corrupt payload_json must not 500 the whole history page.

    Insert one good event and one with literally invalid JSON, hit the
    endpoint, and confirm both rows return — the bad one with payload {}.
    """
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r_corrupt")
    async with open_db(db) as conn:
        await conn.execute(
            """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
               VALUES(?, 1, '2026-04-20T00:00:01.000Z', 'k1', ?)""",
            ("r_corrupt", json.dumps({"seq": 1})),
        )
        await conn.execute(
            """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
               VALUES(?, 2, '2026-04-20T00:00:02.000Z', 'k2', ?)""",
            ("r_corrupt", "{not valid json"),
        )
        await conn.commit()

    resp = await client.get("/v1/runs/r_corrupt/events/history", headers=HDRS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["events"]) == 2
    # Newest-first: the corrupt row (id=2) is first; should have payload {}.
    assert body["events"][0]["kind"] == "k2"
    assert body["events"][0]["payload"] == {}
    assert body["events"][1]["kind"] == "k1"
    assert body["events"][1]["payload"] == {"seq": 1}


@pytest.mark.asyncio
async def test_truncated_when_total_exceeds_limit(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r6")
    await _insert_events(db, "r6", 10)

    resp = await client.get("/v1/runs/r6/events/history?limit=4", headers=HDRS)

    body = resp.json()
    assert len(body["events"]) == 4
    assert [e["payload"]["seq"] for e in body["events"]] == [10, 9, 8, 7]
    assert body["total"] == 10
    assert body["truncated"] is True
    # Cursor equals the min event id (last event in page, since ordered DESC)
    assert body["next_cursor"] == body["events"][-1]["id"]


@pytest.mark.asyncio
async def test_cursor_returns_older_events_no_overlap(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r7")
    await _insert_events(db, "r7", 10)

    page1 = (await client.get("/v1/runs/r7/events/history?limit=4", headers=HDRS)).json()
    cursor = page1["next_cursor"]
    page2 = (await client.get(
        f"/v1/runs/r7/events/history?limit=4&cursor={cursor}", headers=HDRS
    )).json()

    seq1 = [e["payload"]["seq"] for e in page1["events"]]
    seq2 = [e["payload"]["seq"] for e in page2["events"]]
    assert seq1 == [10, 9, 8, 7]
    assert seq2 == [6, 5, 4, 3]
    assert set(seq1).isdisjoint(seq2)


@pytest.mark.asyncio
async def test_next_cursor_null_at_tail(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r8")
    await _insert_events(db, "r8", 6)

    page1 = (await client.get("/v1/runs/r8/events/history?limit=4", headers=HDRS)).json()
    page2 = (await client.get(
        f"/v1/runs/r8/events/history?limit=4&cursor={page1['next_cursor']}",
        headers=HDRS,
    )).json()

    assert page2["truncated"] is False
    assert page2["next_cursor"] is None
    assert len(page2["events"]) == 2


@pytest.mark.asyncio
async def test_limit_clamped_to_max_2000(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r9")
    await _insert_events(db, "r9", 5)

    resp = await client.get("/v1/runs/r9/events/history?limit=99999", headers=HDRS)

    assert resp.status_code == 200
    assert len(resp.json()["events"]) == 5  # all 5 returned, no error


@pytest.mark.asyncio
async def test_limit_clamped_to_min_1(client):
    db = client._transport.app.state.settings.db_path
    await _insert_run(db, "r10")
    await _insert_events(db, "r10", 3)

    resp = await client.get("/v1/runs/r10/events/history?limit=0", headers=HDRS)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 1
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_auth_required(client):
    resp = await client.get("/v1/runs/anything/events/history")  # no header
    assert resp.status_code in (401, 403)
