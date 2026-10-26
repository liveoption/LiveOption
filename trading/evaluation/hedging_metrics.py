"""Hedging strategy evaluation metrics.

Evaluates hedging performance through:
1. Portfolio performance vs buy-and-hold baseline
2. Hedging activity and timing (HAR, ECAR, FHR)
3. Cost and income efficiency (HCR, HIR, HE)
4. Success metrics (EPC, HSR, ERBE)

"""

from __future__ import annotations

import math
from typing import Optional

_EPS = 1e-12


# ============================================================================
# Hedging Activity Metrics
# ============================================================================


def compute_hedging_activity(
    daily_is_hedged: list[bool],
    daily_is_downside_event: list[bool],
) -> dict[str, float]:
    """Compute hedging activity rate and event-conditional rates.

    Parameters
    ----------
    daily_is_hedged : list[bool]
        Is hedging position active on each day [True/False, ...].
    daily_is_downside_event : list[bool]
        Is there a significant downside move on each day [True/False, ...].

    Returns
    -------
    dict
        Keys: har (hedging active rate), ecar (event-conditional active rate),
        fhr (false hedge rate).
    """
    if not daily_is_hedged:
        return {"har": 0.0, "ecar": 0.0, "fhr": 0.0}

    T = len(daily_is_hedged)
    n_hedged = sum(daily_is_hedged)
    n_events = sum(daily_is_downside_event)
    n_false_hedges = sum(h and not e for h, e in zip(daily_is_hedged, daily_is_downside_event))

    har = n_hedged / T if T > 0 else 0.0
    ecar = (
        sum(h and e for h, e in zip(daily_is_hedged, daily_is_downside_event)) / n_events
        if n_events > 0
        else 0.0
    )
    fhr = n_false_hedges / (T - n_events) if (T - n_events) > 0 else 0.0

    return {
        "har": round(har, 4),
        "ecar": round(ecar, 4),
        "fhr": round(fhr, 4),
    }


# ============================================================================
# Hedging Cost and Income Metrics
# ============================================================================


def compute_hedging_cost_income(
    daily_hedge_costs: list[float],
    daily_hedge_income: list[float],
    initial_capital: float = 100_000.0,
) -> dict[str, float]:
    """Compute hedging cost ratio, income ratio, and efficiency.

    Parameters
    ----------
    daily_hedge_costs : list[float]
        Hedging cost (option premium paid) on each day.
    daily_hedge_income : list[float]
        Hedging income (option payoff) on each day.
    initial_capital : float
        Initial portfolio value for ratio normalization.

    Returns
    -------
    dict
        Keys: hcr (hedging cost ratio), hir (hedging income ratio),
        he (hedging efficiency = HIR/HCR).
    """
    if not daily_hedge_costs:
        return {"hcr": 0.0, "hir": 0.0, "he": 0.0}

    total_cost = sum(daily_hedge_costs)
    total_income = sum(daily_hedge_income)

    hcr = total_cost / initial_capital if initial_capital > 0 else 0.0
    hir = total_income / initial_capital if initial_capital > 0 else 0.0
    he = total_income / total_cost if total_cost > _EPS else 0.0

    return {
        "hcr": round(hcr, 4),
        "hir": round(hir, 4),
        "he": round(he, 4),
    }


# ============================================================================
# Hedging Success Metrics
# ============================================================================


def compute_event_payout_coverage(
    daily_hedge_income: list[float],
    daily_unhedged_losses: list[float],
    daily_is_downside_event: list[bool],
) -> dict[str, float]:
    """Compute event payout coverage (EPC).

    EPC = sum(income_on_downside_days) / sum(losses_on_downside_days)
    An EPC of 1.0 means perfect recovery of losses.

    Parameters
    ----------
    daily_hedge_income : list[float]
        Hedging income on each day.
    daily_unhedged_losses : list[float]
        Loss of unhedged portfolio on each day (max(0, prev_val - curr_val)).
    daily_is_downside_event : list[bool]
        Downside event indicator.

    Returns
    -------
    dict
        Keys: epc (event payout coverage).
    """
    if not daily_is_downside_event or sum(daily_is_downside_event) == 0:
        return {"epc": 0.0}

    total_income_on_events = sum(
        inc for inc, is_event in zip(daily_hedge_income, daily_is_downside_event) if is_event
    )
    total_loss_on_events = sum(
        loss for loss, is_event in zip(daily_unhedged_losses, daily_is_downside_event) if is_event
    )

    if total_loss_on_events < _EPS:
        epc = 1.0 if total_income_on_events >= 0 else 0.0
    else:
        epc = total_income_on_events / total_loss_on_events

    return {"epc": round(min(epc, 2.0), 4)}  # Cap at 2.0 for interpretation


def compute_hedge_success_rate(
    daily_returns_hedged: list[float],
    daily_returns_unhedged: list[float],
    daily_returns_benchmark: list[float],
    daily_is_downside_event: list[bool],
) -> dict[str, float]:
    """Compute hedge success rate (HSR).

    HSR = fraction of downside days where:
      - Hedged return > unhedged return, AND
      - Hedged return > benchmark return

    Parameters
    ----------
    daily_returns_hedged : list[float]
        Daily returns of hedged portfolio.
    daily_returns_unhedged : list[float]
        Daily returns of unhedged portfolio.
    daily_returns_benchmark : list[float]
        Daily returns of benchmark (e.g., underlying or SPY).
    daily_is_downside_event : list[bool]
        Downside event indicator.

    Returns
    -------
    dict
        Keys: hsr (hedge success rate in [0, 1]).
    """
    if not daily_is_downside_event or sum(daily_is_downside_event) == 0:
        return {"hsr": 0.0}

    event_days = [
        (h, u, b)
        for h, u, b, is_event in zip(
            daily_returns_hedged, daily_returns_unhedged, daily_returns_benchmark, daily_is_downside_event
        )
        if is_event
    ]

    if not event_days:
        return {"hsr": 0.0}

    successes = sum(1 for h, u, b in event_days if h > u and h > b)
    hsr = successes / len(event_days)

    return {"hsr": round(hsr, 4)}


def compute_excess_return_on_events(
    daily_returns_hedged: list[float],
    daily_returns_baseline: list[float],
    daily_is_downside_event: list[bool],
) -> dict[str, float]:
    """Compute excess return on downside event days (ERBE).

    ERBE = mean(hedged_return - baseline_return) on days where event=True.

    Parameters
    ----------
    daily_returns_hedged : list[float]
        Daily returns of hedged portfolio.
    daily_returns_baseline : list[float]
        Daily returns of baseline (underlying or benchmark).
    daily_is_downside_event : list[bool]
        Downside event indicator.

    Returns
    -------
    dict
        Keys: erbe (excess return on event days).
    """
    if not daily_is_downside_event or sum(daily_is_downside_event) == 0:
        return {"erbe": 0.0}

    event_excess_returns = [
        h - b
        for h, b, is_event in zip(daily_returns_hedged, daily_returns_baseline, daily_is_downside_event)
        if is_event
    ]

    if not event_excess_returns:
        return {"erbe": 0.0}

    erbe = sum(event_excess_returns) / len(event_excess_returns)
    return {"erbe": round(erbe, 6)}


# ============================================================================
# Comprehensive Hedging Report
# ============================================================================


def compute_hedging_metrics(
    daily_is_hedged: list[bool],
    daily_is_downside_event: list[bool],
    daily_hedge_costs: list[float],
    daily_hedge_income: list[float],
    daily_returns_hedged: list[float],
    daily_returns_unhedged: list[float],
    daily_returns_benchmark: list[float],
    daily_unhedged_losses: list[float],
    initial_capital: float = 100_000.0,
) -> dict[str, dict | float]:
    """Comprehensive hedging metrics computation.

    Parameters
    ----------
    daily_is_hedged : list[bool]
        Hedging position active.
    daily_is_downside_event : list[bool]
        Downside event indicator.
    daily_hedge_costs : list[float]
        Hedging costs.
    daily_hedge_income : list[float]
        Hedging income.
    daily_returns_hedged : list[float]
        Hedged portfolio returns.
    daily_returns_unhedged : list[float]
        Unhedged portfolio returns.
    daily_returns_benchmark : list[float]
        Benchmark returns.
    daily_unhedged_losses : list[float]
        Unhedged losses on downside days.
    initial_capital : float
        Initial capital.

    Returns
    -------
    dict
        Comprehensive hedging metrics across all categories.
    """
    activity = compute_hedging_activity(daily_is_hedged, daily_is_downside_event)
    cost_income = compute_hedging_cost_income(daily_hedge_costs, daily_hedge_income, initial_capital)
    epc = compute_event_payout_coverage(daily_hedge_income, daily_unhedged_losses, daily_is_downside_event)
    hsr = compute_hedge_success_rate(daily_returns_hedged, daily_returns_unhedged, daily_returns_benchmark, daily_is_downside_event)
    erbe = compute_excess_return_on_events(daily_returns_hedged, daily_returns_benchmark, daily_is_downside_event)

    return {
        "hedging_activity": activity,
        "cost_income": cost_income,
        "event_metrics": {**epc, **hsr, **erbe},
        "summary": {
            "har": activity["har"],
            "hcr": cost_income["hcr"],
            "he": cost_income["he"],
            "hsr": hsr["hsr"],
            "epc": epc["epc"],
        },
    }
