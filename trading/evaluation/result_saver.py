import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric computation helpers
# ---------------------------------------------------------------------------

def _annualized_return(total_return: float, n_days: int) -> float:
    """AR = (1 + total_return)^(252/n_days) - 1"""
    if n_days <= 0:
        return 0.0
    return (1 + total_return) ** (252 / n_days) - 1


def _sharpe_ratio(returns: list[float], annual_factor: float = 252) -> float:
    """SR = mean(r) / std(r) * sqrt(annual_factor).  Risk-free rate assumed 0."""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean_r / std) * math.sqrt(annual_factor)


def _max_drawdown(equities: list[float]) -> float:
    """MDD = max_t (1 - V_t / peak_t)"""
    if not equities:
        return 0.0
    peak = equities[0]
    mdd = 0.0
    for v in equities:
        if v > peak:
            peak = v
        dd = 1 - v / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
    return mdd


def _weekly_returns(equities: list[float]) -> list[float]:
    """Compute period-over-period returns from an equity series."""
    returns = []
    for i in range(1, len(equities)):
        prev = equities[i - 1]
        if prev > 0:
            returns.append((equities[i] - prev) / prev)
    return returns


def _trade_pnl_by_instrument(trades: list[dict]) -> list[float]:
    """
    Match trades by instrument_id to compute realized P&L per round-trip.

    Each BUY builds a long position (or covers a short); each SELL reduces it.
    When a position crosses zero or closes, the realized P&L is recorded.
    Option contracts use a 100x multiplier.
    """
    OPTION_MULTIPLIER = 100

    # Group trades by instrument
    by_instrument: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_instrument[t["instrument_id"]].append(t)

    pnls: list[float] = []
    for instrument_id, inst_trades in by_instrument.items():
        multiplier = OPTION_MULTIPLIER if instrument_id.startswith("OPTION:") else 1
        qty = 0
        avg_cost = 0.0
        for t in sorted(inst_trades, key=lambda x: x.get("ts", "")):
            side = t.get("side", "").upper()
            tqty = t["qty"]
            price = t["price"]
            fees = t.get("fees", 0.0)

            if side == "BUY":
                # Opening / adding to long, or covering short
                if qty < 0:
                    # Covering short position
                    cover = min(tqty, abs(qty))
                    pnl = cover * (avg_cost - price) * multiplier - fees
                    pnls.append(pnl)
                    tqty -= cover
                    qty += cover
                if tqty > 0:
                    # Remaining qty opens/adds to long
                    total_cost = avg_cost * qty + price * tqty
                    qty += tqty
                    avg_cost = total_cost / qty if qty != 0 else 0.0
            elif side == "SELL":
                if qty > 0:
                    # Closing long position
                    close = min(tqty, qty)
                    pnl = close * (price - avg_cost) * multiplier - fees
                    pnls.append(pnl)
                    tqty -= close
                    qty -= close
                if tqty > 0:
                    # Remaining qty opens/adds to short
                    total_cost = avg_cost * abs(qty) + price * tqty
                    qty -= tqty
                    avg_cost = total_cost / abs(qty) if qty != 0 else 0.0

    return pnls


def _order_metrics(trade_pnls: list[float]) -> dict:
    """Compute Win Rate, Payoff Ratio, and Expected Value from per-trade P&Ls."""
    if not trade_pnls:
        return {
            "total_pnl": 0.0,
            "n_round_trips": 0,
            "win_rate": 0.0,
            "payoff_ratio": 0.0,
            "expected_value": 0.0,
        }

    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    n_total = len(trade_pnls)
    n_win = len(wins)
    wr = n_win / n_total

    avg_win = (sum(wins) / n_win) if wins else 0.0
    avg_loss = (sum(abs(l) for l in losses) / len(losses)) if losses else 0.0
    pr = (avg_win / avg_loss) if avg_loss > 0 else float("inf") if avg_win > 0 else 0.0
    ev = wr * avg_win - (1 - wr) * avg_loss

    return {
        "total_pnl": round(sum(trade_pnls), 2),
        "n_round_trips": n_total,
        "win_rate": round(wr, 4),
        "payoff_ratio": round(pr, 4) if pr != float("inf") else None,
        "expected_value": round(ev, 2),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def save_results(
    records: list[dict],
    output_dir: str,
    run_id: str,
    initial_capital: float = 100_000.0,
    pmb_client=None,
) -> str:
    """
    Save weekly records, equity curve, and evaluation metrics to JSON.

    When *pmb_client* is provided (non-dry-run), fetches PMB SessionSummary
    and trade history to compute the full set of evaluation metrics:
      - Core Portfolio: AR, SR, MDD
      - Core Order: P&L, Win Rate, Payoff Ratio, Expected Value
    """
    os.makedirs(output_dir, exist_ok=True)

    # --- Equity curve from weekly records ---
    equities = [r["account"].get("equity", 0) for r in records]
    initial = initial_capital
    final = equities[-1] if equities else initial
    total_return = (final - initial) / initial if initial else 0

    equity_curve = [
        {"date": r["date"], "equity": r["account"].get("equity", 0)}
        for r in records
    ]

    # --- Basic summary (always available) ---
    n_actions = sum(len(r.get("actions", [])) for r in records)
    n_submitted = sum(len(r.get("submitted", [])) for r in records)
    n_failed = sum(len(r.get("failed", [])) for r in records)
    n_days = len(equities)  # weekly snapshots used as proxy for period length

    summary: dict = {
        "initial_equity": initial,
        "final_equity": round(final, 2),
        "total_return_pct": round(total_return * 100, 2),
        "n_weeks": len(records),
        "n_actions": n_actions,
        "n_submitted": n_submitted,
        "n_failed": n_failed,
    }

    # --- Core portfolio metrics from weekly equity curve ---
    weekly_ret = _weekly_returns(equities)
    # For weekly data the annualization factor is 52
    summary["annualized_return"] = round(
        _annualized_return(total_return, n_days * 7) if n_days > 1 else 0.0, 4
    )
    summary["sharpe_ratio"] = round(_sharpe_ratio(weekly_ret, annual_factor=52), 4)
    summary["max_drawdown"] = round(_max_drawdown(equities), 4)

    # --- PMB session summary (richer data if available) ---
    pmb_summary = None
    if pmb_client is not None:
        try:
            pmb_summary = pmb_client.get_summary()
            summary["pmb_total_return"] = pmb_summary.get("total_return")
            summary["pmb_max_drawdown"] = pmb_summary.get("max_drawdown")
            summary["pmb_fees_paid"] = pmb_summary.get("fees_paid")
            summary["pmb_num_orders"] = pmb_summary.get("num_orders")
            summary["pmb_num_trades"] = pmb_summary.get("num_trades")
            summary["pmb_reject_rate"] = pmb_summary.get("reject_rate")
            summary["pmb_margin_call_count"] = pmb_summary.get("margin_call_count")
        except Exception as e:
            logger.warning(f"Failed to fetch PMB summary: {e}")

    # --- Core order metrics from PMB trade history ---
    order_metrics: dict = {}
    trades: list[dict] = []
    if pmb_client is not None:
        try:
            trades = pmb_client.get_trades()
            trade_pnls = _trade_pnl_by_instrument(trades)
            order_metrics = _order_metrics(trade_pnls)
        except Exception as e:
            logger.warning(f"Failed to fetch PMB trades: {e}")

    if order_metrics:
        summary["order_metrics"] = order_metrics

    # --- Assemble output ---
    output = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "equity_curve": equity_curve,
        "weekly_records": records,
    }
    if trades:
        output["trades"] = trades
    if pmb_summary:
        output["pmb_summary"] = pmb_summary

    path = os.path.join(output_dir, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump(output, f, indent=2)
    return path
