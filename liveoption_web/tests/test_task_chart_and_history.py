"""Historical candles and immutable decision details for the run dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from liveoption_web.db.connection import open_db
from trading.ingest.artifact import write_decision_artifact

HEADERS = {"X-Internal-Secret": "test-secret"}


@pytest.mark.asyncio
async def test_custom_daily_chart_uses_daily_prices_without_a_preset(client, monkeypatch):
    from liveoption_web.api import tasks
    from unittest.mock import Mock
    monkeypatch.setattr(tasks.service, "get_run", AsyncMock(return_value={
        "id": "custom-chart", "scenario": "custom_daily", "universe": ["QQQ"],
        "date_start": "2025-01-06", "date_end": "2025-01-10",
    }))
    reply = Mock()
    reply.json.return_value = [{"date": "2025-01-06", "open": 500, "high": 505, "low": 499, "close": 503}]
    get = Mock(return_value=reply)
    monkeypatch.setattr("requests.get", get)
    response = await client.get("/v1/tasks/custom-chart/chart?symbol=QQQ", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["resolution"] == "1d"
    assert response.json()["bars"][0]["time"] == "2025-01-06"
    assert get.call_args.args[0].endswith("/stock/daily")


@pytest.mark.asyncio
async def test_chart_uses_qfinzero_upq_and_rejects_other_symbols(client, monkeypatch):
    from liveoption_web.api import tasks

    row = {"id": "run-chart", "scenario": "intraday_spy_2025_q1",
           "universe": ["SPY"], "date_start": "2025-01-06", "date_end": "2025-01-08"}
    monkeypatch.setattr(tasks.service, "get_run", AsyncMock(return_value=row))
    calls = []

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"ticker": "SPY", "window_start": 1736173800000000000,
                     "open": 590.0, "high": 591.0, "low": 589.0, "close": 590.5, "volume": 100}]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Reply()

    monkeypatch.setattr("requests.get", fake_get)
    response = await client.get("/v1/tasks/run-chart/chart?symbol=SPY&day=2025-01-06", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["resolution"] == "1m"
    assert response.json()["bars"][0]["open"] == 590.0
    assert calls[0][0].endswith("/svc/upq/stock")
    assert calls[0][1]["params"]["tickers"] == "SPY"
    assert calls[0][1]["params"]["start"] == "2025-01-06T05:00:00"
    assert calls[0][1]["params"]["end"] == "2025-01-07T05:00:00"
    assert (await client.get("/v1/tasks/run-chart/chart?symbol=QQQ&day=2025-01-06", headers=HEADERS)).status_code == 422
    assert (await client.get("/v1/tasks/run-chart/chart?symbol=SPY&day=2025-01-09", headers=HEADERS)).status_code == 422
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_chart_eastern_day_covers_dst_change(client, monkeypatch):
    from liveoption_web.api import tasks

    row = {"id": "run-dst", "scenario": "intraday_spy_2025_q1",
           "universe": ["SPY"], "date_start": "2025-03-09", "date_end": "2025-03-09"}
    monkeypatch.setattr(tasks.service, "get_run", AsyncMock(return_value=row))
    calls = []

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def fake_get(url, **kwargs):
        calls.append(kwargs["params"])
        return Reply()

    monkeypatch.setattr("requests.get", fake_get)
    response = await client.get("/v1/tasks/run-dst/chart?symbol=SPY&day=2025-03-09", headers=HEADERS)
    assert response.status_code == 200
    assert calls[0]["start"] == "2025-03-09T05:00:00"
    assert calls[0]["end"] == "2025-03-10T04:00:00"


@pytest.mark.asyncio
async def test_decision_detail_reads_saved_prompt_response_and_trades(client, tmp_path, monkeypatch):
    db_path = tmp_path / "db.sqlite"
    run_dir = tmp_path / "runs" / "run-history"
    written = write_decision_artifact(
        run_dir, decision_id="decision-history", seq=0, trading_day="2025-01-06",
        decision_ts=datetime(2025, 1, 6, 15, 30, tzinfo=timezone.utc),
        model_id="test-model", provider="anthropic",
        prompt={"system": "Act carefully", "user": "SPY chain"},
        response={"raw": "[]"}, context={"news": "point in time"},
        trades_executed=[], market_snapshot={"SPY": 590.0},
    )
    async with open_db(db_path) as db:
        await db.execute(
            """INSERT INTO runs (id,kind,scenario,config_path,model_id,provider,universe_json,
               date_start,date_end,status,status_updated_at,created_at,storage_dir)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("run-history", "batch", "intraday_spy_2025_q1", "configs/x.yaml", "test-model",
             "anthropic", json.dumps(["SPY"]), "2025-01-06", "2025-01-06", "completed",
             "2025-01-06T20:00:00Z", "2025-01-06T20:00:00Z", str(run_dir)),
        )
        await db.execute(
            """INSERT INTO decision_records (id,run_id,seq,trading_day,decision_ts,model_id,
               provider,prompt_sha256,response_sha256,storage_dir,has_trade,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("decision-history", "run-history", 0, "2025-01-06", "2025-01-06T15:30:00+00:00",
             "test-model", "anthropic", written["prompt_sha256"], written["response_sha256"],
             written["storage_dir"], 0, "2025-01-06T20:00:00Z"),
        )
        await db.commit()
    response = await client.get("/v1/decisions/decision-history", headers=HEADERS)
    assert response.status_code == 200, response.text
    artifact = response.json()["artifact"]
    assert artifact["prompt"]["system"] == "Act carefully"
    assert artifact["response"]["raw"] == "[]"
    assert artifact["context"]["news"] == "point in time"
    assert artifact["market"]["SPY"] == 590.0
    assert (await client.get("/v1/decisions/missing", headers=HEADERS)).status_code == 404
