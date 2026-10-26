"""Tests for in-memory asyncio pubsub."""

from __future__ import annotations

import asyncio
import logging

import pytest

from liveoption_web.events.pubsub import Pubsub


@pytest.mark.asyncio
async def test_fanout_delivers_to_all_subscribers() -> None:
    """publish() delivers the payload to every subscriber of the topic."""
    bus = Pubsub()
    q1 = bus.subscribe("jobs")
    q2 = bus.subscribe("jobs")
    q_other = bus.subscribe("other")

    await bus.publish("jobs", {"event": "started", "job_id": "abc"})

    msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert msg1 == {"event": "started", "job_id": "abc"}
    assert msg2 == {"event": "started", "job_id": "abc"}
    # Other-topic subscriber should not receive anything.
    assert q_other.empty()


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_block_publish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When a subscriber's queue is full, publish drops the message and logs a warning;
    other subscribers and subsequent publishes remain unaffected."""
    bus = Pubsub(max_buffer=2)
    slow = bus.subscribe("jobs")
    fast = bus.subscribe("jobs")

    # Fill the slow subscriber's queue to capacity without draining.
    await bus.publish("jobs", {"n": 1})
    await bus.publish("jobs", {"n": 2})

    # Drain fast subscriber so it stays healthy.
    assert (await asyncio.wait_for(fast.get(), timeout=1.0)) == {"n": 1}
    assert (await asyncio.wait_for(fast.get(), timeout=1.0)) == {"n": 2}

    with caplog.at_level(logging.WARNING, logger="liveoption_web.events.pubsub"):
        # This publish must NOT block — slow queue is full, so drop + warn.
        await asyncio.wait_for(bus.publish("jobs", {"n": 3}), timeout=0.5)

    # Fast subscriber should still receive the third message.
    assert (await asyncio.wait_for(fast.get(), timeout=1.0)) == {"n": 3}
    # Slow subscriber's queue remains at capacity (did not grow).
    assert slow.qsize() == 2
    # A warning was logged about the drop.
    assert any("drop" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = Pubsub()
    q = bus.subscribe("jobs")
    bus.unsubscribe("jobs", q)

    await bus.publish("jobs", {"x": 1})
    assert q.empty()


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_noop() -> None:
    bus = Pubsub()
    # Should not raise.
    await bus.publish("ghost", {"hello": "world"})
