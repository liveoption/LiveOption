from unittest.mock import AsyncMock, Mock

import pytest

from liveoption_web.runs.returns import compare_returns

HEADERS = {"X-Internal-Secret": "test-secret"}


def test_shared_close_baseline_with_missing_middle_values_and_zero_final_equity():
    prices = [{"ticker": ticker, "date": f"2025-01-0{day}", "close": value} for ticker, day, value in
              [("QQQ", 1, 100), ("QQQ", 2, 110), ("QQQ", 3, 120), ("NVDA", 1, 200), ("NVDA", 3, 180)]]
    days = [{"trading_day": f"2025-01-0{day}", "portfolio_value": value} for day, value in [(1, 1000), (2, 900), (3, 0)]]
    result = compare_returns("r", ["QQQ", "NVDA"], prices, days, "2025-01-01", "2025-01-03")
    assert result["baseline_date"] == "2025-01-01" and result["comparison_end"] == "2025-01-03"
    qqq, nvda, account = result["series"]
    assert qqq["points"][-1]["return_pct"] == pytest.approx(0.2)
    assert nvda["points"][1]["return_pct"] is None
    assert nvda["points"][-1]["index_value"] == 90
    assert account["points"][-1]["return_pct"] == -1  # Do not hide bankruptcy.


def test_baseline_starts_on_first_common_date_and_never_uses_starting_cash():
    prices = [{"ticker": "QQQ", "date": "2025-01-01", "close": 50}, {"ticker": "QQQ", "date": "2025-01-02", "close": 100}]
    days = [{"trading_day": "2025-01-02", "portfolio_value": 3000}]
    result = compare_returns("r", ["QQQ"], prices, days, "2025-01-01", "2025-01-03")
    assert result["baseline_date"] == "2025-01-02"
    assert all(series["points"][0]["return_pct"] == 0 for series in result["series"])
    assert result["series"][-1]["baseline_value"] == 3000


def test_missing_account_and_nonfinite_prices_are_not_fabricated():
    result = compare_returns("r", ["QQQ"], [{"ticker": "QQQ", "date": "2025-01-01", "close": float("nan")}], [], "2025-01-01", "2025-01-03")
    assert result["series"] == [] and set(result["unavailable"]) == {"QQQ", "account"}


@pytest.mark.asyncio
async def test_return_api_uses_qfinzero_bulk_prices_and_observed_account_equity(client, monkeypatch):
    from liveoption_web.api import tasks
    row = {"id": "r", "status": "running", "universe": ["QQQ", "NVDA"], "date_start": "2025-01-01", "date_end": "2025-03-31"}
    monkeypatch.setattr(tasks.service, "get_run", AsyncMock(return_value=row))
    monkeypatch.setattr("liveoption_web.eval._common.load_day_completed", lambda *_: [{"trading_day": "2025-01-02", "portfolio_value": 1000}])
    get = Mock(return_value=Mock(json=lambda: [{"ticker": "QQQ", "date": "2025-01-02", "close": 100}, {"ticker": "NVDA", "date": "2025-01-02", "close": 200}]))
    monkeypatch.setattr("requests.get", get)
    response = await client.get("/v1/tasks/r/returns", headers=HEADERS)
    assert response.status_code == 200, response.text
    result = response.json()
    assert {series["id"] for series in result["series"]} == {"QQQ", "NVDA", "account"}
    assert result["provisional"]
    assert get.call_args.kwargs["params"] == {"tickers": "QQQ,NVDA", "start": "2025-01-01", "end": "2025-01-02"}
    assert get.call_args.args[0].endswith("/svc/upq/stock/daily")
    assert (await client.get("/v1/tasks/r/returns?symbols=AAPL", headers=HEADERS)).status_code == 422
    assert (await client.get("/v1/tasks/r/returns?symbols=&account=false", headers=HEADERS)).status_code == 422


@pytest.mark.asyncio
async def test_running_run_without_observations_does_not_fetch_future_prices(client, monkeypatch):
    from liveoption_web.api import tasks
    monkeypatch.setattr(tasks.service, "get_run", AsyncMock(return_value={"id": "r", "status": "running", "universe": ["QQQ"], "date_start": "2025-01-01", "date_end": "2025-12-31"}))
    monkeypatch.setattr("liveoption_web.eval._common.load_day_completed", lambda *_: [])
    get = Mock()
    monkeypatch.setattr("requests.get", get)
    response = await client.get("/v1/tasks/r/returns", headers=HEADERS)
    assert response.status_code == 200 and response.json()["series"] == []
    get.assert_not_called()
