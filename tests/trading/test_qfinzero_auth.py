"""Exercise REST/urllib callers against an authenticated local HTTP fixture."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import pytest

from trading import app
from trading.core.endpoints import qfz_headers
from trading.tools.pmb_client import PMBClient
from trading.tools.upq_tools import get_minute_bars, query_option_chain
from trading.tools.contract_discovery import query_stock_price, _query_chain_with_strikes
from trading.tools.earnings_loader import EarningsLoader, fetch_daily_bars, fetch_npp_news
from trading.tools.npp_context import _post_json


@pytest.fixture
def authenticated_hub(monkeypatch):
    key = "qfz_test_fixture_only"
    monkeypatch.setenv("QFZ_API_KEY", key)
    monkeypatch.setenv("OPENAI_API_KEY", "model-credential-must-not-be-used")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            path = urlparse(self.path).path
            calls.append((self.command, path, self.headers.get("Authorization")))
            if path != "/health" and self.headers.get("Authorization") != f"Bearer {key}":
                self.send_response(401)
                self.end_headers()
                return
            size = int(self.headers.get("Content-Length", 0))
            if size:
                self.rfile.read(size)
            if path.endswith("/health"):
                result = {"status": "ok"}
            elif any("MISSING" in values for values in parse_qs(urlparse(self.path).query).values()):
                result = []
            elif path.endswith("/chain_query"):
                result = [{"ticker": "O:QZSP250328C00500000", "strike": 500, "expiry": "2025-03-28", "close": 2}]
            elif path.startswith("/svc/upq/"):
                result = [{"ticker": "QZSP", "date": "2025-03-24", "close": 500}]
            elif path.startswith("/svc/esp/"):
                result = {"events": [{"tickers": ["QZSP"], "time_utc": "2025-03-24T10:00:00Z", "title": "Sample news"}]}
            else:
                result = {"account_id": "a", "session_id": "s", "trades": [], "orders": []}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        do_GET = do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", key, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_all_broker_operations_send_qfinzero_key(authenticated_hub):
    url, key, calls = authenticated_hub
    broker = PMBClient(url + "/svc/pmb")
    broker.create_account()
    broker.create_session("2025-03-24T09:30:00", "2025-03-24T16:00:00", ["QZSP"], [], "1m")
    broker.place_order({"action": "buy", "contract": "O:QZSP250328C00500000", "qty": 1})
    broker.step()
    broker.add_contracts(["O:QZSP250328C00500000"])
    broker.get_account()
    broker.get_summary()
    broker.get_trades()
    broker.get_orders()
    assert len(calls) == 9
    assert all(auth == f"Bearer {key}" for _, _, auth in calls)


def test_market_and_news_callers_send_key(authenticated_hub):
    url, key, calls = authenticated_hub
    upq, esp = url + "/svc/upq", url + "/svc/esp"
    assert query_option_chain(upq, "QZSP", "2025-03-24", "C", 0, 7)
    assert get_minute_bars(upq, ["QZSP", "O:QZSP250328C00500000"], "2025-03-24T14:00:00", "2025-03-24T14:05:00")
    assert query_stock_price(upq, "QZSP", "2025-03-24") == 500
    assert _query_chain_with_strikes(upq, "QZSP", "2025-03-24", "C", 490, 510, "2025-03-24", "2025-03-28")
    assert fetch_daily_bars(upq, "QZSP", "2025-03-24", "2025-03-24")
    assert fetch_npp_news(esp, "QZSP", "2025-03-20", "2025-03-24")
    assert EarningsLoader(esp).get_earnings_calendar(["QZSP"], "2025-03-01", "2025-03-31")
    assert _post_json(esp + "/esp/news/search", {"tickers": ["QZSP"]})
    assert all(auth == f"Bearer {key}" for _, _, auth in calls)


def test_doctor_checks_key_and_data_without_model_or_trade_calls(authenticated_hub, capsys):
    url, key, calls = authenticated_hub
    assert not app.doctor({"QFZ_HUB_URL": url, "QFZ_API_KEY": "wrong"})
    output = capsys.readouterr().out
    assert "QFZ_API_KEY" in output
    assert key not in output
    assert app.doctor({"QFZ_HUB_URL": url, "QFZ_API_KEY": key}, symbol="QZSP", day="2025-03-24", with_news=True)
    assert all(not path.endswith(("/accounts", "/orders", "/sessions")) for _, path, _ in calls)
    assert "Stock minute bars" in capsys.readouterr().out


def test_auth_can_be_disabled_for_legacy_hubs_without_using_model_key(monkeypatch):
    monkeypatch.delenv("QFZ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "model-only")
    assert qfz_headers() == {}
    monkeypatch.setenv("QFZ_API_KEY", "  qfz_example  ")
    assert qfz_headers() == {"Authorization": "Bearer qfz_example"}
    assert qfz_headers("") == {}


def test_doctor_fails_when_healthy_services_have_no_requested_data(authenticated_hub, capsys):
    url, key, _ = authenticated_hub
    assert not app.doctor({"QFZ_HUB_URL": url, "QFZ_API_KEY": key}, symbol="MISSING", day="2025-03-24")
    output = capsys.readouterr().out
    assert "FAIL  Stock daily bars: no records" in output
    assert "FAIL  Option chain" in output
