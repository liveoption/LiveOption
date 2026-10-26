"""Tests for 0DTE intraday evaluation metrics."""

import pytest

from trading.evaluation.intraday_metrics import (
    compute_intraday_metrics,
    compute_multi_day_intraday_metrics,
    _intraday_max_drawdown,
    _greeks_decomposition,
    _aggregate_greeks_decomposition,
)


# ---------------------------------------------------------------------------
# Test 1: Intraday Maximum Drawdown (IMDD)
# ---------------------------------------------------------------------------

def test_imdd():
    """Known equity curve -> correct IMDD.

    Curve: [100, 110, 95, 105, 90]
    Peak at 110, trough at 90 -> DD = (110-90)/110 = 0.18182...
    But also peak 110 -> trough 95 = 0.13636
    So IMDD = 20/110 = 0.18182...
    """
    equities = [100.0, 110.0, 95.0, 105.0, 90.0]
    imdd = _intraday_max_drawdown(equities)
    expected = (110.0 - 90.0) / 110.0  # 0.18181818...
    assert imdd == pytest.approx(expected, abs=1e-6)


def test_imdd_no_drawdown():
    """Monotonically increasing curve -> IMDD = 0."""
    equities = [100.0, 101.0, 102.0, 103.0]
    assert _intraday_max_drawdown(equities) == 0.0


def test_imdd_single_point():
    """Single-point curve -> IMDD = 0."""
    assert _intraday_max_drawdown([100.0]) == 0.0


def test_imdd_via_compute():
    """IMDD through the public API."""
    equity_curve = [
        {"ts": "2025-01-06T09:30:00", "equity": 100_000},
        {"ts": "2025-01-06T10:00:00", "equity": 102_000},
        {"ts": "2025-01-06T11:00:00", "equity": 99_000},
        {"ts": "2025-01-06T12:00:00", "equity": 101_000},
    ]
    result = compute_intraday_metrics(equity_curve, [])
    # Peak 102000, trough 99000 -> DD = 3000/102000
    expected = 3_000 / 102_000
    assert result["imdd"] == pytest.approx(expected, abs=1e-4)


# ---------------------------------------------------------------------------
# Test 2: Daily Win Rate (DWR)
# ---------------------------------------------------------------------------

def test_dwr():
    """3 winning days out of 5 -> DWR = 0.6."""
    daily_results = [
        {"date": "2025-01-06", "equity_curve": [], "trades": [], "pnl": 500},
        {"date": "2025-01-07", "equity_curve": [], "trades": [], "pnl": -200},
        {"date": "2025-01-08", "equity_curve": [], "trades": [], "pnl": 300},
        {"date": "2025-01-09", "equity_curve": [], "trades": [], "pnl": -100},
        {"date": "2025-01-10", "equity_curve": [], "trades": [], "pnl": 400},
    ]
    result = compute_multi_day_intraday_metrics(daily_results)
    assert result["dwr"] == pytest.approx(0.6)
    assert result["winning_days"] == 3
    assert result["total_days"] == 5


# ---------------------------------------------------------------------------
# Test 3: Expected P&L
# ---------------------------------------------------------------------------

def test_expected_pnl():
    """Known daily P&Ls -> correct mean.

    P&Ls: 500, -200, 300, -100, 400 -> mean = 900/5 = 180.
    """
    daily_results = [
        {"date": "2025-01-06", "equity_curve": [], "trades": [], "pnl": 500},
        {"date": "2025-01-07", "equity_curve": [], "trades": [], "pnl": -200},
        {"date": "2025-01-08", "equity_curve": [], "trades": [], "pnl": 300},
        {"date": "2025-01-09", "equity_curve": [], "trades": [], "pnl": -100},
        {"date": "2025-01-10", "equity_curve": [], "trades": [], "pnl": 400},
    ]
    result = compute_multi_day_intraday_metrics(daily_results)
    assert result["expected_pnl"] == pytest.approx(180.0)


# ---------------------------------------------------------------------------
# Test 4: Intraday Payoff Ratio (on daily P&Ls)
# ---------------------------------------------------------------------------

def test_intraday_payoff_ratio():
    """Wins avg +100, losses avg -50 -> PR = 2.0.

    Winning days: +100, +100, +100 -> avg = 100
    Losing days: -50, -50 -> avg |loss| = 50
    PR = 100 / 50 = 2.0
    """
    daily_results = [
        {"date": "d1", "equity_curve": [], "trades": [], "pnl": 100},
        {"date": "d2", "equity_curve": [], "trades": [], "pnl": -50},
        {"date": "d3", "equity_curve": [], "trades": [], "pnl": 100},
        {"date": "d4", "equity_curve": [], "trades": [], "pnl": -50},
        {"date": "d5", "equity_curve": [], "trades": [], "pnl": 100},
    ]
    result = compute_multi_day_intraday_metrics(daily_results)
    assert result["payoff_ratio"] == pytest.approx(2.0)


def test_payoff_ratio_all_wins():
    """All winning days -> PR = inf."""
    daily_results = [
        {"date": "d1", "equity_curve": [], "trades": [], "pnl": 100},
        {"date": "d2", "equity_curve": [], "trades": [], "pnl": 200},
    ]
    result = compute_multi_day_intraday_metrics(daily_results)
    assert result["payoff_ratio"] == float("inf")


def test_payoff_ratio_all_losses():
    """All losing days -> PR = 0."""
    daily_results = [
        {"date": "d1", "equity_curve": [], "trades": [], "pnl": -100},
        {"date": "d2", "equity_curve": [], "trades": [], "pnl": -200},
    ]
    result = compute_multi_day_intraday_metrics(daily_results)
    assert result["payoff_ratio"] == 0.0


# ---------------------------------------------------------------------------
# Test 5: Greeks decomposition with data
# ---------------------------------------------------------------------------

def test_greeks_decomposition_with_data():
    """Mock trade with full Greeks -> correct component values.

    delta=0.5, gamma=0.1, vega=0.2, theta=-0.05
    entry_underlying=100, exit_underlying=102 -> dS = 2
    entry_iv=0.20, exit_iv=0.25 -> d_sigma = 0.05
    time_held_days=1/390 (one minute)
    pnl=1.50

    delta_pnl = 0.5 * 2 = 1.0
    gamma_pnl = 0.5 * 0.1 * 4 = 0.2
    vega_pnl = 0.2 * 0.05 = 0.01
    theta_pnl = -0.05 * (1/390) = -0.000128205...
    residual = 1.50 - (1.0 + 0.2 + 0.01 + (-0.000128205)) = 0.290128205
    """
    trade = {
        "delta": 0.5,
        "gamma": 0.1,
        "vega": 0.2,
        "theta": -0.05,
        "entry_underlying_price": 100.0,
        "exit_underlying_price": 102.0,
        "entry_iv": 0.20,
        "exit_iv": 0.25,
        "time_held_days": 1 / 390,
        "pnl": 1.50,
    }

    decomp = _greeks_decomposition(trade)

    assert decomp["delta_pnl"] == pytest.approx(1.0, abs=1e-9)
    assert decomp["gamma_pnl"] == pytest.approx(0.2, abs=1e-9)
    assert decomp["vega_pnl"] == pytest.approx(0.01, abs=1e-9)
    assert decomp["theta_pnl"] == pytest.approx(-0.05 / 390, abs=1e-9)

    # Residual = actual - sum(components)
    sum_components = 1.0 + 0.2 + 0.01 + (-0.05 / 390)
    expected_residual = 1.50 - sum_components
    assert decomp["residual"] == pytest.approx(expected_residual, abs=1e-9)


def test_greeks_decomposition_aggregate():
    """Two trades with Greeks -> aggregate sums components."""
    trades = [
        {
            "delta": 0.5, "gamma": 0.1, "vega": 0.2, "theta": -0.05,
            "entry_underlying_price": 100.0, "exit_underlying_price": 102.0,
            "entry_iv": 0.20, "exit_iv": 0.25,
            "time_held_days": 1 / 390, "pnl": 1.50,
        },
        {
            "delta": -0.3, "gamma": 0.05, "vega": 0.1, "theta": -0.03,
            "entry_underlying_price": 102.0, "exit_underlying_price": 101.0,
            "entry_iv": 0.25, "exit_iv": 0.22,
            "time_held_days": 2 / 390, "pnl": 0.50,
        },
    ]

    agg = _aggregate_greeks_decomposition(trades)

    # Trade 1: delta_pnl = 0.5*2 = 1.0
    # Trade 2: delta_pnl = -0.3*(-1) = 0.3
    assert agg["delta_pnl"] == pytest.approx(1.3, abs=1e-9)

    # Trade 1: gamma_pnl = 0.5*0.1*4 = 0.2
    # Trade 2: gamma_pnl = 0.5*0.05*1 = 0.025
    assert agg["gamma_pnl"] == pytest.approx(0.225, abs=1e-9)

    # Trade 1: vega_pnl = 0.2*0.05 = 0.01
    # Trade 2: vega_pnl = 0.1*(-0.03) = -0.003
    assert agg["vega_pnl"] == pytest.approx(0.007, abs=1e-9)

    # Theta and residual are also valid (not None)
    assert agg["theta_pnl"] is not None
    assert agg["residual"] is not None


# ---------------------------------------------------------------------------
# Test 6: Greeks decomposition without data
# ---------------------------------------------------------------------------

def test_greeks_decomposition_no_data():
    """Trade without Greeks fields -> None components."""
    trade = {"pnl": 50.0, "side": "BUY", "qty": 10, "price": 2.0}

    decomp = _greeks_decomposition(trade)

    assert decomp["delta_pnl"] is None
    assert decomp["gamma_pnl"] is None
    assert decomp["vega_pnl"] is None
    assert decomp["theta_pnl"] is None
    assert decomp["residual"] is None


def test_greeks_partial_data():
    """Trade with only delta -> only delta_pnl computed, rest None."""
    trade = {
        "delta": 0.5,
        "entry_underlying_price": 100.0,
        "exit_underlying_price": 103.0,
        "pnl": 2.0,
    }

    decomp = _greeks_decomposition(trade)

    assert decomp["delta_pnl"] == pytest.approx(1.5, abs=1e-9)
    assert decomp["gamma_pnl"] is None  # gamma not provided
    assert decomp["vega_pnl"] is None
    assert decomp["theta_pnl"] is None
    assert decomp["residual"] is None  # can't compute residual without all


# ---------------------------------------------------------------------------
# Test 7: Multi-day aggregation
# ---------------------------------------------------------------------------

def test_multi_day_aggregation():
    """3 days -> correct aggregated metrics."""
    daily_results = [
        {
            "date": "2025-01-06",
            "equity_curve": [
                {"ts": "2025-01-06T09:30", "equity": 100_000},
                {"ts": "2025-01-06T10:00", "equity": 100_500},
                {"ts": "2025-01-06T16:00", "equity": 100_300},
            ],
            "trades": [{"pnl": 300}],
            "pnl": 300,
        },
        {
            "date": "2025-01-07",
            "equity_curve": [
                {"ts": "2025-01-07T09:30", "equity": 100_000},
                {"ts": "2025-01-07T10:00", "equity": 99_500},
                {"ts": "2025-01-07T16:00", "equity": 99_800},
            ],
            "trades": [{"pnl": -200}],
            "pnl": -200,
        },
        {
            "date": "2025-01-08",
            "equity_curve": [
                {"ts": "2025-01-08T09:30", "equity": 100_000},
                {"ts": "2025-01-08T11:00", "equity": 100_800},
                {"ts": "2025-01-08T16:00", "equity": 100_600},
            ],
            "trades": [{"pnl": 600}],
            "pnl": 600,
        },
    ]

    result = compute_multi_day_intraday_metrics(daily_results)

    # DWR: 2 winning days (300, 600) out of 3
    assert result["dwr"] == pytest.approx(2 / 3, abs=1e-6)
    assert result["winning_days"] == 2
    assert result["total_days"] == 3

    # E[P&L] = (300 - 200 + 600) / 3 = 233.33...
    assert result["expected_pnl"] == pytest.approx(233.33, abs=0.01)

    # Payoff Ratio: avg win = (300+600)/2 = 450, avg loss = 200
    # PR = 450/200 = 2.25
    assert result["payoff_ratio"] == pytest.approx(2.25, abs=1e-6)

    # Per-day results are present
    assert len(result["per_day"]) == 3
    assert result["per_day"][0]["date"] == "2025-01-06"

    # Each per-day has IMDD computed
    # Day 1: peak 100500, trough 100300 -> DD = 200/100500
    assert result["per_day"][0]["imdd"] == pytest.approx(200 / 100_500, abs=1e-4)

    # Greeks are None since no Greeks data provided
    assert result["aggregate_greeks"]["delta_pnl"] is None
    assert result["theta_realization_rate"] is None
    assert result["vega_contribution"] is None


def test_multi_day_empty():
    """Empty daily results -> sensible defaults."""
    result = compute_multi_day_intraday_metrics([])
    assert result["dwr"] == 0.0
    assert result["expected_pnl"] == 0.0
    assert result["payoff_ratio"] == 0.0
    assert result["total_days"] == 0


def test_multi_day_with_greeks():
    """Multi-day with full Greeks -> aggregate decomposition works."""
    trade1 = {
        "delta": 0.5, "gamma": 0.1, "vega": 0.2, "theta": -0.05,
        "entry_underlying_price": 100.0, "exit_underlying_price": 102.0,
        "entry_iv": 0.20, "exit_iv": 0.25,
        "time_held_days": 1 / 390, "pnl": 1.50,
    }
    trade2 = {
        "delta": -0.3, "gamma": 0.05, "vega": 0.1, "theta": -0.03,
        "entry_underlying_price": 102.0, "exit_underlying_price": 101.0,
        "entry_iv": 0.25, "exit_iv": 0.22,
        "time_held_days": 2 / 390, "pnl": 0.50,
    }

    daily_results = [
        {
            "date": "2025-01-06",
            "equity_curve": [
                {"ts": "2025-01-06T09:30", "equity": 100_000},
                {"ts": "2025-01-06T16:00", "equity": 100_150},
            ],
            "trades": [trade1],
            "pnl": 1.50,
        },
        {
            "date": "2025-01-07",
            "equity_curve": [
                {"ts": "2025-01-07T09:30", "equity": 100_000},
                {"ts": "2025-01-07T16:00", "equity": 100_050},
            ],
            "trades": [trade2],
            "pnl": 0.50,
        },
    ]

    result = compute_multi_day_intraday_metrics(daily_results)

    # Both days have Greeks, so aggregate should be non-None
    assert result["aggregate_greeks"]["delta_pnl"] is not None
    assert result["aggregate_greeks"]["gamma_pnl"] is not None
    assert result["aggregate_greeks"]["vega_pnl"] is not None
    assert result["theta_realization_rate"] is not None
    assert result["vega_contribution"] is not None

    # Verify aggregate delta_pnl = 0.5*2 + (-0.3)*(-1) = 1.0 + 0.3 = 1.3
    assert result["aggregate_greeks"]["delta_pnl"] == pytest.approx(1.3, abs=1e-6)
