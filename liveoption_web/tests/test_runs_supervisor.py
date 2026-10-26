"""Tests for the async subprocess supervisor (Task 13)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from liveoption_web.db.connection import open_db
from liveoption_web.db.migrations import apply_migrations
from liveoption_web.runs import supervisor


@pytest_asyncio.fixture
async def db_setup(tmp_path):
    db_path = tmp_path / "db.sqlite"
    await apply_migrations(db_path)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    # Seed a parent run + pending fork row.
    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id, provider,
                                date_start, date_end, status, status_updated_at,
                                created_at, storage_dir)
               VALUES ('PARENT', 'base', 'llm_v1', '/tmp/cfg.yaml', 'deepseek-v3',
                       'deepseek', '2026-04-01', '2026-04-10', 'completed',
                       '2026-04-19T00:00:00Z', '2026-04-18T00:00:00Z',
                       '/tmp/runs/parent')""",
        )
        await db.execute(
            """INSERT INTO runs(id, kind, parent_run_id, scenario, config_path,
                                model_id, provider, date_start, date_end, status,
                                status_updated_at, created_at, storage_dir)
               VALUES ('FORK1', 'fork', 'PARENT', 'llm_v1', '/tmp/cfg.yaml',
                       'deepseek-v3', 'deepseek', '2026-04-01', '2026-04-10',
                       'pending', '2026-04-19T00:00:00Z',
                       '2026-04-18T00:00:00Z', ?)""",
            (str(runs_dir / "FORK1"),),
        )
        await db.commit()
    return db_path, runs_dir


class _FakeProc:
    """Stand-in for an :class:`asyncio.subprocess.Process`."""

    def __init__(self, pid: int = 4242, exit_code: int = 0, delay: float = 0.0):
        self.pid = pid
        self._code = exit_code
        self._delay = delay

    async def wait(self) -> int:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._code


@pytest.mark.asyncio
async def test_spawn_fork_updates_pid_and_status(db_setup, monkeypatch):
    db_path, runs_dir = db_setup
    proc = _FakeProc(pid=9999, exit_code=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    pid = await supervisor.spawn_fork(
        db_path=db_path,
        runs_dir=runs_dir,
        ingest_url="http://ingest",
        checkpoint_id="CK1",
        run_id="FORK1",
        parent_run_id="PARENT",
        parent_storage_dir="/tmp/runs/parent",
        mode="diverge",
        prompt_override=None,
        model_override="deepseek-v3",
    )
    assert pid == 9999

    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT pid, status FROM runs WHERE id='FORK1'"
        )
        row = await cur.fetchone()
    assert row[0] == 9999
    assert row[1] == "running"

    # Drain the watcher task so completion is recorded.
    await asyncio.sleep(0.05)
    for _ in range(20):
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT status, exit_code FROM runs WHERE id='FORK1'"
            )
            row = await cur.fetchone()
        if row[0] == "completed":
            break
        await asyncio.sleep(0.05)
    assert row[0] == "completed"
    assert row[1] == 0


@pytest.mark.asyncio
async def test_spawn_fork_records_failure(db_setup, monkeypatch):
    db_path, runs_dir = db_setup
    proc = _FakeProc(pid=111, exit_code=3)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await supervisor.spawn_fork(
        db_path=db_path,
        runs_dir=runs_dir,
        ingest_url="http://ingest",
        checkpoint_id="CK1",
        run_id="FORK1",
        parent_run_id="PARENT",
        parent_storage_dir="/tmp/runs/parent",
        mode="diverge",
        prompt_override=None,
        model_override="deepseek-v3",
    )

    for _ in range(20):
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT status, exit_code FROM runs WHERE id='FORK1'"
            )
            row = await cur.fetchone()
        if row[0] == "failed":
            break
        await asyncio.sleep(0.05)

    assert row[0] == "failed"
    assert row[1] == 3

    # exit_code.txt written into run's storage dir.
    assert (runs_dir / "FORK1" / "exit_code.txt").read_text() == "3"


@pytest.mark.asyncio
async def test_spawn_fork_writes_prompt_override(db_setup, monkeypatch):
    db_path, runs_dir = db_setup
    captured_args: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        captured_args.append(args)
        return _FakeProc(pid=77)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await supervisor.spawn_fork(
        db_path=db_path,
        runs_dir=runs_dir,
        ingest_url="http://ingest",
        checkpoint_id="CK1",
        run_id="FORK1",
        parent_run_id="PARENT",
        parent_storage_dir="/tmp/runs/parent",
        mode="diverge",
        prompt_override="Extra cautious today.",
        model_override=None,
    )

    assert captured_args, "create_subprocess_exec should be called"
    args = captured_args[0]
    assert "resume-from-checkpoint" in args
    assert "--prompt-override" in args
    prompt_file_idx = args.index("--prompt-override") + 1
    prompt_file = Path(args[prompt_file_idx])
    assert prompt_file.exists()
    assert prompt_file.read_text() == "Extra cautious today."
    assert "--model-override" not in args


@pytest.mark.asyncio
async def test_spawn_fork_passes_model_override(db_setup, monkeypatch):
    db_path, runs_dir = db_setup
    captured_args: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        captured_args.append(args)
        return _FakeProc(pid=77)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await supervisor.spawn_fork(
        db_path=db_path,
        runs_dir=runs_dir,
        ingest_url="http://ingest",
        checkpoint_id="CK1",
        run_id="FORK1",
        parent_run_id="PARENT",
        parent_storage_dir="/tmp/runs/parent",
        mode="diverge",
        prompt_override=None,
        model_override="gemini-2.5-pro",
    )
    args = captured_args[0]
    assert "--model-override" in args
    assert args[args.index("--model-override") + 1] == "gemini-2.5-pro"
    assert "--web-ingest" in args
    assert args[args.index("--web-ingest") + 1] == "http://ingest"
    assert "--run-id" in args
    assert args[args.index("--run-id") + 1] == "FORK1"


@pytest.mark.asyncio
async def test_supervisor_enforces_cap_independent_of_api(db_setup, monkeypatch):
    """Non-API callers must also be gated by ``max_concurrent_runs``.

    Seed the DB with ``max`` already-pending fork rows for actor ``alice``,
    then attempt to spawn one more — spawn_fork must raise
    ``RunCapExceeded`` and flip the would-be row to ``cancelled``
    without ever invoking ``create_subprocess_exec``.
    """
    db_path, runs_dir = db_setup
    from liveoption_web.runs.supervisor import RunCapExceeded

    # Insert the target pending row + ``max`` other pending forks for
    # the same actor.  The fixture seeded FORK1 with created_by=NULL;
    # update it to alice so the filter matches.
    async with open_db(db_path) as db:
        await db.execute(
            "UPDATE runs SET created_by='alice' WHERE id='FORK1'",
        )
        for i in range(3):  # cap=3 (passed explicitly below); seed 3 others so FORK1 is the 4th
            await db.execute(
                """INSERT INTO runs(id, kind, parent_run_id, scenario, config_path,
                                    model_id, provider, date_start, date_end, status,
                                    status_updated_at, created_at, created_by,
                                    storage_dir)
                   VALUES (?, 'fork', 'PARENT', 'llm_v1', '/tmp/cfg.yaml',
                           'deepseek-v3', 'deepseek', '2026-04-01',
                           '2026-04-10', 'pending', '2026-04-19T00:00:00Z',
                           '2026-04-18T00:00:00Z', 'alice', ?)""",
                (f"OTHER{i}", str(runs_dir / f"OTHER{i}")),
            )
        await db.commit()

    called = {"n": 0}

    async def fake_exec(*args, **kwargs):  # pragma: no cover - must not fire
        called["n"] += 1
        return _FakeProc(pid=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RunCapExceeded):
        await supervisor.spawn_fork(
            db_path=db_path,
            runs_dir=runs_dir,
            ingest_url="http://ingest",
            checkpoint_id="CK1",
            run_id="FORK1",
            parent_run_id="PARENT",
            parent_storage_dir="/tmp/runs/parent",
            mode="diverge",
            prompt_override=None,
            model_override="deepseek-v3",
            actor_user_id="alice",
            max_concurrent_runs=3,
        )

    assert called["n"] == 0, "subprocess must not launch when cap exceeded"

    async with open_db(db_path) as db:
        cur = await db.execute("SELECT status FROM runs WHERE id='FORK1'")
        row = await cur.fetchone()
    assert row[0] == "cancelled"


@pytest.mark.asyncio
async def test_supervisor_cap_allows_under_limit(db_setup, monkeypatch):
    """spawn_fork proceeds normally when actor is under the cap."""
    db_path, runs_dir = db_setup

    async with open_db(db_path) as db:
        await db.execute(
            "UPDATE runs SET created_by='alice' WHERE id='FORK1'",
        )
        # Only 2 other pending forks, cap=3 → allowed.
        for i in range(2):
            await db.execute(
                """INSERT INTO runs(id, kind, parent_run_id, scenario, config_path,
                                    model_id, provider, date_start, date_end, status,
                                    status_updated_at, created_at, created_by,
                                    storage_dir)
                   VALUES (?, 'fork', 'PARENT', 'llm_v1', '/tmp/cfg.yaml',
                           'deepseek-v3', 'deepseek', '2026-04-01',
                           '2026-04-10', 'pending', '2026-04-19T00:00:00Z',
                           '2026-04-18T00:00:00Z', 'alice', ?)""",
                (f"OTHER{i}", str(runs_dir / f"OTHER{i}")),
            )
        await db.commit()

    proc = _FakeProc(pid=5555, exit_code=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    pid = await supervisor.spawn_fork(
        db_path=db_path,
        runs_dir=runs_dir,
        ingest_url="http://ingest",
        checkpoint_id="CK1",
        run_id="FORK1",
        parent_run_id="PARENT",
        parent_storage_dir="/tmp/runs/parent",
        mode="diverge",
        prompt_override=None,
        model_override="deepseek-v3",
        actor_user_id="alice",
        max_concurrent_runs=3,
    )
    assert pid == 5555
