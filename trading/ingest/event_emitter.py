"""EventEmitter — web-layer event stream emitter. Spec 6.2, 4.4."""
from __future__ import annotations
import fcntl, itertools, json, logging, os, queue, threading
from pathlib import Path
from typing import Any

import requests

from trading.core.time_contract import utc_now, to_iso_z

logger = logging.getLogger(__name__)


class NoopEmitter:
    """No-op implementation used when --web-ingest is not passed."""
    def emit(self, kind: str, payload: dict) -> None: pass
    def close(self, timeout: float = 5.0) -> None: pass
    def post_decision_record(self, decision: dict) -> None: pass


class EventEmitter:
    """Emit structured events to `<run_dir>/events.jsonl` and optionally HTTP POST.

    Threading model:
      - emit() is called on engine main thread; non-blocking in the common path.
      - Background flusher drains queue → JSONL + HTTP POST.
      - On queue overflow, emit() falls back to synchronous JSONL write (spec 4.4).
      - flock(LOCK_EX | LOCK_NB) on the jsonl file enforces single-writer contract.
    """

    def __init__(
        self, run_dir: Path, run_id: str, ingest_url: str | None,
        queue_max: int = 10000, batch_size: int = 100, batch_wait: float = 0.1,
    ) -> None:
        self.run_id = run_id
        self.ingest_url = ingest_url
        self.queue: queue.Queue = queue.Queue(maxsize=queue_max)
        self.seq_counter = itertools.count(1)
        self.jsonl_path = Path(run_dir) / "events.jsonl"
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.jsonl_path, "a", buffering=1)
        try:
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fp.close()
            raise
        self._sync_lock = threading.Lock()  # protects direct writes from overflow path
        self._stop = threading.Event()
        self._paused = threading.Event()   # test hook
        self._batch_size = batch_size
        self._batch_wait = batch_wait
        self._thread = threading.Thread(target=self._flusher, daemon=True, name="EventEmitter")
        self._thread.start()

    def emit(self, kind: str, payload: dict) -> None:
        ev = {
            "subprocess_seq": next(self.seq_counter),
            "ts": to_iso_z(utc_now()),
            "kind": kind,
            "payload": payload,
        }
        try:
            self.queue.put_nowait(ev)
        except queue.Full:
            # spec 4.4: synchronous JSONL fallback — never drop
            logger.warning("event queue overflow; synchronous fallback seq=%d", ev["subprocess_seq"])
            with self._sync_lock:
                self._fp.write(json.dumps(ev) + "\n")
                self._fp.flush()
                os.fsync(self._fp.fileno())

    def close(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)
        try:
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        self._fp.close()

    def _flusher(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                threading.Event().wait(0.05)
                continue
            batch = self._drain()
            if not batch:
                continue
            with self._sync_lock:
                for ev in batch:
                    self._fp.write(json.dumps(ev) + "\n")
                self._fp.flush()
                os.fsync(self._fp.fileno())
            if self.ingest_url:
                self._post_batch(batch)
        # Final drain after stop — loop until empty; single _drain returns at
        # most batch_size, which would silently drop events on shutdown (up to
        # queue_max - batch_size could be lost).
        while True:
            final = self._drain(timeout=0.0)
            if not final:
                break
            with self._sync_lock:
                for ev in final:
                    self._fp.write(json.dumps(ev) + "\n")
                self._fp.flush()
                os.fsync(self._fp.fileno())
            if self.ingest_url:
                self._post_batch(final)

    def _drain(self, timeout: float | None = None) -> list[dict]:
        wait = self._batch_wait if timeout is None else timeout
        batch: list[dict] = []
        try:
            first = self.queue.get(timeout=wait)
            batch.append(first)
        except queue.Empty:
            return batch
        while len(batch) < self._batch_size:
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _post_batch(self, batch: list[dict]) -> None:
        try:
            # Job Server's /_ingest/events requires the shared secret on
            # every request (see liveoption_web/api/ingest.py). Read it
            # from env at POST time so tests can monkeypatch. On absence
            # we still attempt the POST — may land in a dev Job Server
            # with no auth, or fail 403 and fall back to JSONL reconcile.
            secret = os.environ.get("LIVEOPTION_INTERNAL_SECRET", "")
            headers = {"X-Internal-Secret": secret} if secret else {}
            r = requests.post(
                self.ingest_url,
                json={"run_id": self.run_id, "events": batch},
                headers=headers,
                timeout=5,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning("ingest POST failed (will recover via JSONL reconcile): %s", e)

    def post_decision_record(self, decision: dict[str, Any]) -> None:
        """POST a decision_records row to the Job Server.

        Best-effort: failures are logged and swallowed so a network blip never
        aborts a trading decision. The backend ingestor enforces UNIQUE
        (run_id, seq) so duplicate posts are harmless.
        """
        if not self.ingest_url:
            return
        try:
            url = self.ingest_url.replace("/_ingest/events", "/_ingest/decision-record")
            secret = os.environ.get("LIVEOPTION_INTERNAL_SECRET", "")
            headers = {"X-Internal-Secret": secret} if secret else {}
            r = requests.post(
                url,
                json={"run_id": self.run_id, "decision": decision},
                headers=headers,
                timeout=5,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning("decision-record POST failed (run=%s seq=%s): %s",
                           self.run_id, decision.get("seq"), e)

    # Test hooks
    def _pause_for_test(self) -> None: self._paused.set()
    def _resume_for_test(self) -> None: self._paused.clear()
