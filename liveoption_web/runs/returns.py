"""Comparable daily price / account returns with an explicit shared baseline."""
from __future__ import annotations

from datetime import date
from typing import Any

from liveoption_web.runs.report import finite


def compare_returns(run_id: str, symbols: list[str], prices: list[dict], days: list[dict],
                    start: str, end: str, include_account: bool = True) -> dict[str, Any]:
    values: dict[str, dict[str, float]] = {symbol: {} for symbol in symbols}
    warnings = []
    for item in prices:
        symbol = item.get("ticker", item.get("symbol"))
        close = finite(item.get("close"))
        try:
            day = date.fromisoformat(str(item.get("date"))[:10]).isoformat()
        except ValueError:
            continue
        if symbol in values and start <= day <= end and close is not None and close > 0:
            values[symbol][day] = close
    if include_account:
        account = {}
        for item in days:
            day = item.get("trading_day")
            equity = finite(item.get("portfolio_value"))
            if isinstance(day, str) and start <= day <= end and equity is not None:
                account[day] = equity
        values["account"] = account
    unavailable = [key for key, points in values.items() if not points]
    valid = {key: points for key, points in values.items() if points}
    common = set.intersection(*(set(points) for points in valid.values())) if valid else set()
    baseline_days = {day for day in common if all(points[day] > 0 for points in valid.values())}
    baseline = min(baseline_days) if baseline_days else None
    end_day = max(common) if common else None
    series = []
    if baseline is not None and end_day is not None:
        dates = sorted(day for day in set.union(*(set(points) for points in valid.values())) if baseline <= day <= end_day)
        for key, observations in valid.items():
            base_value = observations[baseline]
            series.append({"id": key, "label": key, "kind": "account" if key == "account" else "underlying",
                           "baseline_value": base_value,
                           "points": [{"date": day, "value": observations.get(day),
                                       "return_pct": observations[day] / base_value - 1 if day in observations else None,
                                       "index_value": observations[day] / base_value * 100 if day in observations else None}
                                      for day in dates]})
    elif valid:
        warnings.append("no_common_positive_baseline")
    if unavailable:
        warnings.append("some_series_have_no_observations")
    return {"protocol_version": "liveoption.returns.v1", "run_id": run_id,
            "resolution": "1d", "timezone": "America/New_York", "baseline_date": baseline,
            "comparison_end": end_day, "baseline_policy": "first_common_positive_daily_close",
            "series": series, "unavailable": unavailable, "warnings": warnings,
            "methodology": "return_pct = value / value_at_shared_baseline_close - 1. Underlyings use QFinZero UPQ closing prices (price return, no dividends assumed); account uses observed completed-day PMB equity, including trading costs. Missing observations remain null. Comparison-period returns differ from inception-to-date run returns."}
