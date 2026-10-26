"""Tests for the internal-secret auth dependency (Task 4)."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_missing_secret_returns_403(monkeypatch):
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "shh")
    from liveoption_web.api.deps import verify_internal

    app = FastAPI()

    @app.get("/x")
    async def x(actor=Depends(verify_internal)):
        return {"user": actor.user_id}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/x")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_wrong_secret_returns_403(monkeypatch):
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "shh")
    from liveoption_web.api.deps import verify_internal

    app = FastAPI()

    @app.get("/x")
    async def x(actor=Depends(verify_internal)):
        return {}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/x", headers={"X-Internal-Secret": "wrong"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_correct_secret_passes_actor(monkeypatch):
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "shh")
    from liveoption_web.api.deps import verify_internal

    app = FastAPI()

    @app.get("/x")
    async def x(actor=Depends(verify_internal)):
        return {"user": actor.user_id, "email": actor.email}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get(
            "/x",
            headers={
                "X-Internal-Secret": "shh",
                "X-Actor-User": "user_abc",
                "X-Actor-Email": "a@b.com",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"user": "user_abc", "email": "a@b.com"}
