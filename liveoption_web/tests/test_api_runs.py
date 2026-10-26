"""Tests for the ``/v1/runs`` HTTP surface (Task 8).

Covers list filters + pagination, detail, patch, timeline, pnl,
children, and RFC 7807 404 shape.  Seeding is done directly against
SQLite via ``open_db`` — Task 6 (ingest) owns the HTTP insertion path;
this module only exercises reads and partial updates.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from liveoption_web.db.connection import open_db

HDRS = {"X-Internal-Secret": "test-secret"}


# ---------------------------------------------------------------------------
# Low-level seeders
# ---------------------------------------------------------------------------


async def _insert_run(
    db_path,
    *,
    id: str,
    scenario: str = "weekly_llm",
    kind: str = "batch",
    status: str = "running",
    parent_run_id: str | None = None,
    is_featured: int = 0,
    created_by: str | None = None,
    title: str | None = None,
    universe: list | None = None,
    metrics: dict | None = None,
) -> None:
    async with open_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO runs (
                id, kind, parent_run_id, scenario, config_path, model_id,
                provider, universe_json, date_start, date_end, status,
                status_updated_at, created_at, created_by, title,
                is_featured, storage_dir
            ) VALUES (
                ?, ?, ?, ?, 'configs/x.yaml', 'gpt-5', 'openai',
                ?, '2026-01-01', '2026-01-05', ?,
                '2026-04-18T00:00:00.000Z', '2026-04-18T00:00:00.000Z',
                ?, ?, ?, '/data/runs/x'
            )
            """,
            (
                id,
                kind,
                parent_run_id,
                scenario,
                json.dumps(universe) if universe is not None else None,
                status,
                created_by,
                title,
                is_featured,
            ),
        )
        if metrics is not None:
            await db.execute(
                """
                INSERT INTO run_metrics (
                    run_id, family, generic_json, family_json,
                    behavioral_json, computed_at, evaluator_version
                ) VALUES (?, NULL, ?, NULL, '{}',
                          '2026-04-18T00:00:00.000Z', 'v0-migrated')
                """,
                (id, json.dumps(metrics)),
            )
        await db.commit()


async def _insert_decision(
    db_path,
    *,
    id: str,
    run_id: str,
    seq: int,
    trading_day: str,
    has_trade: int = 1,
    cum_pnl_after: float | None = None,
) -> None:
    async with open_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO decision_records (
                id, run_id, seq, trading_day, decision_ts,
                model_id, provider, prompt_sha256, response_sha256,
                storage_dir, has_trade, cum_pnl_after, created_at
            ) VALUES (
                ?, ?, ?, ?, '2026-01-02T10:00:00-05:00',
                'gpt-5', 'openai', 'a'*64, 'b'*64,
                '/data/dr', ?, ?, '2026-04-18T00:00:00.000Z'
            )
            """,
            (id, run_id, seq, trading_day, has_trade, cum_pnl_after),
        )
        await db.commit()


async def _insert_event(
    db_path, *, run_id: str, seq: int, kind: str, payload: dict
) -> None:
    async with open_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO events (run_id, subprocess_seq, ts, kind, payload_json)
            VALUES (?, ?, '2026-04-18T00:00:00.000Z', ?, ?)
            """,
            (run_id, seq, kind, json.dumps(payload)),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _db_path_from_client(client) -> str:
    return client._transport.app.state.settings.db_path


@pytest_asyncio.fixture
async def seed_runs(client):
    """3 weekly_llm + 2 overlay_vol runs, descending ids."""

    db = _db_path_from_client(client)
    # Insert oldest → newest so descending id ordering is deterministic.
    await _insert_run(db, id="01HRUN1", scenario="weekly_llm", title="first")
    await _insert_run(db, id="01HRUN2", scenario="weekly_llm")
    await _insert_run(db, id="01HRUN3", scenario="weekly_llm")
    await _insert_run(db, id="01HRUN4", scenario="overlay_vol")
    await _insert_run(db, id="01HRUN5", scenario="overlay_vol", is_featured=1)
    return db


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_requires_auth(client):
    r = await client.get("/v1/runs")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_runs_empty(client):
    r = await client.get("/v1/runs", headers=HDRS)
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_list_runs_filters_by_scenario(client, seed_runs):
    r = await client.get("/v1/runs?scenario=weekly_llm", headers=HDRS)
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 3
    assert all(x["scenario"] == "weekly_llm" for x in body["items"])


@pytest.mark.asyncio
async def test_list_runs_filters_by_kind_status_featured(client, seed_runs):
    r = await client.get("/v1/runs?featured=true", headers=HDRS)
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "01HRUN5"
    assert items[0]["is_featured"] is True


@pytest.mark.asyncio
async def test_list_runs_pagination(client, seed_runs):
    r = await client.get("/v1/runs?limit=2", headers=HDRS)
    body = r.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["id"] == "01HRUN5"
    assert body["items"][1]["id"] == "01HRUN4"
    assert body["next_cursor"] == "01HRUN4"

    r2 = await client.get(
        f"/v1/runs?limit=2&cursor={body['next_cursor']}", headers=HDRS
    )
    body2 = r2.json()
    assert [x["id"] for x in body2["items"]] == ["01HRUN3", "01HRUN2"]
    assert body2["next_cursor"] == "01HRUN2"


@pytest.mark.asyncio
async def test_list_runs_filter_parent(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HPAR", scenario="weekly_llm")
    await _insert_run(
        db, id="01HCHD1", kind="fork", parent_run_id="01HPAR", scenario="weekly_llm"
    )
    await _insert_run(
        db, id="01HCHD2", kind="fork", parent_run_id="01HPAR", scenario="weekly_llm"
    )

    r = await client.get("/v1/runs?parent_run_id=01HPAR", headers=HDRS)
    ids = {x["id"] for x in r.json()["items"]}
    assert ids == {"01HCHD1", "01HCHD2"}


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_not_found(client):
    r = await client.get("/v1/runs/nonexistent", headers=HDRS)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "run-not-found"
    assert body["status"] == 404


@pytest.mark.asyncio
async def test_get_run_parses_universe_and_omits_metrics(client):
    """Detail no longer exposes metrics; universe still parses."""
    db = _db_path_from_client(client)
    await _insert_run(
        db,
        id="01HRUN_JSON",
        universe=["AAPL", "MSFT"],
        metrics={"sharpe": 1.23},
    )
    r = await client.get("/v1/runs/01HRUN_JSON", headers=HDRS)
    assert r.status_code == 200
    body = r.json()
    assert body["universe"] == ["AAPL", "MSFT"]
    assert body["is_featured"] is False
    # Metrics moved to a dedicated endpoint; raw *_json keys must not leak.
    assert "metrics" not in body
    assert "universe_json" not in body
    assert "metrics_json" not in body


@pytest.mark.asyncio
async def test_get_run_metrics_endpoint(client):
    """``GET /v1/runs/{id}/metrics`` parses run_metrics JSON columns.

    Phase C: a 'v0-migrated' evaluator_version mismatches the current
    evaluator and triggers a synchronous recompute (spec §5.4). The
    response carries the freshly-computed block, not the migrated blob.
    """
    db = _db_path_from_client(client)
    await _insert_run(
        db,
        id="01HRUN_METRICS",
        metrics={"sharpe": 1.23, "cum_pnl": 500.0},
    )
    r = await client.get("/v1/runs/01HRUN_METRICS/metrics", headers=HDRS)
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "01HRUN_METRICS"
    assert body["family"] is None
    # No events were inserted, so generic_stats returns the zero-shape.
    assert "annualized_return" in body["generic"]
    assert body["generic"]["n_days"] == 0
    assert body["family_specific"] is None
    # behavioral block is now populated (zeros for placeholder fields).
    assert "intent_consistency" in body["behavioral"]
    # Evaluator version reflects current code, not 'v0-migrated'.
    assert body["evaluator_version"] != "v0-migrated"


@pytest.mark.asyncio
async def test_get_run_metrics_not_found(client):
    """Missing run_metrics row returns RFC 7807 metrics-not-found 404."""
    db = _db_path_from_client(client)
    # Run exists but has no run_metrics row.
    await _insert_run(db, id="01HRUN_NOMETRIC")
    r = await client.get("/v1/runs/01HRUN_NOMETRIC/metrics", headers=HDRS)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "metrics-not-found"
    assert body["status"] == 404


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_run_title(client, seed_runs):
    r = await client.patch(
        "/v1/runs/01HRUN1",
        headers=HDRS,
        json={"title": "New title", "is_featured": True},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r2 = await client.get("/v1/runs/01HRUN1", headers=HDRS)
    body = r2.json()
    assert body["title"] == "New title"
    assert body["is_featured"] is True


@pytest.mark.asyncio
async def test_patch_run_notes_only(client, seed_runs):
    r = await client.patch(
        "/v1/runs/01HRUN2", headers=HDRS, json={"notes": "hello"}
    )
    assert r.status_code == 200
    r2 = await client.get("/v1/runs/01HRUN2", headers=HDRS)
    assert r2.json()["notes"] == "hello"
    # untouched fields stay None / default
    assert r2.json()["title"] is None
    assert r2.json()["is_featured"] is False


@pytest.mark.asyncio
async def test_patch_run_not_found(client):
    r = await client.patch(
        "/v1/runs/nope", headers=HDRS, json={"title": "x"}
    )
    assert r.status_code == 404
    assert r.json()["type"] == "run-not-found"


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_ordered_by_seq(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HTL")
    # Insert out-of-order; API must sort ascending by seq.
    await _insert_decision(
        db, id="d3", run_id="01HTL", seq=3, trading_day="2026-01-03"
    )
    await _insert_decision(
        db, id="d1", run_id="01HTL", seq=1, trading_day="2026-01-01"
    )
    await _insert_decision(
        db, id="d2", run_id="01HTL", seq=2, trading_day="2026-01-02",
        has_trade=0,
    )

    r = await client.get("/v1/runs/01HTL/timeline", headers=HDRS)
    assert r.status_code == 200
    items = r.json()["items"]
    assert [x["seq"] for x in items] == [1, 2, 3]
    # has_trade must round-trip as a real bool.
    assert items[0]["has_trade"] is True
    assert items[1]["has_trade"] is False


@pytest.mark.asyncio
async def test_timeline_run_not_found(client):
    r = await client.get("/v1/runs/nope/timeline", headers=HDRS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PnL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_series_from_day_completed_events(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HPNL")
    await _insert_event(
        db,
        run_id="01HPNL",
        seq=1,
        kind="day.completed",
        payload={
            "trading_day": "2026-01-02",
            "cum_pnl": 100.0,
            "portfolio_value": 100100.0,
        },
    )
    # Non-day.completed events must be ignored.
    await _insert_event(
        db, run_id="01HPNL", seq=2, kind="heartbeat", payload={"x": 1}
    )
    await _insert_event(
        db,
        run_id="01HPNL",
        seq=3,
        kind="day.completed",
        payload={
            "trading_day": "2026-01-03",
            "cum_pnl": 250.0,
            "portfolio_value": 100250.0,
        },
    )

    r = await client.get("/v1/runs/01HPNL/pnl", headers=HDRS)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert items[0] == {
        "trading_day": "2026-01-02",
        "cum_pnl": 100.0,
        "portfolio_value": 100100.0,
    }
    assert items[1]["cum_pnl"] == 250.0


@pytest.mark.asyncio
async def test_pnl_run_not_found(client):
    r = await client.get("/v1/runs/nope/pnl", headers=HDRS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Children
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_children_lists_forks(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HPARENT")
    await _insert_run(
        db, id="01HCHILD1", kind="fork", parent_run_id="01HPARENT"
    )
    await _insert_run(
        db, id="01HCHILD2", kind="fork", parent_run_id="01HPARENT"
    )
    # Unrelated run should NOT appear.
    await _insert_run(db, id="01HOTHER")

    r = await client.get("/v1/runs/01HPARENT/children", headers=HDRS)
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()["items"]]
    assert ids == ["01HCHILD2", "01HCHILD1"]  # DESC by id


@pytest.mark.asyncio
async def test_children_run_not_found(client):
    r = await client.get("/v1/runs/nope/children", headers=HDRS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_requires_auth(client):
    r = await client.get("/v1/runs/a/compare/b")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_compare_run_a_not_found(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HB")
    r = await client.get("/v1/runs/nope/compare/01HB", headers=HDRS)
    assert r.status_code == 404
    assert r.json()["type"] == "run-not-found"


@pytest.mark.asyncio
async def test_compare_run_b_not_found(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HA")
    r = await client.get("/v1/runs/01HA/compare/nope", headers=HDRS)
    assert r.status_code == 404
    assert r.json()["type"] == "run-not-found"


@pytest.mark.asyncio
async def test_compare_returns_metrics_days_and_decisions(client):
    db = _db_path_from_client(client)
    await _insert_run(
        db, id="01HA", metrics={"sharpe": 1.5, "cum_pnl": 500.0}
    )
    await _insert_run(
        db, id="01HB", metrics={"sharpe": 0.9, "cum_pnl": 200.0}
    )

    # Overlapping + disjoint trading days to exercise alignment.
    await _insert_event(
        db, run_id="01HA", seq=1, kind="day.completed",
        payload={"trading_day": "2026-01-02", "cum_pnl": 100.0, "portfolio_value": 100100.0},
    )
    await _insert_event(
        db, run_id="01HA", seq=2, kind="day.completed",
        payload={"trading_day": "2026-01-03", "cum_pnl": 200.0, "portfolio_value": 100200.0},
    )
    await _insert_event(
        db, run_id="01HB", seq=1, kind="day.completed",
        payload={"trading_day": "2026-01-02", "cum_pnl": 50.0, "portfolio_value": 100050.0},
    )
    await _insert_event(
        db, run_id="01HB", seq=3, kind="day.completed",
        payload={"trading_day": "2026-01-04", "cum_pnl": 150.0, "portfolio_value": 100150.0},
    )

    # Decision records, varying seq overlap.
    await _insert_decision(
        db, id="da1", run_id="01HA", seq=1, trading_day="2026-01-02",
        cum_pnl_after=100.0,
    )
    await _insert_decision(
        db, id="da2", run_id="01HA", seq=2, trading_day="2026-01-03",
        cum_pnl_after=200.0,
    )
    await _insert_decision(
        db, id="db1", run_id="01HB", seq=1, trading_day="2026-01-02",
        cum_pnl_after=50.0, has_trade=0,
    )
    await _insert_decision(
        db, id="db3", run_id="01HB", seq=3, trading_day="2026-01-04",
        cum_pnl_after=150.0,
    )

    r = await client.get("/v1/runs/01HA/compare/01HB", headers=HDRS)
    assert r.status_code == 200
    body = r.json()

    assert body["a"]["id"] == "01HA"
    assert body["a"]["metrics"] == {"sharpe": 1.5, "cum_pnl": 500.0}
    assert body["b"]["id"] == "01HB"
    assert body["b"]["metrics"] == {"sharpe": 0.9, "cum_pnl": 200.0}

    days = body["days"]
    # Aligned by trading_day, sorted ascending, with None when missing.
    assert [d["trading_day"] for d in days] == [
        "2026-01-02", "2026-01-03", "2026-01-04",
    ]
    assert days[0]["a"]["cum_pnl"] == 100.0
    assert days[0]["b"]["cum_pnl"] == 50.0
    assert days[1]["a"]["cum_pnl"] == 200.0
    assert days[1]["b"] is None
    assert days[2]["a"] is None
    assert days[2]["b"]["cum_pnl"] == 150.0

    decisions = body["decisions"]
    # Aligned by seq ascending with None when missing.
    assert [d["seq"] for d in decisions] == [1, 2, 3]
    assert decisions[0]["a"]["id"] == "da1"
    assert decisions[0]["b"]["id"] == "db1"
    assert decisions[0]["a"]["has_trade"] is True
    assert decisions[0]["b"]["has_trade"] is False
    assert decisions[1]["a"]["id"] == "da2"
    assert decisions[1]["b"] is None
    assert decisions[2]["a"] is None
    assert decisions[2]["b"]["id"] == "db3"


@pytest.mark.asyncio
async def test_post_run_metrics_recompute_endpoint(client):
    """``POST /v1/runs/{id}/metrics:recompute`` re-aggregates and returns row."""
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HRECOMP")
    r = await client.post(
        "/v1/runs/01HRECOMP/metrics:recompute", headers=HDRS
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == "01HRECOMP"
    assert body["family"] is None
    assert "intent_consistency" in body["behavioral"]


@pytest.mark.asyncio
async def test_post_run_metrics_recompute_returns_404_for_unknown(client):
    r = await client.post(
        "/v1/runs/does-not-exist/metrics:recompute", headers=HDRS
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Phase C7 — same-family compare enforcement
# ---------------------------------------------------------------------------


async def _set_family(db_path, run_id: str, family: str | None) -> None:
    async with open_db(db_path) as db:
        await db.execute(
            "UPDATE runs SET family=? WHERE id=?", (family, run_id)
        )
        await db.commit()


@pytest.mark.asyncio
async def test_compare_same_family_returns_200(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HSAMEA")
    await _insert_run(db, id="01HSAMEB")
    await _set_family(db, "01HSAMEA", "intraday")
    await _set_family(db, "01HSAMEB", "intraday")
    r = await client.get("/v1/runs/01HSAMEA/compare/01HSAMEB", headers=HDRS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["a"]["family"] == "intraday"
    assert body["b"]["family"] == "intraday"


@pytest.mark.asyncio
async def test_compare_cross_family_returns_400(client):
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HCROSSA")
    await _insert_run(db, id="01HCROSSB")
    await _set_family(db, "01HCROSSA", "overlay")
    await _set_family(db, "01HCROSSB", "earnings")
    r = await client.get("/v1/runs/01HCROSSA/compare/01HCROSSB", headers=HDRS)
    assert r.status_code == 400
    body = r.json()
    assert body["type"] == "cross-family-compare-not-supported"


@pytest.mark.asyncio
async def test_compare_null_vs_null_is_allowed(client):
    """Two null-family runs compare without 400."""
    db = _db_path_from_client(client)
    await _insert_run(db, id="01HNULLA")
    await _insert_run(db, id="01HNULLB")
    # both family stays NULL
    r = await client.get("/v1/runs/01HNULLA/compare/01HNULLB", headers=HDRS)
    assert r.status_code == 200, r.text
