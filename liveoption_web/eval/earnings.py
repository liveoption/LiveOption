"""Earnings-family evaluator (spec §5.2)."""
from __future__ import annotations

import sqlite3

from liveoption_web.eval._common import (
    generic_stats,
    load_day_completed,
    load_fills,
    sum_greeks_attribution,
)


EVALUATOR_VERSION = "v1-phaseC"


def aggregate(run_id: str, conn: sqlite3.Connection) -> dict:
    days = load_day_completed(conn, run_id)
    fills = load_fills(conn, run_id)
    generic = generic_stats(days)
    greeks = sum_greeks_attribution(days)

    # Per-ticker realized pnl rough-cut: bucket fills by symbol's
    # underlying ticker (from the fill payload's `underlying` field).
    by_ticker: dict[str, float] = {}
    for f in fills:
        ticker = str(f.get("underlying") or f.get("symbol", "") or "?")
        try:
            qty = float(f.get("qty", 0.0))
            price = float(f.get("price", 0.0))
            by_ticker[ticker] = by_ticker.get(ticker, 0.0) + (qty * price)
        except (TypeError, ValueError):
            continue

    return {
        "generic": generic,
        "vega_pnl_total": greeks.get("vega_pnl", 0.0),
        "per_ticker_notional": by_ticker,
        "greeks_attribution": greeks,
    }
