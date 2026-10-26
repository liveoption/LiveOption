"""Advanced scenario performance metrics (0-1 / 0-100 scale).

Implements comprehensive, normalized metrics for cross-scenario and cross-model
evaluation. All metrics are designed to be absolute values in 0-1 or 0-100 scale.

Reference: docs/ADVANCED_SCENARIO_METRICS.md
"""

from __future__ import annotations

import math
from typing import Optional

_EPS = 1e-12


# ============================================================================
# TIER 1: Basic Normalized Metrics (0-100 Scale)
# ============================================================================


def _normalize(value: float, target: float, cap: float = 100.0) -> float:
    """Normalize value to [0, cap] where target=100 (or cap if value > target).

    Parameters
    ----------
    value : float
        The value to normalize.
    target : float
        The target/baseline value (corresponds to 100 or cap).
    cap : float
        Maximum cap for the normalized value (default 100).

    Returns
    -------
    float
        Normalized value in [0, cap].
    """
    if target <= 0:
        return 0.0
    normalized = (value / target) * cap
    return min(cap, max(0.0, normalized))


def profitability_score(
    return_pct: float,
    sharpe_ratio: float,
    win_rate: float,
    return_target: float = 5.0,
    sharpe_target: float = 0.5,
    wr_target: float = 50.0,
) -> float:
    """Profitability Score (0-100).

    Measures total return adjusted for risk taken.

    Parameters
    ----------
    return_pct : float
        Total return percentage (0-100).
    sharpe_ratio : float
        Sharpe ratio (annualized).
    win_rate : float
        Win rate (0-100).
    return_target : float
        Target return for 100 score (default 5%).
    sharpe_target : float
        Target Sharpe for 100 score (default 0.5).
    wr_target : float
        Target win rate for 100 score (default 50%).

    Returns
    -------
    float
        Score in [0, 100].
    """
    r_score = _normalize(return_pct, return_target, cap=100)
    s_score = _normalize(sharpe_ratio, sharpe_target, cap=100)
    wr_score = _normalize(win_rate, wr_target, cap=100)

    return (r_score * 0.4 + s_score * 0.4 + wr_score * 0.2)


def risk_management_score(
    max_drawdown: float,
    cvar_5pct: float,
    expected_return: float,
    annual_return: float = 5.0,
    target_dd: float = 20.0,
) -> float:
    """Risk Management Score (0-100).

    Evaluates downside control relative to upside.

    Parameters
    ----------
    max_drawdown : float
        Maximum drawdown (0-1, e.g., 0.15 = 15%).
    cvar_5pct : float
        Conditional Value at Risk at 5% tail (typically negative).
    expected_return : float
        Expected return per trade (in dollars or %).
    annual_return : float
        Expected annual return (for Calmar ratio) (default 5%).
    target_dd : float
        Target max drawdown (%) for baseline (default 20%).

    Returns
    -------
    float
        Score in [0, 100].
    """
    # 1. Drawdown component (lower is better)
    dd_pct = max_drawdown * 100
    dd_score = 50 * (1 - min(1.0, dd_pct / target_dd))

    # 2. Calmar ratio component (annual_return / max_drawdown)
    if max_drawdown > 0:
        calmar = annual_return / (dd_pct + _EPS)
        calmar_score = _normalize(calmar, target=0.5, cap=25)
    else:
        calmar_score = 25 if annual_return > 0 else 0

    # 3. CVaR tail ratio component (expected / tail_loss, capped at 2.0)
    if cvar_5pct < 0 and expected_return > 0:
        tail_ratio = expected_return / (abs(cvar_5pct) + _EPS)
        tail_score = _normalize(min(tail_ratio, 2.0), target=1.5, cap=25)
    else:
        tail_score = 0.0

    return dd_score + calmar_score + tail_score


def consistency_score(
    returns_list: list[float],
    mean_return: float = 0.0,
) -> float:
    """Consistency Score (0-100).

    Measures return stability across time periods.

    Parameters
    ----------
    returns_list : list[float]
        Period-over-period returns (e.g., daily returns as decimals).
    mean_return : float
        Mean of returns (computed if not provided).

    Returns
    -------
    float
        Score in [0, 100].
    """
    if len(returns_list) < 2:
        return 50.0  # Neutral for insufficient data

    if not mean_return:
        mean_return = sum(returns_list) / len(returns_list)

    # Coefficient of Variation (lower is better for consistency)
    variance = sum((r - mean_return) ** 2 for r in returns_list) / (len(returns_list) - 1)
    std_dev = math.sqrt(variance)

    if std_dev == 0 or mean_return == 0:
        cv = 0.0
    else:
        cv = std_dev / abs(mean_return)

    # CV score: 1 - min(cv, 2.0) / 2.0 (cap at 2.0 CV)
    cv_score = 50 * (1 - min(cv, 2.0) / 2.0)

    # Fraction of positive days
    positive_days = sum(1 for r in returns_list if r > 0)
    frac_positive = positive_days / len(returns_list)
    pos_score = 30 * frac_positive

    # Skewness (prefer negative skew for options: large losses rare)
    if std_dev > 0:
        skewness = sum((r - mean_return) ** 3 for r in returns_list) / (len(returns_list) * (std_dev ** 3))
        # Normalize skewness to [-1, 1], prefer negative skew
        skew_score = 20 * max(0, 1 - max(skewness, -1))
    else:
        skew_score = 20.0

    return cv_score + pos_score + skew_score


def execution_quality_score(
    fill_ratio: float,
    turnover: float,
    fees_ratio: float,
    turnover_target: float = 2.0,
    fees_target: float = 0.5,
) -> float:
    """Execution Quality Score (0-100).

    Evaluates trade execution efficiency and costs.

    Parameters
    ----------
    fill_ratio : float
        Fraction of orders filled (0-1).
    turnover : float
        Turnover ratio (total_traded_value / avg_equity).
    fees_ratio : float
        Fee percentage (fees / total_pnl × 100).
    turnover_target : float
        Target turnover for 100 score (default 2.0).
    fees_target : float
        Target fees % for 100 score (default 0.5%).

    Returns
    -------
    float
        Score in [0, 100].
    """
    fill_score = 50 * fill_ratio

    # Turnover component (lower is better, normalized to target)
    turnover_score = 30 * (1 - min(1.0, turnover / turnover_target))

    # Fees component (lower is better)
    fees_score = 20 * (1 - min(1.0, fees_ratio / fees_target))

    return fill_score + turnover_score + fees_score


def trade_quality_score(
    win_rate: float,
    payoff_ratio: float,
    expected_value: float,
    initial_capital: float = 100_000.0,
    wr_target: float = 55.0,
    pr_target: float = 1.5,
) -> float:
    """Trade Quality Score (0-100).

    Measures quality of individual trading decisions.

    Parameters
    ----------
    win_rate : float
        Win rate (0-100).
    payoff_ratio : float
        Average win / average loss.
    expected_value : float
        Expected value per trade (in dollars).
    initial_capital : float
        Initial capital (for EV normalization).
    wr_target : float
        Target win rate for 100 score (default 55%).
    pr_target : float
        Target payoff ratio for 100 score (default 1.5).

    Returns
    -------
    float
        Score in [0, 100].
    """
    wr_score = _normalize(win_rate, wr_target, cap=40)

    # Cap payoff ratio at 3.0 for scoring
    pr_capped = min(payoff_ratio, 3.0)
    pr_score = _normalize(pr_capped, pr_target, cap=35)

    # Expected value component
    # Normalize to 1% of initial capital as target
    ev_target = initial_capital * 0.01
    ev_score = _normalize(expected_value, ev_target, cap=25)

    return wr_score + pr_score + ev_score


# ============================================================================
# TIER 2: Composite Indices (0-1 Scale)
# ============================================================================


def overall_performance_index(
    profitability: float,
    risk_management: float,
    consistency: float,
    trade_quality: float,
    execution: float,
) -> float:
    """Overall Performance Index (OPI) (0-1).

    Single-number ranking for cross-scenario comparison.

    Parameters
    ----------
    profitability : float
        Profitability score (0-100).
    risk_management : float
        Risk management score (0-100).
    consistency : float
        Consistency score (0-100).
    trade_quality : float
        Trade quality score (0-100).
    execution : float
        Execution quality score (0-100).

    Returns
    -------
    float
        OPI in [0, 1].
    """
    opi = (
        0.35 * (profitability / 100)
        + 0.25 * (risk_management / 100)
        + 0.20 * (consistency / 100)
        + 0.15 * (trade_quality / 100)
        + 0.05 * (execution / 100)
    )
    return min(1.0, opi)


def risk_adjusted_return_index(
    sharpe_ratio: float,
    sortino_ratio: float,
    calmar_ratio: float,
) -> float:
    """Risk-Adjusted Return Index (RARI) (0-1).

    Isolates return quality from risk management.

    Parameters
    ----------
    sharpe_ratio : float
        Sharpe ratio (annualized).
    sortino_ratio : float
        Sortino ratio (annualized).
    calmar_ratio : float
        Calmar ratio (annual return / max DD).

    Returns
    -------
    float
        RARI in [0, 1].
    """
    sharpe_score = min(1.0, sharpe_ratio / 2.0)
    sortino_score = min(1.0, sortino_ratio / 3.0)
    calmar_score = min(1.0, calmar_ratio / 1.0)

    rari = 0.60 * sharpe_score + 0.20 * sortino_score + 0.20 * calmar_score
    return min(1.0, rari)


def downside_protection_index(
    max_drawdown: float,
    cvar_5pct: float,
    expected_daily_return: float,
    tail_loss_severity: float = 0.0,
    drawdown_recovery_days: int = 0,
) -> float:
    """Downside Protection Index (DPI) (0-1).

    Measures effectiveness of risk controls and tail protection.

    Parameters
    ----------
    max_drawdown : float
        Maximum drawdown (0-1, e.g., 0.15 = 15%).
    cvar_5pct : float
        CVaR at 5% tail (typically negative).
    expected_daily_return : float
        Expected daily return (e.g., 0.001 = 0.1%).
    tail_loss_severity : float
        Severe loss percentage (default 0%).
    drawdown_recovery_days : int
        Average days to recover from drawdown (default 0).

    Returns
    -------
    float
        DPI in [0, 1].
    """
    # Drawdown component (lower is better; target 25%)
    dd_score = 0.40 * (1 - min(1.0, (max_drawdown * 100) / 25.0))

    # CVaR component
    if cvar_5pct < 0 and expected_daily_return > 0:
        cvar_ratio = expected_daily_return / (abs(cvar_5pct) + _EPS)
        cvar_score = 0.30 * min(1.0, cvar_ratio)
    else:
        cvar_score = 0.0

    # Tail loss component (target 5%)
    tail_score = 0.20 * (1 - min(1.0, tail_loss_severity / 5.0))

    # Recovery time component (target 20 days)
    recovery_score = 0.10 * (1 - min(1.0, drawdown_recovery_days / 20.0))

    return dd_score + cvar_score + tail_score + recovery_score


def model_reliability_index(
    consistency: float,
    prediction_error_rate: float = 0.0,
    scenario_std_dev: float = 0.0,
    scenario_mean_return: float = 0.0,
    fill_ratio: float = 1.0,
) -> float:
    """Model Reliability Index (MRI) (0-1).

    Evaluates consistency of model performance across scenarios.

    Parameters
    ----------
    consistency : float
        Consistency score (0-100).
    prediction_error_rate : float
        Fraction of incorrect predictions (0-1).
    scenario_std_dev : float
        Std dev of returns across scenarios.
    scenario_mean_return : float
        Mean return across scenarios.
    fill_ratio : float
        Order fill ratio (0-1).

    Returns
    -------
    float
        MRI in [0, 1].
    """
    consistency_score = 0.40 * (consistency / 100)
    error_score = 0.30 * (1 - prediction_error_rate)

    # Scenario stability (lower variation is better)
    if scenario_mean_return > 0:
        cv = scenario_std_dev / scenario_mean_return
        stability_score = 0.20 * (1 - min(1.0, cv))
    else:
        stability_score = 0.0

    fill_score = 0.10 * fill_ratio

    mri = consistency_score + error_score + stability_score + fill_score
    return min(1.0, mri)


# ============================================================================
# TIER 3: Strategy-Specific Composites (0-1 Scale)
# ============================================================================


def earnings_diagnostics(
    directional_accuracy: float,
    vega_exposure_at_entry: float,
    avg_pnl_correct: float,
    avg_pnl_wrong: float,
    total_pnl: float = 0.0,
    total_premium_paid: float = 1.0,
) -> dict[str, float]:
    """Detailed diagnostics for earnings trading (VEE, CP&L, WP&L).

    Parameters
    ----------
    directional_accuracy : float
        Fraction of correct direction (0-1).
    vega_exposure_at_entry : float
        Vega sensitivity at entry (V_entry * delta_sigma / premium).
    avg_pnl_correct : float
        Average P&L when direction correct.
    avg_pnl_wrong : float
        Average P&L when direction wrong.
    total_pnl : float
        Total P&L.
    total_premium_paid : float
        Total premium spent.

    Returns
    -------
    dict
        Keys: da, vee, cpl, wpl, premium_efficiency.
    """
    return {
        "da": round(directional_accuracy, 4),
        "vee": round(min(vega_exposure_at_entry, 10.0), 4),  # Cap for sanity
        "cpl": round(avg_pnl_correct, 2),
        "wpl": round(avg_pnl_wrong, 2),
        "premium_efficiency": round(total_pnl / total_premium_paid, 4) if total_premium_paid > _EPS else 0.0,
    }


def earnings_strategy_composite(
    directional_accuracy: float,
    avg_pnl_correct: float,
    avg_pnl_wrong: float,
    skip_rate: float,
    total_pnl: float = 0.0,
    total_premium_paid: float = 1.0,
    vega_exposure_at_entry: float = 0.0,
    da_target: float = 55.0,
) -> float:
    """Earnings Strategy Composite (0-1).

    Integrates directional accuracy, vega exposure, and P&L differentials.

    Parameters
    ----------
    directional_accuracy : float
        Fraction of correct direction calls (0-1).
    avg_pnl_correct : float
        Average P&L when direction was correct.
    avg_pnl_wrong : float
        Average P&L when direction was wrong (typically negative).
    skip_rate : float
        Fraction of events skipped (0-1).
    total_pnl : float
        Total P&L (for premium efficiency).
    total_premium_paid : float
        Total premium spent.
    vega_exposure_at_entry : float
        Vega exposure at entry (used for diagnostics).
    da_target : float
        Target directional accuracy (%) for 100 score (default 55%).

    Returns
    -------
    float
        Composite in [0, 1].
    """
    # DA component
    da_score = _normalize(directional_accuracy * 100, da_target, cap=100)
    da_component = 0.40 * (da_score / 100)

    # Conditional P&L component (penalize if CP&L is too low despite correct direction)
    if abs(avg_pnl_wrong) > _EPS:
        pnl_diff = (avg_pnl_correct - avg_pnl_wrong) / abs(avg_pnl_wrong)
        pnl_score = _normalize(min(pnl_diff, 2.0), target=1.0, cap=100)
    else:
        pnl_score = 0.0
    pnl_component = 0.25 * (pnl_score / 100)

    # Skip rate component (reward selectivity)
    selectivity_component = 0.20 * (1 - skip_rate)

    # Premium efficiency component
    if total_premium_paid > _EPS:
        premium_eff = total_pnl / total_premium_paid
        eff_score = _normalize(min(premium_eff, 2.0), target=1.5, cap=100)
    else:
        eff_score = 0.0
    eff_component = 0.15 * (eff_score / 100)

    return da_component + pnl_component + selectivity_component + eff_component


def overlay_diagnostics(
    excess_ar_hold: float,
    downside_protection_score: float,
    upside_sacrifice_ratio: float = 0.0,
    income_contribution_ratio: float = 0.0,
    regime_robustness_score: float = 0.0,
    baseline_dominance_rate: float = 0.0,
) -> dict[str, float]:
    """Detailed diagnostics for overlay strategies (DPS, ICR, USR, RRS, BDR).

    Parameters
    ----------
    excess_ar_hold : float
        Excess AR vs buy-and-hold (%).
    downside_protection_score : float
        DPS in [0, 1].
    upside_sacrifice_ratio : float
        USR in [0, 1] (how much upside is lost).
    income_contribution_ratio : float
        ICR (premium / total P&L).
    regime_robustness_score : float
        RRS (performance consistency across regimes).
    baseline_dominance_rate : float
        BDR (fraction of windows where agent > baseline).

    Returns
    -------
    dict
        Diagnostic metrics.
    """
    return {
        "excess_ar": round(excess_ar_hold, 4),
        "dps": round(min(downside_protection_score, 1.0), 4),
        "usr": round(min(upside_sacrifice_ratio, 1.0), 4),
        "icr": round(income_contribution_ratio, 4),
        "rrs": round(regime_robustness_score, 4),
        "bdr": round(baseline_dominance_rate, 4),
    }


def overlay_strategy_composite(
    excess_ar_hold: float,
    downside_protection_score: float,
    volatility_delta: float,
    baseline_dominance_rate: float,
    income_contribution_ratio: float,
    upside_sacrifice_ratio: float = 0.0,
    regime_robustness_score: float = 0.0,
    target_excess_ar: float = 2.0,
) -> float:
    """Overlay Strategy Composite (0-1).

    Evaluates yield-enhancement strategies through profitability, risk control,
    and behavioral alignment (reduced upside sacrifice, regime robustness).

    Parameters
    ----------
    excess_ar_hold : float
        Excess annualized return over buy-and-hold baseline (%).
    downside_protection_score : float
        DPS = (max_dd_hold - max_dd_agent) / max_dd_hold (0-1).
    volatility_delta : float
        Change in volatility (agent - hold) (%).
    baseline_dominance_rate : float
        Fraction of rolling windows where agent > hold (0-1).
    income_contribution_ratio : float
        Premium received / total P&L.
    upside_sacrifice_ratio : float
        USR (1 - agent_upside / hold_upside), target ~0.2-0.3.
    regime_robustness_score : float
        RRS (mean excess AR - lambda * std), target > 0.
    target_excess_ar : float
        Target excess return for 100 score (%, default 2%).

    Returns
    -------
    float
        Composite in [0, 1].
    """
    # Excess AR component
    ar_score = _normalize(excess_ar_hold, target_excess_ar, cap=100)
    ar_component = 0.35 * (ar_score / 100)

    # DPS component (higher is better)
    dps_component = 0.25 * min(1.0, downside_protection_score)

    # Volatility delta component (lower is better, target 2%)
    vol_score = _normalize(volatility_delta, target=2.0, cap=100)
    vol_component = 0.20 * (1 - vol_score / 100)

    # BDR component (higher is better, target 60%)
    bdr_component = 0.15 * min(1.0, baseline_dominance_rate / 0.6)

    # ICR component (target 1.0)
    icr_score = _normalize(min(income_contribution_ratio, 2.0), target=1.0, cap=100)
    icr_component = 0.05 * (icr_score / 100)

    return ar_component + dps_component + vol_component + bdr_component + icr_component


def intraday_diagnostics(
    daily_return_pct: float,
    intraday_max_drawdown: float,
    theta_realization_rate: float,
    vega_contribution: float = 0.0,
    gamma_contribution: float = 0.0,
    daily_win_rate: float = 0.0,
) -> dict[str, float]:
    """Detailed diagnostics for 0DTE intraday trading (Greeks decomposition).

    Parameters
    ----------
    daily_return_pct : float
        Daily return (%).
    intraday_max_drawdown : float
        Intraday MDD (0-1).
    theta_realization_rate : float
        Theta realization (0-2+).
    vega_contribution : float
        Vega contribution to P&L (0-1, fraction).
    gamma_contribution : float
        Gamma contribution to P&L (0-1, fraction).
    daily_win_rate : float
        Win rate (0-1).

    Returns
    -------
    dict
        Diagnostic metrics.
    """
    return {
        "daily_return": round(daily_return_pct, 4),
        "imdd": round(intraday_max_drawdown * 100, 4),
        "theta_realization": round(theta_realization_rate, 4),
        "vega_contrib": round(min(abs(vega_contribution), 1.0), 4),
        "gamma_contrib": round(min(abs(gamma_contribution), 1.0), 4),
        "daily_win_rate": round(daily_win_rate, 4),
    }


def intraday_strategy_composite(
    daily_return_pct: float,
    intraday_max_drawdown: float,
    theta_realization_rate: float,
    daily_win_rate: float,
    fill_ratio: float,
    vega_contribution: float = 0.0,
    gamma_contribution: float = 0.0,
    return_target: float = 2.0,
    imdd_target: float = 8.0,
    theta_target: float = 0.8,
    wr_target: float = 55.0,
) -> float:
    """0DTE Intraday Strategy Composite (0-1).

    Evaluates ultra-short-horizon trading through profitability, risk control,
    theta efficiency, and sustainable edge sources (Greeks decomposition).

    Parameters
    ----------
    daily_return_pct : float
        Daily return percentage (%).
    intraday_max_drawdown : float
        Intraday maximum drawdown (0-1, e.g., 0.08 = 8%).
    theta_realization_rate : float
        Actual theta P&L / theoretical theta (0-1).
    daily_win_rate : float
        Fraction of winning days (0-1).
    fill_ratio : float
        Order fill ratio (0-1).
    vega_contribution : float
        Vega contribution to total P&L (0-1, fraction).
    gamma_contribution : float
        Gamma contribution to total P&L (0-1, fraction).
    return_target : float
        Target daily return for 100 score (%, default 2%).
    imdd_target : float
        Target intraday MDD for 100 score (%, default 8%).
    theta_target : float
        Target theta realization for 100 score (default 0.8).
    wr_target : float
        Target daily win rate for 100 score (%, default 55%).

    Returns
    -------
    float
        Composite in [0, 1].
    """
    # Return component
    return_score = _normalize(daily_return_pct, return_target, cap=100)
    return_component = 0.35 * (return_score / 100)

    # Drawdown component (lower is better)
    imdd_pct = intraday_max_drawdown * 100
    imdd_score = _normalize(imdd_pct, imdd_target, cap=100)
    dd_component = 0.25 * (1 - imdd_score / 100)

    # Theta realization component
    theta_score = _normalize(min(theta_realization_rate, 1.0), theta_target, cap=100)
    theta_component = 0.20 * (theta_score / 100)

    # Win rate component
    wr_score = _normalize(daily_win_rate * 100, wr_target, cap=100)
    wr_component = 0.15 * (wr_score / 100)

    # Fill component
    fill_component = 0.05 * fill_ratio

    return return_component + dd_component + theta_component + wr_component + fill_component


# ============================================================================
# TIER 3 (Extended): Hedging and Diagnostic Metrics
# ============================================================================


def hedging_strategy_composite(
    hedge_active_rate: float,
    event_conditional_active_rate: float,
    false_hedge_rate: float,
    hedging_efficiency: float,
    event_payout_coverage: float,
    hedge_success_rate: float,
) -> float:
    """Hedging Strategy Composite (0-1).

    Evaluates hedge quality through activity timing, cost efficiency, and success.

    Parameters
    ----------
    hedge_active_rate : float
        Fraction of days with active hedge (0-1).
    event_conditional_active_rate : float
        Fraction of downside days with active hedge (0-1), target ~0.8+.
    false_hedge_rate : float
        Fraction of false hedges (during stable periods) (0-1), target ~0.1-.
    hedging_efficiency : float
        Hedge income / cost (0-2+), target > 1.0.
    event_payout_coverage : float
        Hedge payout / unhedged loss (0-2+), target 1.0.
    hedge_success_rate : float
        Fraction of downside days where hedge helped (0-1), target ~0.7+.

    Returns
    -------
    float
        Composite in [0, 1].
    """
    # Activity timing (reward high event coverage, penalize false hedges)
    activity_score = (event_conditional_active_rate * 0.7) + ((1 - false_hedge_rate) * 0.3)
    activity_component = 0.25 * min(1.0, activity_score)

    # Efficiency component (HE should be > 1.0)
    he_score = _normalize(hedging_efficiency, target=1.5, cap=100)
    efficiency_component = 0.25 * (he_score / 100)

    # Coverage component (EPC should be close to 1.0)
    coverage_score = _normalize(event_payout_coverage, target=1.0, cap=100)
    coverage_component = 0.25 * (coverage_score / 100)

    # Success rate component
    hsr_score = _normalize(hedge_success_rate * 100, target=70.0, cap=100)
    success_component = 0.25 * (hsr_score / 100)

    return activity_component + efficiency_component + coverage_component + success_component


# ============================================================================
# Extended Diagnostic Indices
# ============================================================================


def greek_edge_index(
    theta_pnl: float,
    vega_pnl: float,
    gamma_pnl: float,
    total_pnl: float,
) -> float:
    """Greek Edge Index (0-1).

    Measures dependence on sustainable options edges (theta, vega, gamma).
    Higher score means more of the P&L comes from structural edges rather
    than directional luck.

    Parameters
    ----------
    theta_pnl : float
        P&L from time decay.
    vega_pnl : float
        P&L from volatility moves.
    gamma_pnl : float
        P&L from convexity/gamma.
    total_pnl : float
        Total P&L.

    Returns
    -------
    float
        Edge index in [0, 1].
    """
    if total_pnl <= 0:
        return 0.0

    edge_pnl = theta_pnl + (0.5 * vega_pnl) + (0.5 * gamma_pnl)
    edge_ratio = edge_pnl / total_pnl

    # Clamp to [0, 1]: >100% edge means gamma + vega provided more than total
    return min(1.0, max(0.0, edge_ratio))


def iv_crush_resilience_index(
    avg_pnl_with_iv_crush: float,
    avg_pnl_without_iv_crush: float,
) -> float:
    """IV Crush Resilience Index (0-1).

    Measures how well strategy performs despite post-event IV crush
    (relevant for earnings and high-volatility scenarios).

    Parameters
    ----------
    avg_pnl_with_iv_crush : float
        Average P&L in high IV-crush scenarios.
    avg_pnl_without_iv_crush : float
        Average P&L in low IV-crush scenarios.

    Returns
    -------
    float
        Resilience score in [0, 1].
    """
    if avg_pnl_without_iv_crush <= _EPS:
        return 0.0

    # Ratio: want to minimize loss vs no-crush case
    loss_ratio = min(0, avg_pnl_with_iv_crush) / abs(avg_pnl_without_iv_crush)
    resilience = max(0.0, 1.0 + loss_ratio)  # 0 = complete loss, 1 = unaffected

    return min(1.0, resilience)


def leverage_assessment_index(
    max_single_position_leverage: float,
    avg_leverage: float,
    leverage_target: float = 1.5,
) -> float:
    """Leverage Assessment Index (0-1).

    Evaluates risk profile through position sizing and leverage usage.

    Parameters
    ----------
    max_single_position_leverage : float
        Maximum leverage on any single position (>1.0 = leveraged).
    avg_leverage : float
        Average portfolio leverage.
    leverage_target : float
        Target average leverage for 100 score (default 1.5x).

    Returns
    -------
    float
        Index in [0, 1] (higher = more conservative/appropriate sizing).
    """
    # Penalize excessive single-position leverage
    max_lev_penalty = max(0.0, 1.0 - (max_single_position_leverage - 2.0) / 5.0)

    # Reward appropriate average leverage
    avg_lev_score = _normalize(avg_leverage, leverage_target, cap=100)
    avg_lev_component = 0.7 * max(0.0, 1.0 - abs(avg_lev_score / 100 - 0.5))

    return (max_lev_penalty * 0.3) + avg_lev_component


# ============================================================================
# Utility Functions
# ============================================================================


def percentile_rank(score: float, all_scores: list[float]) -> float:
    """Compute percentile rank of a score (0-100).

    Parameters
    ----------
    score : float
        The score to rank.
    all_scores : list[float]
        All scores for comparison.

    Returns
    -------
    float
        Percentile rank in [0, 100].
    """
    if not all_scores:
        return 0.0
    sorted_scores = sorted(all_scores, reverse=True)
    try:
        rank = sorted_scores.index(score)
    except ValueError:
        # Find closest match if exact match not found
        rank = sum(1 for s in sorted_scores if s > score)

    if len(sorted_scores) == 1:
        return 100.0

    percentile = (1 - rank / (len(sorted_scores) - 1)) * 100
    return min(100.0, max(0.0, percentile))


def compute_all_metrics(
    equity_curve: list[dict],
    trades: list[dict],
    events: list[dict] | None = None,
    baseline_equity: list[dict] | None = None,
    strategy_type: str = "general",
    **kwargs,
) -> dict:
    """Compute all advanced metrics from raw data.

    Parameters
    ----------
    equity_curve : list[dict]
        Agent equity curve [{"ts": str, "equity": float}, ...].
    trades : list[dict]
        Trade list with pnl, qty, price, fees, etc.
    events : list[dict], optional
        Events list for earnings-specific metrics.
    baseline_equity : list[dict], optional
        Baseline equity curve for overlay comparison.
    strategy_type : str
        Strategy type: "earnings", "overlay", "intraday", or "general".
    **kwargs
        Additional parameters for normalization targets.

    Returns
    -------
    dict
        Comprehensive metrics dict with all tiers and indices.
    """
    # Import base metrics computation
    from trading.evaluation.metrics import (
        compute_metrics,
        _period_returns,
        _max_drawdown,
    )

    # Compute base metrics
    base_metrics = compute_metrics(equity_curve, trades, scale="1d")

    if not equity_curve:
        return {
            "tier1": {},
            "tier2": {},
            "tier3": {},
            "error": "empty_equity_curve",
        }

    equities = [p["equity"] for p in equity_curve]
    returns = _period_returns(equities)

    # Compute Tier 1 scores
    tier1 = {
        "profitability": profitability_score(
            base_metrics["return_pct"],
            base_metrics["sharpe"],
            base_metrics["win_rate"] * 100,
            **{k: v for k, v in kwargs.items() if k.endswith("_target")},
        ),
        "risk_management": risk_management_score(
            base_metrics["max_drawdown"],
            base_metrics["cvar_5pct"],
            base_metrics["expected_value"],
            **{k: v for k, v in kwargs.items() if k in ["annual_return", "target_dd"]},
        ),
        "consistency": consistency_score(returns),
        "execution": execution_quality_score(
            base_metrics["fill_ratio"],
            base_metrics["turnover"],
            base_metrics["fees_paid"] / (abs(base_metrics["pnl"]) + 1) * 100,
        ),
        "trade_quality": trade_quality_score(
            base_metrics["win_rate"] * 100,
            base_metrics["payoff_ratio"],
            base_metrics["expected_value"],
        ),
    }

    # Compute Tier 2 indices
    tier2 = {
        "opi": overall_performance_index(**tier1),
        "rari": risk_adjusted_return_index(
            base_metrics["sharpe"],
            base_metrics.get("sortino", base_metrics["sharpe"]),
            base_metrics.get("calmar", 0.0),
        ),
        "dpi": downside_protection_index(
            base_metrics["max_drawdown"],
            base_metrics["cvar_5pct"],
            sum(returns) / len(returns) if returns else 0.0,
        ),
    }

    # Compute Tier 3 (strategy-specific)
    tier3 = {}
    if strategy_type == "earnings" and events:
        from trading.evaluation.earnings_metrics import compute_earnings_metrics

        earnings_metrics = compute_earnings_metrics(events)
        tier3["earnings_composite"] = earnings_strategy_composite(
            earnings_metrics["da"],
            earnings_metrics.get("cpl", 0.0),
            earnings_metrics.get("wpl", 0.0),
            earnings_metrics.get("skip_rate", 0.0),
            earnings_metrics.get("total_pnl", 0.0),
        )
    elif strategy_type == "overlay" and baseline_equity:
        from trading.evaluation.overlay_metrics import compute_profit_increase_metrics

        overlay_metrics = compute_profit_increase_metrics(
            equity_curve,
            baseline_equity,
            None,  # No ETF benchmark
            trades,
            equities[0] if equities else 100_000,
        )
        tier3["overlay_composite"] = overlay_strategy_composite(
            overlay_metrics.get("excess_ar_hold", 0.0) * 100,
            overlay_metrics.get("dps", 0.0),
            overlay_metrics.get("volatility_delta", 0.0) * 100,
            overlay_metrics.get("bdr", 0.0),
            overlay_metrics.get("icr", 0.0),
        )
    elif strategy_type == "intraday":
        tier3["intraday_composite"] = intraday_strategy_composite(
            base_metrics["return_pct"],
            base_metrics["max_drawdown"],
            0.8,  # Default theta realization (needs to be computed separately)
            base_metrics["win_rate"],
            base_metrics["fill_ratio"],
        )

    # Combine results
    return {
        "base_metrics": base_metrics,
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
    }
