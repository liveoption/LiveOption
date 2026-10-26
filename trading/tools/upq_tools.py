# trading/tools/upq_tools.py
"""
UPQ (Universal Price Query) tools for option chain queries and minute bar retrieval.

Merged from free_trading/chain_helper.py with additional minute bar support.
"""
import requests
from datetime import date, timedelta
from trading.core.endpoints import qfz_headers


def select_strike(
    spot: float, otm_pct: float, right: str, available_strikes: list[float]
) -> float:
    """Pick strike closest to spot*(1+otm_pct) for calls, spot*(1-otm_pct) for puts."""
    target = spot * (1 + otm_pct) if right.upper() == "C" else spot * (1 - otm_pct)
    return min(available_strikes, key=lambda s: abs(s - target))


def filter_by_dte(
    contracts: list[dict], as_of: date, dte_min: int, dte_max: int
) -> list[dict]:
    """Keep contracts whose DTE is in [dte_min, dte_max], adding 'dte' field."""
    result = []
    for c in contracts:
        dte = (date.fromisoformat(c["expiry"]) - as_of).days
        if dte_min <= dte <= dte_max:
            result.append({**c, "dte": dte})
    return result


def query_option_chain(
    upq_base_url: str,
    underlying: str,
    trade_date: str,
    right: str,
    dte_min: int,
    dte_max: int,
) -> list[dict]:
    """
    Query UPQ chain_query endpoint and return DTE-filtered contracts.
    Each returned dict has: ticker, strike, expiry, dte, close, volume, etc.
    """
    as_of = date.fromisoformat(trade_date)
    expiry_min = (as_of + timedelta(days=dte_min)).isoformat()
    expiry_max = (as_of + timedelta(days=dte_max)).isoformat()
    params = {
        "underlying": underlying,
        "date": trade_date,
        "type": right,
        "expiry_min": expiry_min,
        "expiry_max": expiry_max,
        "include_greeks": "true",
    }
    resp = requests.get(f"{upq_base_url}/option/chain_query", params=params, headers=qfz_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    # UPQ returns a raw JSON array of contract dicts
    contracts = data if isinstance(data, list) else data.get("contracts", [])
    return filter_by_dte(contracts, as_of=as_of, dte_min=dte_min, dte_max=dte_max)


def get_minute_bars(
    upq_base_url: str,
    symbols: list[str],
    start: str,
    end: str,
    include_greeks: bool = False,
) -> dict[str, list[dict]]:
    """
    Fetch minute OHLCV bars from UPQ for the given symbols and time range.

    Uses ``/option/ticker_query?resolution=minute`` for option contracts
    (OPRA format ``O:...``) and ``/stock`` for stock tickers.

    Args:
        upq_base_url: UPQ base URL (e.g. "http://127.0.0.1:19777/svc/upq")
        symbols: list of tickers or OPRA contract IDs
        start: ISO timestamp for window start (YYYY-MM-DDTHH:MM:SS)
        end: ISO timestamp for window end (YYYY-MM-DDTHH:MM:SS)
        include_greeks: request Greeks for option contracts

    Returns:
        dict mapping symbol -> list of bar dicts with keys:
        open, high, low, close, volume, window_start
    """
    result: dict[str, list[dict]] = {}
    for symbol in symbols:
        if symbol.startswith("O:"):
            params = {
                "contract": symbol,
                "start": start,
                "end": end,
                "resolution": "minute",
            }
            if include_greeks:
                params["include_greeks"] = "true"
            resp = requests.get(
                f"{upq_base_url}/option/ticker_query", params=params, headers=qfz_headers(), timeout=30,
            )
        else:
            resp = requests.get(
                f"{upq_base_url}/stock",
                params={"tickers": symbol, "start": start, "end": end},
                headers=qfz_headers(),
                timeout=30,
            )
        resp.raise_for_status()
        result[symbol] = resp.json()
    return result
