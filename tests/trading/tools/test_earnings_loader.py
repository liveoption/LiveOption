"""Tests for the earnings data loader."""

import pytest
from unittest.mock import patch

from trading.tools.earnings_loader import (
    EarningsLoader,
    _is_bmo_from_utc as _is_bmo,
    _map_ticker_to_upq,
    _map_upq_to_ticker,
    _safe_float,
)


# ---------------------------------------------------------------------------
# Ticker mapping
# ---------------------------------------------------------------------------


class TestTickerMapping:
    def test_brkb_to_upq(self):
        assert _map_ticker_to_upq("BRK.B") == "BRKB"

    def test_brkb_from_upq(self):
        assert _map_upq_to_ticker("BRKB") == "BRK.B"

    def test_regular_ticker_unchanged(self):
        assert _map_ticker_to_upq("AAPL") == "AAPL"
        assert _map_upq_to_ticker("AAPL") == "AAPL"


# ---------------------------------------------------------------------------
# BMO detection
# ---------------------------------------------------------------------------


class TestIsBMO:
    def test_bmo_by_ticker(self):
        assert all(_is_bmo("", ticker) for ticker in ("JPM", "TSM", "UNH", "PG", "BRK.B"))

    def test_amc_by_ticker(self):
        assert all(not _is_bmo("", ticker) for ticker in ("AAPL", "NVDA", "TSLA"))

    def test_time_override(self):
        # UTC timestamps override the fallback ticker heuristic.
        assert _is_bmo("2025-01-30T13:30:00Z", "AAPL") is True
        assert _is_bmo("2025-01-30T21:30:00Z", "JPM") is False

    def test_empty_time(self):
        assert _is_bmo("", "JPM") is True
        assert _is_bmo("", "AAPL") is False


# ---------------------------------------------------------------------------
# EarningsLoader (offline mode)
# ---------------------------------------------------------------------------


class TestEarningsLoaderOffline:
    """Test EarningsLoader when ESP returns no events."""

    def test_empty_calendar(self):
        loader = EarningsLoader()
        with patch.object(loader, "_query_npp", return_value=[]):
            events = loader.get_earnings_calendar(
                tickers=["AAPL", "NVDA"],
                start="2025-01-01",
                end="2025-03-31",
            )
        assert events == []

    def test_empty_historical(self):
        loader = EarningsLoader()
        with patch.object(loader, "_query_npp", return_value=[]):
            reactions = loader.get_historical_reactions("AAPL", "2025-04-01", n=4)
        assert reactions == []

    def test_event_context_empty(self):
        loader = EarningsLoader()
        with patch.object(loader, "_query_npp", return_value=[]):
            ctx = loader.get_event_context("AAPL", "2025-04-30")
        assert ctx["event"] == {}
        assert ctx["historical_reactions"] == []


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_normalize_events(self):
        loader = EarningsLoader()
        raw = [
            {
                "tickers": ["BRKB"],
                "time_utc": "2025-02-14T12:30:00Z",
                "payload": {
                    "actual_eps": "4.50",
                    "estimated_eps": "4.25",
                    "eps_surprise_percent": "5.88",
                    "actual_revenue": "97800000000",
                    "estimated_revenue": "95000000000",
                    "fiscal_period": "Q4",
                    "fiscal_year": "2024",
                },
                "importance": "high",
            }
        ]
        events = loader._normalize_events(raw)
        assert len(events) == 1
        ev = events[0]
        assert ev["ticker"] == "BRK.B"
        assert ev["upq_ticker"] == "BRKB"
        assert ev["date"] == "2025-02-14"
        assert ev["is_bmo"] is True
        assert ev["actual_eps"] == pytest.approx(4.50)
        assert ev["estimated_eps"] == pytest.approx(4.25)
        assert ev["eps_surprise_percent"] == pytest.approx(5.88)
        assert ev["fiscal_period"] == "Q4"

    def test_normalize_null_values(self):
        loader = EarningsLoader()
        raw = [
            {
                "tickers": ["AAPL"],
                "time_utc": "2025-04-30T21:30:00Z",
                "payload": {
                    "actual_eps": None,
                    "estimated_eps": "1.50",
                    "eps_surprise_percent": None,
                    "actual_revenue": None,
                    "estimated_revenue": None,
                    "fiscal_period": "Q2",
                    "fiscal_year": "2025",
                },
                "importance": None,
            }
        ]
        events = loader._normalize_events(raw)
        ev = events[0]
        assert ev["actual_eps"] is None
        assert ev["estimated_eps"] == pytest.approx(1.50)
        assert ev["eps_surprise_percent"] is None
        assert ev["is_bmo"] is False  # AAPL is AMC


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCaching:
    def test_calendar_caching(self):
        loader = EarningsLoader()
        with patch.object(loader, "_query_npp", return_value=[]) as query:
            events1 = loader.get_earnings_calendar(["AAPL"], "2025-01-01", "2025-12-31")
            events2 = loader.get_earnings_calendar(["AAPL"], "2025-01-01", "2025-12-31")
        query.assert_called_once()
        assert events1 is events2  # same object from cache


# ---------------------------------------------------------------------------
# safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_valid_float(self):
        assert _safe_float(3.14) == pytest.approx(3.14)
        assert _safe_float("2.5") == pytest.approx(2.5)
        assert _safe_float(0) == pytest.approx(0.0)

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_invalid_returns_none(self):
        assert _safe_float("not_a_number") is None
        assert _safe_float("") is None
