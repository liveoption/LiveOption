"""Earnings Bet evaluation metrics (Appendix B.3 / C.4).

Computes scenario-specific metrics for earnings event trading:

- **Directional Accuracy (DA)**: fraction of correct direction predictions
- **Vega Exposure at Entry (VEE)**: V_entry * delta_sigma / premium_paid
- **Conditional P&L correct (CP&L)**: E[P&L | direction correct]
- **Conditional P&L wrong (WP&L)**: E[P&L | direction wrong]
"""

from __future__ import annotations

import math

# Small constant to avoid division-by-zero.
_EPS = 1e-12


def compute_earnings_metrics(events: list[dict]) -> dict:
    """Compute earnings-specific evaluation metrics.

    Parameters
    ----------
    events : list[dict]
        List of completed earnings event dicts.  Each must contain:

        - ``predicted_direction`` (str): ``"up"`` or ``"down"`` (agent's
          prediction, inferred from position: calls = up, puts = down).
        - ``actual_direction`` (str): ``"up"`` or ``"down"`` (actual move
          after earnings).
        - ``pnl`` (float): realized P&L for this event.
        - ``premium_paid`` (float): total premium spent at entry.
        - ``vega_entry`` (float | None): aggregate vega at entry.
        - ``iv_change`` (float | None): change in implied volatility after
          earnings (typically negative due to IV crush).
        - ``skipped`` (bool): True if the agent chose HOLD / skipped the event.

    Returns
    -------
    dict
        Keys: ``da``, ``vee``, ``cpl``, ``wpl``, ``skip_rate``,
        ``n_events``, ``n_traded``, ``n_correct``, ``n_wrong``,
        ``total_pnl``, ``avg_pnl``.
    """
    if not events:
        return _empty_metrics()

    # Separate skipped vs traded events
    traded = [e for e in events if not e.get("skipped", False)]
    skipped = [e for e in events if e.get("skipped", False)]

    n_events = len(events)
    n_traded = len(traded)
    n_skipped = len(skipped)

    if n_traded == 0:
        result = _empty_metrics()
        result["n_events"] = n_events
        result["skip_rate"] = 1.0
        return result

    # Directional Accuracy (DA)
    correct = [
        e for e in traded if e.get("predicted_direction") == e.get("actual_direction")
    ]
    wrong = [
        e for e in traded if e.get("predicted_direction") != e.get("actual_direction")
    ]
    n_correct = len(correct)
    n_wrong = len(wrong)
    da = n_correct / n_traded

    # Vega Exposure at Entry (VEE)
    # VEE = mean(vega_entry * |iv_change| / premium_paid) across traded events
    vee_values = []
    for e in traded:
        vega = e.get("vega_entry")
        iv_chg = e.get("iv_change")
        premium = e.get("premium_paid", 0)
        if vega is not None and iv_chg is not None and premium > 0:
            vee_values.append(abs(vega * iv_chg) / premium)
    vee = sum(vee_values) / len(vee_values) if vee_values else 0.0

    # Conditional P&L
    cpl = _mean_pnl(correct)  # E[P&L | direction correct]
    wpl = _mean_pnl(wrong)  # E[P&L | direction wrong]

    # Aggregate stats
    total_pnl = sum(e.get("pnl", 0) for e in traded)
    avg_pnl = total_pnl / n_traded

    return {
        "da": round(da, 4),
        "vee": round(vee, 4),
        "cpl": round(cpl, 2),
        "wpl": round(wpl, 2),
        "skip_rate": round(n_skipped / n_events, 4),
        "n_events": n_events,
        "n_traded": n_traded,
        "n_correct": n_correct,
        "n_wrong": n_wrong,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 2),
    }


def compute_event_pnl_summary(events: list[dict]) -> dict:
    """Compute per-event P&L distribution summary.

    Parameters
    ----------
    events : list[dict]
        Same format as ``compute_earnings_metrics``.

    Returns
    -------
    dict
        Keys: ``mean_pnl``, ``median_pnl``, ``std_pnl``, ``best_pnl``,
        ``worst_pnl``, ``pnl_values``.
    """
    traded = [e for e in events if not e.get("skipped", False)]
    pnls = [e.get("pnl", 0) for e in traded]

    if not pnls:
        return {
            "mean_pnl": 0.0,
            "median_pnl": 0.0,
            "std_pnl": 0.0,
            "best_pnl": 0.0,
            "worst_pnl": 0.0,
            "pnl_values": [],
        }

    mean_pnl = sum(pnls) / len(pnls)
    sorted_pnls = sorted(pnls)
    n = len(sorted_pnls)
    median_pnl = (
        sorted_pnls[n // 2]
        if n % 2 == 1
        else (sorted_pnls[n // 2 - 1] + sorted_pnls[n // 2]) / 2
    )
    var = sum((p - mean_pnl) ** 2 for p in pnls) / max(n - 1, 1)
    std_pnl = math.sqrt(var)

    return {
        "mean_pnl": round(mean_pnl, 2),
        "median_pnl": round(median_pnl, 2),
        "std_pnl": round(std_pnl, 2),
        "best_pnl": round(max(pnls), 2),
        "worst_pnl": round(min(pnls), 2),
        "pnl_values": [round(p, 2) for p in pnls],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mean_pnl(events: list[dict]) -> float:
    """Mean P&L across a list of events."""
    if not events:
        return 0.0
    pnls = [e.get("pnl", 0) for e in events]
    return sum(pnls) / len(pnls)


def _empty_metrics() -> dict:
    """Return a zero-initialized metrics dict."""
    return {
        "da": 0.0,
        "vee": 0.0,
        "cpl": 0.0,
        "wpl": 0.0,
        "skip_rate": 0.0,
        "n_events": 0,
        "n_traded": 0,
        "n_correct": 0,
        "n_wrong": 0,
        "total_pnl": 0.0,
        "avg_pnl": 0.0,
    }
