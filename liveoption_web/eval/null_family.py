"""Null-family evaluator — generic block only (no family-specific metrics).

Used by baseline weekly_llm and dryrun_smoke runs that don't map to a
supported scenario family.
"""
from __future__ import annotations

import sqlite3

from liveoption_web.eval._common import generic_stats, load_day_completed


EVALUATOR_VERSION = "v1-phaseC"


def aggregate(run_id: str, conn: sqlite3.Connection) -> dict:
    days = load_day_completed(conn, run_id)
    return {"generic": generic_stats(days)}
