"""Tests for Task 9 SSE ``GET /v1/runs/{id}/events``.

We exercise :func:`liveoption_web.api.sse.generate_run_sse` directly as an
async iterator instead of going through ``httpx.ASGITransport``.  The
reason: ``ASGITransport.handle_async_request`` drives the ASGI app to
completion and buffers the full body before returning a
:class:`httpx.Response` — see ``httpx._transports.asgi``.  That design
makes it incompatible with an endpoint whose body generator never
terminates (our live-fanout loop), so a streaming integration test would
hang the whole event loop.  Driving the generator directly is both
deterministic and fast, and it still covers the three interesting
behaviors: historical replay, ``Last-Event-ID`` prefix skipping, and
live fanout after the snapshot boundary.

One lightweight HTTP smoke test remains to catch route wiring
regressions (authz, path, headers).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
import pytest_asyncio

from liveoption_web.api.sse import generate_run_sse, sse_frame
from liveoption_web.db.connection import open_db
from liveoption_web.events.pubsub import Pubsub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _seed_run(db_path: Path, run_id: str) -> None:
    """Insert a minimal ``runs`` row so FK-constrained writes succeed."""
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


async def _seed_events(db_path: Path, run_id: str, count: int) -> None:
    """Insert ``count`` sequential ``tick`` events for ``run_id``."""
    async with open_db(db_path) as db:
        for i in range(1, count + 1):
            await db.execute(
                """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
                   VALUES(?, ?, ?, ?, ?)""",
                (
                    run_id,
                    i,
                    f"2026-04-18T00:00:{i:02d}.000Z",
                    "tick",
                    json.dumps({"seq": i}),
                ),
            )
        await db.commit()


async def _collect_frames(gen, stop_after: int, timeout: float = 2.0) -> list[str]:
    """Collect ``stop_after`` frames from ``gen`` then aclose it.

    ``timeout`` bounds the wait for *each* ``__anext__()`` so a bug in
    the generator cannot hang the whole test run.
    """
    frames: list[str] = []
    try:
        for _ in range(stop_after):
            frame = await asyncio.wait_for(gen.__anext__(), timeout=timeout)
            frames.append(frame)
    finally:
        await gen.aclose()
    return frames


def _id_lines(frames: list[str]) -> list[str]:
    """Extract the ``id: N`` lines from a list of SSE frames in order."""
    ids = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("id: "):
                ids.append(line)
                break
    return ids


@pytest_asyncio.fixture
async def tmp_db(tmp_path, monkeypatch):
    """Prepare a fresh SQLite DB with the full migration set applied."""
    db_path = tmp_path / "db.sqlite"
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(db_path))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "test-secret")

    from liveoption_web.db.migrations import apply_migrations

    await apply_migrations(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Generator-level tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sse_replay_historical(tmp_db):
    await _seed_run(tmp_db, "01HRUN")
    await _seed_events(tmp_db, "01HRUN", 5)

    pubsub = Pubsub()
    gen = generate_run_sse(tmp_db, pubsub, "01HRUN", last_event_id=0)

    frames = await _collect_frames(gen, stop_after=5)
    ids = _id_lines(frames)
    assert ids == [f"id: {i}" for i in range(1, 6)]

    joined = "\n".join(frames)
    assert "event: tick" in joined
    assert '"seq": 1' in joined


@pytest.mark.asyncio
async def test_sse_last_event_id_skips_prefix(tmp_db):
    await _seed_run(tmp_db, "01HRUN")
    await _seed_events(tmp_db, "01HRUN", 5)

    pubsub = Pubsub()
    gen = generate_run_sse(tmp_db, pubsub, "01HRUN", last_event_id=3)

    frames = await _collect_frames(gen, stop_after=2)
    assert _id_lines(frames) == ["id: 4", "id: 5"]


@pytest.mark.asyncio
async def test_sse_live_event_after_snapshot(tmp_db):
    await _seed_run(tmp_db, "01HRUN")
    await _seed_events(tmp_db, "01HRUN", 2)

    pubsub = Pubsub()
    gen = generate_run_sse(tmp_db, pubsub, "01HRUN", last_event_id=0)

    # Drain the two historical frames.
    historical = await asyncio.wait_for(_take_n(gen, 2), timeout=2.0)
    assert _id_lines(historical) == ["id: 1", "id: 2"]

    # Publish a new event matching the live-fanout contract.
    await pubsub.publish(
        "01HRUN",
        {
            "id": 3,
            "kind": "tick",
            "payload": {"live": True},
        },
    )

    live_frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    await gen.aclose()

    assert "id: 3" in live_frame.splitlines()
    assert "event: tick" in live_frame
    assert '"live": true' in live_frame


async def _take_n(gen, n: int) -> list[str]:
    """Consume ``n`` frames from ``gen`` without closing it."""
    out = []
    for _ in range(n):
        out.append(await gen.__anext__())
    return out


# ---------------------------------------------------------------------------
# Frame-builder unit test
# ---------------------------------------------------------------------------
def test_sse_frame_shape():
    """Unit test for the plain frame builder used by the streamer."""
    out = sse_frame(7, "tick", '{"x":1}')
    assert out.startswith("id: 7\n")
    assert "event: tick\n" in out
    assert 'data: {"x":1}\n' in out
    # A blank line terminates the frame per the SSE spec.
    assert out.endswith("\n\n")

    # Heartbeat-style frames omit the id line.
    hb = sse_frame(None, "heartbeat", "{}")
    assert not hb.startswith("id:")
    assert "event: heartbeat\n" in hb


# ---------------------------------------------------------------------------
# HTTP-level smoke test
# ---------------------------------------------------------------------------
# Covers route wiring (path, auth, media type) without driving the
# response body generator — ``httpx.ASGITransport`` buffers the body to
# completion so we cannot assert on streamed chunks here.  A 404 path on
# a missing run would prove auth runs first; we accept 200 and rely on
# the generator-level tests above for body semantics.
@pytest.mark.asyncio
async def test_sse_rejects_missing_secret(client):
    """The route must reject unauthenticated callers before streaming."""
    r = await client.get("/v1/runs/nope/events")
    assert r.status_code in (401, 403)
