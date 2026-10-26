# trading/tools/pmb_client.py
"""
Synchronous HTTP client for the PMB REST API.

Migrated from free_trading/pmb_client.py with the following changes:
- Removed dependency on free_trading.config (settings)
- create_session() accepts a `frequency` parameter instead of hardcoding "1d"

API shapes follow the QFinZero v1 PMB REST contract:
https://github.com/CityU-MLO/qfinzero
  POST /v1/accounts          body: {initial_cash, start_date}
  POST /v1/sessions          body: CreateSessionRequest
  GET  /v1/accounts/{id}     returns AccountState snapshot
  POST /v1/orders            body: CreateOrderRequest (nested OrderSpec)
  POST /v1/sessions/{id}/step
"""
import requests

from trading.core.endpoints import DEFAULT_PMB_URL, qfz_headers


class PMBClient:
    def __init__(self, base_url: str = DEFAULT_PMB_URL):
        self.base_url = base_url.rstrip("/")
        self.account_id: str | None = None
        self.session_id: str | None = None

    def create_account(self, initial_cash: float = 100_000.0, start_date: str = "2025-01-01") -> str:
        resp = requests.post(
            f"{self.base_url}/v1/accounts",
            json={"initial_cash": initial_cash, "start_date": start_date},
            headers=qfz_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        self.account_id = resp.json()["account_id"]
        return self.account_id

    def create_session(
        self,
        start_ts: str,
        end_ts: str,
        stocks: list[str],
        options: list[str],
        frequency: str = "1d",
        execution_config: dict | None = None,
    ) -> str:
        """Create a PMB session.

        Args:
            start_ts: UTC ISO timestamp, preferably with explicit offset
            end_ts: UTC ISO timestamp, preferably with explicit offset
            stocks: list of stock symbols
            options: list of OPRA option contract IDs
            frequency: bar frequency — "1d" for daily, "1m" for minute
        """
        assert self.account_id, "call create_account() first"
        body = {
            "account_id": self.account_id,
            "frequency": frequency,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "universe": {"stocks": stocks, "options": options},
            "execution_config": execution_config or {
                "slippage_bps": 1.0,
                "option_spread_pct": 0.05,
                "fee_model": {
                    "stock_fee_per_share": 0.0005,
                    "option_fee_per_contract": 0.65,
                },
            },
        }
        resp = requests.post(f"{self.base_url}/v1/sessions", json=body, headers=qfz_headers(), timeout=600)
        resp.raise_for_status()
        self.session_id = resp.json()["session_id"]
        return self.session_id

    def get_account(self) -> dict:
        assert self.account_id, "call create_account() first"
        resp = requests.get(
            f"{self.base_url}/v1/accounts/{self.account_id}", headers=qfz_headers(), timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    def place_order(self, action: dict, spread_id: str | None = None) -> dict:
        """
        Submit one trade action to PMB.

        action dict keys: action, instrument_type, contract, qty, order_type, limit_price (opt)
        spread_id: optional identifier linking legs of a multi-leg spread
        """
        assert self.session_id and self.account_id, "call create_session() first"
        side = action["action"]
        if side not in ("buy", "sell"):
            raise ValueError(f"invalid action '{side}', must be 'buy' or 'sell'")

        instrument_type = action.get("instrument_type", "option").upper()
        contract = action["contract"]

        if instrument_type == "STOCK":
            instrument = {"type": "STOCK", "symbol": contract}
        else:
            instrument = {"type": "OPTION", "contract": contract}

        order_spec = {
            "instrument": instrument,
            "side": side.upper(),
            "order_type": action.get("order_type", "MARKET"),
            "qty": int(action["qty"]),
        }
        if "limit_price" in action and action["limit_price"] is not None:
            order_spec["limit_price"] = float(action["limit_price"])

        body = {
            "session_id": self.session_id,
            "account_id": self.account_id,
            "order": order_spec,
        }
        if spread_id:
            body["spread_id"] = spread_id
        resp = requests.post(f"{self.base_url}/v1/orders", json=body, headers=qfz_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def step(self, n: int = 1) -> dict:
        assert self.session_id, "call create_session() first"
        resp = requests.post(
            f"{self.base_url}/v1/sessions/{self.session_id}/step",
            json={"step": n},
            headers=qfz_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def add_contracts(self, contracts: list[str]) -> dict:
        """Dynamically load option contracts into the session cache."""
        assert self.session_id, "call create_session() first"
        resp = requests.post(
            f"{self.base_url}/v1/sessions/{self.session_id}/add_contracts",
            json={"contracts": contracts},
            headers=qfz_headers(),
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def get_summary(self) -> dict:
        """Fetch SessionSummary from PMB (total_return, max_drawdown, fees, etc.)."""
        assert self.session_id, "call create_session() first"
        resp = requests.get(
            f"{self.base_url}/v1/sessions/{self.session_id}/summary", headers=qfz_headers(), timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    def get_trades(self) -> list[dict]:
        """Fetch all executed trades for the current account."""
        assert self.account_id, "call create_account() first"
        resp = requests.get(
            f"{self.base_url}/v1/accounts/{self.account_id}/trades", headers=qfz_headers(), timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("trades", [])

    def get_orders(self, status_in: str | None = None) -> list[dict]:
        """Fetch orders, optionally filtered by status (comma-separated)."""
        assert self.account_id, "call create_account() first"
        params = {}
        if status_in:
            params["status_in"] = status_in
        resp = requests.get(
            f"{self.base_url}/v1/accounts/{self.account_id}/orders",
            params=params,
            headers=qfz_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("orders", [])
