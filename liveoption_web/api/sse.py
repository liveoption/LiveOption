"""Server-Sent Events helpers for the Job Server.

The HTTP route (``liveoption_web.api.runs.stream_events``) delegates the
streaming body to :func:`generate_run_sse` here.  Keeping the builder +
generator in a dedicated module serves two goals:

* It makes the frame format unit-testable without an HTTP round trip.
* It keeps ``runs.py`` small so Task 15's ``/compare`` route can append
  cleanly without merge churn.

Streaming protocol
------------------
Each frame is a standard SSE record::

    id: <db event id>\n
    event: <kind>\n
    data: <payload json>\n
    \n

A heartbeat frame is emitted every ~15 seconds of silence; it has no
``id`` field so a resuming client never treats it as a checkpoint.

Resume semantics
----------------
The ``Last-Event-ID`` header (auto-sent by browsers using
``EventSource``) lets a client continue from where a previous stream
disconnected.  The generator:

1. **Subscribes first** so any event published while we are reading the
   historical snapshot is captured in the subscriber queue.
2. **Snapshots the DB** to determine the highest event id currently
   persisted for the run (``floor_id``).
3. **Replays** rows whose id is in ``(last_event_id, floor_id]`` in
   ascending order.
4. **Switches to live** fanout, skipping any duplicates with id
   ``<= floor_id`` that were already part of the replay.

The subscribe-before-snapshot ordering is deliberate: it closes the
race where an event is published between the snapshot and the
subscribe call.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

from fastapi import Request

from liveoption_web.db.connection import open_db
from liveoption_web.events.pubsub import Pubsub

#: Seconds of pubsub silence before a heartbeat frame is sent.
HEARTBEAT_INTERVAL_SECS = 15.0


def sse_frame(event_id: int | None, kind: str, payload_json: str) -> str:
    """Format one SSE record.

    ``payload_json`` must already be a JSON-encoded string — callers are
    responsible for serialization so they can reuse the exact bytes they
    persisted in ``events.payload_json`` without a redundant round trip
    through ``json.loads``.
    """
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {kind}")
    lines.append(f"data: {payload_json}")
    lines.append("")  # blank line terminates the frame
    return "\n".join(lines) + "\n"


async def generate_run_sse(
    db_path: Path,
    pubsub: Pubsub,
    run_id: str,
    last_event_id: int,
    request: Request | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for ``run_id`` — historical replay then live fanout.

    When ``request`` is provided, the live-fanout loop polls
    ``request.is_disconnected()`` between waits so the generator exits
    promptly after the client hangs up.  Without this poll, ASGI
    transports that deliver ``http.disconnect`` only on the request
    receive channel (e.g. httpx ``ASGITransport``) can deadlock on
    ``aclose`` because the generator stays parked inside
    ``queue.get()``.
    """
    # Subscribe *before* reading the DB snapshot so nothing published in
    # the interim is silently dropped.
    queue = pubsub.subscribe(run_id)
    try:
        async with open_db(Path(db_path)) as db:
            cur = await db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM events WHERE run_id=?",
                (run_id,),
            )
            row = await cur.fetchone()
            floor_id = row[0] if row else 0

            cur = await db.execute(
                """SELECT id, kind, payload_json
                   FROM events
                   WHERE run_id=? AND id>? AND id<=?
                   ORDER BY id""",
                (run_id, last_event_id, floor_id),
            )
            rows = await cur.fetchall()

        for event_id, kind, payload_json in rows:
            yield sse_frame(event_id, kind, payload_json)

        # Live fanout.  Events with id <= floor_id were already replayed
        # from the DB and must be skipped to avoid a duplicate at the
        # snapshot boundary.
        #
        # The inner ``wait_for`` uses a short poll interval so we can
        # notice a disconnected client without waiting a full heartbeat
        # period.  A heartbeat frame is only emitted once the cumulative
        # idle time crosses ``HEARTBEAT_INTERVAL_SECS``.
        poll_interval = 0.25
        idle = 0.0
        while True:
            if request is not None:
                try:
                    if await request.is_disconnected():
                        return
                except Exception:
                    # Defensive: if the ASGI receive channel is gone we
                    # treat that as a disconnect as well.
                    return
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=poll_interval)
            except asyncio.TimeoutError:
                idle += poll_interval
                if idle >= HEARTBEAT_INTERVAL_SECS:
                    yield sse_frame(None, "heartbeat", "{}")
                    idle = 0.0
                continue

            idle = 0.0
            if ev["id"] <= floor_id:
                continue
            yield sse_frame(ev["id"], ev["kind"], json.dumps(ev["payload"]))
    finally:
        pubsub.unsubscribe(run_id, queue)
