"""Tests for overlay strategy evaluation metrics (Profit Increase + Hedging)."""

import math
import pytest

from trading.evaluation.overlay_metrics import (
    compute_profit_increase_metrics,
    compute_hedging_metrics,
)


# ===================================================================
# Helpers
# ===================================================================

def _make_equity_curve(equities: list[float], start_date: str = "2025-01-06") -> list[dict]:
    """Build a daily equity curve from a list of equity values."""
    curve = []
    year, month, day = map(int, start_date.split("-"))
    for i, eq in enumerate(equities):
        d = day + i
        curve.append({"ts": f"{year:04d}-{month:02d}-{d:02d}", "equity": eq})
    return curve


# ===================================================================
# Profit Increase Tests
# ===================================================================


def test_excess_ar_positive():
    """Agent beats buy-and-hold -> positive excess AR."""
    # Agent grows 10% over 100 days, hold grows 5%
    n = 101
    agent_eq = [100_000 * (1 + 0.10 * i / (n - 1)) for i in range(n)]
    hold_eq = [100_000 * (1 + 0.05 * i / (n - 1)) for i in range(n)]

    agent_curve = _make_equity_curve(agent_eq)
    hold_curve = _make_equity_curve(hold_eq)

    result = compute_profit_increase_metrics(
        agent_equity=agent_curve,
        hold_equity=hold_curve,
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )

    assert result["excess_ar_hold"] > 0, (
        f"Expected positive excess AR, got {result['excess_ar_hold']}"
    )


def test_dps_positive():
    """Agent has lower max drawdown than hold -> DPS > 0."""
    # Hold has a 20% drawdown; agent only has a 5% drawdown
    hold_equities = [100_000, 105_000, 110_000, 88_000, 95_000, 100_000]
    agent_equities = [100_000, 104_000, 108_000, 102_600, 106_000, 110_000]

    hold_curve = _make_equity_curve(hold_equities)
    agent_curve = _make_equity_curve(agent_equities)

    result = compute_profit_increase_metrics(
        agent_equity=agent_curve,
        hold_equity=hold_curve,
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )

    assert result["dps"] > 0, f"Expected DPS > 0, got {result['dps']}"
    # Also verify drawdown_delta is negative (agent has less drawdown)
    assert result["drawdown_delta"] < 0, (
        f"Expected negative drawdown delta, got {result['drawdown_delta']}"
    )


def test_icr_high():
    """When most PnL comes from premium income, ICR should be close to 1."""
    # Agent PnL is ~1000, premium received is 950 (95% of PnL)
    agent_equities = [100_000, 100_300, 100_600, 100_800, 101_000]
    hold_equities = [100_000, 100_200, 100_400, 100_600, 100_800]

    agent_curve = _make_equity_curve(agent_equities)
    hold_curve = _make_equity_curve(hold_equities)

    trades = [
        {"premium_received": 500.0, "pnl": 520.0},
        {"premium_received": 450.0, "pnl": 480.0},
    ]

    result = compute_profit_increase_metrics(
        agent_equity=agent_curve,
        hold_equity=hold_curve,
        etf_equity=None,
        trades=trades,
        initial_capital=100_000,
    )

    # Total premium = 950, total PnL = 1000, ICR = 950/1000 = 0.95
    assert result["icr"] > 0.9, f"Expected ICR close to 1, got {result['icr']}"
    assert result["icr"] < 1.1, f"Expected ICR <= ~1, got {result['icr']}"


def test_bdr_all_windows():
    """Agent dominates hold in every rolling window -> BDR = 1.0."""
    # Agent strictly above hold every day
    n = 50
    agent_eq = [100_000 + 100 * i for i in range(n)]
    hold_eq = [100_000 + 50 * i for i in range(n)]

    agent_curve = _make_equity_curve(agent_eq)
    hold_curve = _make_equity_curve(hold_eq)

    result = compute_profit_increase_metrics(
        agent_equity=agent_curve,
        hold_equity=hold_curve,
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )

    assert result["bdr"] == 1.0, f"Expected BDR = 1.0, got {result['bdr']}"


def test_excess_ar_etf():
    """Excess AR over ETF is computed when ETF curve is provided."""
    n = 101
    agent_eq = [100_000 * (1 + 0.15 * i / (n - 1)) for i in range(n)]
    hold_eq = [100_000 * (1 + 0.10 * i / (n - 1)) for i in range(n)]
    etf_eq = [100_000 * (1 + 0.08 * i / (n - 1)) for i in range(n)]

    result = compute_profit_increase_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        etf_equity=_make_equity_curve(etf_eq),
        trades=[],
        initial_capital=100_000,
    )

    assert result["excess_ar_etf"] is not None
    assert result["excess_ar_etf"] > 0


# ===================================================================
# RRS (Regime Robustness Score) Tests
# ===================================================================


def test_rrs_returned_in_profit_increase_metrics():
    """RRS key is present in the profit-increase result dict."""
    n = 50
    agent_eq = [100_000 + 100 * i for i in range(n)]
    hold_eq = [100_000 + 50 * i for i in range(n)]

    result = compute_profit_increase_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )

    assert "rrs" in result


def test_rrs_positive_when_agent_dominates():
    """Agent consistently beats hold across regimes -> RRS > 0."""
    # 60-day curve: hold has a drawdown (bear) then recovery (bull)
    # Agent consistently outperforms by +0.2% per day
    n = 60
    hold_eq = []
    agent_eq = []
    v_hold = 100_000.0
    v_agent = 100_000.0
    for i in range(n):
        hold_eq.append(v_hold)
        agent_eq.append(v_agent)
        if i < 20:
            # Bear phase: hold drops ~0.5% per day
            v_hold *= 0.995
            v_agent *= 0.997  # agent drops less
        elif i < 40:
            # Bull phase: hold grows ~0.5% per day
            v_hold *= 1.005
            v_agent *= 1.007  # agent grows more
        else:
            # Sideways: hold flat
            v_hold *= 1.0001
            v_agent *= 1.002  # agent still outperforms

    result = compute_profit_increase_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )

    assert result["rrs"] > 0, f"Expected RRS > 0, got {result['rrs']}"


def test_rrs_short_curve_returns_zero():
    """Curve shorter than rolling window returns RRS = 0."""
    n = 10  # shorter than default rolling_window=20
    agent_eq = [100_000 + 100 * i for i in range(n)]
    hold_eq = [100_000 + 50 * i for i in range(n)]

    result = compute_profit_increase_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )

    assert result["rrs"] == 0.0


def test_rrs_penalizes_inconsistency():
    """If agent outperforms in one regime but underperforms in another,
    RRS is lower than when it consistently outperforms."""
    # Consistent outperformer
    n = 80
    hold_eq_consistent = []
    agent_eq_consistent = []
    v_hold = 100_000.0
    v_agent = 100_000.0
    for i in range(n):
        hold_eq_consistent.append(v_hold)
        agent_eq_consistent.append(v_agent)
        if i < 30:
            v_hold *= 0.994  # bear
            v_agent *= 0.996
        elif i < 60:
            v_hold *= 1.006  # bull
            v_agent *= 1.008
        else:
            v_hold *= 1.0001  # sideways
            v_agent *= 1.002

    rrs_consistent = compute_profit_increase_metrics(
        agent_equity=_make_equity_curve(agent_eq_consistent),
        hold_equity=_make_equity_curve(hold_eq_consistent),
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )["rrs"]

    # Inconsistent: outperforms in bull, underperforms in bear
    hold_eq_inconsistent = []
    agent_eq_inconsistent = []
    v_hold = 100_000.0
    v_agent = 100_000.0
    for i in range(n):
        hold_eq_inconsistent.append(v_hold)
        agent_eq_inconsistent.append(v_agent)
        if i < 30:
            v_hold *= 0.994  # bear
            v_agent *= 0.990  # agent worse in bear
        elif i < 60:
            v_hold *= 1.006  # bull
            v_agent *= 1.012  # agent much better in bull
        else:
            v_hold *= 1.0001  # sideways
            v_agent *= 1.002

    rrs_inconsistent = compute_profit_increase_metrics(
        agent_equity=_make_equity_curve(agent_eq_inconsistent),
        hold_equity=_make_equity_curve(hold_eq_inconsistent),
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )["rrs"]

    assert rrs_consistent > rrs_inconsistent, (
        f"Consistent RRS ({rrs_consistent}) should be higher than "
        f"inconsistent RRS ({rrs_inconsistent})"
    )


# ===================================================================
# Hedging Tests
# ===================================================================


def test_har():
    """50% of days hedged -> HAR = 0.5."""
    # 10 equity points -> 9 return days.  Hedge active on days 0-3 (4 days).
    # But we want exactly 50%, so 10 return days, 5 hedged.
    n = 11  # 10 return periods
    hold_eq = [100_000 + 100 * i for i in range(n)]
    agent_eq = [100_000 + 120 * i for i in range(n)]

    # One trade active on days 0..4 (5 out of 10 days)
    trades = [
        {
            "hedge_active_days": [0, 1, 2, 3, 4],
            "premium_paid": 100.0,
            "payoff": 50.0,
        },
    ]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        trades=trades,
        initial_capital=100_000,
    )

    assert result["har"] == pytest.approx(0.5), f"Expected HAR=0.5, got {result['har']}"


def test_ecar():
    """All downside days hedged -> ECAR = 1.0."""
    # Create hold curve with clear downside days (below -1% threshold)
    hold_equities = [
        100_000,  # day start
        101_000,  # day 0: +1%
        99_000,   # day 1: -1.98% (downside)
        100_500,  # day 2: +1.52%
        98_400,   # day 3: -2.09% (downside)
        99_500,   # day 4: +1.12%
    ]
    agent_equities = [
        100_000,
        101_000,
        99_500,   # less loss on downside day
        100_500,
        99_000,   # less loss on downside day
        99_800,
    ]

    # Downside days are indices 1 and 3 (0-indexed return periods)
    # Hedge is active on days 1 and 3 (the downside days)
    trades = [
        {
            "hedge_active_days": [1, 3],
            "premium_paid": 200.0,
            "payoff": 500.0,
        },
    ]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_equities),
        hold_equity=_make_equity_curve(hold_equities),
        trades=trades,
        initial_capital=100_000,
    )

    assert result["ecar"] == pytest.approx(1.0), f"Expected ECAR=1.0, got {result['ecar']}"


def test_hedging_efficiency():
    """Payoff > cost -> HE > 1."""
    n = 11
    hold_eq = [100_000 + 50 * i for i in range(n)]
    agent_eq = [100_000 + 80 * i for i in range(n)]

    trades = [
        {
            "hedge_active_days": list(range(10)),
            "premium_paid": 200.0,
            "payoff": 500.0,
        },
    ]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        trades=trades,
        initial_capital=100_000,
    )

    # HIR = 500 / 100_000 = 0.005, HCR = 200 / 100_000 = 0.002
    # HE = HIR / HCR = 2.5
    assert result["he"] > 1.0, f"Expected HE > 1, got {result['he']}"
    assert result["he"] == pytest.approx(2.5, rel=1e-6)


def test_epc():
    """Full payout coverage -> EPC = 1.0."""
    # One downside day where hold loses 2000, and hedge pays back 2000
    hold_equities = [100_000, 102_000, 100_000]
    # day 0: +2%, day 1: -1.96% (downside)
    # Unhedged loss on day 1 = 102_000 - 100_000 = 2000

    agent_equities = [100_000, 102_000, 102_000]  # agent doesn't lose

    trades = [
        {
            "hedge_active_days": [1],
            "premium_paid": 100.0,
            "payoff": 2000.0,
        },
    ]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_equities),
        hold_equity=_make_equity_curve(hold_equities),
        trades=trades,
        initial_capital=100_000,
    )

    assert result["epc"] == pytest.approx(1.0), f"Expected EPC=1.0, got {result['epc']}"


def test_hsr():
    """Hedged portfolio beats unhedged on all downside days -> HSR = 1.0."""
    hold_equities = [
        100_000,
        99_000,   # day 0: -1% (borderline — check threshold)
        97_000,   # day 1: -2.02% (downside)
        98_000,   # day 2: +1.03%
        95_000,   # day 3: -3.06% (downside)
    ]
    # Agent does better on the downside days
    agent_equities = [
        100_000,
        99_500,   # day 0: -0.5%  (hold: -1%)
        97_500,   # day 1: -2.01% (hold: -2.02%) — agent beats hold
        98_500,   # day 2: +1.03%
        96_000,   # day 3: -2.54% (hold: -3.06%) — agent beats hold
    ]

    trades = [
        {
            "hedge_active_days": [0, 1, 2, 3],
            "premium_paid": 300.0,
            "payoff": 600.0,
        },
    ]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_equities),
        hold_equity=_make_equity_curve(hold_equities),
        trades=trades,
        initial_capital=100_000,
        downside_threshold=-0.01,
    )

    # Downside days (hold return < -1%): day 1 (-2.02%) and day 3 (-3.06%)
    # Day 0 is exactly -1%, which is NOT < -1%, so not a downside event
    # On both downside days, agent return > hold return -> HSR = 1.0
    assert result["hsr"] == pytest.approx(1.0), f"Expected HSR=1.0, got {result['hsr']}"


# ===================================================================
# ERBE (Excess Return over Baseline on Event Days) Tests
# ===================================================================


def test_erbe_returned_in_hedging_metrics():
    """ERBE key is present in the hedging result dict."""
    n = 11
    hold_eq = [100_000 + 50 * i for i in range(n)]
    agent_eq = [100_000 + 80 * i for i in range(n)]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        trades=[],
        initial_capital=100_000,
    )

    assert "erbe" in result


def test_erbe_none_when_no_baseline():
    """ERBE is None when baseline_returns is not provided."""
    n = 11
    hold_eq = [100_000 + 50 * i for i in range(n)]
    agent_eq = [100_000 + 80 * i for i in range(n)]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        trades=[],
        initial_capital=100_000,
        baseline_returns=None,
    )

    assert result["erbe"] is None


def test_erbe_positive_when_agent_beats_baseline_on_down_days():
    """Agent outperforms baseline on downside days -> ERBE > 0."""
    # Hold has a big down day so D_t = 1
    hold_equities = [
        100_000,
        101_000,  # day 0: +1%
        98_500,   # day 1: -2.475% (downside)
        99_500,   # day 2: +1.015%
        96_500,   # day 3: -3.015% (downside)
        97_500,   # day 4: +1.036%
    ]

    # Agent does better on downside days
    agent_equities = [
        100_000,
        101_000,  # day 0: +1%
        99_500,   # day 1: -1.485% (better than hold)
        100_000,  # day 2: +0.503%
        98_000,   # day 3: -2.0% (better than hold)
        99_000,   # day 4: +1.02%
    ]

    # Baseline (e.g. underlying) returns — same as hold returns
    # hold returns: [+1%, -2.475%, +1.015%, -3.015%, +1.036%]
    baseline_returns = [0.01, -0.02475, 0.01015, -0.03015, 0.01036]

    trades = [
        {
            "hedge_active_days": [1, 3],
            "premium_paid": 100.0,
            "payoff": 300.0,
        },
    ]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_equities),
        hold_equity=_make_equity_curve(hold_equities),
        trades=trades,
        initial_capital=100_000,
        baseline_returns=baseline_returns,
    )

    # On downside days (1 and 3):
    # Agent returns: day1 = -1.485%, day3 = -2.0%
    # Baseline returns: day1 = -2.475%, day3 = -3.015%
    # Excess: day1 = -0.01485 - (-0.02475) = 0.0099, day3 = -0.02 - (-0.03015) = 0.01015
    # ERBE = (0.0099 + 0.01015) / 2 > 0
    assert result["erbe"] is not None
    assert result["erbe"] > 0, f"Expected ERBE > 0, got {result['erbe']}"


def test_erbe_zero_when_no_downside_days():
    """ERBE is None when there are no downside days (sum_D = 0)."""
    # All days are up days
    n = 6
    hold_eq = [100_000 + 500 * i for i in range(n)]
    agent_eq = [100_000 + 600 * i for i in range(n)]

    baseline_returns = [0.005] * (n - 1)

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_eq),
        hold_equity=_make_equity_curve(hold_eq),
        trades=[],
        initial_capital=100_000,
        baseline_returns=baseline_returns,
    )

    # No downside days, so ERBE cannot be computed -> None
    assert result["erbe"] is None


def test_fhr():
    """Hedge active on non-downside days contributes to FHR."""
    hold_equities = [
        100_000,
        101_000,  # day 0: +1% (no downside)
        102_000,  # day 1: +0.99% (no downside)
        100_000,  # day 2: -1.96% (downside)
        101_000,  # day 3: +1% (no downside)
    ]
    agent_equities = [100_000, 101_000, 102_000, 100_500, 101_500]

    # Hedge active on ALL days (0, 1, 2, 3)
    trades = [
        {
            "hedge_active_days": [0, 1, 2, 3],
            "premium_paid": 200.0,
            "payoff": 300.0,
        },
    ]

    result = compute_hedging_metrics(
        agent_equity=_make_equity_curve(agent_equities),
        hold_equity=_make_equity_curve(hold_equities),
        trades=trades,
        initial_capital=100_000,
    )

    # 1 downside day (day 2), 3 non-downside days (0, 1, 3)
    # Hedge active on all 3 non-downside days -> FHR = 3/3 = 1.0
    assert result["fhr"] == pytest.approx(1.0), f"Expected FHR=1.0, got {result['fhr']}"


# ===================================================================
# Edge Cases
# ===================================================================


def test_empty_equity_curves():
    """Empty curves should not crash; return zero/None metrics."""
    result_pi = compute_profit_increase_metrics(
        agent_equity=[],
        hold_equity=[],
        etf_equity=None,
        trades=[],
        initial_capital=100_000,
    )
    # Should get numeric values without crashing
    assert result_pi["excess_ar_hold"] == 0.0
    assert "rrs" in result_pi

    result_h = compute_hedging_metrics(
        agent_equity=[],
        hold_equity=[],
        trades=[],
        initial_capital=100_000,
    )
    assert result_h["har"] == 0.0
    assert "erbe" in result_h
