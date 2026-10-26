"""Intraday-family evaluator (spec §5.2)."""
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

    # Trades per day (proxy for intraday scalping intensity).
    trades_per_day = (len(fills) / max(1, generic.get("n_days", 1)))

    return {
        "generic": generic,
        "trades_per_day": trades_per_day,
        "gamma_pnl_total": greeks.get("gamma_pnl", 0.0),
        "theta_pnl_total": greeks.get("theta_pnl", 0.0),
        "greeks_attribution": greeks,
    }
