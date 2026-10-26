"""Reliability drill integration tests (Plan B Task 16 / Spec 8.7).

These port the manual reliability drill items from Spec 8.7 into an
automated pytest suite so regressions surface in CI rather than being
caught only on the launch-gate manual run.

Coverage map (Spec 8.7 item → test):

* "Kill subprocess directly (SIGKILL) while FastAPI alive → FastAPI's
  ``wait()`` handler writes ``exit_code``, marks run ``failed``, no
  orphaned DB row." → :func:`test_kill_subprocess_marks_failed_no_orphan`.
* "``kill -9 $(pidof uvicorn)`` while a fork is running → systemd restarts
  FastAPI → reconcile marks fork ``orphaned``." →
  :func:`test_kill_fastapi_restart_reconciles_cleanly`.
* "Under 3 concurrent forks, SQLite ``database is locked`` never
  occurs." → :func:`test_concurrent_forks_no_sqlite_locked`.
* "Missing ``X-Internal-Secret`` on direct qlib call → 403." →
  :func:`test_internal_secret_required`.
* "qlib :19720 only reachable via autossh tunnel" (loopback bind) →
  :func:`test_loopback_only_bind`.

Skipped (requires real network/process harness, kept manual):

* ``test_tunnel_drop_sse_resumes_no_loss`` — needs autossh + real SSE
  client; exercised during the launch-gate manual drill only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from liveoption_web.config import Settings
from liveoption_web.db.connection import open_db
from liveoption_web.db.migrations import apply_migrations
from liveoption_web.events.reconcile import reconcile_on_startup
from liveoption_web.runs import supervisor


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


class _FakeProc:
    """Stand-in for :class:`asyncio.subprocess.Process`.

    Mirrors the helper in ``test_runs_supervisor.py`` — kept local so
    this suite has no cross-file dependency on other tests.
    """

    def __init__(self, pid: int = 4242, exit_code: int = 0, delay: float = 0.0):
        self.pid = pid
        self._code = exit_code
        self._delay = delay

    async def wait(self) -> int:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._code


async def _seed_parent_and_fork(db_path: Path, runs_dir: Path, fork_id: str) -> None:
    """Insert a completed parent run + a pending fork row."""
    async with open_db(db_path) as db:
        # Parent may already exist from a previous seed call in the same DB.
        cur = await db.execute("SELECT id FROM runs WHERE id='PARENT'")
        if not await cur.fetchone():
            await db.execute(
                """INSERT INTO runs(id, kind, scenario, config_path, model_id,
                                    provider, date_start, date_end, status,
                                    status_updated_at, created_at, storage_dir)
                   VALUES ('PARENT', 'base', 'llm_v1', '/tmp/cfg.yaml',
                           'deepseek-v3', 'deepseek', '2026-04-01',
                           '2026-04-10', 'completed',
                           '2026-04-19T00:00:00Z', '2026-04-18T00:00:00Z',
                           '/tmp/runs/parent')""",
            )
        await db.execute(
            """INSERT INTO runs(id, kind, parent_run_id, scenario, config_path,
                                model_id, provider, date_start, date_end,
                                status, status_updated_at, created_at,
                                storage_dir)
               VALUES (?, 'fork', 'PARENT', 'llm_v1', '/tmp/cfg.yaml',
                       'deepseek-v3', 'deepseek', '2026-04-01',
                       '2026-04-10', 'pending',
                       '2026-04-19T00:00:00Z', '2026-04-18T00:00:00Z', ?)""",
            (fork_id, str(runs_dir / fork_id)),
        )
        await db.commit()


@pytest_asyncio.fixture
async def db_and_runs(tmp_path):
    db_path = tmp_path / "db.sqlite"
    await apply_migrations(db_path)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return db_path, runs_dir


# ---------------------------------------------------------------------------
# Spec 8.7 — Restart & recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_subprocess_marks_failed_no_orphan(db_and_runs, monkeypatch):
    """SIGKILL'd fork subprocess → watcher records ``failed`` + ``exit_code.txt``."""
    db_path, runs_dir = db_and_runs
    await _seed_parent_and_fork(db_path, runs_dir, "FORK1")

    # SIGKILL surfaces as a negative exit_code (-9).  The supervisor's
    # watcher must classify that as ``failed`` (not ``cancelled``) since
    # only SIGTERM/SIGINT map to the cancelled state.
    proc = _FakeProc(pid=777, exit_code=-9)

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
        model_override=None,
    )

    # Drive the watcher to completion.
    row = None
    for _ in range(20):
        async with open_db(db_path) as db:
            cur = await db.execute(
                "SELECT status, exit_code FROM runs WHERE id='FORK1'"
            )
            row = await cur.fetchone()
        if row and row[0] == "failed":
            break
        await asyncio.sleep(0.05)

    assert row is not None
    assert row[0] == "failed"
    assert row[1] == -9
    # No other fork rows — in particular no duplicate "orphaned" row.
    async with open_db(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM runs WHERE id='FORK1'")
        (count,) = await cur.fetchone()
    assert count == 1
    assert (runs_dir / "FORK1" / "exit_code.txt").read_text() == "-9"


@pytest.mark.asyncio
async def test_kill_fastapi_restart_reconciles_cleanly(db_and_runs):
    """FastAPI killed mid-run → on restart, reconcile marks dead-PID row ``orphaned``."""
    db_path, runs_dir = db_and_runs

    # Seed a "running" row whose PID is guaranteed-dead (a very high PID
    # unlikely to exist; confirmed dead by os.kill(pid, 0) inside the
    # reconcile helper).
    dead_pid = 2_000_000_000
    storage_dir = runs_dir / "GHOST"
    storage_dir.mkdir()
    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id,
                                provider, date_start, date_end, status,
                                status_updated_at, pid, created_at, storage_dir)
               VALUES ('GHOST', 'fork', 'llm_v1', '/tmp/cfg.yaml',
                       'deepseek-v3', 'deepseek', '2026-04-01',
                       '2026-04-10', 'running',
                       '2026-04-19T00:00:00Z', ?, '2026-04-18T00:00:00Z', ?)""",
            (dead_pid, str(storage_dir)),
        )
        await db.commit()

    # Minimal Settings for reconcile (only db_path is exercised here).
    settings = Settings(
        internal_secret="test",
        db_path=db_path,
        runs_dir=runs_dir,
    )

    await reconcile_on_startup(settings)

    async with open_db(db_path) as db:
        cur = await db.execute("SELECT status FROM runs WHERE id='GHOST'")
        (status,) = await cur.fetchone()
    assert status == "orphaned"


# ---------------------------------------------------------------------------
# Spec 8.7 — Concurrency (WAL mode tolerance)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_concurrent_forks_no_sqlite_locked(db_and_runs, monkeypatch):
    """3 concurrent supervisor spawns → WAL handles it, no ``database is locked``."""
    db_path, runs_dir = db_and_runs
    fork_ids = ["FORKA", "FORKB", "FORKC"]
    for fid in fork_ids:
        await _seed_parent_and_fork(db_path, runs_dir, fid)

    # Small random-ish wait so multiple watchers race on the DB.
    async def fake_exec(*args, **kwargs):
        return _FakeProc(pid=1234, exit_code=0, delay=0.02)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    results = await asyncio.gather(
        *[
            supervisor.spawn_fork(
                db_path=db_path,
                runs_dir=runs_dir,
                ingest_url="http://ingest",
                checkpoint_id="CK1",
                run_id=fid,
                parent_run_id="PARENT",
                parent_storage_dir="/tmp/runs/parent",
                mode="diverge",
                prompt_override=None,
                model_override=None,
            )
            for fid in fork_ids
        ],
        return_exceptions=True,
    )

    # No spawn raised (in particular no "database is locked").
    for r in results:
        assert not isinstance(r, Exception), f"spawn_fork raised: {r!r}"

    # All watchers drain to ``completed`` — if a watcher hit a DB lock
    # it would never flip the row out of ``running``.
    for _ in range(40):
        async with open_db(db_path) as db:
            cur = await db.execute(
                f"SELECT status FROM runs WHERE id IN ({','.join('?' * len(fork_ids))})",
                tuple(fork_ids),
            )
            statuses = [r[0] for r in await cur.fetchall()]
        if all(s == "completed" for s in statuses):
            break
        await asyncio.sleep(0.05)

    assert statuses == ["completed"] * len(fork_ids), statuses


# ---------------------------------------------------------------------------
# Spec 8.7 — Security
# ---------------------------------------------------------------------------


# Representative sample of /v1 routes — one per router module.  A missing
# secret must yield 403 for every one; the dependency short-circuits
# before any handler body runs so placeholder path params are fine.
V1_PROBE_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/v1/runs", None),
    ("POST", "/v1/runs", {}),
    ("GET", "/v1/runs/RUN1", None),
    ("GET", "/v1/runs/RUN1/metrics", None),
    ("POST", "/v1/runs/RUN1/metrics:recompute", {}),
    ("PATCH", "/v1/runs/RUN1", {}),
    ("GET", "/v1/runs/RUN1/timeline", None),
    ("GET", "/v1/runs/RUN1/pnl", None),
    ("GET", "/v1/runs/RUN1/children", None),
    ("GET", "/v1/runs/RUN1/compare/RUN2", None),
    ("GET", "/v1/runs/RUN1/events", None),
    ("GET", "/v1/runs/RUN1/events/history", None),
    ("GET", "/v1/runs/RUN1/checkpoints", None),
    ("GET", "/v1/checkpoints/CK1", None),
    ("GET", "/v1/checkpoints/CK1/questions", None),
    ("POST", "/v1/checkpoints/CK1/forks", {}),
    ("POST", "/v1/checkpoints/CK1/questions", {}),
    ("GET", "/v1/questions/Q1/stream", None),
    ("GET", "/v1/audit/targets/run/RUN1", None),
    ("POST", "/v1/decisions/DEC1/promote", {}),
    ("GET", "/v1/decisions/DEC1", None),
    ("GET", "/v1/scenarios", None),
    ("GET", "/v1/me", None),
    ("GET", "/v1/tasks/presets", None),
    ("POST", "/v1/tasks", {}),
    ("GET", "/v1/tasks/RUN1", None),
    ("GET", "/v1/tasks/RUN1/chart", None),
    ("GET", "/v1/tasks/RUN1/analysis", None),
    ("GET", "/v1/tasks/RUN1/report", None),
    ("GET", "/v1/tasks/RUN1/logs", None),
    ("GET", "/v1/tasks/RUN1/returns", None),
    ("GET", "/v1/model-settings", None),
    ("PUT", "/v1/model-settings", {}),
    ("POST", "/v1/model-settings/{group_id}/models/refresh", None),
]


@pytest.mark.asyncio
async def test_internal_secret_required(tmp_path, monkeypatch):
    """Every /v1 route returns 403 without ``X-Internal-Secret``."""
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "test-secret")
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))

    from liveoption_web.main import create_app

    app = await create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        for method, path, body in V1_PROBE_ROUTES:
            if method == "GET":
                r = await c.get(path)
            elif method == "POST":
                r = await c.post(path, json=body or {})
            elif method == "PATCH":
                r = await c.patch(path, json=body or {})
            elif method == "PUT":
                r = await c.put(path, json=body or {})
            else:  # pragma: no cover - defensive
                raise AssertionError(f"unexpected method {method}")
            assert r.status_code == 403, (
                f"{method} {path} expected 403, got {r.status_code} "
                f"(body: {r.text[:200]})"
            )


@pytest.mark.asyncio
async def test_all_v1_routes_covered_by_probe(tmp_path, monkeypatch):
    """Guard: the V1_PROBE_ROUTES list stays in sync with the actual router.

    If a new /v1 route is added without a probe entry, this test fails
    so the secret-required drill doesn't silently miss it.
    """
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "test-secret")
    monkeypatch.setenv("LIVEOPTION_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LIVEOPTION_RUNS_DIR", str(tmp_path / "runs"))

    from liveoption_web.main import create_app

    app = await create_app()
    mounted: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/v1/") and path != "/v1":
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            mounted.add((m, path))

    probed = {(m, p) for m, p, _ in V1_PROBE_ROUTES}
    # Normalise probed paths back to the FastAPI-style templates by
    # replacing the placeholder values we used (RUN1, CK1, etc.) with
    # their original ``{param}`` form.
    placeholder_map = {
        "RUN1": "{run_id}",
        "RUN2": "{b}",
        "CK1": "{checkpoint_id}",
        "Q1": "{question_id}",
        "DEC1": "{decision_id}",
    }
    # Some routes have multi-param templates we need to special-case.
    multi_param_overrides = {
        ("GET", "/v1/runs/RUN1/compare/RUN2"): ("GET", "/v1/runs/{a}/compare/{b}"),
        ("GET", "/v1/audit/targets/run/RUN1"): (
            "GET",
            "/v1/audit/targets/{target_type}/{target_id}",
        ),
    }
    normalised: set[tuple[str, str]] = set()
    for method, path in probed:
        if (method, path) in multi_param_overrides:
            normalised.add(multi_param_overrides[(method, path)])
            continue
        norm = path
        for v, tpl in placeholder_map.items():
            norm = norm.replace(v, tpl)
        normalised.add((method, norm))

    missing = mounted - normalised
    assert not missing, (
        "New /v1 routes without a V1_PROBE_ROUTES entry — add them so "
        f"test_internal_secret_required stays exhaustive: {sorted(missing)}"
    )


def test_loopback_only_bind(monkeypatch):
    """Default ``Settings.host`` is loopback-only (127.0.0.1).

    The qlib deployment relies on the Job Server never binding to a
    public interface; a regression here would expose the API to the
    CityU network.
    """
    monkeypatch.setenv("LIVEOPTION_INTERNAL_SECRET", "x")
    # Clear any host override a developer shell may have set.
    monkeypatch.delenv("LIVEOPTION_HOST", raising=False)
    s = Settings()
    assert s.host == "127.0.0.1"


# ---------------------------------------------------------------------------
# Manual-only drill items (documented; not automated)
# ---------------------------------------------------------------------------
#
# ``test_tunnel_drop_sse_resumes_no_loss`` is intentionally omitted: it
# requires a real autossh tunnel + a long-lived SSE client and is part
# of the launch-gate manual drill (Spec 8.7 "Event durability & SSE").
