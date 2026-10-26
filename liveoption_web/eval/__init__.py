"""Per-family evaluators that aggregate run-level metrics.

Each `liveoption_web/eval/<family>.py` exposes:

  EVALUATOR_VERSION: str  # bump triggers recompute on next read
  def aggregate(run_id: str, conn) -> dict

This package's `aggregate_for(run_id, db_path)` dispatches to the right
evaluator based on `runs.family`, computes `generic` / `family_specific`
/ `behavioral` blocks, and upserts the `run_metrics` row in a single
transaction.

"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from liveoption_web.eval import (
    behavioral,
    earnings,
    intraday,
    null_family,
    overlay,
)


_FAMILY_TO_MODULE = {
    "overlay": overlay,
    "earnings": earnings,
    "intraday": intraday,
}


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _evaluator_for(family: str | None):
    """Return the evaluator module to call for this family value."""

    if family is None:
        return null_family
    return _FAMILY_TO_MODULE.get(family, null_family)


def aggregate_for(run_id: str, db_path: Path) -> dict | None:
    """Compute + upsert metrics for ``run_id``. Returns the persisted row.

    Synchronous (sqlite3); safe to call from any context. Returns
    ``None`` if the run does not exist. Always writes a fresh row, even
    on partial-failure inputs (evaluators are defensive — they return
    placeholder zeros rather than raising on missing data).
    """

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, family FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        family = row["family"]

        evaluator = _evaluator_for(family)
        family_metrics = evaluator.aggregate(run_id, conn)
        generic_block = family_metrics.pop("generic", {})
        family_specific = (
            family_metrics if family is not None else None
        )

        behavioral_block = behavioral.aggregate(run_id, conn)

        evaluator_version = (
            f"{getattr(evaluator, 'EVALUATOR_VERSION', 'v1-phaseC')}"
            f"+behavioral:{behavioral.EVALUATOR_VERSION}"
        )
        computed_at = _utc_now_z()
        generic_json = json.dumps(generic_block, sort_keys=True)
        family_json = (
            json.dumps(family_specific, sort_keys=True)
            if family_specific is not None
            else None
        )
        behavioral_json = json.dumps(behavioral_block, sort_keys=True)

        # UPSERT: PRIMARY KEY is run_id.
        conn.execute(
            """INSERT INTO run_metrics(
                run_id, family, generic_json, family_json, behavioral_json,
                computed_at, evaluator_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                family=excluded.family,
                generic_json=excluded.generic_json,
                family_json=excluded.family_json,
                behavioral_json=excluded.behavioral_json,
                computed_at=excluded.computed_at,
                evaluator_version=excluded.evaluator_version
            """,
            (
                run_id,
                family,
                generic_json,
                family_json,
                behavioral_json,
                computed_at,
                evaluator_version,
            ),
        )
        conn.commit()
        return {
            "run_id": run_id,
            "family": family,
            "generic": generic_block,
            "family_specific": family_specific,
            "behavioral": behavioral_block,
            "computed_at": computed_at,
            "evaluator_version": evaluator_version,
        }
    finally:
        conn.close()


def current_evaluator_version(family: str | None) -> str:
    """Return the version string used by `aggregate_for` for this family."""

    evaluator = _evaluator_for(family)
    return (
        f"{getattr(evaluator, 'EVALUATOR_VERSION', 'v1-phaseC')}"
        f"+behavioral:{behavioral.EVALUATOR_VERSION}"
    )
