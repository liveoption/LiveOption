"""Behavioral evaluator (spec §5.5).

Reads decision artifact files (prompt.json / response.json / trade.json)
under each decision_records.storage_dir to compute LLM intent metrics.
First-pass: simple intent_consistency proxy + safe placeholder zeros for
the remaining behavioral fields. Refinements deferred to Phase E.

Metric fields:
  intent_consistency, directional_bias, confidence_calibration,
  position_sizing, stop_loss_adherence, leverage_utilization,
  greeks_attribution_summary
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from liveoption_web.eval._common import (
    load_day_completed,
    sum_greeks_attribution,
)


EVALUATOR_VERSION = "v2-null-unmeasured"


def _safe_load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None


def aggregate(run_id: str, conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT id, storage_dir, has_trade
            FROM decision_records WHERE run_id=? ORDER BY seq ASC""",
        (run_id,),
    ).fetchall()

    n_decisions = len(rows)
    n_with_trade = 0
    intent_matches = 0  # count of decisions where prompt-stated intent
    #                    matches the trade direction (very rough proxy).
    intent_observed = 0

    for r in rows:
        has_trade = bool(
            r["has_trade"] if isinstance(r, sqlite3.Row) else r[2]
        )
        if has_trade:
            n_with_trade += 1
        storage_dir = (
            r["storage_dir"] if isinstance(r, sqlite3.Row) else r[1]
        )
        if not storage_dir:
            continue
        sd = Path(storage_dir)
        prompt = _safe_load_json(sd / "prompt.json")
        trade = _safe_load_json(sd / "trade.json")
        if prompt is None or trade is None or not has_trade:
            continue
        # Look for any intent text in the prompt that hints "buy" or
        # "sell"; compare to the trade's qty sign. This is the first
        # iteration proxy — Phase E will refine.
        prompt_text = json.dumps(prompt).lower()
        trade_qty = trade.get("qty", 0)
        try:
            trade_qty = float(trade_qty)
        except (TypeError, ValueError):
            trade_qty = 0.0
        intent_long = "buy" in prompt_text or "long" in prompt_text
        intent_short = "sell" in prompt_text or "short" in prompt_text
        if intent_long != intent_short:  # exactly one stated direction
            intent_observed += 1
            if (intent_long and trade_qty > 0) or (
                intent_short and trade_qty < 0
            ):
                intent_matches += 1

    intent_consistency = (
        intent_matches / intent_observed if intent_observed > 0 else None
    )

    # greeks_attribution_summary from day.completed.greeks_summary roll-up.
    days = load_day_completed(conn, run_id)
    greeks_summary = sum_greeks_attribution(days)

    return {
        "intent_consistency": intent_consistency,
        "directional_bias": None,        # unmeasured; never imply true zero
        "confidence_calibration": None,
        "position_sizing": None,
        "stop_loss_adherence": None,
        "leverage_utilization": None,
        "greeks_attribution_summary": greeks_summary,
        "n_decisions": n_decisions,
        "n_with_trade": n_with_trade,
    }
