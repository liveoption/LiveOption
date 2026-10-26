"""Tests for /v1/checkpoints/:id/forks (Task 13).

The fork endpoint spawns a ``trading.cli resume-from-checkpoint``
subprocess.  Unit tests mock ``spawn_fork`` so no real process is
launched; the supervisor's own behaviour is exercised separately in
:mod:`test_runs_supervisor`.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from liveoption_web.config import get_settings
from liveoption_web.db.connection import open_db


HDR = {
    "X-Internal-Secret": "test-secret",
    "X-Actor-User": "alice",
    "X-Actor-Email": "alice@example.com",
}


async def _seed_ready_checkpoint(
    *,
    run_id: str = "01HRUN000000000000000000RUN",
    decision_id: str = "01HDEC000000000000000000DEC",
    ckpt_id: str = "01HCKP000000000000000000CKP",
    status: str = "ready",
) -> None:
    settings = get_settings()
    async with open_db(settings.db_path) as db:
        await db.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id, provider,
                                universe_json, date_start, date_end, status,
                                status_updated_at, created_at, storage_dir)
               VALUES (?, 'base', 'llm_v1', '/tmp/cfg.yaml', 'deepseek-v3',
                       'deepseek', '[\"SPY\"]', '2026-04-01', '2026-04-10',
                       'completed', '2026-04-19T00:00:00Z',
                       '2026-04-18T00:00:00Z', '/tmp/runs/parent')""",
            (run_id,),
        )
        await db.execute(
            """INSERT INTO decision_records(id, run_id, seq, trading_day, decision_ts,
                                            model_id, provider, prompt_sha256,
                                            response_sha256, storage_dir, has_trade,
                                            created_at)
               VALUES (?, ?, 1, '2026-04-02',
                       '2026-04-02T15:00:00-04:00', 'deepseek-v3', 'deepseek',
                       'abc', 'def', '/tmp/runs/r1/decisions/d1', 1,
                       '2026-04-18T00:00:00Z')""",
            (decision_id, run_id),
        )
        await db.execute(
            """INSERT INTO checkpoints(id, run_id, decision_record_id, storage_dir,
                                       fingerprint, promoted_at, promoted_by, status,
                                       status_updated_at)
               VALUES (?, ?, ?, '/tmp/runs/r1/checkpoints/c1', 'fp-1',
                       '2026-04-18T00:00:01Z', 'alice', ?, '2026-04-18T00:00:01Z')""",
            (ckpt_id, run_id, decision_id, status),
        )
        await db.commit()


@pytest.fixture
def mock_spawn(monkeypatch):
    mock = AsyncMock(return_value=12345)
    monkeypatch.setattr("liveoption_web.api.forks.spawn_fork", mock)
    return mock


@pytest.mark.asyncio
async def test_fork_requires_secret(client):
    r = await client.post(
        "/v1/checkpoints/01HCKP000000000000000000CKP/forks",
        json={"mode": "diverge", "model_override": "deepseek-v3"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_fork_rejects_non_diverge_mode(client, mock_spawn):
    await _seed_ready_checkpoint()
    r = await client.post(
        "/v1/checkpoints/01HCKP000000000000000000CKP/forks",
        headers=HDR,
        json={"mode": "replay", "model_override": "deepseek-v3"},
    )
    assert r.status_code == 422  # literal validation
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_fork_requires_override(client, mock_spawn):
    await _seed_ready_checkpoint()
    r = await client.post(
        "/v1/checkpoints/01HCKP000000000000000000CKP/forks",
        headers=HDR,
        json={"mode": "diverge"},
    )
    assert r.status_code == 422
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_fork_checkpoint_not_found(client, mock_spawn):
    r = await client.post(
        "/v1/checkpoints/nope/forks",
        headers=HDR,
        json={"mode": "diverge", "model_override": "deepseek-v3"},
    )
    assert r.status_code == 404
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_fork_checkpoint_not_ready(client, mock_spawn):
    await _seed_ready_checkpoint(status="pending")
    r = await client.post(
        "/v1/checkpoints/01HCKP000000000000000000CKP/forks",
        headers=HDR,
        json={"mode": "diverge", "model_override": "deepseek-v3"},
    )
    assert r.status_code == 400
    mock_spawn.assert_not_called()


@pytest.mark.asyncio
async def test_fork_happy_path(client, mock_spawn):
    await _seed_ready_checkpoint()
    r = await client.post(
        "/v1/checkpoints/01HCKP000000000000000000CKP/forks",
        headers=HDR,
        json={
            "mode": "diverge",
            "model_override": "deepseek-v3",
            "title": "test fork",
        },
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["run_id"]
    assert body["events_url"] == f"/v1/runs/{body['run_id']}/events"
    mock_spawn.assert_awaited_once()

    # Run row inserted with kind='fork' + parent linkage + fork fields.
    settings = get_settings()
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT kind, parent_run_id, parent_checkpoint_id, status, "
            "fork_mode, fork_prompt_override, fork_model_override, "
            "created_by, title, model_id FROM runs WHERE id=?",
            (body["run_id"],),
        )
        row = await cur.fetchone()
    assert row is not None
    (kind, parent_run, parent_ck, status, fmode, fprompt, fmodel,
     created_by, title, model_id) = row
    assert kind == "fork"
    assert parent_run == "01HRUN000000000000000000RUN"
    assert parent_ck == "01HCKP000000000000000000CKP"
    assert status == "pending"
    assert fmode == "diverge"
    assert fprompt is None
    assert fmodel == "deepseek-v3"
    assert created_by == "alice"
    assert title == "test fork"
    assert model_id == "deepseek-v3"  # override applied


@pytest.mark.asyncio
async def test_fork_writes_audit_log(client, mock_spawn):
    await _seed_ready_checkpoint()
    r = await client.post(
        "/v1/checkpoints/01HCKP000000000000000000CKP/forks",
        headers=HDR,
        json={
            "mode": "diverge",
            "prompt_override": "Be extra cautious.",
            "model_override": None,
        },
    )
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    settings = get_settings()
    async with open_db(settings.db_path) as db:
        cur = await db.execute(
            "SELECT actor_user_id, action, target_type, target_id, payload_json "
            "FROM audit_log WHERE target_id=? AND action='fork.create'",
            (run_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "alice"
    payload = json.loads(row[4])
    assert payload["checkpoint_id"] == "01HCKP000000000000000000CKP"
    assert payload["mode"] == "diverge"
    assert payload["prompt_override"] is True
    assert payload["model_override"] is None


@pytest.mark.asyncio
async def test_fork_rate_limit(client, mock_spawn, monkeypatch):
    await _seed_ready_checkpoint()
    # Insert 4 running runs to exhaust the default shared pool quota
    # (max_concurrent_runs = 4).
    settings = get_settings()
    async with open_db(settings.db_path) as db:
        for i in range(4):
            await db.execute(
                """INSERT INTO runs(id, kind, parent_run_id, parent_checkpoint_id,
                                    scenario, config_path, model_id, provider,
                                    date_start, date_end, status, status_updated_at,
                                    created_at, created_by, storage_dir)
                   VALUES (?, 'fork', '01HRUN000000000000000000RUN',
                           '01HCKP000000000000000000CKP', 'llm_v1', '/tmp/cfg.yaml',
                           'deepseek-v3', 'deepseek', '2026-04-01', '2026-04-10',
                           'running', '2026-04-19T00:00:00Z',
                           '2026-04-18T00:00:00Z', 'alice', ?)""",
                (f"01HFORK000000000000000000F{i}", f"/tmp/runs/fork{i}"),
            )
        await db.commit()

    r = await client.post(
        "/v1/checkpoints/01HCKP000000000000000000CKP/forks",
        headers=HDR,
        json={"mode": "diverge", "model_override": "deepseek-v3"},
    )
    assert r.status_code == 409
    mock_spawn.assert_not_called()
