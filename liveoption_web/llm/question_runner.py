"""Background task that drives a follow-up-question LLM stream.

The flow for a single question:

1. Flip ``questions.status`` to ``streaming`` (so the API rate limit
   reflects in-flight work).
2. Pull the frozen prompt/response text from the checkpoint's
   ``storage_dir`` (best-effort — falls back to empty context if the
   files are missing, so the DB row stays consistent even in degraded
   deployments).
3. Invoke the injected async LLM ``stream_fn`` with a composed prompt.
4. For each emitted delta:

   * ``INSERT`` a row into ``question_chunks`` (``(question_id, seq)``
     primary key, monotonically increasing).
   * Publish ``{"seq", "ts", "delta_text"}`` on pubsub topic
     ``question:<id>`` so the SSE stream can fan it out live.

5. On normal completion: ``UPDATE questions`` with the assembled
   response text + ``status='completed'`` and publish a terminal
   ``{"done": True}`` marker.
6. On exception: ``UPDATE questions`` with ``status='failed'`` +
   ``error_message`` and publish ``{"failed": True, "error": ...}``.

The function is intentionally defensive: it never raises to the caller
(the background task). Any terminal error is recorded on the row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Protocol

from liveoption_web.db.connection import open_db
from liveoption_web.events.pubsub import Pubsub

logger = logging.getLogger(__name__)


class StreamFn(Protocol):
    """Signature of the injectable LLM stream callable.

    ``prompt`` is the fully-composed messages-as-string context built by
    the runner; ``model_id`` is an optional override. Implementations
    return an async iterator that yields string deltas.
    """

    def __call__(
        self, *, prompt: str, model_id: str | None
    ) -> AsyncIterator[str]: ...


def _utc_now_z() -> str:
    """Return ``YYYY-MM-DDTHH:MM:SS.mmmZ`` — matches schema CHECK."""
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


async def _load_checkpoint_context(storage_dir: str) -> tuple[str, str]:
    """Best-effort load of ``prompt.txt`` + ``response.txt`` from a checkpoint.

    Missing files yield empty strings so downstream prompt composition
    never crashes in a degraded environment — the LLM just gets a
    thinner context.
    """
    root = Path(storage_dir)
    prompt = ""
    response = ""
    try:
        pf = root / "prompt.txt"
        if pf.exists():
            prompt = pf.read_text()
    except OSError as e:
        logger.warning("could not read prompt.txt from %s: %s", root, e)
    try:
        rf = root / "response.txt"
        if rf.exists():
            response = rf.read_text()
    except OSError as e:
        logger.warning("could not read response.txt from %s: %s", root, e)
    return prompt, response


def _compose_prompt(orig_prompt: str, orig_response: str,
                    qa_tree: list[tuple[str, str]], user_prompt: str) -> str:
    """Compose the flat prompt passed to the LLM stream.

    Format is intentionally simple (not provider-specific) because the
    runner treats the LLM as a pluggable stream source. Callers wiring
    up a real client should convert this into proper ``messages=[]``.
    """
    parts: list[str] = []
    if orig_prompt:
        parts.append("# Original Prompt\n" + orig_prompt)
    if orig_response:
        parts.append("# Original Response\n" + orig_response)
    for q, a in qa_tree:
        parts.append("# Prior Follow-up\n" + q + "\n\n" + a)
    parts.append("# Follow-up\n" + user_prompt)
    return "\n\n".join(parts)


async def _load_parent_chain(
    db_path: Path, parent_question_id: str | None
) -> list[tuple[str, str]]:
    """Walk ``parent_question_id`` → root, oldest first.

    Returns ``[(prompt, response), ...]``. Rows with a NULL
    ``response_text`` are skipped (still-streaming parents would
    otherwise leak partial content).
    """
    if not parent_question_id:
        return []
    chain: list[tuple[str, str]] = []
    cursor_id: str | None = parent_question_id
    async with open_db(db_path) as db:
        while cursor_id:
            cur = await db.execute(
                "SELECT user_prompt, response_text, parent_question_id "
                "FROM questions WHERE id=?",
                (cursor_id,),
            )
            row = await cur.fetchone()
            if row is None:
                break
            if row[1]:
                chain.append((row[0], row[1]))
            cursor_id = row[2]
    chain.reverse()
    return chain


async def run_question(
    *,
    db_path: Path,
    pubsub: Pubsub,
    question_id: str,
    checkpoint_id: str,
    user_prompt: str,
    model_id: str | None,
    parent_question_id: str | None,
    stream_fn: Callable[..., AsyncIterator[str]] | None,
) -> None:
    """Execute the LLM follow-up for ``question_id``.

    This function is the target of ``asyncio.create_task`` from the HTTP
    handler. It never raises — every failure path lands on the row as
    ``status='failed'`` + ``error_message``.
    """
    if stream_fn is None:
        await _fail(db_path, pubsub, question_id,
                    "no LLM stream configured on app.state.question_llm_stream")
        return

    try:
        # ------- 1. Flip to streaming
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE questions SET status='streaming', status_updated_at=? "
                "WHERE id=? AND status='pending'",
                (_utc_now_z(), question_id),
            )
            await db.commit()

        # ------- 2. Load checkpoint context + QA tree
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT storage_dir FROM checkpoints WHERE id=?",
                (checkpoint_id,),
            )
            row = await cur.fetchone()
        storage_dir = row[0] if row else ""
        orig_prompt, orig_response = await _load_checkpoint_context(storage_dir)
        qa_tree = await _load_parent_chain(db_path, parent_question_id)
        composed = _compose_prompt(orig_prompt, orig_response, qa_tree, user_prompt)

        # ------- 3. Stream
        seq = 0
        assembled: list[str] = []
        async for delta in stream_fn(prompt=composed, model_id=model_id):
            if not delta:
                continue
            seq += 1
            ts = _utc_now_z()
            async with open_db(db_path) as db:
                await db.execute(
                    "INSERT INTO question_chunks(question_id, seq, ts, delta_text) "
                    "VALUES(?, ?, ?, ?)",
                    (question_id, seq, ts, delta),
                )
                await db.commit()
            assembled.append(delta)
            await pubsub.publish(
                f"question:{question_id}",
                {"seq": seq, "ts": ts, "delta_text": delta},
            )

        # ------- 4. Finalize OK
        final_text = "".join(assembled)
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE questions SET response_text=?, response_tokens=?, "
                "status='completed', status_updated_at=? WHERE id=?",
                (final_text, len(assembled), _utc_now_z(), question_id),
            )
            await db.commit()
        await pubsub.publish(
            f"question:{question_id}",
            {"done": True, "response_tokens": len(assembled)},
        )

    except Exception as e:  # noqa: BLE001 — we want to record anything
        logger.exception("question runner failed for %s", question_id)
        await _fail(db_path, pubsub, question_id, f"{type(e).__name__}: {e}")


async def _fail(db_path: Path, pubsub: Pubsub, question_id: str,
                error_message: str) -> None:
    """Record a terminal failure + publish a ``failed`` marker."""
    try:
        async with open_db(db_path) as db:
            await db.execute(
                "UPDATE questions SET status='failed', error_message=?, "
                "status_updated_at=? WHERE id=?",
                (error_message, _utc_now_z(), question_id),
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not persist failure for %s", question_id)
    await pubsub.publish(
        f"question:{question_id}",
        {"failed": True, "error": error_message},
    )
