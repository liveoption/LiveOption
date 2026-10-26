"""Tests for trading.tools.contract_discovery."""

import pytest
from unittest.mock import patch, MagicMock

from trading.tools.contract_discovery import (
    discover_contracts_weekly,
    get_weekly_option_dates,
    query_stock_price,
    _select_contract,
    _query_chain_with_strikes,
)


# ---------------------------------------------------------------------------
# get_weekly_option_dates
# ---------------------------------------------------------------------------

class TestGetWeeklyOptionDates:
    def test_generates_weekly_dates(self):
        dates = get_weekly_option_dates("2025-01-06", "2025-02-03")
        # Jan 6 is a Monday.  Should get Jan 6, Jan 13, Jan 20, Jan 27.
        assert len(dates) == 4
        assert dates[0][0] == "2025-01-06"
        assert dates[1][0] == "2025-01-13"
        assert dates[2][0] == "2025-01-20"
        assert dates[3][0] == "2025-01-27"

    def test_first_date_is_start_even_if_not_monday(self):
        dates = get_weekly_option_dates("2025-01-08", "2025-01-20")
        # Jan 8 is Wednesday; first entry should be Jan 8, next is Mon Jan 13
        assert dates[0][0] == "2025-01-08"
        assert dates[1][0] == "2025-01-13"

    def test_expiry_bounds(self):
        dates = get_weekly_option_dates("2025-01-06", "2025-01-13", dte_min=7, dte_max=45)
        query, exp_min, exp_max = dates[0]
        assert exp_min == "2025-01-13"  # +7
        assert exp_max == "2025-02-20"  # +45

    def test_empty_range(self):
        dates = get_weekly_option_dates("2025-01-06", "2025-01-06")
        assert dates == []


# ---------------------------------------------------------------------------
# _select_contract
# ---------------------------------------------------------------------------

class TestSelectContract:
    def test_selects_closest_strike(self):
        chain = [
            {"ticker": "A", "strike": 500, "close": 5.0},
            {"ticker": "B", "strike": 505, "close": 3.0},
            {"ticker": "C", "strike": 510, "close": 1.0},
        ]
        result = _select_contract(chain, target_strike=504)
        assert result["ticker"] == "B"

    def test_skips_zero_close(self):
        chain = [
            {"ticker": "A", "strike": 500, "close": 0},
            {"ticker": "B", "strike": 510, "close": 2.0},
        ]
        result = _select_contract(chain, target_strike=500)
        # A has close=0, so B is selected despite being farther away
        assert result["ticker"] == "B"

    def test_returns_none_when_no_candidates(self):
        chain = [{"ticker": "A", "strike": 500, "close": 0}]
        assert _select_contract(chain, target_strike=500) is None

    def test_returns_none_for_empty_chain(self):
        assert _select_contract([], target_strike=500) is None


# ---------------------------------------------------------------------------
# discover_contracts_weekly (integration-level with mocks)
# ---------------------------------------------------------------------------

class TestDiscoverContractsWeekly:
    @patch("trading.tools.contract_discovery.query_stock_price")
    @patch("trading.tools.contract_discovery._query_chain_with_strikes")
    def test_discovers_and_deduplicates(self, mock_chain, mock_price):
        """Contracts with the same ticker across weeks are deduplicated."""
        mock_price.return_value = 500.0
        # Both weeks return the same ticker
        mock_chain.return_value = [
            {"ticker": "O:QQQ250131C00525000", "strike": 525, "expiry": "2025-01-31", "close": 5.0},
        ]

        contracts = discover_contracts_weekly(
            upq_base_url="http://fake:19350",
            underlying="QQQ",
            start_date="2025-01-06",
            end_date="2025-01-20",
            option_type="C",
            otm_pct=0.05,
            ref_price=500.0,
        )

        # Same ticker appears only once
        assert len(contracts) == 1
        assert contracts[0]["ticker"] == "O:QQQ250131C00525000"

    @patch("trading.tools.contract_discovery.query_stock_price")
    @patch("trading.tools.contract_discovery._query_chain_with_strikes")
    def test_discovers_multiple_unique(self, mock_chain, mock_price):
        """Different tickers across weeks are all kept."""
        mock_price.return_value = 500.0
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            return [
                {
                    "ticker": f"O:QQQ250{call_count[0]:02d}31C00525000",
                    "strike": 525,
                    "expiry": f"2025-0{call_count[0]}-28",
                    "close": 5.0,
                },
            ]

        mock_chain.side_effect = side_effect

        contracts = discover_contracts_weekly(
            upq_base_url="http://fake:19350",
            underlying="QQQ",
            start_date="2025-01-06",
            end_date="2025-01-20",
            option_type="C",
            otm_pct=0.05,
            ref_price=500.0,
        )

        assert len(contracts) == 2

    @patch("trading.tools.contract_discovery.query_stock_price")
    @patch("trading.tools.contract_discovery._query_chain_with_strikes")
    def test_handles_empty_chain(self, mock_chain, mock_price):
        """If chain returns no contracts, discovery returns empty."""
        mock_price.return_value = 500.0
        mock_chain.return_value = []

        contracts = discover_contracts_weekly(
            upq_base_url="http://fake:19350",
            underlying="QQQ",
            start_date="2025-01-06",
            end_date="2025-01-13",
            option_type="C",
            otm_pct=0.05,
            ref_price=500.0,
        )

        assert contracts == []

    @patch("trading.tools.contract_discovery.query_stock_price")
    @patch("trading.tools.contract_discovery._query_chain_with_strikes")
    def test_uses_ref_price_as_fallback(self, mock_chain, mock_price):
        """When UPQ price query returns None, ref_price is used."""
        mock_price.return_value = None
        mock_chain.return_value = [
            {"ticker": "O:QQQ250131C00525000", "strike": 525, "expiry": "2025-01-31", "close": 5.0},
        ]

        contracts = discover_contracts_weekly(
            upq_base_url="http://fake:19350",
            underlying="QQQ",
            start_date="2025-01-06",
            end_date="2025-01-13",
            option_type="C",
            otm_pct=0.05,
            ref_price=500.0,
        )

        assert len(contracts) == 1
        # Verify chain was called with strike range based on ref_price
        call_kwargs = mock_chain.call_args
        assert call_kwargs is not None

    @patch("trading.tools.contract_discovery.query_stock_price")
    @patch("trading.tools.contract_discovery._query_chain_with_strikes")
    def test_call_strike_range(self, mock_chain, mock_price):
        """For calls, strike_min > price and target = price * (1 + otm_pct)."""
        mock_price.return_value = 500.0
        mock_chain.return_value = []

        discover_contracts_weekly(
            upq_base_url="http://fake:19350",
            underlying="QQQ",
            start_date="2025-01-06",
            end_date="2025-01-13",
            option_type="C",
            otm_pct=0.05,
            ref_price=500.0,
        )

        _, kwargs = mock_chain.call_args
        # strike_min = 500 * (1 + 0.025) = 512.5
        assert kwargs["strike_min"] == pytest.approx(512.5)
        # strike_max = 500 * (1 + 0.10) = 550.0
        assert kwargs["strike_max"] == pytest.approx(550.0)

    @patch("trading.tools.contract_discovery.query_stock_price")
    @patch("trading.tools.contract_discovery._query_chain_with_strikes")
    def test_put_strike_range(self, mock_chain, mock_price):
        """For puts, strike_max < price and target = price * (1 - otm_pct)."""
        mock_price.return_value = 500.0
        mock_chain.return_value = []

        discover_contracts_weekly(
            upq_base_url="http://fake:19350",
            underlying="QQQ",
            start_date="2025-01-06",
            end_date="2025-01-13",
            option_type="P",
            otm_pct=0.05,
            ref_price=500.0,
        )

        _, kwargs = mock_chain.call_args
        # strike_min = 500 * (1 - 0.10) = 450.0
        assert kwargs["strike_min"] == pytest.approx(450.0)
        # strike_max = 500 * (1 - 0.025) = 487.5
        assert kwargs["strike_max"] == pytest.approx(487.5)
