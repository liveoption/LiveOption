"""Tests for :func:`spawn_batch` — the batch sibling of ``spawn_fork``.

Task 6 of the web-run-launcher design (spec §4.4).  The supervisor
provides a thin function that:

* shares the ``max_concurrent_runs`` cap with forks,
* materialises ``runs_dir/<run_id>/``,
* strips ``LIVEOPTION_INTERNAL_SECRET`` from the subprocess env,
* execs a ready-made argv list (composed by the launch endpoint),
* fires the same ``_watch_subprocess`` task as forks.

The caller (T7's launch endpoint) is expected to have inserted the
``runs`` row with ``kind='batch'``, ``status='pending'``, and the
serialised ``launch_spec_json`` *before* invoking ``spawn_batch``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio

from liveoption_web.db.connection import open_db
from liveoption_web.db.migrations import apply_migrations
from liveoption_web.runs import supervisor

# Ensure Settings can be constructed in test runs that import config.
os.environ.setdefault("LIVEOPTION_INTERNAL_SECRET", "test-secret")


@pytest_asyncio.fixture
async def db_setup(tmp_path):
    db_path = tmp_path / "db.sqlite"
    await apply_migrations(db_path)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    # Pre-insert the batch pending row (as T7's launch endpoint would).
    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id, provider,
                                date_start, date_end, status, status_updated_at,
                                created_at, storage_dir, launch_spec_json)
               VALUES ('BATCH1', 'batch', 'llm_v1', '/tmp/cfg.yaml', 'deepseek-v3',
                       'deepseek', '2026-04-01', '2026-04-10', 'pending',
                       '2026-04-19T00:00:00Z', '2026-04-18T00:00:00Z', ?,
                       '{"scenario":"llm_v1","provider":"deepseek"}')""",
            (str(runs_dir / "BATCH1"),),
        )
        await db.commit()
    return db_path, runs_dir


class _FakeProc:
    def __init__(self, pid: int = 4242, exit_code: int = 0, delay: float = 0.0):
        self.pid = pid
        self._code = exit_code
        self._delay = delay

    async def wait(self) -> int:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._code


def _default_argv() -> list[str]:
    """Stand-in for the argv T7 will build."""

    return [
        "/path/to/python",
        "-m",
        "trading.cli",
        "run-scenario",
        "--config",
        "/tmp/cfg.yaml",
        "--web-ingest",
        "http://ingest",
        "--run-id",
        "BATCH1",
    ]


@pytest.mark.asyncio
async def test_spawn_batch_updates_pending_row_to_running(db_setup, monkeypatch):
    db_path, runs_dir = db_setup
    proc = _FakeProc(pid=9999, exit_code=0, delay=0.05)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    pid = await supervisor.spawn_batch(
        db_path=db_path,
        runs_dir=runs_dir,
        run_id="BATCH1",
        argv=_default_argv(),
        env={"PATH": "/usr/bin"},
    )
    assert pid == 9999

    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT pid, status, launch_spec_json FROM runs WHERE id='BATCH1'"
        )
        row = await cur.fetchone()
    assert row[0] == 9999
    assert row[1] == "running"
    # launch_spec_json preserved — spawn_batch must not clobber it.
    assert row[2] == '{"scenario":"llm_v1","provider":"deepseek"}'

    # Drain the watcher and confirm completion is recorded.
    for _ in range(30):
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT status, exit_code FROM runs WHERE id='BATCH1'"
            )
            row = await cur.fetchone()
        if row[0] == "completed":
            break
        await asyncio.sleep(0.05)
    assert row[0] == "completed"
    assert row[1] == 0


@pytest.mark.asyncio
async def test_spawn_batch_raises_cap_exceeded_and_cancels_row(
    db_setup, monkeypatch
):
    db_path, runs_dir = db_setup

    # Fill cap with 4 other pending/running runs so BATCH1 is the 5th.
    async with open_db(db_path) as db:
        for i in range(4):
            await db.execute(
                """INSERT INTO runs(id, kind, scenario, config_path, model_id,
                                    provider, date_start, date_end, status,
                                    status_updated_at, created_at, storage_dir)
                   VALUES (?, 'batch', 'llm_v1', '/tmp/cfg.yaml', 'deepseek-v3',
                           'deepseek', '2026-04-01', '2026-04-10',
                           'pending', '2026-04-19T00:00:00Z',
                           '2026-04-18T00:00:00Z', ?)""",
                (f"OTHER{i}", str(runs_dir / f"OTHER{i}")),
            )
        await db.commit()

    called = {"n": 0}

    async def fake_exec(*args, **kwargs):  # pragma: no cover - must not fire
        called["n"] += 1
        return _FakeProc(pid=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(supervisor.RunCapExceeded):
        await supervisor.spawn_batch(
            db_path=db_path,
            runs_dir=runs_dir,
            run_id="BATCH1",
            argv=_default_argv(),
            env={"PATH": "/usr/bin"},
            max_concurrent_runs=4,
        )

    assert called["n"] == 0, "subprocess must not launch when cap exceeded"

    async with open_db(db_path) as db:
        cur = await db.execute("SELECT status FROM runs WHERE id='BATCH1'")
        row = await cur.fetchone()
    assert row[0] == "cancelled"


@pytest.mark.asyncio
async def test_spawn_batch_env_forwards_internal_secret_strips_clerk(
    db_setup, monkeypatch
):
    """Subprocess MUST keep ``LIVEOPTION_INTERNAL_SECRET`` — its event
    emitter POSTs back to ``/_ingest/events`` and the shared secret is
    the only way it can authenticate. Clerk secrets ARE stripped since
    the subprocess has no Clerk interaction.

    Regression guard for the 2026-04-24 fix where an earlier denylist
    included ``LIVEOPTION_INTERNAL_SECRET`` and broke event ingest.
    """
    db_path, runs_dir = db_setup
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["args"] = args
        return _FakeProc(pid=77)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await supervisor.spawn_batch(
        db_path=db_path,
        runs_dir=runs_dir,
        run_id="BATCH1",
        argv=_default_argv(),
        env={
            "DEEPSEEK_API_KEY": "sk-test",
            "LIVEOPTION_INTERNAL_SECRET": "shh",
            "CLERK_SECRET_KEY": "sk_live_clerk",
            "CLERK_JWT_KEY": "jwk-clerk",
            "PATH": "/usr/bin",
        },
    )

    env = captured["env"]
    assert env is not None
    # LLM + PATH + INTERNAL_SECRET (needed by event_emitter) flow through.
    assert env.get("DEEPSEEK_API_KEY") == "sk-test"
    assert env.get("PATH") == "/usr/bin"
    assert env.get("LIVEOPTION_INTERNAL_SECRET") == "shh"
    # Clerk secrets are still stripped — subprocess has no Clerk use.
    assert "CLERK_SECRET_KEY" not in env
    assert "CLERK_JWT_KEY" not in env


@pytest.mark.asyncio
async def test_spawn_batch_emits_run_launched_audit_log(db_setup, monkeypatch, caplog):
    """On successful spawn, supervisor logs a structured `run.launched` event."""
    db_path, runs_dir = db_setup

    async def fake_exec(*args, **kwargs):
        return _FakeProc(pid=9999)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    caplog.set_level("INFO", logger="liveoption_web.runs.supervisor")
    await supervisor.spawn_batch(
        db_path=db_path,
        runs_dir=runs_dir,
        run_id="BATCH1",
        argv=_default_argv(),
        env={"PATH": "/usr/bin"},
        actor_email="haochenluo02@gmail.com",
    )

    launched = [r for r in caplog.records if r.msg == "run.launched"]
    assert launched, "expected a run.launched log record"
    rec = launched[0]
    assert rec.run_id == "BATCH1"
    assert rec.actor_email == "haochenluo02@gmail.com"
    assert rec.pid == 9999
    # argv_hash is a 16-hex-char sha256 prefix.
    assert isinstance(rec.argv_hash, str)
    assert len(rec.argv_hash) == 16
    int(rec.argv_hash, 16)  # raises if not hex
