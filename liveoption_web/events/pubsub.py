"""In-memory asyncio pubsub with per-subscriber bounded queues.

Designed for single-process SSE fanout: each subscriber gets its own
``asyncio.Queue`` with a bounded buffer. ``publish()`` is non-blocking —
if a subscriber's queue is full, that subscriber's message is dropped
and a warning is logged, so a slow consumer cannot stall publishers or
other subscribers.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_BUFFER = 1000


class Pubsub:
    """Topic-based in-memory pubsub for asyncio.

    Not safe across processes — intended for a single FastAPI worker.
    """

    def __init__(self, max_buffer: int = DEFAULT_MAX_BUFFER) -> None:
        if max_buffer <= 0:
            raise ValueError("max_buffer must be positive")
        self._max_buffer = max_buffer
        self._subscribers: dict[str, list[asyncio.Queue[Any]]] = defaultdict(list)

    def subscribe(self, topic: str) -> asyncio.Queue[Any]:
        """Register a new subscriber for ``topic`` and return its queue."""
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._max_buffer)
        self._subscribers[topic].append(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue[Any]) -> None:
        """Remove ``q`` from ``topic``'s subscriber list; no-op if absent."""
        subs = self._subscribers.get(topic)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            return
        if not subs:
            # Clean up empty topic entry.
            self._subscribers.pop(topic, None)

    async def publish(self, topic: str, payload: Any) -> None:
        """Fan ``payload`` out to every subscriber of ``topic``.

        Non-blocking per subscriber: uses ``put_nowait`` and drops the
        message (with a warning log) for any subscriber whose queue is full.
        """
        subs = self._subscribers.get(topic)
        if not subs:
            return
        # Iterate over a snapshot so unsubscribe during publish is safe.
        for q in list(subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning(
                    "pubsub: dropping message on topic %r for slow subscriber "
                    "(queue full at %d)",
                    topic,
                    self._max_buffer,
                )
