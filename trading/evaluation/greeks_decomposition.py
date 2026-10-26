"""Advanced Greek decomposition and analysis for option trading.

Aggregates Greek-based P&L components across trades and episodes to understand
the sources of profit (delta, gamma, theta, vega, volga, vanna) and compute
Greek realization rates.

"""

from __future__ import annotations

import math
from typing import Optional

_EPS = 1e-12


# ============================================================================
# Single-Trade Greek Decomposition
# ============================================================================


def decompose_trade_greeks(trade: dict) -> dict[str, float | None]:
    """Decompose a single trade's P&L into Greek components using BSM first-order.

    Uses first-order Taylor approximation:
    ΔP&L ≈ Δ·ΔS + 0.5·Γ·(ΔS)² + ν·Δσ + Θ·Δt + (volga·(Δσ)² + vanna·Δσ·ΔS) + ε

    Parameters
    ----------
    trade : dict
        Trade dict with Greek fields. Required for full decomposition:
        - ``entry_underlying_price`` (float): underlying price at entry
        - ``exit_underlying_price`` (float): underlying price at exit
        - ``entry_iv`` (float): implied vol at entry (as decimal)
        - ``exit_iv`` (float): implied vol at exit (as decimal)
        - ``delta`` (float): delta at entry
        - ``gamma`` (float): gamma at entry
        - ``vega`` (float): vega at entry (per 1% IV move)
        - ``theta`` (float): theta at entry (daily decay)
        - ``volga`` (float, optional): volga at entry
        - ``vanna`` (float, optional): vanna at entry
        - ``time_held_days`` (float): days held
        - ``pnl`` (float): realized P&L

    Returns
    -------
    dict
        Keys: delta_pnl, gamma_pnl, vega_pnl, theta_pnl, volga_pnl, vanna_pnl, residual
        All values may be None if required fields missing.
    """
    result = {
        "delta_pnl": None,
        "gamma_pnl": None,
        "vega_pnl": None,
        "theta_pnl": None,
        "volga_pnl": None,
        "vanna_pnl": None,
        "residual": None,
    }

    # Price move component (delta + gamma)
    entry_price = trade.get("entry_underlying_price")
    exit_price = trade.get("exit_underlying_price")
    if entry_price is not None and exit_price is not None and entry_price > 0:
        ds = exit_price - entry_price

        delta = trade.get("delta")
        if delta is not None:
            result["delta_pnl"] = delta * ds

        gamma = trade.get("gamma")
        if gamma is not None:
            result["gamma_pnl"] = 0.5 * gamma * (ds ** 2)

    # IV move component (vega + volga)
    entry_iv = trade.get("entry_iv")
    exit_iv = trade.get("exit_iv")
    if entry_iv is not None and exit_iv is not None:
        div = exit_iv - entry_iv

        vega = trade.get("vega")
        if vega is not None:
            result["vega_pnl"] = vega * div

        volga = trade.get("volga")
        if volga is not None:
            result["volga_pnl"] = 0.5 * volga * (div ** 2)

    # Cross-gamma component (vanna)
    if (
        entry_price is not None
        and exit_price is not None
        and entry_iv is not None
        and exit_iv is not None
        and entry_price > 0
    ):
        ds = exit_price - entry_price
        div = exit_iv - entry_iv
        vanna = trade.get("vanna")
        if vanna is not None:
            result["vanna_pnl"] = vanna * ds * div

    # Time decay component (theta)
    theta = trade.get("theta")
    time_held = trade.get("time_held_days")
    if theta is not None and time_held is not None:
        result["theta_pnl"] = theta * time_held

    # Residual (unexplained P&L)
    actual_pnl = trade.get("pnl")
    if actual_pnl is not None:
        components = [
            result["delta_pnl"],
            result["gamma_pnl"],
            result["vega_pnl"],
            result["volga_pnl"],
            result["vanna_pnl"],
            result["theta_pnl"],
        ]
        computed = sum(c for c in components if c is not None)
        result["residual"] = actual_pnl - computed

    return result


# ============================================================================
# Aggregate Greek Analysis
# ============================================================================


def aggregate_greeks(trades: list[dict]) -> dict[str, float]:
    """Aggregate Greek P&L components across all trades.

    Parameters
    ----------
    trades : list[dict]
        List of trade dicts with Greek decomposition.

    Returns
    -------
    dict
        Keys: delta_pnl, gamma_pnl, vega_pnl, theta_pnl, volga_pnl, vanna_pnl,
        residual, total_pnl, count_with_*
    """
    result = {
        "delta_pnl": 0.0,
        "gamma_pnl": 0.0,
        "vega_pnl": 0.0,
        "theta_pnl": 0.0,
        "volga_pnl": 0.0,
        "vanna_pnl": 0.0,
        "residual": 0.0,
        "total_pnl": 0.0,
        "count_total": len(trades),
        "count_with_delta": 0,
        "count_with_gamma": 0,
        "count_with_vega": 0,
        "count_with_theta": 0,
        "count_with_volga": 0,
        "count_with_vanna": 0,
    }

    for trade in trades:
        decomp = decompose_trade_greeks(trade)
        pnl = trade.get("pnl", 0.0)

        if decomp["delta_pnl"] is not None:
            result["delta_pnl"] += decomp["delta_pnl"]
            result["count_with_delta"] += 1

        if decomp["gamma_pnl"] is not None:
            result["gamma_pnl"] += decomp["gamma_pnl"]
            result["count_with_gamma"] += 1

        if decomp["vega_pnl"] is not None:
            result["vega_pnl"] += decomp["vega_pnl"]
            result["count_with_vega"] += 1

        if decomp["theta_pnl"] is not None:
            result["theta_pnl"] += decomp["theta_pnl"]
            result["count_with_theta"] += 1

        if decomp["volga_pnl"] is not None:
            result["volga_pnl"] += decomp["volga_pnl"]
            result["count_with_volga"] += 1

        if decomp["vanna_pnl"] is not None:
            result["vanna_pnl"] += decomp["vanna_pnl"]
            result["count_with_vanna"] += 1

        if decomp["residual"] is not None:
            result["residual"] += decomp["residual"]

        result["total_pnl"] += pnl

    return result


def greek_contribution_ratios(
    aggregate_greeks: dict[str, float],
) -> dict[str, float]:
    """Compute the contribution of each Greek component to total P&L.

    Parameters
    ----------
    aggregate_greeks : dict[str, float]
        Output from aggregate_greeks().

    Returns
    -------
    dict
        Keys: delta_contrib, gamma_contrib, vega_contrib, theta_contrib,
        volga_contrib, vanna_contrib, residual_contrib
        Each value in [0, 1] (percentage of total P&L).
    """
    total = aggregate_greeks.get("total_pnl", 0.0)
    if total == 0:
        return {
            "delta_contrib": 0.0,
            "gamma_contrib": 0.0,
            "vega_contrib": 0.0,
            "theta_contrib": 0.0,
            "volga_contrib": 0.0,
            "vanna_contrib": 0.0,
            "residual_contrib": 0.0,
        }

    return {
        "delta_contrib": aggregate_greeks.get("delta_pnl", 0.0) / abs(total) if total > 0 else 0.0,
        "gamma_contrib": aggregate_greeks.get("gamma_pnl", 0.0) / abs(total) if total > 0 else 0.0,
        "vega_contrib": aggregate_greeks.get("vega_pnl", 0.0) / abs(total) if total > 0 else 0.0,
        "theta_contrib": aggregate_greeks.get("theta_pnl", 0.0) / abs(total) if total > 0 else 0.0,
        "volga_contrib": aggregate_greeks.get("volga_pnl", 0.0) / abs(total) if total > 0 else 0.0,
        "vanna_contrib": aggregate_greeks.get("vanna_pnl", 0.0) / abs(total) if total > 0 else 0.0,
        "residual_contrib": aggregate_greeks.get("residual", 0.0) / abs(total) if total > 0 else 0.0,
    }


# ============================================================================
# Greek Realization Rates (for 0DTE/intraday)
# ============================================================================


def theta_realization_rate(
    actual_theta_pnl: float,
    theoretical_theta: float,
    time_held_days: float,
) -> float:
    """Compute theta realization rate: actual / theoretical.

    Parameters
    ----------
    actual_theta_pnl : float
        Realized P&L from theta.
    theoretical_theta : float
        Theta value at trade entry (daily decay rate).
    time_held_days : float
        Time held in days.

    Returns
    -------
    float
        Realization rate in [0, 2+] (1.0 = 100% realization).
    """
    if abs(theoretical_theta) < _EPS or time_held_days < _EPS:
        return 0.0
    theoretical_pnl = theoretical_theta * time_held_days
    if abs(theoretical_pnl) < _EPS:
        return 0.0
    return actual_theta_pnl / theoretical_pnl


def gamma_realization_rate(
    actual_gamma_pnl: float,
    theoretical_gamma: float,
    price_move: float,
) -> float:
    """Compute gamma realization rate: actual / theoretical.

    Parameters
    ----------
    actual_gamma_pnl : float
        Realized P&L from gamma (convexity).
    theoretical_gamma : float
        Gamma value at trade entry.
    price_move : float
        Underlying price move (exit_price - entry_price).

    Returns
    -------
    float
        Realization rate in [0, 2+] (1.0 = 100% realization).
    """
    if abs(theoretical_gamma) < _EPS:
        return 0.0
    theoretical_pnl = 0.5 * theoretical_gamma * (price_move ** 2)
    if abs(theoretical_pnl) < _EPS:
        return 0.0
    return actual_gamma_pnl / theoretical_pnl


def vega_realization_rate(
    actual_vega_pnl: float,
    theoretical_vega: float,
    iv_move: float,
) -> float:
    """Compute vega realization rate: actual / theoretical.

    Parameters
    ----------
    actual_vega_pnl : float
        Realized P&L from vega (IV change).
    theoretical_vega : float
        Vega at trade entry (per 1 unit IV move).
    iv_move : float
        IV change (exit_iv - entry_iv).

    Returns
    -------
    float
        Realization rate in [0, 2+] (1.0 = 100% realization).
    """
    if abs(theoretical_vega) < _EPS:
        return 0.0
    theoretical_pnl = theoretical_vega * iv_move
    if abs(theoretical_pnl) < _EPS:
        return 0.0
    return actual_vega_pnl / theoretical_pnl


# ============================================================================
# Daily/Episode-Level Greek Analysis
# ============================================================================


def daily_greeks_summary(daily_trades: list[dict]) -> dict[str, float]:
    """Compute daily Greek breakdown for a single trading day/episode.

    Parameters
    ----------
    daily_trades : list[dict]
        Trades executed on a single day/episode.

    Returns
    -------
    dict
        Daily P&L breakdown by Greek component.
    """
    agg = aggregate_greeks(daily_trades)
    return {
        "date": daily_trades[0].get("date") if daily_trades else None,
        "total_pnl": agg["total_pnl"],
        "delta_pnl": agg["delta_pnl"],
        "gamma_pnl": agg["gamma_pnl"],
        "vega_pnl": agg["vega_pnl"],
        "theta_pnl": agg["theta_pnl"],
        "volga_pnl": agg["volga_pnl"],
        "vanna_pnl": agg["vanna_pnl"],
        "residual": agg["residual"],
        "num_trades": len(daily_trades),
    }


def multi_day_greeks_stats(daily_summaries: list[dict]) -> dict[str, float]:
    """Compute statistics across multiple daily summaries.

    Parameters
    ----------
    daily_summaries : list[dict]
        Output from daily_greeks_summary() for multiple days.

    Returns
    -------
    dict
        Mean, std, min, max for each Greek component.
    """
    if not daily_summaries:
        return {}

    components = ["total_pnl", "delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl", "volga_pnl", "vanna_pnl", "residual"]
    result = {}

    for comp in components:
        values = [d.get(comp, 0.0) for d in daily_summaries]
        result[f"{comp}_mean"] = sum(values) / len(values) if values else 0.0
        result[f"{comp}_std"] = _std_dev(values)
        result[f"{comp}_min"] = min(values) if values else 0.0
        result[f"{comp}_max"] = max(values) if values else 0.0

    return result


def _std_dev(values: list[float]) -> float:
    """Compute sample standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(max(0.0, var))
