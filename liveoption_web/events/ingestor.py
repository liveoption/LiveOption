"""Persistence + fanout for subprocess-emitted events.

The engine side (worker subprocesses) POSTs to ``/_ingest/*``; those
handlers delegate the actual DB write and in-process pubsub publish to
the helpers in this module so the logic is unit-testable without
instantiating FastAPI.

Idempotency strategy
--------------------
Each table has a natural uniqueness constraint that doubles as a
replay-safety net:

* ``events``   — ``UNIQUE (run_id, subprocess_seq)`` (migration 001).
* ``decision_records`` — ``UNIQUE (run_id, seq)``.
* ``question_chunks``  — composite ``PRIMARY KEY (question_id, seq)``.

On a constraint violation we treat the row as an already-delivered
duplicate rather than a failure, so the subprocess can safely retry a
batch whose prior response was lost in transit.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from liveoption_web.db.connection import open_db
from liveoption_web.events.pubsub import Pubsub


def _is_unique_violation(exc: BaseException) -> bool:
    """Return True if ``exc`` represents a SQLite UNIQUE constraint failure."""
    if isinstance(exc, sqlite3.IntegrityError):
        msg = str(exc).upper()
        return "UNIQUE" in msg
    return "UNIQUE" in str(exc).upper()


async def ingest_events(
    db_path: Path,
    run_id: str,
    events: list[dict[str, Any]],
    pubsub: Pubsub,
) -> dict[str, int]:
    """Append ``events`` to the ``events`` table and fan out on ``run_id``.

    Returns ``{"accepted": N, "duplicates": M}``.  Duplicates are events
    whose ``(run_id, subprocess_seq)`` already exists — they are counted
    but not re-published to the pubsub topic.
    """
    accepted = 0
    duplicates = 0
    async with open_db(Path(db_path)) as db:
        for ev in events:
            try:
                cur = await db.execute(
                    """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
                       VALUES(?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        ev["subprocess_seq"],
                        ev["ts"],
                        ev["kind"],
                        json.dumps(ev.get("payload", {})),
                    ),
                )
            except Exception as e:  # noqa: BLE001 — narrow via helper
                if _is_unique_violation(e):
                    duplicates += 1
                    continue
                raise
            accepted += 1
            event_id = cur.lastrowid
            await pubsub.publish(
                run_id,
                {
                    "id": event_id,
                    "ts": ev["ts"],
                    "kind": ev["kind"],
                    "payload": ev.get("payload", {}),
                },
            )
        await db.commit()
    return {"accepted": accepted, "duplicates": duplicates}


async def ingest_decision_record(
    db_path: Path,
    run_id: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Insert a decision_record row.

    On duplicate ``(run_id, seq)`` returns ``{"duplicate": True}`` with a
    200 response from the caller — this mirrors the replay semantics used
    for events.
    """
    now = _now_z()
    async with open_db(Path(db_path)) as db:
        try:
            await db.execute(
                """
                INSERT INTO decision_records(
                    id, run_id, seq, trading_day, decision_ts,
                    model_id, provider, prompt_sha256, response_sha256,
                    storage_dir, has_trade, promoted_checkpoint_id,
                    cum_pnl_after, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision["id"],
                    run_id,
                    decision["seq"],
                    decision["trading_day"],
                    decision["decision_ts"],
                    decision["model_id"],
                    decision["provider"],
                    decision["prompt_sha256"],
                    decision["response_sha256"],
                    decision["storage_dir"],
                    1 if decision.get("has_trade") else 0,
                    decision.get("promoted_checkpoint_id"),
                    decision.get("cum_pnl_after"),
                    now,
                ),
            )
        except Exception as e:  # noqa: BLE001
            if _is_unique_violation(e):
                return {"duplicate": True}
            raise
        await db.commit()
    return {"duplicate": False}


async def ingest_question_chunk(
    db_path: Path,
    chunk: dict[str, Any],
    pubsub: Pubsub,
) -> dict[str, Any]:
    """Append a streaming question chunk + broadcast on ``question:<id>``.

    Duplicate ``(question_id, seq)`` rows are silently de-duped; the
    caller still sees ``{"ok": True, "duplicate": True}`` so retries are
    safe.
    """
    question_id = chunk["question_id"]
    async with open_db(Path(db_path)) as db:
        try:
            await db.execute(
                """INSERT INTO question_chunks(question_id, seq, ts, delta_text)
                   VALUES(?, ?, ?, ?)""",
                (question_id, chunk["seq"], chunk["ts"], chunk["delta_text"]),
            )
        except Exception as e:  # noqa: BLE001
            if _is_unique_violation(e):
                return {"ok": True, "duplicate": True}
            raise
        await db.commit()
    await pubsub.publish(
        f"question:{question_id}",
        {
            "seq": chunk["seq"],
            "ts": chunk["ts"],
            "delta_text": chunk["delta_text"],
        },
    )
    return {"ok": True, "duplicate": False}


def _now_z() -> str:
    """Return the current UTC instant as ``YYYY-MM-DDTHH:MM:SS.sssZ``.

    Kept local so ingestor.py has no cross-module utility dependency; the
    format mirrors the CHECK constraints in migration 001.
    """
    from datetime import datetime, timezone

    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
