"""Tests for /healthz endpoint (Task 5)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_ok(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "reachable"
    from liveoption_web.db.migrations import MIGRATIONS_DIR

    expected_version = max(
        int(p.name.split("_", 1)[0])
        for p in MIGRATIONS_DIR.glob("*.sql")
        if p.name.split("_", 1)[0].isdigit()
    )
    assert body["version"] == expected_version


@pytest.mark.asyncio
async def test_healthz_no_auth_needed(client):
    # No X-Internal-Secret header — /healthz is public.
    r = await client.get("/healthz")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_app_has_pubsub_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "test-secret")
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))

    from liveoption_web.events.pubsub import Pubsub
    from liveoption_web.main import create_app

    app = await create_app()
    assert isinstance(app.state.pubsub, Pubsub)
