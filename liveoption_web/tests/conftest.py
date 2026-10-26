"""Shared pytest fixtures for liveoption_web tests."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """Async HTTP client bound to a fresh app with tmp DB + runs dir."""
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "test-secret")
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))

    from liveoption_web.main import create_app

    app = await create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
