import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trading.checkpoint.captured import (
    CapturedCheckpoint,
    GenericPortfolioSummary,
    OverlayView,
    EarningsView,
    IntradayView,
)


def _generic() -> GenericPortfolioSummary:
    return GenericPortfolioSummary(
        cash=87421.50,
        positions=[],
        realized_pnl=-1234.0,
        unrealized_pnl=540.2,
        gross_exposure=25000.0,
        net_delta=0.42,
    )


def test_captured_round_trip_no_family_view():
    ckpt = CapturedCheckpoint(
        schema_version="1",
        run_id="run_abc",
        decision_id="dec_xyz",
        captured_at=datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc),
        generic=_generic(),
        family_view=None,
        runner_state={"opaque": "blob"},
    )
    blob = ckpt.model_dump_json()
    parsed = CapturedCheckpoint.model_validate_json(blob)
    assert parsed.family_view is None
    assert parsed.runner_state == {"opaque": "blob"}


def test_captured_overlay_view_discriminator():
    ckpt = CapturedCheckpoint(
        schema_version="1",
        run_id="run_abc",
        decision_id="dec_xyz",
        captured_at=datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc),
        generic=_generic(),
        family_view=OverlayView(
            kind="overlay",
            underlying_holdings=[{"symbol": "SPY", "shares": 100, "cost_basis": 480.0, "current_mv": 50000.0}],
            overlay_legs=[{"contract": "SPY 480C 30d", "income_to_date": 320.0, "days_to_expiry": 14}],
            delta_hedge_status={"net_delta": 0.42, "target_delta": 0.0},
        ),
        runner_state={},
    )
    blob = ckpt.model_dump_json()
    parsed = CapturedCheckpoint.model_validate_json(blob)
    assert parsed.family_view.kind == "overlay"
    assert parsed.family_view.overlay_legs[0]["income_to_date"] == 320.0


def test_captured_earnings_view_discriminator():
    ckpt = CapturedCheckpoint(
        schema_version="1",
        run_id="run_abc",
        decision_id="dec_xyz",
        captured_at=datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc),
        generic=_generic(),
        family_view=EarningsView(
            kind="earnings",
            active_event_windows=[{"ticker": "AAPL", "earnings_date": "2025-01-30", "phase": "in_window", "vega_exposure": 312.0}],
            pending_events=[],
            post_event_realized=[],
        ),
        runner_state={},
    )
    parsed = CapturedCheckpoint.model_validate_json(ckpt.model_dump_json())
    assert parsed.family_view.kind == "earnings"


def test_captured_intraday_view_discriminator():
    ckpt = CapturedCheckpoint(
        schema_version="1",
        run_id="run_abc",
        decision_id="dec_xyz",
        captured_at=datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc),
        generic=_generic(),
        family_view=IntradayView(
            kind="intraday",
            minutes_to_close=42,
            same_day_open_count=3,
            gamma_exposure=120.5,
            intraday_realized_today=85.0,
        ),
        runner_state={},
    )
    parsed = CapturedCheckpoint.model_validate_json(ckpt.model_dump_json())
    assert parsed.family_view.kind == "intraday"
    assert parsed.family_view.minutes_to_close == 42


def test_captured_unknown_family_kind_rejected():
    with pytest.raises(ValidationError):
        CapturedCheckpoint.model_validate({
            "schema_version": "1",
            "run_id": "run_abc",
            "decision_id": "dec_xyz",
            "captured_at": "2025-01-15T14:30:00+00:00",
            "generic": _generic().model_dump(),
            "family_view": {"kind": "bogus"},
            "runner_state": {},
        })


def test_captured_schema_version_required():
    with pytest.raises(ValidationError):
        CapturedCheckpoint.model_validate({
            "run_id": "run_abc",
            "decision_id": "dec_xyz",
            "captured_at": "2025-01-15T14:30:00+00:00",
            "generic": _generic().model_dump(),
            "family_view": None,
            "runner_state": {},
        })
