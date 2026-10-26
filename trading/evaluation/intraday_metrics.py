"""0DTE intraday-specific evaluation metrics.

Computes intraday diagnostics, including:
  - Intraday Maximum Drawdown (IMDD)
  - Daily Win Rate (DWR)
  - Expected P&L  E[P&L]
  - Payoff Ratio (intraday, on daily P&L)
  - Greeks P&L Decomposition (Delta, Gamma, Vega, Theta, Residual)
  - Theta Realization Rate
  - Vega Contribution

Designed for single-day 0DTE sessions and multi-day aggregation.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Single-day metrics
# ---------------------------------------------------------------------------

def _intraday_max_drawdown(equities: list[float]) -> float:
    """IMDD = max_{t1<t2} (V(t1) - V(t2)) / V(t1).

    Same calculation as regular MDD, applied to a single intraday session.
    Returns a fraction in [0.0, 1.0].  Returns 0.0 for empty or single-point
    curves.
    """
    if len(equities) < 2:
        return 0.0
    peak = equities[0]
    mdd = 0.0
    for v in equities:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def _greeks_decomposition(trade: dict) -> dict[str, float | None]:
    """Decompose a single trade's P&L into Greek components.

    Required fields on *trade* for a full decomposition:
        delta, gamma, vega, theta,
        entry_underlying_price, exit_underlying_price,
        entry_iv, exit_iv,
        time_held_days, pnl

    If any Greek field is missing the corresponding component is ``None``.
    If all Greek fields are present the residual is also computed.

    Returns a dict with keys:
        delta_pnl, gamma_pnl, vega_pnl, theta_pnl, residual
    """
    result: dict[str, float | None] = {
        "delta_pnl": None,
        "gamma_pnl": None,
        "vega_pnl": None,
        "theta_pnl": None,
        "residual": None,
    }

    # Check whether the basic underlying price move is available
    entry_ul = trade.get("entry_underlying_price")
    exit_ul = trade.get("exit_underlying_price")
    has_price_move = entry_ul is not None and exit_ul is not None

    if has_price_move:
        ds = exit_ul - entry_ul

        delta = trade.get("delta")
        if delta is not None:
            result["delta_pnl"] = delta * ds

        gamma = trade.get("gamma")
        if gamma is not None:
            result["gamma_pnl"] = 0.5 * gamma * ds ** 2

    # Vega component requires IV data
    vega = trade.get("vega")
    entry_iv = trade.get("entry_iv")
    exit_iv = trade.get("exit_iv")
    if vega is not None and entry_iv is not None and exit_iv is not None:
        result["vega_pnl"] = vega * (exit_iv - entry_iv)

    # Theta component requires time held
    theta = trade.get("theta")
    time_held = trade.get("time_held_days")
    if theta is not None and time_held is not None:
        result["theta_pnl"] = theta * time_held

    # Residual: only when all four components are computed
    actual_pnl = trade.get("pnl")
    components = [result["delta_pnl"], result["gamma_pnl"],
                  result["vega_pnl"], result["theta_pnl"]]
    if actual_pnl is not None and all(c is not None for c in components):
        result["residual"] = actual_pnl - sum(components)  # type: ignore[arg-type]

    return result


def _aggregate_greeks_decomposition(
    trades: list[dict],
) -> dict[str, float | None]:
    """Aggregate Greeks decomposition across multiple trades.

    Returns summed delta_pnl, gamma_pnl, vega_pnl, theta_pnl, residual.
    A component is ``None`` if *any* trade lacks that component.
    """
    keys = ["delta_pnl", "gamma_pnl", "vega_pnl", "theta_pnl", "residual"]
    totals: dict[str, float | None] = {k: 0.0 for k in keys}

    if not trades:
        return {k: None for k in keys}

    for trade in trades:
        decomp = _greeks_decomposition(trade)
        for k in keys:
            if decomp[k] is None:
                totals[k] = None
            elif totals[k] is not None:
                totals[k] = totals[k] + decomp[k]  # type: ignore[operator]

    return totals


def _theta_realization_rate(trades: list[dict]) -> float | None:
    """Theta Realization Rate = sum(theta_pnl) / sum(theta_entry * dt).

    Returns ``None`` when data is insufficient or theoretical theta is zero.
    """
    actual_theta_pnl = 0.0
    theoretical_theta = 0.0
    valid = False

    for t in trades:
        theta = t.get("theta")
        time_held = t.get("time_held_days")
        pnl = t.get("pnl")
        if theta is None or time_held is None or pnl is None:
            continue

        # Decompose to get actual theta P&L vs theoretical
        decomp = _greeks_decomposition(t)
        if decomp["theta_pnl"] is None:
            continue

        # Theoretical theta contribution = theta * dt
        theoretical_theta += theta * time_held
        # Actual theta P&L from the decomposition
        actual_theta_pnl += decomp["theta_pnl"]  # type: ignore[operator]
        valid = True

    if not valid or theoretical_theta == 0.0:
        return None

    return actual_theta_pnl / theoretical_theta


def _vega_contribution(trades: list[dict]) -> float | None:
    """Vega Contribution = sum(vega_pnl) / total_pnl.

    Returns ``None`` when vega data is unavailable or total P&L is zero.
    """
    decomp = _aggregate_greeks_decomposition(trades)
    vega_pnl = decomp["vega_pnl"]
    if vega_pnl is None:
        return None

    total_pnl = sum(t.get("pnl", 0.0) for t in trades)
    if total_pnl == 0.0:
        return None

    return vega_pnl / total_pnl


# ---------------------------------------------------------------------------
# Public API: single-day
# ---------------------------------------------------------------------------

def compute_intraday_metrics(
    equity_curve: list[dict],
    trades: list[dict],
    initial_capital: float = 100_000.0,
) -> dict:
    """Compute 0DTE intraday metrics for a single trading session.

    Parameters
    ----------
    equity_curve : list[dict]
        Intraday equity points: ``[{"ts": str, "equity": float}, ...]``.
    trades : list[dict]
        Trade records for the session. Optional Greek fields:
        ``delta``, ``gamma``, ``vega``, ``theta``,
        ``entry_underlying_price``, ``exit_underlying_price``,
        ``entry_iv``, ``exit_iv``, ``time_held_days``, ``pnl``.
    initial_capital : float
        Starting capital (used as fallback if equity_curve is empty).

    Returns
    -------
    dict
        Keys: ``imdd``, ``pnl``, ``greeks_decomposition``
        (dict with delta_pnl / gamma_pnl / vega_pnl / theta_pnl / residual),
        ``theta_realization_rate``, ``vega_contribution``.
    """
    equities = [p["equity"] for p in equity_curve] if equity_curve else []

    # IMDD
    imdd = _intraday_max_drawdown(equities)

    # Session P&L
    if len(equities) >= 2:
        pnl = equities[-1] - equities[0]
    elif trades:
        pnl = sum(t.get("pnl", 0.0) for t in trades)
    else:
        pnl = 0.0

    # Greeks decomposition
    greeks = _aggregate_greeks_decomposition(trades)

    # Theta Realization Rate
    theta_rr = _theta_realization_rate(trades)

    # Vega Contribution
    vega_cont = _vega_contribution(trades)

    return {
        "imdd": round(imdd, 6),
        "pnl": round(pnl, 2),
        "greeks_decomposition": greeks,
        "theta_realization_rate": (
            round(theta_rr, 6) if theta_rr is not None else None
        ),
        "vega_contribution": (
            round(vega_cont, 6) if vega_cont is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# Public API: multi-day aggregation
# ---------------------------------------------------------------------------

def compute_multi_day_intraday_metrics(
    daily_results: list[dict],
) -> dict:
    """Aggregate intraday metrics across multiple 0DTE trading days.

    Parameters
    ----------
    daily_results : list[dict]
        Each entry has at least:
        ``{"date": str, "equity_curve": list[dict], "trades": list[dict], "pnl": float}``.

    Returns
    -------
    dict
        Keys: ``dwr``, ``expected_pnl``, ``payoff_ratio``, ``total_days``,
        ``winning_days``, ``daily_pnls``, ``per_day`` (list of per-day
        ``compute_intraday_metrics`` results), ``aggregate_greeks``,
        ``theta_realization_rate``, ``vega_contribution``.
    """
    if not daily_results:
        return {
            "dwr": 0.0,
            "expected_pnl": 0.0,
            "payoff_ratio": 0.0,
            "total_days": 0,
            "winning_days": 0,
            "daily_pnls": [],
            "per_day": [],
            "aggregate_greeks": {
                "delta_pnl": None,
                "gamma_pnl": None,
                "vega_pnl": None,
                "theta_pnl": None,
                "residual": None,
            },
            "theta_realization_rate": None,
            "vega_contribution": None,
        }

    daily_pnls: list[float] = []
    per_day: list[dict] = []
    all_trades: list[dict] = []

    for day in daily_results:
        ec = day.get("equity_curve", [])
        trades = day.get("trades", [])
        day_pnl = day.get("pnl")

        day_metrics = compute_intraday_metrics(ec, trades)
        per_day.append({"date": day.get("date"), **day_metrics})

        # Use explicit pnl if provided, else fall back to computed
        pnl_val = day_pnl if day_pnl is not None else day_metrics["pnl"]
        daily_pnls.append(pnl_val)

        all_trades.extend(trades)

    total_days = len(daily_pnls)
    winning_days = sum(1 for p in daily_pnls if p > 0)

    # DWR
    dwr = winning_days / total_days if total_days > 0 else 0.0

    # E[P&L]
    expected_pnl = sum(daily_pnls) / total_days if total_days > 0 else 0.0

    # Payoff Ratio on daily P&Ls
    winning_pnls = [p for p in daily_pnls if p > 0]
    losing_pnls = [p for p in daily_pnls if p < 0]
    if winning_pnls and losing_pnls:
        avg_win = sum(winning_pnls) / len(winning_pnls)
        avg_loss = sum(abs(p) for p in losing_pnls) / len(losing_pnls)
        payoff_ratio = avg_win / avg_loss if avg_loss != 0 else float("inf")
    elif winning_pnls:
        payoff_ratio = float("inf")
    else:
        payoff_ratio = 0.0

    # Aggregate Greeks across all trades from all days
    aggregate_greeks = _aggregate_greeks_decomposition(all_trades)

    # Aggregate theta realization rate and vega contribution
    theta_rr = _theta_realization_rate(all_trades)
    vega_cont = _vega_contribution(all_trades)

    return {
        "dwr": round(dwr, 6),
        "expected_pnl": round(expected_pnl, 2),
        "payoff_ratio": round(payoff_ratio, 6),
        "total_days": total_days,
        "winning_days": winning_days,
        "daily_pnls": [round(p, 2) for p in daily_pnls],
        "per_day": per_day,
        "aggregate_greeks": aggregate_greeks,
        "theta_realization_rate": (
            round(theta_rr, 6) if theta_rr is not None else None
        ),
        "vega_contribution": (
            round(vega_cont, 6) if vega_cont is not None else None
        ),
    }
