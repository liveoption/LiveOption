"""Contract discovery for overlay strategies.

Pre-discovers option contracts for the entire backtest period by querying
the UPQ chain at each Monday, finding the contract closest to the target
OTM % strike.  Migrated from qfinzero overlay_helpers.discover_contracts_weekly().
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import requests
from trading.core.endpoints import qfz_headers

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stock price helper
# ---------------------------------------------------------------------------

def query_stock_price(
    upq_base_url: str,
    underlying: str,
    trade_date: str,
) -> float | None:
    """Get stock close price from UPQ for a specific date."""
    try:
        resp = requests.get(
            f"{upq_base_url}/stock/daily",
            headers=qfz_headers(),
            params={
                "tickers": underlying,
                "start": trade_date,
                "end": trade_date,
                "fields": "ticker,date,close",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json()
            if rows:
                return rows[0]["close"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Chain query helper (strike-range based, for discovery)
# ---------------------------------------------------------------------------

def _query_chain_with_strikes(
    upq_base_url: str,
    underlying: str,
    trade_date: str,
    option_type: str,
    strike_min: float,
    strike_max: float,
    expiry_min: str,
    expiry_max: str,
) -> list[dict]:
    """Query UPQ chain_query with explicit strike and expiry bounds."""
    params = {
        "underlying": underlying,
        "date": trade_date,
        "type": option_type,
        "strike_min": strike_min,
        "strike_max": strike_max,
        "expiry_min": expiry_min,
        "expiry_max": expiry_max,
        "include_greeks": "true",
    }
    try:
        resp = requests.get(
            f"{upq_base_url}/option/chain_query",
            headers=qfz_headers(),
            params=params,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else data.get("contracts", [])
        logger.warning("chain_query returned %d", resp.status_code)
        return []
    except Exception as exc:
        logger.warning("chain_query failed: %s", exc)
        return []


def _select_contract(chain: list[dict], target_strike: float) -> dict | None:
    """Select the contract with strike closest to *target_strike*.

    Only considers contracts with ``close > 0`` (has trading activity).
    """
    candidates = [c for c in chain if c.get("close", 0) > 0]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c["strike"] - target_strike))


# ---------------------------------------------------------------------------
# Weekly date generation
# ---------------------------------------------------------------------------

def get_weekly_option_dates(
    start_date: str,
    end_date: str,
    dte_min: int = 7,
    dte_max: int = 45,
) -> list[tuple[str, str, str]]:
    """Generate ``(query_date, expiry_min, expiry_max)`` tuples for each week.

    *query_date* is the Monday of each week (or *start_date* for the first).
    *expiry_min/max* derive from the DTE window.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    result: list[tuple[str, str, str]] = []

    current = start
    while current < end:
        query_date = current.strftime("%Y-%m-%d")
        expiry_min = (current + timedelta(days=dte_min)).strftime("%Y-%m-%d")
        expiry_max = (current + timedelta(days=dte_max)).strftime("%Y-%m-%d")
        result.append((query_date, expiry_min, expiry_max))
        # Advance to next Monday
        days_to_monday = 7 - current.weekday()
        if current.weekday() == 0:
            days_to_monday = 7
        current = current + timedelta(days=days_to_monday)

    return result


# ---------------------------------------------------------------------------
# Main discovery function
# ---------------------------------------------------------------------------

def discover_contracts_weekly(
    upq_base_url: str,
    underlying: str,
    start_date: str,
    end_date: str,
    option_type: str,
    otm_pct: float,
    ref_price: float,
    dte_min: int = 7,
    dte_max: int = 45,
) -> list[dict]:
    """Pre-discover option contracts for a weekly rebalance schedule.

    For each Monday in ``[start_date, end_date)``, queries UPQ for the best
    contract near the target OTM strike.  Deduplicates by ticker so each
    unique contract appears only once.

    Args:
        upq_base_url: UPQ base URL (e.g. ``http://127.0.0.1:19777/svc/upq``).
        underlying: Stock symbol (e.g. ``"QQQ"``).
        start_date / end_date: Backtest window (``"YYYY-MM-DD"``).
        option_type: ``"C"`` for calls, ``"P"`` for puts.
        otm_pct: OTM percentage for strike target (e.g. ``0.05``).
        ref_price: Reference stock price used as fallback.
        dte_min / dte_max: Target DTE window.

    Returns:
        List of ``{ticker, strike, expiry, close, query_date}`` dicts.
    """
    weeks = get_weekly_option_dates(start_date, end_date, dte_min, dte_max)
    contracts: list[dict] = []
    seen_tickers: set[str] = set()

    for query_date, expiry_min, expiry_max in weeks:
        week_price = (
            query_stock_price(upq_base_url, underlying, query_date) or ref_price
        )

        if option_type == "C":
            strike_min = week_price * (1 + otm_pct * 0.5)
            strike_max = week_price * (1 + otm_pct * 2.0)
            target_strike = week_price * (1 + otm_pct)
        else:  # P
            strike_min = week_price * (1 - otm_pct * 2.0)
            strike_max = week_price * (1 - otm_pct * 0.5)
            target_strike = week_price * (1 - otm_pct)

        chain = _query_chain_with_strikes(
            upq_base_url=upq_base_url,
            underlying=underlying,
            trade_date=query_date,
            option_type=option_type,
            strike_min=strike_min,
            strike_max=strike_max,
            expiry_min=expiry_min,
            expiry_max=expiry_max,
        )

        selected = _select_contract(chain, target_strike)
        if selected:
            ticker = selected["ticker"]
            if ticker in seen_tickers:
                logger.debug(
                    "DISCOVERY %s (spot=$%.2f): %s already discovered, skipping",
                    query_date, week_price, ticker,
                )
                continue
            seen_tickers.add(ticker)
            contracts.append({
                "ticker": ticker,
                "strike": selected["strike"],
                "expiry": selected["expiry"],
                "close": selected["close"],
                "query_date": query_date,
            })
            logger.info(
                "DISCOVERY %s (spot=$%.2f): %s strike=$%.2f expiry=%s premium=$%.2f",
                query_date, week_price, ticker,
                selected["strike"], selected["expiry"], selected["close"],
            )
        else:
            logger.info(
                "DISCOVERY %s (spot=$%.2f): no suitable %s contract found",
                query_date, week_price, option_type,
            )

    return contracts
