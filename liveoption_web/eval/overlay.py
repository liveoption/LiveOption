"""Overlay-family evaluator (spec §5.2).

Reads `events` (day.completed + fill.executed) and produces overlay-
specific aggregates: overlay_income_total, underlying_drift_pnl, plus
the shared `generic` block.
"""
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

    # Overlay income = sum of credits from option fills (negative qty == short).
    overlay_income_total = 0.0
    for f in fills:
        try:
            qty = float(f.get("qty", 0.0))
            price = float(f.get("price", 0.0))
            if qty < 0:  # short option leg → credit received
                overlay_income_total += abs(qty) * price * 100.0
        except (TypeError, ValueError):
            continue

    underlying_drift_pnl = generic.get("total_pnl", 0.0) - overlay_income_total

    return {
        "generic": generic,
        "overlay_income_total": overlay_income_total,
        "underlying_drift_pnl": underlying_drift_pnl,
        "greeks_attribution": greeks,
    }
