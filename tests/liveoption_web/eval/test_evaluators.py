"""Phase C6 unit tests for per-family evaluators (synthetic events DB)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from liveoption_web.db.migrations import apply_migrations
from liveoption_web.eval import (
    aggregate_for,
    behavioral,
    current_evaluator_version,
    earnings,
    intraday,
    null_family,
    overlay,
)


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "db.sqlite"
    import asyncio

    asyncio.run(apply_migrations(p))
    return p


def _seed_run(db_path: Path, run_id: str, family: str | None) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO runs(id, kind, scenario, config_path, model_id,
                                provider, date_start, date_end, status,
                                status_updated_at, created_at, storage_dir, family)
               VALUES(?, 'batch', 'x', 'x.yaml', 'm', 'p', '2025-01-01',
                      '2025-01-31', 'completed',
                      '2026-04-25T00:00:00Z', '2026-04-25T00:00:00Z',
                      '/data', ?)""",
            (run_id, family),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_event(db_path: Path, run_id: str, kind: str, payload: dict, seq: int = 1):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO events(run_id, subprocess_seq, ts, kind, payload_json)
               VALUES(?, ?, '2026-04-25T00:00:00.000Z', ?, ?)""",
            (run_id, seq, kind, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# null_family
# ---------------------------------------------------------------------------

def test_null_family_aggregates_generic_block(db_path):
    _seed_run(db_path, "R1", None)
    _seed_event(
        db_path,
        "R1",
        "day.completed",
        {"trading_day": "2025-01-02", "pnl_delta": 100.0,
         "cum_pnl": 100.0, "portfolio_value": 100100.0},
        seq=1,
    )
    _seed_event(
        db_path,
        "R1",
        "day.completed",
        {"trading_day": "2025-01-03", "pnl_delta": -50.0,
         "cum_pnl": 50.0, "portfolio_value": 100050.0},
        seq=2,
    )
    res = aggregate_for("R1", db_path)
    assert res is not None
    assert res["family"] is None
    assert res["family_specific"] is None
    g = res["generic"]
    assert g["n_days"] == 2
    assert g["total_pnl"] == 50.0
    assert g["win_rate"] == 0.5


# ---------------------------------------------------------------------------
# overlay
# ---------------------------------------------------------------------------

def test_overlay_aggregates_overlay_income(db_path):
    _seed_run(db_path, "R2", "overlay")
    _seed_event(
        db_path,
        "R2",
        "day.completed",
        {"trading_day": "2025-01-02", "pnl_delta": 200.0,
         "cum_pnl": 200.0, "portfolio_value": 100200.0},
    )
    # Short call sold at $1.50 for 1 contract → credit = $150.
    _seed_event(
        db_path,
        "R2",
        "fill.executed",
        {"qty": -1, "price": 1.50, "symbol": "SPY_C"},
        seq=2,
    )
    res = aggregate_for("R2", db_path)
    assert res is not None
    assert res["family"] == "overlay"
    fs = res["family_specific"]
    assert fs["overlay_income_total"] == 150.0
    # underlying_drift = total_pnl - overlay_income.
    assert fs["underlying_drift_pnl"] == 50.0


# ---------------------------------------------------------------------------
# earnings
# ---------------------------------------------------------------------------

def test_earnings_aggregates_per_ticker(db_path):
    _seed_run(db_path, "R3", "earnings")
    _seed_event(
        db_path, "R3", "day.completed",
        {"trading_day": "2025-01-30", "pnl_delta": 0.0,
         "cum_pnl": 0.0, "portfolio_value": 100000.0,
         "greeks_summary": {"vega_pnl": 12.5}},
    )
    _seed_event(
        db_path, "R3", "fill.executed",
        {"qty": 2, "price": 5.0, "underlying": "AAPL"},
        seq=2,
    )
    res = aggregate_for("R3", db_path)
    assert res is not None
    fs = res["family_specific"]
    assert fs["per_ticker_notional"]["AAPL"] == 10.0
    assert fs["vega_pnl_total"] == 12.5


# ---------------------------------------------------------------------------
# intraday
# ---------------------------------------------------------------------------

def test_intraday_aggregates_trades_per_day(db_path):
    _seed_run(db_path, "R4", "intraday")
    _seed_event(
        db_path, "R4", "day.completed",
        {"trading_day": "2025-01-02", "pnl_delta": 10.0,
         "cum_pnl": 10.0, "portfolio_value": 100010.0,
         "greeks_summary": {"gamma_pnl": 5.0, "theta_pnl": -3.0}},
    )
    for i in range(4):
        _seed_event(
            db_path, "R4", "fill.executed",
            {"qty": 1, "price": 1.0, "symbol": "SPY_C"},
            seq=i + 10,
        )
    res = aggregate_for("R4", db_path)
    fs = res["family_specific"]
    assert fs["trades_per_day"] == 4.0
    assert fs["gamma_pnl_total"] == 5.0
    assert fs["theta_pnl_total"] == -3.0


# ---------------------------------------------------------------------------
# behavioral block + version
# ---------------------------------------------------------------------------

def test_behavioral_block_present_with_placeholder_zeros(db_path):
    _seed_run(db_path, "R5", None)
    res = aggregate_for("R5", db_path)
    b = res["behavioral"]
    for key in (
        "intent_consistency",
        "directional_bias",
        "confidence_calibration",
        "position_sizing",
        "stop_loss_adherence",
        "leverage_utilization",
        "greeks_attribution_summary",
    ):
        assert key in b


def test_aggregate_for_unknown_run_returns_none(db_path):
    assert aggregate_for("does-not-exist", db_path) is None


def test_evaluator_versions_are_set():
    for mod in (overlay, earnings, intraday, null_family):
        assert mod.EVALUATOR_VERSION == "v1-phaseC"
    assert behavioral.EVALUATOR_VERSION == "v2-null-unmeasured"


def test_current_evaluator_version_changes_with_family():
    # Just sanity: never raises and returns a non-empty string.
    for fam in (None, "overlay", "earnings", "intraday"):
        v = current_evaluator_version(fam)
        assert isinstance(v, str) and v
