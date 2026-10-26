"""WorkerErrorAccumulator — dedup-on-emit error bucketing for worker subprocesses.

Trading workers emit ``worker.error`` events through the existing event
pipeline (`trading.ingest.event_emitter.EventEmitter`).  A persistent
fault — e.g. an LLM provider returning 401 on every decision call —
would otherwise emit one event per decision and drown the user in
duplicate toasts.

The accumulator buckets errors by ``(run_id, category)`` and only
flushes on:

* the **first** occurrence in a bucket (count flips 0→1),
* every **5 seconds** since the last flush for that bucket (live tail),
* every **10×** growth threshold (count crosses 1, 10, 100, 1000…),
* an explicit :meth:`flush_all` (called from the worker's terminate hook).

This bounds emission to O(log N) per category while still surfacing the
true count to the operator at run end.

The class is deliberately transport-agnostic: ``emit_fn`` receives the
fully-shaped payload dict and is expected to forward it through
whatever channel the worker uses.  In production that's the
``EventEmitter.emit("worker.error", payload)`` call; tests pass a list
collector.

"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from trading.core.time_contract import to_iso_z, utc_now

Severity = Literal["error", "warning", "info"]
Category = Literal[
    "auth",
    "rate_limit",
    "network",
    "parse",
    "artifact_write",
    "internal",
]

# Truncation cap from spec §1 ("≤ 200 chars").
_MAX_MESSAGE_LEN = 200

# Periodic-flush interval in seconds (spec §2).
_FLUSH_INTERVAL_SEC = 5.0


def _is_threshold(count: int) -> bool:
    """Return True iff *count* is a power of ten (1, 10, 100, …).

    The spec calls for flushes when count "crosses" 1, 10, 100…  Each
    integer count is observed exactly once on the way up, so checking
    equality against the next power-of-ten boundary is equivalent to
    "the count just crossed it".
    """

    if count <= 0:
        return False
    while count % 10 == 0:
        count //= 10
    return count == 1


@dataclass
class _ErrorBucket:
    count: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    last_message: Optional[str] = None
    last_context: Optional[dict] = None
    last_severity: Severity = "error"
    last_flush_monotonic: float = 0.0
    pending_since_flush: int = 0  # increments since the last flush

    def to_payload(self, *, run_id: str, category: Category) -> dict:
        # spec §1 payload schema; ``context`` omitted when None so the
        # event body stays minimal for the common 401-loop case.
        out: dict = {
            "severity": self.last_severity,
            "category": category,
            "message": self.last_message or "",
            "count": self.count,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }
        if self.last_context is not None:
            out["context"] = self.last_context
        return out


class WorkerErrorAccumulator:
    """Per-(run_id, category) bucket store with damped emission.

    Thread-safe: ``record`` and ``flush_all`` may be called from the
    decision loop and from a finalizer thread concurrently.  The
    accumulator does not own a flusher thread of its own; the periodic
    5s flush is checked lazily on each :meth:`record` call.  This keeps
    the lifecycle simple — no thread to join, no shutdown race.
    """

    def __init__(
        self,
        *,
        run_id: str,
        emit_fn: Callable[[dict], None],
        now_monotonic: Callable[[], float] | None = None,
        now_iso: Callable[[], str] | None = None,
        flush_interval_sec: float = _FLUSH_INTERVAL_SEC,
    ) -> None:
        self._run_id = run_id
        self._emit_fn = emit_fn
        self._now_monotonic = now_monotonic or _default_monotonic
        self._now_iso = now_iso or _default_iso
        self._flush_interval = flush_interval_sec
        self._buckets: dict[Category, _ErrorBucket] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        category: Category,
        message: str,
        severity: Severity = "error",
        context: dict | None = None,
    ) -> None:
        """Record one error occurrence; flush if a trigger fires.

        ``message`` is truncated to 200 characters per spec §1.
        """

        truncated = (message or "")[:_MAX_MESSAGE_LEN]
        ts = self._now_iso()
        now_mono = self._now_monotonic()

        with self._lock:
            bucket = self._buckets.get(category)
            if bucket is None:
                bucket = _ErrorBucket()
                self._buckets[category] = bucket
                bucket.first_ts = ts
            bucket.count += 1
            bucket.pending_since_flush += 1
            bucket.last_ts = ts
            bucket.last_message = truncated
            bucket.last_context = context
            bucket.last_severity = severity

            should_flush = False
            # Trigger 1: first occurrence in the bucket.
            if bucket.count == 1:
                should_flush = True
            # Trigger 2: 10× growth threshold.
            elif _is_threshold(bucket.count):
                should_flush = True
            # Trigger 3: 5s elapsed since last flush, with at least one
            # un-flushed event waiting.
            elif (
                bucket.pending_since_flush > 0
                and now_mono - bucket.last_flush_monotonic >= self._flush_interval
            ):
                should_flush = True

            if should_flush:
                payload = bucket.to_payload(
                    run_id=self._run_id, category=category
                )
                bucket.last_flush_monotonic = now_mono
                bucket.pending_since_flush = 0
            else:
                payload = None

        if payload is not None:
            self._emit_fn(payload)

    def flush_all(self) -> None:
        """Flush every bucket that has un-flushed events.

        Called from the worker's terminate hook so the operator sees
        the *true* final counts even if no other trigger fired in the
        intervening window.
        """

        now_mono = self._now_monotonic()
        to_emit: list[dict] = []
        with self._lock:
            for category, bucket in self._buckets.items():
                if bucket.pending_since_flush == 0:
                    continue
                payload = bucket.to_payload(
                    run_id=self._run_id, category=category
                )
                bucket.last_flush_monotonic = now_mono
                bucket.pending_since_flush = 0
                to_emit.append(payload)
        for payload in to_emit:
            self._emit_fn(payload)

    # ------------------------------------------------------------------
    # Test hooks / introspection
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[Category, _ErrorBucket]:
        """Shallow copy of the current buckets — for tests/diagnostics."""

        with self._lock:
            return dict(self._buckets)


def _default_monotonic() -> float:
    import time

    return time.monotonic()


def _default_iso() -> str:
    return to_iso_z(utc_now())
