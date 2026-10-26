"""Overlay strategy evaluation metrics (Profit Increase + Hedging).

Computes income and hedging diagnostics. All equity curves
are lists of ``{"ts": str, "equity": float}`` dicts.
"""

from __future__ import annotations

import math
from typing import Optional

from trading.evaluation.metrics import _period_returns, _max_drawdown

# Small constant to avoid division-by-zero
_EPS = 1e-12


# ===================================================================
# Helpers
# ===================================================================

def _annualized_return(equities: list[float], periods_per_year: float = 252.0) -> float:
    """AR = (V_T / V_0) ^ (periods_per_year / T) - 1.

    Returns 0.0 when the curve is too short or V_0 is zero.
    """
    if len(equities) < 2:
        return 0.0
    v0 = equities[0]
    vt = equities[-1]
    if v0 <= 0:
        return 0.0
    T = len(equities) - 1  # number of return periods
    ratio = vt / v0
    if ratio <= 0:
        return -1.0
    return ratio ** (periods_per_year / T) - 1


def _volatility(returns: list[float], periods_per_year: float = 252.0) -> float:
    """Annualized volatility (sample std of returns * sqrt(A))."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def _extract_equities(curve: list[dict]) -> list[float]:
    """Extract the equity values from an equity curve."""
    return [p["equity"] for p in curve]


# ===================================================================
# Profit Increase Metrics
# ===================================================================

def compute_profit_increase_metrics(
    agent_equity: list[dict],
    hold_equity: list[dict],
    etf_equity: list[dict] | None,
    trades: list[dict],
    initial_capital: float,
) -> dict:
    """Compute profit-increase overlay metrics.

    Parameters
    ----------
    agent_equity : list[dict]
        Agent portfolio equity curve ``[{"ts": str, "equity": float}, ...]``.
    hold_equity : list[dict]
        Buy-and-hold baseline equity curve (same timestamps).
    etf_equity : list[dict] | None
        ETF benchmark equity curve (e.g. JEPQ).  ``None`` to skip.
    trades : list[dict]
        Option trades with at least ``pnl`` and ``premium_received`` keys.
    initial_capital : float
        Starting portfolio value.

    Returns
    -------
    dict
        Keys: ``excess_ar_hold``, ``excess_ar_etf``, ``drawdown_delta``,
        ``volatility_delta``, ``dps``, ``icr``, ``usr``, ``bdr``, ``rrs``.
    """
    agent_eq = _extract_equities(agent_equity)
    hold_eq = _extract_equities(hold_equity)

    # Annualized returns
    ar_agent = _annualized_return(agent_eq)
    ar_hold = _annualized_return(hold_eq)

    # 1. Excess AR over Hold
    excess_ar_hold = ar_agent - ar_hold

    # 2. Excess AR over ETF
    excess_ar_etf: float | None = None
    if etf_equity is not None and len(etf_equity) >= 2:
        etf_eq = _extract_equities(etf_equity)
        ar_etf = _annualized_return(etf_eq)
        excess_ar_etf = ar_agent - ar_etf

    # 3. Drawdown Delta  (negative = agent has less drawdown = better)
    mdd_agent = _max_drawdown(agent_eq)
    mdd_hold = _max_drawdown(hold_eq)
    drawdown_delta = mdd_agent - mdd_hold

    # 4. Volatility Delta
    ret_agent = _period_returns(agent_eq)
    ret_hold = _period_returns(hold_eq)
    vol_agent = _volatility(ret_agent)
    vol_hold = _volatility(ret_hold)
    volatility_delta = vol_agent - vol_hold

    # 5. Downside Protection Score (DPS)
    #    DPS = (MDD(hold) - MDD(agent)) / (MDD(hold) + eps)
    dps = (mdd_hold - mdd_agent) / (mdd_hold + _EPS)

    # 6. Income Contribution Ratio (ICR)
    #    ICR = total_premium_received / (|total_pnl| + eps)
    total_premium = sum(t.get("premium_received", 0.0) for t in trades)
    total_pnl = agent_eq[-1] - agent_eq[0] if len(agent_eq) >= 2 else 0.0
    icr = total_premium / (abs(total_pnl) + _EPS)

    # 7. Upside Sacrifice Ratio (USR)
    #    USR = 1 - UP(agent) / (UP(hold) + eps)
    #    where UP = mean return on top 10% days
    up_agent = _upside_potential(ret_agent)
    up_hold = _upside_potential(ret_hold)
    usr = 1.0 - up_agent / (up_hold + _EPS)

    # 8. Baseline Dominance Rate (BDR)
    #    Fraction of 20-day rolling windows where agent cumulative return > hold
    bdr = _baseline_dominance_rate(agent_eq, hold_eq, window=20)

    # 9. Regime Robustness Score (RRS)  — Appendix C.3
    #    RRS = Mean_k(ΔAR_k) - λ · Std_k(ΔAR_k)
    #    where k indexes regime windows (bull / bear / sideways)
    rrs = _regime_robustness_score(agent_eq, hold_eq)

    result: dict = {
        "excess_ar_hold": excess_ar_hold,
        "excess_ar_etf": excess_ar_etf,
        "drawdown_delta": drawdown_delta,
        "volatility_delta": volatility_delta,
        "dps": dps,
        "icr": icr,
        "usr": usr,
        "bdr": bdr,
        "rrs": rrs,
    }
    return result


def _regime_robustness_score(
    agent_eq: list[float],
    hold_eq: list[float],
    rolling_window: int = 20,
    bull_threshold: float = 0.05,
    bear_threshold: float = -0.05,
    lam: float = 1.0,
) -> float:
    """Regime Robustness Score (Appendix C.3).

    Splits the equity curve into regime windows based on the *hold*
    portfolio's rolling return over ``rolling_window`` days:

    - **bull**: rolling return > ``bull_threshold``
    - **bear**: rolling return < ``bear_threshold``
    - **sideways**: otherwise

    For each contiguous regime window we compute:
        ΔAR_k = AR(agent_window) − AR(hold_window)

    Then:
        RRS = Mean(ΔAR_k) − λ · Std(ΔAR_k)

    Returns 0.0 when there are fewer than 2 regime windows.
    """
    n = min(len(agent_eq), len(hold_eq))
    if n <= rolling_window:
        return 0.0

    # Classify each day (starting at index ``rolling_window``) into a regime
    regimes: list[str] = []
    for i in range(rolling_window, n):
        start_val = hold_eq[i - rolling_window]
        if start_val <= 0:
            regimes.append("sideways")
            continue
        rolling_ret = (hold_eq[i] / start_val) - 1
        if rolling_ret > bull_threshold:
            regimes.append("bull")
        elif rolling_ret < bear_threshold:
            regimes.append("bear")
        else:
            regimes.append("sideways")

    # Group into contiguous regime windows and compute ΔAR for each
    delta_ars: list[float] = []
    if not regimes:
        return 0.0

    window_start = 0
    current_regime = regimes[0]
    offset = rolling_window  # regimes[i] corresponds to equity index offset+i

    for i in range(1, len(regimes)):
        if regimes[i] != current_regime:
            # Close out the previous window
            _add_regime_delta_ar(
                agent_eq, hold_eq, offset + window_start, offset + i, delta_ars,
            )
            window_start = i
            current_regime = regimes[i]
    # Final window
    _add_regime_delta_ar(
        agent_eq, hold_eq, offset + window_start, offset + len(regimes), delta_ars,
    )

    if len(delta_ars) < 2:
        # With only one window we cannot compute std; return the mean directly
        return delta_ars[0] if delta_ars else 0.0

    mean_dar = sum(delta_ars) / len(delta_ars)
    var_dar = sum((d - mean_dar) ** 2 for d in delta_ars) / (len(delta_ars) - 1)
    std_dar = math.sqrt(var_dar)
    return mean_dar - lam * std_dar


def _add_regime_delta_ar(
    agent_eq: list[float],
    hold_eq: list[float],
    start_idx: int,
    end_idx: int,
    delta_ars: list[float],
) -> None:
    """Compute ΔAR for a regime window and append to *delta_ars*.

    Requires at least 2 equity points in the window.
    """
    if end_idx - start_idx < 2:
        return
    agent_window = agent_eq[start_idx:end_idx]
    hold_window = hold_eq[start_idx:end_idx]
    ar_a = _annualized_return(agent_window)
    ar_h = _annualized_return(hold_window)
    delta_ars.append(ar_a - ar_h)


def _upside_potential(returns: list[float], quantile: float = 0.90) -> float:
    """Mean return on the top (1 - quantile) fraction of days.

    E.g. quantile=0.90 → mean of top 10% of daily returns.
    Returns 0.0 for empty input.
    """
    if not returns:
        return 0.0
    sorted_r = sorted(returns)
    n = len(sorted_r)
    cutoff_idx = max(int(math.floor(quantile * n)), 0)
    top = sorted_r[cutoff_idx:]
    if not top:
        return 0.0
    return sum(top) / len(top)


def _baseline_dominance_rate(
    agent_eq: list[float],
    baseline_eq: list[float],
    window: int = 20,
) -> float:
    """Fraction of rolling *window*-day windows where agent beats baseline.

    For each window, computes cumulative return = V_end / V_start - 1
    and checks if agent > baseline.
    """
    n = min(len(agent_eq), len(baseline_eq))
    if n <= window:
        return 0.0

    num_windows = 0
    wins = 0
    for start in range(n - window):
        end = start + window
        agent_ret = (agent_eq[end] / agent_eq[start] - 1) if agent_eq[start] > 0 else 0.0
        base_ret = (baseline_eq[end] / baseline_eq[start] - 1) if baseline_eq[start] > 0 else 0.0
        num_windows += 1
        if agent_ret > base_ret:
            wins += 1

    return wins / num_windows if num_windows > 0 else 0.0


# ===================================================================
# Hedging Metrics
# ===================================================================

def compute_hedging_metrics(
    agent_equity: list[dict],
    hold_equity: list[dict],
    trades: list[dict],
    initial_capital: float,
    downside_threshold: float = -0.01,
    baseline_returns: list[float] | None = None,
) -> dict:
    """Compute hedging overlay metrics.

    Parameters
    ----------
    agent_equity : list[dict]
        Hedged portfolio equity curve.
    hold_equity : list[dict]
        Unhedged (buy-and-hold) equity curve.
    trades : list[dict]
        Hedge trades.  Each trade should contain:

        - ``hedge_active_days`` (list[int]): 0-based day indices when
          this hedge position was active.
        - ``premium_paid`` (float): premium paid to open the hedge.
        - ``payoff`` (float): total payoff received from the hedge.
    initial_capital : float
        Initial portfolio value (P_0).
    downside_threshold : float
        Daily return threshold below which a day is classified as a
        downside event (default -1%).
    baseline_returns : list[float] | None
        Per-period returns for a baseline asset (e.g. underlying or ETF).
        When provided, ERBE (Excess Return on Baseline Event days) is
        computed.  Length must match the number of return periods
        (``len(hold_equity) - 1``).  ``None`` to skip.

    Returns
    -------
    dict
        Keys: ``har``, ``ecar``, ``fhr``, ``hcr``, ``hir``, ``he``,
        ``epc``, ``hsr``, ``erbe``.
    """
    hold_eq = _extract_equities(hold_equity)
    agent_eq = _extract_equities(agent_equity)
    T = len(hold_eq)

    if T < 2:
        return {k: 0.0 for k in ("har", "ecar", "fhr", "hcr", "hir", "he", "epc", "hsr")} | {"erbe": None}

    # Daily returns for the unhedged (hold) portfolio
    hold_returns = _period_returns(hold_eq)  # length T-1
    agent_returns = _period_returns(agent_eq)

    num_days = len(hold_returns)  # T-1

    # Build per-day indicators ------------------------------------------

    # H_t: whether a hedge is active on day t (zero-indexed).
    hedge_active = [0] * num_days
    for t in trades:
        for day_idx in t.get("hedge_active_days", []):
            if 0 <= day_idx < num_days:
                hedge_active[day_idx] = 1

    # D_t: downside event indicator
    downside = [1 if r < downside_threshold else 0 for r in hold_returns]

    # Per-day payoff and cost from trades (distribute across active days)
    day_payoff = [0.0] * num_days
    day_cost = [0.0] * num_days
    for t in trades:
        active_days = [d for d in t.get("hedge_active_days", []) if 0 <= d < num_days]
        n_active = len(active_days)
        if n_active == 0:
            continue
        payoff_per_day = t.get("payoff", 0.0) / n_active
        cost_per_day = t.get("premium_paid", 0.0) / n_active
        for d in active_days:
            day_payoff[d] += payoff_per_day
            day_cost[d] += cost_per_day

    # ---------------------------------------------------------------
    # 1. Hedging Active Rate (HAR) = sum(H_t) / T
    # ---------------------------------------------------------------
    har = sum(hedge_active) / num_days

    # ---------------------------------------------------------------
    # 2. Event-Conditional Active Rate (ECAR)
    #    = sum(H_t * D_t) / sum(D_t)
    # ---------------------------------------------------------------
    sum_D = sum(downside)
    ecar = (
        sum(h * d for h, d in zip(hedge_active, downside)) / sum_D
        if sum_D > 0
        else 0.0
    )

    # ---------------------------------------------------------------
    # 3. False Hedge Rate (FHR)
    #    = sum(H_t * (1 - D_t)) / sum(1 - D_t)
    # ---------------------------------------------------------------
    sum_nonD = num_days - sum_D
    fhr = (
        sum(h * (1 - d) for h, d in zip(hedge_active, downside)) / sum_nonD
        if sum_nonD > 0
        else 0.0
    )

    # ---------------------------------------------------------------
    # 4. Hedging Cost Ratio (HCR) = total premium paid / P_0
    # ---------------------------------------------------------------
    total_cost = sum(t.get("premium_paid", 0.0) for t in trades)
    hcr = total_cost / initial_capital if initial_capital > 0 else 0.0

    # ---------------------------------------------------------------
    # 5. Hedging Income Ratio (HIR) = total payoff / P_0
    # ---------------------------------------------------------------
    total_payoff = sum(t.get("payoff", 0.0) for t in trades)
    hir = total_payoff / initial_capital if initial_capital > 0 else 0.0

    # ---------------------------------------------------------------
    # 6. Hedging Efficiency (HE) = HIR / HCR  (>1 means net profitable)
    # ---------------------------------------------------------------
    he = hir / hcr if hcr > 0 else 0.0

    # ---------------------------------------------------------------
    # 7. Event Payout Coverage (EPC)
    #    = sum(I_t * D_t) / sum(L_t^unhedged * D_t)
    #    L_t^unhedged = max(P_{t-1} - P_t, 0) on hold portfolio
    # ---------------------------------------------------------------
    # Unhedged losses on downside days
    unhedged_losses_on_down = 0.0
    payoff_on_down = 0.0
    for i in range(num_days):
        if downside[i]:
            loss = max(hold_eq[i] - hold_eq[i + 1], 0.0)
            unhedged_losses_on_down += loss
            payoff_on_down += day_payoff[i]

    epc = (
        payoff_on_down / unhedged_losses_on_down
        if unhedged_losses_on_down > 0
        else 0.0
    )

    # ---------------------------------------------------------------
    # 8. Hedge Success Rate (HSR)
    #    Fraction of downside days where hedged portfolio beat unhedged
    # ---------------------------------------------------------------
    success_count = 0
    down_count = 0
    for i in range(num_days):
        if downside[i]:
            down_count += 1
            if len(agent_returns) > i and agent_returns[i] > hold_returns[i]:
                success_count += 1

    hsr = success_count / down_count if down_count > 0 else 0.0

    # ---------------------------------------------------------------
    # 9. Excess Return over Baseline on Event days (ERBE) — Appendix C.4
    #    ERBE = Σ_t (r_t - r_t^baseline) * D_t / Σ_t D_t
    # ---------------------------------------------------------------
    erbe: float | None = None
    if baseline_returns is not None and sum_D > 0:
        excess_sum = 0.0
        n_base = len(baseline_returns)
        for i in range(num_days):
            if downside[i] and i < len(agent_returns) and i < n_base:
                excess_sum += agent_returns[i] - baseline_returns[i]
        erbe = excess_sum / sum_D

    return {
        "har": har,
        "ecar": ecar,
        "fhr": fhr,
        "hcr": hcr,
        "hir": hir,
        "he": he,
        "epc": epc,
        "hsr": hsr,
        "erbe": erbe,
    }
