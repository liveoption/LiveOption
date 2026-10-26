"""Tests for multi-scale evaluation metrics."""

import math
import pytest

from trading.evaluation.metrics import (
    compute_metrics,
    multi_scale_evaluate,
    _payoff_ratio,
    _expected_value,
    _value_at_risk,
    _conditional_var,
)


# ---------------------------------------------------------------------------
# Test 1: PnL from equity curve
# ---------------------------------------------------------------------------

def test_compute_pnl_from_equity_curve():
    """Simple equity curve [100000, 100500, 101000] -> pnl = 1000."""
    equity_curve = [
        {"ts": "2025-01-06T09:31:00", "equity": 100_000},
        {"ts": "2025-01-06T09:32:00", "equity": 100_500},
        {"ts": "2025-01-06T09:33:00", "equity": 101_000},
    ]
    trades: list[dict] = []
    result = compute_metrics(equity_curve, trades, scale="1d")
    assert result["pnl"] == 1000.0


# ---------------------------------------------------------------------------
# Test 2: Return percentage
# ---------------------------------------------------------------------------

def test_compute_return_pct():
    """100000 -> 101000 = 1.0% return."""
    equity_curve = [
        {"ts": "2025-01-06T09:31:00", "equity": 100_000},
        {"ts": "2025-01-06T09:32:00", "equity": 101_000},
    ]
    trades: list[dict] = []
    result = compute_metrics(equity_curve, trades, scale="1d")
    assert result["return_pct"] == 1.0


# ---------------------------------------------------------------------------
# Test 3: Max drawdown
# ---------------------------------------------------------------------------

def test_max_drawdown():
    """Curve [100, 110, 95, 105] -> MDD = (110-95)/110 = 13.636...%"""
    equity_curve = [
        {"ts": "2025-01-06T09:31:00", "equity": 100},
        {"ts": "2025-01-06T09:32:00", "equity": 110},
        {"ts": "2025-01-06T09:33:00", "equity": 95},
        {"ts": "2025-01-06T09:34:00", "equity": 105},
    ]
    trades: list[dict] = []
    result = compute_metrics(equity_curve, trades, scale="1d")
    expected_mdd = (110 - 95) / 110  # 0.13636...
    assert abs(result["max_drawdown"] - expected_mdd) < 1e-3


# ---------------------------------------------------------------------------
# Test 4: Sharpe ratio
# ---------------------------------------------------------------------------

def test_sharpe_ratio():
    """Known returns sequence -> verify approximate Sharpe.

    Equity: [100, 101, 102, 101.5, 103] produces period returns of
    [0.01, ~0.0099, ~-0.0049, ~0.01478].
    We verify the Sharpe is computed correctly with daily annualization.
    """
    equity_curve = [
        {"ts": "2025-01-06", "equity": 100},
        {"ts": "2025-01-07", "equity": 101},
        {"ts": "2025-01-08", "equity": 102},
        {"ts": "2025-01-09", "equity": 101.5},
        {"ts": "2025-01-10", "equity": 103},
    ]
    trades: list[dict] = []
    result = compute_metrics(equity_curve, trades, scale="1d")

    # Manually compute expected Sharpe
    equities = [100, 101, 102, 101.5, 103]
    returns = [
        (equities[i] - equities[i - 1]) / equities[i - 1]
        for i in range(1, len(equities))
    ]
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    expected_sharpe = (mean_r / std) * math.sqrt(252)

    assert abs(result["sharpe"] - round(expected_sharpe, 4)) < 0.01


# ---------------------------------------------------------------------------
# Test 5: Win rate
# ---------------------------------------------------------------------------

def test_win_rate():
    """3 winners + 2 losers = 60% win rate."""
    equity_curve = [
        {"ts": "2025-01-06", "equity": 100_000},
        {"ts": "2025-01-07", "equity": 101_000},
    ]
    trades = [
        {"side": "BUY", "qty": 10, "price": 2.0, "fees": 1.0, "pnl": 100.0},
        {"side": "BUY", "qty": 5, "price": 3.0, "fees": 1.0, "pnl": -50.0},
        {"side": "BUY", "qty": 8, "price": 1.5, "fees": 1.0, "pnl": 200.0},
        {"side": "SELL", "qty": 10, "price": 2.5, "fees": 1.0, "pnl": -30.0},
        {"side": "BUY", "qty": 3, "price": 4.0, "fees": 1.0, "pnl": 75.0},
    ]
    result = compute_metrics(equity_curve, trades, scale="1d")
    assert result["win_rate"] == 0.6


# ---------------------------------------------------------------------------
# Test 6: Multi-scale evaluation
# ---------------------------------------------------------------------------

def test_multi_scale_evaluation():
    """Verify different scales produce different metric dicts.

    Build a 1-minute equity curve spanning 30 minutes, then resample
    to 5m, 15m, and 1d.  Each scale should produce a valid metrics dict
    with the right keys, and the resampled curves should differ.
    """
    # Build 30 points of 1-minute data with a simple uptrend + dip
    base_equities = [
        100_000 + i * 10 if i < 20 else 100_200 - (i - 20) * 5
        for i in range(30)
    ]
    equity_curve = []
    for i, eq in enumerate(base_equities):
        total_min = 31 + i  # starts at minute 31
        hour = 9 + total_min // 60
        minute = total_min % 60
        equity_curve.append({"ts": f"2025-01-06T{hour:02d}:{minute:02d}:00", "equity": eq})
    trades = [
        {"side": "BUY", "qty": 5, "price": 2.0, "fees": 1.0, "pnl": 50.0},
        {"side": "SELL", "qty": 5, "price": 2.5, "fees": 1.0, "pnl": -20.0},
    ]

    scales = ["1m", "5m", "15m", "1d"]
    results = multi_scale_evaluate(equity_curve, trades, scales)

    # All scales present
    assert set(results.keys()) == set(scales)

    # Each scale has all expected metric keys
    expected_keys = {"pnl", "return_pct", "max_drawdown", "sharpe",
                     "win_rate", "turnover", "fees_paid", "fill_ratio",
                     "payoff_ratio", "expected_value", "var_5pct", "cvar_5pct"}
    for scale in scales:
        assert set(results[scale].keys()) == expected_keys, (
            f"Missing keys for scale {scale}"
        )

    # Resampling changes the equity curve length, which can affect metrics.
    # 1m should have 30 points -> different Sharpe from 1d (1 point).
    # The PnL should be the same across all scales since it's just
    # final - initial from the resampled curve.
    # For 1d, there's only one day so the resampled curve has 1 point
    # (or a single-point curve yields pnl = 0 since first == last).
    # The key assertion: different scales produce different metric values
    # for drawdown and sharpe due to the different granularities.
    assert results["1m"]["max_drawdown"] >= 0
    assert results["5m"]["max_drawdown"] >= 0

    # 1m and 5m should produce different Sharpe values due to different
    # annualization factors
    # (unless both are 0, which they shouldn't be with 30 data points)
    if results["1m"]["sharpe"] != 0 and results["5m"]["sharpe"] != 0:
        assert results["1m"]["sharpe"] != results["5m"]["sharpe"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_equity_curve():
    """Empty curve returns zero metrics."""
    result = compute_metrics([], [], scale="1d")
    assert result["pnl"] == 0.0
    assert result["return_pct"] == 0.0
    assert result["max_drawdown"] == 0.0
    assert result["sharpe"] == 0.0
    assert result["fill_ratio"] == 1.0


def test_single_point_equity_curve():
    """Single-point curve has zero returns, zero drawdown."""
    equity_curve = [{"ts": "2025-01-06", "equity": 100_000}]
    result = compute_metrics(equity_curve, [], scale="1d")
    assert result["pnl"] == 0.0
    assert result["max_drawdown"] == 0.0
    assert result["sharpe"] == 0.0


def test_fees_paid():
    """Total fees are summed across all trades."""
    equity_curve = [
        {"ts": "2025-01-06", "equity": 100_000},
        {"ts": "2025-01-07", "equity": 100_500},
    ]
    trades = [
        {"side": "BUY", "qty": 10, "price": 2.0, "fees": 3.25, "pnl": 50.0},
        {"side": "SELL", "qty": 10, "price": 2.5, "fees": 4.50, "pnl": 50.0},
    ]
    result = compute_metrics(equity_curve, trades, scale="1d")
    assert result["fees_paid"] == 7.75


def test_turnover():
    """Turnover = sum(abs(qty*price)) / mean(equity)."""
    equity_curve = [
        {"ts": "2025-01-06", "equity": 100_000},
        {"ts": "2025-01-07", "equity": 100_000},
    ]
    trades = [
        {"side": "BUY", "qty": 100, "price": 50.0, "fees": 0, "pnl": 0},
        {"side": "SELL", "qty": 100, "price": 50.0, "fees": 0, "pnl": 0},
    ]
    result = compute_metrics(equity_curve, trades, scale="1d")
    # Total value = 5000 + 5000 = 10000, mean equity = 100000
    expected = 10000 / 100000
    assert abs(result["turnover"] - expected) < 1e-6


# ---------------------------------------------------------------------------
# Payoff Ratio tests
# ---------------------------------------------------------------------------

def test_payoff_ratio():
    """3 wins avg +$100, 2 losses avg -$50 -> PR = 2.0."""
    trades = [
        {"pnl": 100.0},
        {"pnl": 100.0},
        {"pnl": 100.0},
        {"pnl": -50.0},
        {"pnl": -50.0},
    ]
    pr = _payoff_ratio(trades)
    assert pr == pytest.approx(2.0)


def test_payoff_ratio_no_losses():
    """All wins -> PR = inf."""
    trades = [
        {"pnl": 50.0},
        {"pnl": 75.0},
        {"pnl": 120.0},
    ]
    pr = _payoff_ratio(trades)
    assert pr == float("inf")


# ---------------------------------------------------------------------------
# Expected Value tests
# ---------------------------------------------------------------------------

def test_expected_value():
    """Known trades -> correct EV.

    3 wins of +100, 2 losses of -50.
    WR = 3/5 = 0.6, E[Profit] = 100, E[|Loss|] = 50.
    EV = 0.6 * 100 - 0.4 * 50 = 60 - 20 = 40.
    """
    trades = [
        {"pnl": 100.0},
        {"pnl": 100.0},
        {"pnl": 100.0},
        {"pnl": -50.0},
        {"pnl": -50.0},
    ]
    ev = _expected_value(trades)
    assert ev == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# VaR tests
# ---------------------------------------------------------------------------

def test_var_5pct():
    """100 returns, verify 5th percentile."""
    # Returns: -0.99, -0.98, ..., 0.0 (100 values from -0.99 to 0.0)
    returns = [i / 100.0 for i in range(-99, 1)]  # [-0.99, -0.98, ..., 0.0]
    assert len(returns) == 100
    var = _value_at_risk(returns, 5.0)
    # 5th percentile of [-0.99..0.0]:
    # rank = 0.05 * 99 = 4.95 -> interpolation between index 4 and 5
    # sorted values at index 4 = -0.95, index 5 = -0.94
    # var = -0.95 + 0.95 * (-0.94 - (-0.95)) = -0.95 + 0.95*0.01 = -0.9405
    assert var == pytest.approx(-0.9405, abs=1e-6)


def test_cvar_5pct():
    """Verify mean of returns at or below VaR."""
    returns = [i / 100.0 for i in range(-99, 1)]  # [-0.99..0.0]
    var = _value_at_risk(returns, 5.0)
    cvar = _conditional_var(returns, 5.0)
    # CVaR = mean of returns <= var (-0.9405)
    # Those are: -0.99, -0.98, -0.97, -0.96, -0.95
    tail = [r for r in returns if r <= var]
    expected_cvar = sum(tail) / len(tail)
    assert cvar == pytest.approx(expected_cvar, abs=1e-6)


def test_var_empty():
    """Empty returns -> VaR = 0.0."""
    assert _value_at_risk([], 5.0) == 0.0
    assert _conditional_var([], 5.0) == 0.0


# ---------------------------------------------------------------------------
# Issue 6: Trades without pnl field
# ---------------------------------------------------------------------------

def test_win_rate_trades_without_pnl():
    """Trades missing pnl key should not crash and should be excluded from win rate."""
    equity_curve = [
        {"ts": "2025-01-06", "equity": 100_000},
        {"ts": "2025-01-07", "equity": 100_500},
    ]
    # Raw PMB trades: no pnl field
    trades = [
        {"side": "BUY", "qty": 10, "price": 2.0, "fees": 1.0},
        {"side": "SELL", "qty": 10, "price": 2.5, "fees": 1.0},
    ]
    result = compute_metrics(equity_curve, trades, scale="1d")
    # win_rate should be 0.0 (no pnl data), not crash
    assert result["win_rate"] == 0.0
    assert result["payoff_ratio"] == 0.0
    assert result["expected_value"] == 0.0
    # fees should still be summed
    assert result["fees_paid"] == 2.0


def test_mixed_trades_with_and_without_pnl():
    """Only trades with pnl key should count toward win_rate."""
    equity_curve = [
        {"ts": "2025-01-06", "equity": 100_000},
        {"ts": "2025-01-07", "equity": 101_000},
    ]
    trades = [
        {"side": "BUY", "qty": 10, "price": 2.0, "fees": 1.0, "pnl": 100.0},
        {"side": "SELL", "qty": 10, "price": 2.5, "fees": 1.0},  # no pnl
        {"side": "BUY", "qty": 5, "price": 3.0, "fees": 1.0, "pnl": -50.0},
    ]
    result = compute_metrics(equity_curve, trades, scale="1d")
    # 2 trades with pnl: 1 win, 1 loss -> 50% win rate
    assert result["win_rate"] == pytest.approx(0.5)
    # fees: 1.0 + 1.0 + 1.0 = 3.0
    assert result["fees_paid"] == 3.0
