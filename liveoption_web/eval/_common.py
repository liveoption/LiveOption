"""Shared evaluator helpers — events table reads, generic stat math.

Pure functions over a sqlite3.Connection. Defensive against missing
payload keys; never raises on malformed JSON (silently skips).
"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any


def load_day_completed(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Return parsed day.completed payloads in event-id order."""

    rows = conn.execute(
        """SELECT payload_json FROM events
            WHERE run_id=? AND kind='day.completed'
            ORDER BY id ASC""",
        (run_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        raw = r["payload_json"] if isinstance(r, sqlite3.Row) else r[0]
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out


def load_fills(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Return parsed fill.executed payloads in event-id order."""

    rows = conn.execute(
        """SELECT payload_json FROM events
            WHERE run_id=? AND kind='fill.executed'
            ORDER BY id ASC""",
        (run_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        raw = r["payload_json"] if isinstance(r, sqlite3.Row) else r[0]
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out


def generic_stats(days: list[dict]) -> dict[str, float]:
    """Compute family-agnostic AR / Sharpe / MDD / win_rate from day.completed.

    Returns zeros (not None) when inputs are too sparse to compute,
    matching the contract that `run_metrics.generic_json` is always a
    JSON object that's safe to render.
    """

    pnls = [float(d.get("pnl_delta", 0.0)) for d in days]
    cum = [float(d.get("cum_pnl", 0.0)) for d in days]
    portfolio = [float(d.get("portfolio_value", 0.0)) for d in days]

    if not pnls:
        return {
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "n_days": 0,
        }

    # Daily returns vs starting portfolio value.
    starting = portfolio[0] - pnls[0] if portfolio else 0.0
    if starting <= 0:
        starting = 100000.0  # safe default
    daily_returns = [p / starting for p in pnls]
    mean = sum(daily_returns) / len(daily_returns)
    if len(daily_returns) > 1:
        var = sum((x - mean) ** 2 for x in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(var)
    else:
        std = 0.0
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0
    annualized_return = mean * 252

    # Max drawdown over cumulative pnl curve (vs running peak).
    peak = float("-inf")
    mdd = 0.0
    for c in cum:
        peak = max(peak, c)
        dd = peak - c
        if dd > mdd:
            mdd = dd

    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / len(pnls)

    return {
        "annualized_return": annualized_return,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "win_rate": win_rate,
        "total_pnl": cum[-1] if cum else 0.0,
        "n_days": len(pnls),
    }


def paper_portfolio_stats(days: list[dict]) -> dict[str, float | int | None]:
    """Paper-style portfolio statistics from daily equity observations.

    Missing or sparse observations yield null, not a fabricated zero.
    The first day's starting equity is inferred from its equity and P&L delta.
    """
    points: list[tuple[float, float]] = []
    for day in days:
        try:
            equity = float(day["portfolio_value"])
            pnl = float(day["pnl_delta"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(equity) and math.isfinite(pnl) and equity > 0:
            points.append((equity, pnl))
    empty: dict[str, float | int | None] = {
        "annualized_return": None, "sharpe": None,
        "max_drawdown_pct": None, "var_95": None, "cvar_95": None,
        "n_days": len(points), "coverage_pct": len(points) / len(days) if days else 0.0,
    }
    if not points:
        return empty
    initial = points[0][0] - points[0][1]
    if initial <= 0:
        return empty
    equities = [initial] + [equity for equity, _ in points]
    returns = [equities[i] / equities[i - 1] - 1 for i in range(1, len(equities))]
    peak = initial
    drawdowns = []
    for equity in equities:
        peak = max(peak, equity)
        drawdowns.append((peak - equity) / peak)
    result = dict(empty)
    result["max_drawdown_pct"] = max(drawdowns)
    if len(returns) >= 2 and equities[-1] > 0:
        result["annualized_return"] = (equities[-1] / initial) ** (252 / len(returns)) - 1
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        if variance > 0:
            result["sharpe"] = mean / math.sqrt(variance) * math.sqrt(252)
    if len(returns) >= 20:
        ordered = sorted(returns)
        tail_n = max(1, math.ceil(len(ordered) * 0.05))
        result["var_95"] = -ordered[tail_n - 1]
        result["cvar_95"] = -sum(ordered[:tail_n]) / tail_n
    return result


def sum_greeks_attribution(days: list[dict]) -> dict[str, float]:
    """Sum per-day greeks_summary across days (keys: delta_pnl, ...)."""

    keys = (
        "delta_pnl",
        "gamma_pnl",
        "theta_pnl",
        "vega_pnl",
        "volga_pnl",
        "vanna_pnl",
        "residual",
    )
    totals = {k: 0.0 for k in keys}
    for d in days:
        gs = d.get("greeks_summary") or {}
        for k in keys:
            try:
                totals[k] += float(gs.get(k, 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
    return totals
