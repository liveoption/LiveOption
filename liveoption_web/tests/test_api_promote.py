"""Tests for Task 10: /v1/decisions/:id/promote + crash-safe promotion.

Validates:
* Promotion copies the decision artifact into a checkpoint directory.
* Resulting DB state: checkpoint row with status='ready',
  decision_records.promoted_checkpoint_id back-link set,
  audit_log row with action='checkpoint.promote'.
* Artifact finalization is crash-safe: files are written to a tmp dir
  and ``os.rename``d into place (no partial ``ckpt_<id>`` dirs remain
  if promotion succeeds).
* Idempotency: promoting the same decision a second time returns 409.
* 404 when the decision does not exist.
* 403 without the internal secret.

The real promotion protocol invokes ``python -m trading.cli
capture-checkpoint`` which is out of scope for this task; tests
monkeypatch the subprocess hook to write a stub ``runner_checkpoint.json``
instead (see Step 4 in the plan).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from liveoption_web.db.connection import open_db


async def _seed_run_and_decision(
    db_path: Path,
    runs_dir: Path,
    run_id: str = "01HRUN",
    decision_id: str = "01HDEC",
) -> None:
    """Seed a run + decision_record and lay down the decision artifact.

    Creates ``<runs_dir>/<run_id>/decisions/<decision_id>/{record,market,
    context}.json`` plus the matching DB rows so ``promote`` can succeed.
    """
    run_dir = runs_dir / run_id
    decision_dir = run_dir / "decisions" / decision_id
    decision_dir.mkdir(parents=True, exist_ok=True)
    (decision_dir / "record.json").write_text(
        json.dumps({"id": decision_id, "trade": {"symbol": "SPY"}})
    )
    (decision_dir / "market.json").write_text(json.dumps({"spot": 500.0}))
    (decision_dir / "context.json").write_text(json.dumps({"prompt": "why"}))

    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id,
                                provider, date_start, date_end, status,
                                status_updated_at, created_at, storage_dir)
               VALUES(?, 'batch', 'weekly_llm', 'x.yaml', 'gpt-4', 'openai',
                      '2025-01-01', '2025-01-31', 'running',
                      '2026-04-18T10:00:00Z', '2026-04-18T10:00:00Z', ?)""",
            (run_id, str(run_dir)),
        )
        await db.execute(
            """INSERT INTO decision_records(id, run_id, seq, trading_day,
                                            decision_ts, model_id, provider,
                                            prompt_sha256, response_sha256,
                                            storage_dir, has_trade,
                                            created_at)
               VALUES(?, ?, 1, '2025-01-02',
                      '2025-01-02T10:00:00-05:00', 'gpt-4', 'openai',
                      'aa', 'bb', ?, 1, '2026-04-18T10:00:01Z')""",
            (decision_id, run_id, str(decision_dir)),
        )
        await db.commit()


def _fake_capture_checkpoint(cmd, timeout=None):
    """Replace trading.cli capture-checkpoint with a stub that writes JSON."""
    # cmd layout: ["python","-m","trading.cli","capture-checkpoint",
    #              "--run-id", r, "--decision-id", d,
    #              "--output-path", path]
    idx = cmd.index("--output-path")
    out = Path(cmd[idx + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"runner_state": "stub"}))
    return 0


@pytest.mark.asyncio
async def test_promote_requires_internal_secret(client):
    r = await client.post("/v1/decisions/01HDEC/promote")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_promote_unknown_decision_returns_404(client, monkeypatch):
    from liveoption_web.checkpoint import promote as promote_mod

    monkeypatch.setattr(promote_mod.subprocess, "check_call", _fake_capture_checkpoint)
    r = await client.post(
        "/v1/decisions/does-not-exist/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_promote_copies_decision_artifact_and_writes_db(client, monkeypatch):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)

    from liveoption_web.checkpoint import promote as promote_mod

    monkeypatch.setattr(promote_mod.subprocess, "check_call", _fake_capture_checkpoint)

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    ckpt_id = body["id"]
    assert body["status"] == "ready"

    # Files on disk: final checkpoint dir exists, tmp dir removed.
    ckpt_dir = runs_dir / "01HRUN" / "checkpoints" / ckpt_id
    assert ckpt_dir.is_dir()
    assert (ckpt_dir / "record.json").read_text()
    assert (ckpt_dir / "market.json").read_text()
    assert (ckpt_dir / "context.json").read_text()
    assert (ckpt_dir / "runner_checkpoint.json").read_text()
    meta = json.loads((ckpt_dir / "metadata.json").read_text())
    assert meta["checkpoint_id"] == ckpt_id
    assert meta["decision_id"] == "01HDEC"
    # No leftover tmp dirs.
    siblings = list((runs_dir / "01HRUN" / "checkpoints").iterdir())
    assert all(not s.name.endswith(".tmp") for s in siblings)

    # DB state.
    async with open_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT status, decision_record_id, promoted_by FROM checkpoints WHERE id=?",
                (ckpt_id,),
            )
        ).fetchone()
        assert row == ("ready", "01HDEC", "alice")

        dr = await (
            await db.execute(
                "SELECT promoted_checkpoint_id FROM decision_records WHERE id=?",
                ("01HDEC",),
            )
        ).fetchone()
        assert dr[0] == ckpt_id

        audit = await (
            await db.execute(
                """SELECT actor_user_id, action, target_type, target_id
                   FROM audit_log WHERE target_id=? AND action='checkpoint.promote'""",
                (ckpt_id,),
            )
        ).fetchall()
        assert len(audit) == 1
        assert audit[0] == ("alice", "checkpoint.promote", "checkpoint", ckpt_id)


@pytest.mark.asyncio
async def test_promote_idempotent_returns_409_on_second_call(client, monkeypatch):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)

    from liveoption_web.checkpoint import promote as promote_mod

    monkeypatch.setattr(promote_mod.subprocess, "check_call", _fake_capture_checkpoint)

    r1 = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r2.status_code == 409
    # RFC 7807 body.
    body = r2.json()
    assert body["type"] == "already-promoted"
    assert body["status"] == 409


@pytest.mark.asyncio
async def test_promote_inserts_writing_row_before_finalize(client, monkeypatch):
    """Spec 3.6 state machine: ``status='writing'`` row exists before
    the tmp→final rename, so a crash leaves reconcile a breadcrumb."""
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)

    from liveoption_web.checkpoint import promote as promote_mod

    observed: list[tuple[str, bool]] = []

    # Inspect DB synchronously inside the check_call stub.
    def spy(cmd, timeout=None):
        import sqlite3

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT status FROM checkpoints WHERE decision_record_id=?",
                ("01HDEC",),
            ).fetchone()
        finally:
            conn.close()
        ckroot = runs_dir / "01HRUN" / "checkpoints"
        tmp_present = any(p.name.endswith(".tmp") for p in ckroot.iterdir())
        observed.append((row[0] if row else None, tmp_present))
        _fake_capture_checkpoint(cmd, timeout=timeout)

    monkeypatch.setattr(promote_mod.subprocess, "check_call", spy)

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 201, r.text
    # Captured state: writing row was present AND tmp dir existed mid-flight.
    assert observed == [("writing", True)]


@pytest.mark.asyncio
async def test_promote_crash_before_rename_leaves_writing_row_for_reconcile(
    client, monkeypatch, tmp_path
):
    """If the artifact finalization crashes before ``os.rename`` the
    ``writing`` row stays behind; reconcile cleans the tmp dir and
    removes the row so a retry promote can succeed."""
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)

    from liveoption_web.checkpoint import promote as promote_mod

    def boom(cmd, timeout=None):
        # Simulate crash after copytree, before metadata write / rename.
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(promote_mod.subprocess, "check_call", boom)

    # Call promote_decision directly: the RuntimeError should propagate
    # through ASGITransport, so drive the core API instead of the HTTP
    # layer to observe the post-crash DB state.
    with pytest.raises(RuntimeError, match="simulated crash"):
        await promote_mod.promote_decision(
            db_path=db_path,
            runs_dir=runs_dir,
            decision_id="01HDEC",
            actor_user_id="alice",
        )

    # Row is in 'writing' state; tmp dir already cleaned up by the except block.
    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT id, status, storage_dir FROM checkpoints "
            "WHERE decision_record_id=?",
            ("01HDEC",),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[1] == "writing"
    ckpt_id, _, storage_dir = row
    final_dir = Path(storage_dir)
    assert not final_dir.exists()

    # Now simulate reconcile: it should drop the stale 'writing' row
    # since the final dir never materialized.
    from liveoption_web.config import get_settings
    from liveoption_web.events.reconcile import reconcile_on_startup

    await reconcile_on_startup(get_settings())

    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT id FROM checkpoints WHERE decision_record_id=?", ("01HDEC",)
        )
        assert await cur.fetchone() is None

    # Retry promote now succeeds and produces a 'ready' checkpoint.
    def ok(cmd, timeout=None):
        _fake_capture_checkpoint(cmd, timeout=timeout)

    monkeypatch.setattr(promote_mod.subprocess, "check_call", ok)
    r2 = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_promote_retry_when_writing_row_has_no_artifact(client, monkeypatch):
    """Idempotent retry: if a stale ``writing`` row exists but no artifact
    is on disk, promote drops the row and retries cleanly."""
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)

    # Pre-seed a stale writing row pointing at a non-existent dir.
    stale_final = runs_dir / "01HRUN" / "checkpoints" / "01HSTALE"
    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO checkpoints(
                    id, run_id, decision_record_id, storage_dir, fingerprint,
                    portfolio_summary_json, promoted_at, promoted_by, status,
                    status_updated_at, label)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "01HSTALE",
                "01HRUN",
                "01HDEC",
                str(stale_final),
                "fp",
                "{}",
                "2026-04-18T10:00:00Z",
                "alice",
                "writing",
                "2026-04-18T10:00:00Z",
                None,
            ),
        )
        await db.commit()

    from liveoption_web.checkpoint import promote as promote_mod

    monkeypatch.setattr(promote_mod.subprocess, "check_call", _fake_capture_checkpoint)

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert new_id != "01HSTALE"

    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT id, status FROM checkpoints WHERE decision_record_id=?",
            ("01HDEC",),
        )
        rows = await cur.fetchall()
    # Stale row gone, new 'ready' row present.
    assert len(rows) == 1
    assert rows[0] == (new_id, "ready")


@pytest.mark.asyncio
async def test_promote_returns_409_when_writing_row_has_artifact(client, monkeypatch):
    """Idempotent retry guard: if a stale ``writing`` row exists AND the
    artifact dir is present on disk, refuse with 409 rather than clobber."""
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)

    stale_final = runs_dir / "01HRUN" / "checkpoints" / "01HKEEP"
    stale_final.mkdir(parents=True)
    (stale_final / "metadata.json").write_text("{}")

    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO checkpoints(
                    id, run_id, decision_record_id, storage_dir, fingerprint,
                    portfolio_summary_json, promoted_at, promoted_by, status,
                    status_updated_at, label)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "01HKEEP",
                "01HRUN",
                "01HDEC",
                str(stale_final),
                "fp",
                "{}",
                "2026-04-18T10:00:00Z",
                "alice",
                "writing",
                "2026-04-18T10:00:00Z",
                None,
            ),
        )
        await db.commit()

    from liveoption_web.checkpoint import promote as promote_mod

    monkeypatch.setattr(promote_mod.subprocess, "check_call", _fake_capture_checkpoint)

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Phase C5 — promote persists portfolio_summary_json from CapturedCheckpoint
# ---------------------------------------------------------------------------


def _make_captured_writer(family_view_payload: dict | None):
    """Return a stub `subprocess.check_call` that writes a CapturedCheckpoint."""

    def _writer(cmd, timeout=None):
        idx = cmd.index("--output-path")
        out = Path(cmd[idx + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        run_idx = cmd.index("--run-id")
        dec_idx = cmd.index("--decision-id")
        envelope = {
            "schema_version": "1",
            "run_id": cmd[run_idx + 1],
            "decision_id": cmd[dec_idx + 1],
            "captured_at": "2025-01-15T14:30:00Z",
            "generic": {
                "cash": 100000.0,
                "positions": [],
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "gross_exposure": 0.0,
                "net_delta": 0.0,
            },
            "family_view": family_view_payload,
            "runner_state": {"x": 1},
        }
        out.write_text(json.dumps(envelope))
        return 0

    return _writer


async def _set_run_family(db_path: Path, run_id: str, family: str | None) -> None:
    async with open_db(db_path) as db:
        await db.execute(
            "UPDATE runs SET family=? WHERE id=?", (family, run_id)
        )
        await db.commit()


@pytest.mark.asyncio
async def test_promote_persists_overlay_family_view(client, monkeypatch):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)
    await _set_run_family(db_path, "01HRUN", "overlay")

    from liveoption_web.checkpoint import promote as promote_mod

    overlay_view = {
        "kind": "overlay",
        "underlying_holdings": [{"symbol": "SPY", "shares": 100}],
        "overlay_legs": [],
        "delta_hedge_status": {"net": 0.0},
    }
    monkeypatch.setattr(
        promote_mod.subprocess,
        "check_call",
        _make_captured_writer(overlay_view),
    )

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 201, r.text
    ckpt_id = r.json()["id"]

    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT portfolio_summary_json FROM checkpoints WHERE id=?",
            (ckpt_id,),
        )
        row = await cur.fetchone()
    assert row is not None and row[0] is not None
    summary = json.loads(row[0])
    assert summary["family"] == "overlay"
    assert summary["family_view"]["kind"] == "overlay"
    assert summary["family_view"]["underlying_holdings"][0]["symbol"] == "SPY"
    assert summary["generic"]["cash"] == 100000.0


@pytest.mark.asyncio
async def test_promote_persists_earnings_family_view(client, monkeypatch):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)
    await _set_run_family(db_path, "01HRUN", "earnings")

    from liveoption_web.checkpoint import promote as promote_mod

    earnings_view = {
        "kind": "earnings",
        "active_event_windows": [
            {"ticker": "AAPL", "phase": "in_window"}
        ],
        "pending_events": [],
        "post_event_realized": [],
    }
    monkeypatch.setattr(
        promote_mod.subprocess,
        "check_call",
        _make_captured_writer(earnings_view),
    )

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 201, r.text
    ckpt_id = r.json()["id"]
    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT portfolio_summary_json FROM checkpoints WHERE id=?",
            (ckpt_id,),
        )
        row = await cur.fetchone()
    summary = json.loads(row[0])
    assert summary["family"] == "earnings"
    assert summary["family_view"]["kind"] == "earnings"


@pytest.mark.asyncio
async def test_promote_persists_intraday_family_view(client, monkeypatch):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)
    await _set_run_family(db_path, "01HRUN", "intraday")

    from liveoption_web.checkpoint import promote as promote_mod

    intraday_view = {
        "kind": "intraday",
        "minutes_to_close": 90,
        "same_day_open_count": 3,
        "gamma_exposure": 12.5,
        "intraday_realized_today": -42.0,
    }
    monkeypatch.setattr(
        promote_mod.subprocess,
        "check_call",
        _make_captured_writer(intraday_view),
    )

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 201, r.text
    ckpt_id = r.json()["id"]
    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT portfolio_summary_json FROM checkpoints WHERE id=?",
            (ckpt_id,),
        )
        row = await cur.fetchone()
    summary = json.loads(row[0])
    assert summary["family"] == "intraday"
    assert summary["family_view"]["kind"] == "intraday"
    assert summary["family_view"]["minutes_to_close"] == 90


@pytest.mark.asyncio
async def test_promote_null_family_view_for_null_family(client, monkeypatch):
    db_path = Path(os.environ["LIVEOPTION_DB_PATH"])
    runs_dir = Path(os.environ["LIVEOPTION_RUNS_DIR"])
    await _seed_run_and_decision(db_path, runs_dir)
    # run.family stays NULL.
    from liveoption_web.checkpoint import promote as promote_mod

    monkeypatch.setattr(
        promote_mod.subprocess,
        "check_call",
        _make_captured_writer(None),
    )

    r = await client.post(
        "/v1/decisions/01HDEC/promote",
        headers={"X-Internal-Secret": "test-secret", "X-Actor-User": "alice"},
    )
    assert r.status_code == 201, r.text
    ckpt_id = r.json()["id"]
    async with open_db(db_path) as db:
        cur = await db.execute(
            "SELECT portfolio_summary_json FROM checkpoints WHERE id=?",
            (ckpt_id,),
        )
        row = await cur.fetchone()
    summary = json.loads(row[0])
    assert summary["family"] is None
    assert summary["family_view"] is None
    assert summary["generic"]["cash"] == 100000.0
