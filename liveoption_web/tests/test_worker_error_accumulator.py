"""Unit tests for ``trading.ingest.worker_error.WorkerErrorAccumulator``.

Covers the four flush triggers from spec §2:

1. First occurrence in a bucket flushes immediately.
2. Counts crossing 10× thresholds (1, 10, 100, …) flush.
3. The 5s timer flushes a bucket even without a threshold cross.
4. ``flush_all()`` emits final counts on terminate.
"""

from __future__ import annotations

import pytest

from trading.ingest.worker_error import WorkerErrorAccumulator


class _FakeClock:
    """Deterministic monotonic clock + ISO ts source for tests."""

    def __init__(self) -> None:
        self.mono = 0.0
        self.iso_seq = 0

    def advance(self, seconds: float) -> None:
        self.mono += seconds

    def monotonic(self) -> float:
        return self.mono

    def iso(self) -> str:
        # Stable, monotonically increasing ISO-Z stamp.  The accumulator
        # does not parse it, so any RFC3339-Z string works.
        self.iso_seq += 1
        return f"2026-04-27T00:00:{self.iso_seq:02d}Z"


def _make(emitted: list[dict], clock: _FakeClock) -> WorkerErrorAccumulator:
    return WorkerErrorAccumulator(
        run_id="01HRUN",
        emit_fn=emitted.append,
        now_monotonic=clock.monotonic,
        now_iso=clock.iso,
    )


def test_first_error_flushes_immediately():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    acc.record(category="auth", message="Qwen 401")

    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["category"] == "auth"
    assert ev["count"] == 1
    assert ev["severity"] == "error"
    assert ev["message"] == "Qwen 401"
    assert ev["first_ts"] == ev["last_ts"]


def test_ten_errors_flush_twice():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    for _ in range(10):
        acc.record(category="auth", message="Qwen 401")

    counts = [ev["count"] for ev in emitted]
    assert counts == [1, 10]


def test_one_hundred_errors_flush_three_times():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    for _ in range(100):
        acc.record(category="auth", message="Qwen 401")

    counts = [ev["count"] for ev in emitted]
    assert counts == [1, 10, 100]


def test_5s_timer_flushes_without_threshold_cross():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    # First call → flush #1 at count=1.
    acc.record(category="auth", message="Qwen 401")
    assert [ev["count"] for ev in emitted] == [1]

    # Two more events within 5s — no threshold cross, no timer fire.
    clock.advance(2.0)
    acc.record(category="auth", message="Qwen 401")
    clock.advance(1.0)
    acc.record(category="auth", message="Qwen 401")
    assert [ev["count"] for ev in emitted] == [1]

    # Cross the 5s mark from the last flush, then record one more.
    clock.advance(2.5)  # total since flush #1: 5.5s
    acc.record(category="auth", message="Qwen 401")

    counts = [ev["count"] for ev in emitted]
    # Second flush is from the timer trigger; bucket count is 4 by now.
    assert counts == [1, 4]


def test_flush_all_includes_final_counts():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    # 3 errors in auth (only first flushes via the immediate trigger),
    # then 1 error in network (which also flushes immediately).
    for _ in range(3):
        acc.record(category="auth", message="Qwen 401")
    acc.record(category="network", message="connection reset")

    # Only count=1 events emitted so far.
    pre_terminal = [(ev["category"], ev["count"]) for ev in emitted]
    assert pre_terminal == [("auth", 1), ("network", 1)]

    # Terminal flush picks up the un-flushed remainder of the auth
    # bucket (count is now 3) but skips the network bucket because
    # nothing accumulated since its first flush.
    acc.flush_all()
    post_terminal = [
        (ev["category"], ev["count"]) for ev in emitted[len(pre_terminal):]
    ]
    assert post_terminal == [("auth", 3)]


def test_flush_all_is_idempotent_when_nothing_pending():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    acc.record(category="auth", message="Qwen 401")
    assert len(emitted) == 1
    acc.flush_all()
    acc.flush_all()
    # Single flush from the immediate trigger; no extras.
    assert len(emitted) == 1


def test_message_truncated_to_200_chars():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    long = "x" * 500
    acc.record(category="internal", message=long)
    assert len(emitted[0]["message"]) == 200


def test_context_propagated_when_present():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    ctx = {"provider": "qwen", "decision_seq": 42}
    acc.record(category="auth", message="Qwen 401", context=ctx)
    assert emitted[0]["context"] == ctx


def test_distinct_categories_have_independent_buckets():
    emitted: list[dict] = []
    clock = _FakeClock()
    acc = _make(emitted, clock)

    acc.record(category="auth", message="401")
    acc.record(category="rate_limit", message="429")
    # Both first occurrences flush immediately.
    assert [(ev["category"], ev["count"]) for ev in emitted] == [
        ("auth", 1),
        ("rate_limit", 1),
    ]
