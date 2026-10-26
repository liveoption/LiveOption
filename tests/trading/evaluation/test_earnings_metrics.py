"""Tests for earnings-specific evaluation metrics (DA, VEE, CP&L, WP&L)."""

import pytest

from trading.evaluation.earnings_metrics import (
    compute_earnings_metrics,
    compute_event_pnl_summary,
)


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_event(
    predicted: str = "up",
    actual: str = "up",
    pnl: float = 100.0,
    premium_paid: float = 500.0,
    vega_entry: float | None = 10.0,
    iv_change: float | None = -0.15,
    skipped: bool = False,
) -> dict:
    return {
        "predicted_direction": predicted,
        "actual_direction": actual,
        "pnl": pnl,
        "premium_paid": premium_paid,
        "vega_entry": vega_entry,
        "iv_change": iv_change,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Directional Accuracy (DA)
# ---------------------------------------------------------------------------


class TestDirectionalAccuracy:
    def test_all_correct(self):
        events = [
            _make_event("up", "up", pnl=200),
            _make_event("down", "down", pnl=150),
            _make_event("up", "up", pnl=300),
        ]
        result = compute_earnings_metrics(events)
        assert result["da"] == pytest.approx(1.0)
        assert result["n_correct"] == 3
        assert result["n_wrong"] == 0

    def test_all_wrong(self):
        events = [
            _make_event("up", "down", pnl=-200),
            _make_event("down", "up", pnl=-150),
        ]
        result = compute_earnings_metrics(events)
        assert result["da"] == pytest.approx(0.0)
        assert result["n_correct"] == 0
        assert result["n_wrong"] == 2

    def test_mixed(self):
        events = [
            _make_event("up", "up", pnl=200),
            _make_event("up", "down", pnl=-100),
            _make_event("down", "down", pnl=150),
            _make_event("down", "up", pnl=-50),
        ]
        result = compute_earnings_metrics(events)
        assert result["da"] == pytest.approx(0.5)
        assert result["n_correct"] == 2
        assert result["n_wrong"] == 2


# ---------------------------------------------------------------------------
# Vega Exposure at Entry (VEE)
# ---------------------------------------------------------------------------


class TestVEE:
    def test_basic_vee(self):
        """VEE = mean(|vega * iv_change| / premium_paid)."""
        events = [
            _make_event(vega_entry=10.0, iv_change=-0.20, premium_paid=500.0),
            _make_event(vega_entry=8.0, iv_change=-0.10, premium_paid=400.0),
        ]
        result = compute_earnings_metrics(events)
        # Event 1: |10 * -0.20| / 500 = 0.004
        # Event 2: |8 * -0.10| / 400 = 0.002
        # Mean: (0.004 + 0.002) / 2 = 0.003
        assert result["vee"] == pytest.approx(0.003)

    def test_vee_with_missing_data(self):
        """Events with None vega/iv_change are excluded from VEE calculation."""
        events = [
            _make_event(vega_entry=10.0, iv_change=-0.20, premium_paid=500.0),
            _make_event(vega_entry=None, iv_change=None, premium_paid=500.0),
        ]
        result = compute_earnings_metrics(events)
        # Only first event counted: |10 * -0.20| / 500 = 0.004
        assert result["vee"] == pytest.approx(0.004)

    def test_vee_zero_premium(self):
        """Zero premium events are excluded from VEE."""
        events = [
            _make_event(vega_entry=10.0, iv_change=-0.20, premium_paid=0.0),
        ]
        result = compute_earnings_metrics(events)
        assert result["vee"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Conditional P&L
# ---------------------------------------------------------------------------


class TestConditionalPnL:
    def test_cpl_correct_direction(self):
        """CP&L = E[P&L | direction correct]."""
        events = [
            _make_event("up", "up", pnl=200),
            _make_event("up", "up", pnl=400),
            _make_event("up", "down", pnl=-100),
        ]
        result = compute_earnings_metrics(events)
        assert result["cpl"] == pytest.approx(300.0)  # (200 + 400) / 2

    def test_wpl_wrong_direction(self):
        """WP&L = E[P&L | direction wrong]."""
        events = [
            _make_event("up", "up", pnl=200),
            _make_event("up", "down", pnl=-100),
            _make_event("down", "up", pnl=-300),
        ]
        result = compute_earnings_metrics(events)
        assert result["wpl"] == pytest.approx(-200.0)  # (-100 + -300) / 2

    def test_no_wrong_predictions(self):
        """All correct => WP&L = 0."""
        events = [_make_event("up", "up", pnl=500)]
        result = compute_earnings_metrics(events)
        assert result["cpl"] == pytest.approx(500.0)
        assert result["wpl"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Skip rate
# ---------------------------------------------------------------------------


class TestSkipRate:
    def test_no_skips(self):
        events = [_make_event(), _make_event()]
        result = compute_earnings_metrics(events)
        assert result["skip_rate"] == pytest.approx(0.0)

    def test_all_skipped(self):
        events = [
            _make_event(skipped=True),
            _make_event(skipped=True),
        ]
        result = compute_earnings_metrics(events)
        assert result["skip_rate"] == pytest.approx(1.0)
        assert result["n_traded"] == 0

    def test_partial_skip(self):
        events = [
            _make_event(skipped=False),
            _make_event(skipped=True),
            _make_event(skipped=False),
            _make_event(skipped=True),
        ]
        result = compute_earnings_metrics(events)
        assert result["skip_rate"] == pytest.approx(0.5)
        assert result["n_traded"] == 2
        assert result["n_events"] == 4


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------


class TestAggregateStats:
    def test_total_and_avg_pnl(self):
        events = [
            _make_event(pnl=200),
            _make_event(pnl=-100),
            _make_event(pnl=300),
        ]
        result = compute_earnings_metrics(events)
        assert result["total_pnl"] == pytest.approx(400.0)
        assert result["avg_pnl"] == pytest.approx(400.0 / 3, rel=0.01)


# ---------------------------------------------------------------------------
# PnL summary
# ---------------------------------------------------------------------------


class TestPnLSummary:
    def test_basic_summary(self):
        events = [
            _make_event(pnl=100),
            _make_event(pnl=200),
            _make_event(pnl=-50),
            _make_event(pnl=300),
        ]
        result = compute_event_pnl_summary(events)
        assert result["mean_pnl"] == pytest.approx(137.5)
        assert result["best_pnl"] == pytest.approx(300.0)
        assert result["worst_pnl"] == pytest.approx(-50.0)
        assert result["std_pnl"] > 0
        assert len(result["pnl_values"]) == 4

    def test_median_odd(self):
        events = [
            _make_event(pnl=100),
            _make_event(pnl=200),
            _make_event(pnl=300),
        ]
        result = compute_event_pnl_summary(events)
        assert result["median_pnl"] == pytest.approx(200.0)

    def test_median_even(self):
        events = [
            _make_event(pnl=100),
            _make_event(pnl=200),
        ]
        result = compute_event_pnl_summary(events)
        assert result["median_pnl"] == pytest.approx(150.0)

    def test_empty_events(self):
        result = compute_event_pnl_summary([])
        assert result["mean_pnl"] == 0.0
        assert result["pnl_values"] == []

    def test_skipped_excluded(self):
        events = [
            _make_event(pnl=100),
            _make_event(pnl=200, skipped=True),
        ]
        result = compute_event_pnl_summary(events)
        assert len(result["pnl_values"]) == 1
        assert result["mean_pnl"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_events(self):
        result = compute_earnings_metrics([])
        assert result["da"] == 0.0
        assert result["n_events"] == 0
        assert result["total_pnl"] == 0.0
