"""Tests for trading.tools.pmb_client — migrated from tests/free_trading/test_pmb_client.py."""
from unittest.mock import patch, MagicMock
from trading.tools.pmb_client import PMBClient
from trading.core.endpoints import qfz_headers


def _make_client() -> PMBClient:
    client = PMBClient(base_url="http://localhost:19320")
    client.account_id = "acc-1"
    client.session_id = "sess-1"
    return client


@patch("trading.tools.pmb_client.requests.post")
def test_create_session_passes_execution_costs(mock_post):
    mock_post.return_value.json.return_value = {"session_id": "sess-2"}
    client = _make_client()
    client.create_session("2025-01-06", "2025-01-07", ["SPY"], [],
                          execution_config={"slippage_bps": 4, "option_spread_pct": 0.08,
                                            "fee_model": {"stock_fee_per_share": 0.001, "option_fee_per_contract": 1.25}})
    body = mock_post.call_args.kwargs["json"]
    assert body["execution_config"]["fee_model"]["option_fee_per_contract"] == 1.25
    assert body["execution_config"]["slippage_bps"] == 4


@patch("trading.tools.pmb_client.requests.get")
def test_get_summary(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "session_id": "sess-1",
        "total_return": 0.05,
        "max_drawdown": 0.02,
        "fees_paid": 10.0,
        "num_orders": 5,
        "num_trades": 4,
        "reject_rate": 0.1,
        "margin_call_count": 0,
    }
    mock_get.return_value = mock_resp

    client = _make_client()
    summary = client.get_summary()

    mock_get.assert_called_once_with(
        "http://localhost:19320/v1/sessions/sess-1/summary", headers=qfz_headers(), timeout=10
    )
    assert summary["total_return"] == 0.05
    assert summary["num_trades"] == 4


@patch("trading.tools.pmb_client.requests.get")
def test_get_trades(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "trades": [
            {"trade_id": "t1", "instrument_id": "AAPL", "side": "BUY", "qty": 10, "price": 100.0},
            {"trade_id": "t2", "instrument_id": "AAPL", "side": "SELL", "qty": 10, "price": 110.0},
        ]
    }
    mock_get.return_value = mock_resp

    client = _make_client()
    trades = client.get_trades()

    mock_get.assert_called_once_with(
        "http://localhost:19320/v1/accounts/acc-1/trades", headers=qfz_headers(), timeout=10
    )
    assert len(trades) == 2


@patch("trading.tools.pmb_client.requests.get")
def test_get_orders(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "orders": [{"order_id": "o1", "status": "FILLED"}]
    }
    mock_get.return_value = mock_resp

    client = _make_client()
    orders = client.get_orders(status_in="FILLED")

    mock_get.assert_called_once_with(
        "http://localhost:19320/v1/accounts/acc-1/orders",
        params={"status_in": "FILLED"},
        headers=qfz_headers(),
        timeout=10,
    )
    assert len(orders) == 1


@patch("trading.tools.pmb_client.requests.get")
def test_get_orders_no_filter(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"orders": []}
    mock_get.return_value = mock_resp

    client = _make_client()
    client.get_orders()

    mock_get.assert_called_once_with(
        "http://localhost:19320/v1/accounts/acc-1/orders",
        params={},
        headers=qfz_headers(),
        timeout=10,
    )


@patch("trading.tools.pmb_client.requests.post")
def test_create_session_with_frequency(mock_post):
    """Verify that the frequency parameter is passed through to the PMB API."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"session_id": "sess-new"}
    mock_post.return_value = mock_resp

    client = PMBClient(base_url="http://localhost:19320")
    client.account_id = "acc-1"

    session_id = client.create_session(
        start_ts="2025-01-06T09:30:00",
        end_ts="2025-01-10T16:00:00",
        stocks=["SPY"],
        options=[],
        frequency="1m",
    )

    assert session_id == "sess-new"
    assert client.session_id == "sess-new"

    # Verify the request body includes frequency="1m"
    call_args = mock_post.call_args
    request_body = call_args[1]["json"]
    assert request_body["frequency"] == "1m"
    assert request_body["account_id"] == "acc-1"
    assert request_body["start_ts"] == "2025-01-06T09:30:00"
    assert request_body["end_ts"] == "2025-01-10T16:00:00"
    assert request_body["universe"]["stocks"] == ["SPY"]


@patch("trading.tools.pmb_client.requests.post")
def test_create_session_default_frequency(mock_post):
    """Verify that default frequency is '1d' when not specified."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"session_id": "sess-daily"}
    mock_post.return_value = mock_resp

    client = PMBClient(base_url="http://localhost:19320")
    client.account_id = "acc-1"

    client.create_session(
        start_ts="2025-01-06T09:30:00",
        end_ts="2025-01-10T16:00:00",
        stocks=["SPY"],
        options=[],
    )

    call_args = mock_post.call_args
    request_body = call_args[1]["json"]
    assert request_body["frequency"] == "1d"
