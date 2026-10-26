"""Multi-scale evaluation metrics for trading pipelines.

Computes performance, risk, and execution metrics at configurable time
scales (1m, 5m, 15m, 1h, 1d).  Designed for both intraday and daily
equity curves.
"""

from __future__ import annotations

import math
from datetime import datetime


# ---------------------------------------------------------------------------
# Annualization factors: number of periods per year for each scale
# ---------------------------------------------------------------------------
_ANNUALIZATION_FACTORS: dict[str, float] = {
    "1m": 252 * 390,       # 252 trading days * 390 minutes/day
    "5m": 252 * 78,        # 252 * (390/5)
    "15m": 252 * 26,       # 252 * (390/15)
    "1h": 252 * 6.5,       # 252 * 6.5 hours/day
    "1d": 252,             # 252 trading days
}

# ---------------------------------------------------------------------------
# Resampling bucket sizes in minutes (for grouping equity points)
# ---------------------------------------------------------------------------
_SCALE_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "1d": 1440,  # full day
}


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO-format timestamp string to a datetime object."""
    # Support both "T" separator and space separator, with or without tz
    ts = ts.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts!r}")


def _resample_equity_curve(
    equity_curve: list[dict],
    scale: str,
) -> list[dict]:
    """Resample an equity curve to a coarser time scale.

    Groups equity points into time buckets of *scale* width and takes the
    last equity value in each bucket.  Returns a new list of
    ``{"ts": str, "equity": float}`` dicts, one per bucket.
    """
    if not equity_curve:
        return []

    bucket_minutes = _SCALE_MINUTES.get(scale)
    if bucket_minutes is None:
        raise ValueError(f"Unknown scale: {scale!r}")

    # For daily scale, bucket by calendar date
    if scale == "1d":
        buckets: dict[str, dict] = {}
        for point in equity_curve:
            dt = _parse_ts(point["ts"])
            key = dt.strftime("%Y-%m-%d")
            buckets[key] = point  # last point per day wins
        return list(buckets.values())

    # For intraday scales, bucket by truncating to the nearest N minutes
    buckets_intra: dict[str, dict] = {}
    for point in equity_curve:
        dt = _parse_ts(point["ts"])
        # Truncate to nearest bucket_minutes boundary
        total_minutes = dt.hour * 60 + dt.minute
        bucket_start = (total_minutes // bucket_minutes) * bucket_minutes
        key = f"{dt.strftime('%Y-%m-%d')}T{bucket_start // 60:02d}:{bucket_start % 60:02d}"
        buckets_intra[key] = point  # last point per bucket wins
    return list(buckets_intra.values())


def compute_metrics(
    equity_curve: list[dict],
    trades: list[dict],
    scale: str = "1d",
) -> dict:
    """Compute performance metrics from an equity curve and trade list.

    Parameters
    ----------
    equity_curve : list[dict]
        List of ``{"ts": str, "equity": float}`` points.
    trades : list[dict]
        List of trade dicts with keys: ``side``, ``qty``, ``price``,
        ``fees``.  The ``pnl`` key is optional but required for
        ``win_rate``, ``payoff_ratio``, and ``expected_value`` to be
        meaningful.  Raw PMB trades lack ``pnl``; callers should
        compute it before passing trades here.
    scale : str
        Time scale for annualization (``"1m"``, ``"5m"``, ``"15m"``,
        ``"1h"``, ``"1d"``).

    Returns
    -------
    dict
        Computed metrics including pnl, return_pct, max_drawdown,
        sharpe, win_rate, turnover, fees_paid, fill_ratio.
    """
    if not equity_curve:
        return {
            "pnl": 0.0,
            "return_pct": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "turnover": 0.0,
            "fees_paid": 0.0,
            "fill_ratio": 1.0,
            "payoff_ratio": 0.0,
            "expected_value": 0.0,
            "var_5pct": 0.0,
            "cvar_5pct": 0.0,
        }

    equities = [p["equity"] for p in equity_curve]
    initial_equity = equities[0]
    final_equity = equities[-1]

    # PnL ($)
    pnl = final_equity - initial_equity

    # Return (%)
    return_pct = (pnl / initial_equity * 100) if initial_equity != 0 else 0.0

    # Max Drawdown (%)
    max_dd = _max_drawdown(equities)

    # Period-over-period returns for Sharpe calculation
    returns = _period_returns(equities)

    # Sharpe ratio
    annualization_factor = _ANNUALIZATION_FACTORS.get(scale, 252)
    sharpe = _sharpe_ratio(returns, annualization_factor)

    # Win rate
    win_rate = _win_rate(trades)

    # Turnover: sum of abs(trade_value) / mean(equity)
    turnover = _turnover(trades, equities)

    # Fees paid
    fees_paid = sum(t.get("fees", 0.0) for t in trades)

    # Fill ratio: filled / total (default 1.0 if not available)
    total_trades = len(trades)
    filled_trades = sum(1 for t in trades if t.get("filled", True))
    fill_ratio = (filled_trades / total_trades) if total_trades > 0 else 1.0

    return {
        "pnl": round(pnl, 2),
        "return_pct": round(return_pct, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "turnover": round(turnover, 4),
        "fees_paid": round(fees_paid, 2),
        "fill_ratio": round(fill_ratio, 4),
        "payoff_ratio": round(_payoff_ratio(trades), 4),
        "expected_value": round(_expected_value(trades), 4),
        "var_5pct": round(_value_at_risk(returns, 5.0), 6),
        "cvar_5pct": round(_conditional_var(returns, 5.0), 6),
    }


def multi_scale_evaluate(
    equity_curve: list[dict],
    trades: list[dict],
    scales: list[str],
) -> dict[str, dict]:
    """Evaluate metrics at multiple time scales.

    For each scale, resamples the equity curve to that frequency and
    computes metrics.

    Parameters
    ----------
    equity_curve : list[dict]
        Full-resolution equity curve (list of ``{"ts": str, "equity": float}``).
    trades : list[dict]
        Trade list (same for all scales).
    scales : list[str]
        Scales to evaluate, e.g. ``["1m", "5m", "15m", "1d"]``.

    Returns
    -------
    dict[str, dict]
        Mapping from scale name to metrics dict.
    """
    results: dict[str, dict] = {}
    for scale in scales:
        resampled = _resample_equity_curve(equity_curve, scale)
        results[scale] = compute_metrics(resampled, trades, scale)
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _period_returns(equities: list[float]) -> list[float]:
    """Compute period-over-period returns from an equity series."""
    returns: list[float] = []
    for i in range(1, len(equities)):
        prev = equities[i - 1]
        if prev > 0:
            returns.append((equities[i] - prev) / prev)
    return returns


def _max_drawdown(equities: list[float]) -> float:
    """MDD as fraction (0.0 to 1.0): max peak-to-trough decline / peak."""
    if not equities:
        return 0.0
    peak = equities[0]
    mdd = 0.0
    for v in equities:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
    return mdd


def _sharpe_ratio(returns: list[float], annualization_factor: float) -> float:
    """Sharpe = mean(returns) / std(returns) * sqrt(annualization_factor).

    Uses sample std (ddof=1).  Returns 0.0 if fewer than 2 data points
    or std is zero.
    """
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean_r / std) * math.sqrt(annualization_factor)


def _win_rate(trades: list[dict]) -> float:
    """Fraction of trades with pnl > 0.

    Only considers trades that have a ``pnl`` key.  Trades without ``pnl``
    (e.g. raw PMB trades) are excluded from the denominator.
    """
    pnl_trades = [t for t in trades if "pnl" in t]
    if not pnl_trades:
        return 0.0
    winners = sum(1 for t in pnl_trades if t["pnl"] > 0)
    return winners / len(pnl_trades)


def _turnover(trades: list[dict], equities: list[float]) -> float:
    """Turnover = sum(abs(trade_value)) / mean(equity)."""
    if not trades or not equities:
        return 0.0
    total_value = sum(abs(t.get("qty", 0) * t.get("price", 0.0)) for t in trades)
    mean_equity = sum(equities) / len(equities)
    if mean_equity == 0:
        return 0.0
    return total_value / mean_equity


def _payoff_ratio(trades: list[dict]) -> float:
    """E[Profit | win] / E[|Loss| | loss].

    Average profit on winning trades divided by average absolute loss on
    losing trades.  Returns 0.0 when there are no trades, ``float('inf')``
    when there are wins but no losses, and 0.0 when there are losses but
    no wins.  Trades without a ``pnl`` key are ignored.
    """
    pnl_trades = [t for t in trades if "pnl" in t]
    if not pnl_trades:
        return 0.0
    wins = [t["pnl"] for t in pnl_trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in pnl_trades if t["pnl"] < 0]
    if not wins:
        return 0.0
    if not losses:
        return float("inf")
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(abs(l) for l in losses) / len(losses)
    if avg_loss == 0:
        return float("inf")
    return avg_win / avg_loss


def _expected_value(trades: list[dict]) -> float:
    """WR x E[Profit] - (1-WR) x E[|Loss|].

    Expected profit per trade.  Returns 0.0 when there are no trades.
    Trades without a ``pnl`` key are ignored.
    """
    pnl_trades = [t for t in trades if "pnl" in t]
    if not pnl_trades:
        return 0.0
    wins = [t["pnl"] for t in pnl_trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in pnl_trades if t["pnl"] < 0]
    wr = len(wins) / len(pnl_trades)
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(abs(l) for l in losses) / len(losses)) if losses else 0.0
    return wr * avg_win - (1 - wr) * avg_loss


def _value_at_risk(returns: list[float], alpha: float = 5.0) -> float:
    """VaR at *alpha* percentile.

    Returns the *alpha*-th percentile of *returns* using linear
    interpolation (matching ``numpy.percentile`` default).  Returns 0.0
    for empty input.
    """
    if not returns:
        return 0.0
    sorted_r = sorted(returns)
    n = len(sorted_r)
    # Use linear interpolation (numpy-compatible)
    rank = (alpha / 100.0) * (n - 1)
    lower = int(math.floor(rank))
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_r[lower] + frac * (sorted_r[upper] - sorted_r[lower])


def _conditional_var(returns: list[float], alpha: float = 5.0) -> float:
    """CVaR (Expected Shortfall) at *alpha* percentile.

    Mean of all returns that are less than or equal to the VaR threshold.
    Returns 0.0 for empty input.
    """
    if not returns:
        return 0.0
    var = _value_at_risk(returns, alpha)
    tail = [r for r in returns if r <= var]
    if not tail:
        return var  # edge-case: no values at or below VaR
    return sum(tail) / len(tail)
