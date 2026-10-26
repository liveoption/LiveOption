"""Opt-in real QFinZero smoke. Creates an isolated simulated account/session.

LIVEOPTION_TEST_QFINZERO=1 uv run pytest tests/trading/test_qfinzero_integration.py
No model API calls, personal account access or live-money trades are involved.
"""
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import requests

from trading.core.endpoints import DEFAULT_PMB_URL, DEFAULT_UPQ_URL, qfz_headers
from trading.tools.pmb_client import PMBClient


@pytest.mark.skipif(os.getenv("LIVEOPTION_TEST_QFINZERO") != "1", reason="requires an explicitly enabled real QFinZero hub")
def test_real_option_session_order_and_account():
    symbol = os.getenv("LIVEOPTION_TEST_SYMBOL", "QZSP")
    day = os.getenv("LIVEOPTION_TEST_DATE", "2025-03-24")
    following_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    response = requests.get(f"{DEFAULT_UPQ_URL}/option/chain_query", params={
        "underlying": symbol, "date": day, "type": "C",
        "expiry_min": following_day,
        "expiry_max": (date.fromisoformat(day) + timedelta(days=7)).isoformat(), "include_greeks": "true",
    }, headers=qfz_headers(), timeout=30)
    response.raise_for_status()
    rows = response.json()
    rows = rows if isinstance(rows, list) else rows.get("contracts", [])
    candidates = [row for row in rows if float(row.get("close") or 0) > 0]
    assert candidates, f"QFinZero needs option coverage for {symbol} {day}"
    # Pick an actively traded contract; never depend on a hard-coded strike.
    chosen = max(candidates, key=lambda row: float(row.get("volume") or 0))
    contract = chosen["ticker"]
    broker = PMBClient(DEFAULT_PMB_URL)
    broker.create_account(initial_cash=100000, start_date=day)
    # PMB/UPQ timestamps are UTC; derive regular-session ET with DST support.
    opening = datetime.fromisoformat(f"{day}T09:30:00").replace(tzinfo=ZoneInfo("America/New_York"))
    broker.create_session(start_ts=opening.astimezone(timezone.utc).isoformat(),
                         end_ts=(opening + timedelta(minutes=30)).astimezone(timezone.utc).isoformat(),
                         stocks=[symbol], options=[contract], frequency="1m")
    broker.step(5)
    order = broker.place_order({"action": "buy", "instrument_type": "option", "contract": contract, "qty": 1, "order_type": "MARKET"})
    assert order
    broker.step()
    fills = broker.get_trades()
    assert any(contract in str(fill) for fill in fills), "Simulated option order did not fill"
    assert broker.get_account()
    assert broker.get_summary()
