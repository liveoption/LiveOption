"""Integration tests for PnL decomposition pipeline."""

from __future__ import annotations

import pytest

from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
)
from trading.runner import TradingRunner


def _make_option_bar(
    contract: str = "O:SPY250110C00595000",
    close: float = 2.50,
    delta: float | None = 0.50,
    gamma: float | None = 0.05,
    theta: float | None = -0.02,
    vega: float | None = 0.10,
    iv: float | None = 0.20,
) -> BarData:
    return BarData(
        symbol=contract,
        open=2.0, high=2.8, low=1.9, close=close,
        volume=500, window_start="2025-01-06T10:30:00",
        instrument_type="OPTION",
        delta=delta, gamma=gamma, theta=theta, vega=vega, iv=iv,
    )


def _make_stock_bar(
    symbol: str = "SPY",
    close: float = 595.0,
) -> BarData:
    return BarData(
        symbol=symbol,
        open=594.0, high=596.0, low=593.0, close=close,
        volume=10000, window_start="2025-01-06T10:30:00",
        instrument_type="STOCK",
    )


def _make_market_event(bars: list[BarData]) -> MarketEvent:
    return MarketEvent(event_ts="2025-01-06T10:30:00", bars=bars, bar_history={})


def _make_position(
    contract: str = "O:SPY250110C00595000",
    qty: int = 5,
    mark_price: float = 2.50,
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side="LONG" if qty > 0 else "SHORT",
        qty=qty,
        avg_entry=2.30,
        mark_price=mark_price,
        unrealized_pnl=100.0,
        dte=4,
        stale=False,
        stale_minutes=0,
    )


def _make_portfolio(positions: list[PositionInfo]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=90000, equity=100000, daily_pnl=0, daily_pnl_pct=0,
        positions=positions,
    )


class TestEnrichPositionGreeksExtended:
    def test_iv_populated(self):
        pos = _make_position()
        portfolio = _make_portfolio([pos])
        bar = _make_option_bar(iv=0.25)
        market = _make_market_event([bar, _make_stock_bar()])

        TradingRunner._enrich_position_greeks(portfolio, market)

        assert pos.iv == 0.25

    def test_underlying_price_populated(self):
        pos = _make_position()
        portfolio = _make_portfolio([pos])
        bar = _make_option_bar()
        stock_bar = _make_stock_bar(close=600.0)
        market = _make_market_event([bar, stock_bar])

        TradingRunner._enrich_position_greeks(portfolio, market)

        assert pos.underlying_price == 600.0

    def test_strike_and_right_populated(self):
        pos = _make_position(contract="O:SPY250110C00595000")
        portfolio = _make_portfolio([pos])
        bar = _make_option_bar(contract="O:SPY250110C00595000")
        market = _make_market_event([bar, _make_stock_bar()])

        TradingRunner._enrich_position_greeks(portfolio, market)

        assert pos.strike == 595.0
        assert pos.right == "C"


from dataclasses import dataclass, field as dc_field


@dataclass
class MockTradingState:
    current_ts: str = "2025-01-06T10:30:00"
    current_date: str = "2025-01-06"
    is_new_day: bool = False
    market_event: MarketEvent | None = None
    portfolio: PortfolioSnapshot | None = None
    fills: list = dc_field(default_factory=list)




from trading.core.time_contract import utc_from_iso as _utc_from_iso


def _ts(s: str):
    """Helper: convert naive ET ISO string to tz-aware UTC datetime for tests."""
    if not (s.endswith("Z") or "+" in s):
        s = s + "+00:00"
    return _utc_from_iso(s)


class TestComputePnlDecomposition:
    def test_first_step_all_zeros(self):
        """First step has no prev snapshot → decomposition all zeros."""
        from trading.runner import TradingRunner, _PositionGreeksSnapshot

        pos = _make_position(qty=5, mark_price=2.50)
        portfolio = _make_portfolio([pos])
        option_bar = _make_option_bar(close=2.50, iv=0.20)
        stock_bar = _make_stock_bar(close=595.0)
        market = _make_market_event([option_bar, stock_bar])

        TradingRunner._enrich_position_greeks(portfolio, market)

        current_snapshot = TradingRunner._build_greeks_snapshot(
            portfolio, market, _ts("2025-01-06T10:30:00")
        )
        decomp = TradingRunner._compute_step_decomposition(
            current_snapshot, None, _ts("2025-01-06T10:30:00")
        )

        assert decomp.step_delta_pnl == 0.0
        assert decomp.step_total_pnl == 0.0
        assert len(decomp.positions) == 0

    def test_basic_two_step_delta(self):
        """S moves 595→596 with delta=0.50, qty=5 → delta_pnl = 250."""
        from trading.runner import TradingRunner, _PositionGreeksSnapshot

        Snap = _PositionGreeksSnapshot

        prev_snap = {
            "O:SPY250110C00595000": Snap(
                contract="O:SPY250110C00595000",
                mark_price=2.50,
                underlying_price=595.0,
                iv=0.20,
                delta=0.50,
                gamma=0.05,
                theta=-0.02,
                vega=0.10,
                volga=0.01,
                vanna=0.005,
                qty=5,
                timestamp=_ts("2025-01-06T10:30:00"),
            )
        }

        current_snap = {
            "O:SPY250110C00595000": Snap(
                contract="O:SPY250110C00595000",
                mark_price=3.0,
                underlying_price=596.0,
                iv=0.20,
                delta=0.52,
                gamma=0.05,
                theta=-0.02,
                vega=0.10,
                volga=0.01,
                vanna=0.005,
                qty=5,
                timestamp=_ts("2025-01-06T10:31:00"),
            )
        }

        decomp = TradingRunner._compute_step_decomposition(
            current_snap, prev_snap, _ts("2025-01-06T10:31:00")
        )

        assert len(decomp.positions) == 1
        # delta_pnl = 5 * 100 * 0.50 * (596-595) = 250.0
        assert decomp.positions[0].delta_pnl == pytest.approx(250.0)
        assert decomp.step_delta_pnl == pytest.approx(250.0)
        # total_pnl = (3.0 - 2.5) * 5 * 100 = 250.0
        assert decomp.step_total_pnl == pytest.approx(250.0)

    def test_stock_position_delta_only(self):
        """Stock position: delta=1.0, gamma/theta/vega known zero."""
        from trading.runner import TradingRunner, _PositionGreeksSnapshot

        Snap = _PositionGreeksSnapshot

        prev_snap = {
            "SPY": Snap(
                contract="SPY",
                mark_price=595.0,
                underlying_price=595.0,
                iv=0.0,
                delta=1.0,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                volga=None,
                vanna=None,
                qty=100,
                timestamp=_ts("2025-01-06T10:30:00"),
            )
        }
        current_snap = {
            "SPY": Snap(
                contract="SPY",
                mark_price=596.0,
                underlying_price=596.0,
                iv=0.0,
                delta=1.0,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                volga=None,
                vanna=None,
                qty=100,
                timestamp=_ts("2025-01-06T10:31:00"),
            )
        }

        decomp = TradingRunner._compute_step_decomposition(
            current_snap, prev_snap, _ts("2025-01-06T10:31:00")
        )

        assert len(decomp.positions) == 1
        # delta_pnl = 100 * 1 * 1.0 * 1.0 = 100
        assert decomp.positions[0].delta_pnl == pytest.approx(100.0)
        assert decomp.positions[0].gamma_pnl == pytest.approx(0.0)
        assert decomp.positions[0].theta_pnl == pytest.approx(0.0)
        assert decomp.positions[0].vega_pnl == pytest.approx(0.0)
        # volga/vanna are None for stock (prev.volga=None)
        assert decomp.positions[0].volga_pnl is None
        assert decomp.positions[0].vanna_pnl is None


from trading.core.schemas import FillEvent, PnLDecomposition


class TestPatchFillPnl:
    def test_closing_long_fill(self):
        from trading.runner import TradingRunner, _PositionGreeksSnapshot

        Snap = _PositionGreeksSnapshot

        runner = object.__new__(TradingRunner)
        runner._prev_step_snapshot = {
            "O:SPY250110C00595000": Snap(
                contract="O:SPY250110C00595000", mark_price=2.50,
                underlying_price=595.0, iv=0.20, delta=0.50, gamma=0.05,
                theta=-0.02, vega=0.10, volga=0.01, vanna=0.005,
                qty=5, timestamp=_ts("2025-01-06T10:30:00"),
            )
        }

        state = MockTradingState()
        state.portfolio = _make_portfolio([])  # position closed
        state.portfolio.pnl_decomposition = PnLDecomposition()
        state.fills = [
            FillEvent(
                trade_id="t1", order_id="o1",
                contract="O:SPY250110C00595000",
                side="SELL", qty=5, price=3.00, fees=1.50,
                fill_ts="2025-01-06T10:31:00",
            )
        ]

        runner._patch_fill_pnl(state)

        # last_hop = (3.00 - 2.50) * min(5, 5) * 100 = 250.0
        assert state.portfolio.pnl_decomposition.fill_last_hop_pnl == pytest.approx(250.0)
        assert state.portfolio.pnl_decomposition.fees == pytest.approx(1.50)

    def test_cross_zero_fill(self):
        from trading.runner import TradingRunner, _PositionGreeksSnapshot

        Snap = _PositionGreeksSnapshot

        runner = object.__new__(TradingRunner)
        runner._prev_step_snapshot = {
            "O:SPY250110C00595000": Snap(
                contract="O:SPY250110C00595000", mark_price=2.50,
                underlying_price=595.0, iv=0.20, delta=0.50, gamma=0.05,
                theta=-0.02, vega=0.10, volga=0.01, vanna=0.005,
                qty=2, timestamp=_ts("2025-01-06T10:30:00"),
            )
        }

        state = MockTradingState()
        state.portfolio = _make_portfolio([])
        state.portfolio.pnl_decomposition = PnLDecomposition()
        state.fills = [
            FillEvent(
                trade_id="t1", order_id="o1",
                contract="O:SPY250110C00595000",
                side="SELL", qty=5, price=3.00, fees=2.00,
                fill_ts="2025-01-06T10:31:00",
            )
        ]

        runner._patch_fill_pnl(state)

        # closing_qty = min(5, 2) = 2, last_hop = (3.00 - 2.50) * 2 * 100 = 100.0
        assert state.portfolio.pnl_decomposition.fill_last_hop_pnl == pytest.approx(100.0)

    def test_new_position_fill_no_last_hop(self):
        from trading.runner import TradingRunner

        runner = object.__new__(TradingRunner)
        runner._prev_step_snapshot = {}  # no prev positions

        state = MockTradingState()
        state.portfolio = _make_portfolio([_make_position()])
        state.portfolio.pnl_decomposition = PnLDecomposition()
        state.fills = [
            FillEvent(
                trade_id="t1", order_id="o1",
                contract="O:SPY250110C00595000",
                side="BUY", qty=5, price=2.50, fees=1.00,
                fill_ts="2025-01-06T10:31:00",
            )
        ]

        runner._patch_fill_pnl(state)

        assert state.portfolio.pnl_decomposition.fill_last_hop_pnl == pytest.approx(0.0)
        assert state.portfolio.pnl_decomposition.fees == pytest.approx(1.00)

    def test_no_fills_no_change(self):
        from trading.runner import TradingRunner

        runner = object.__new__(TradingRunner)
        runner._prev_step_snapshot = {}

        state = MockTradingState()
        state.portfolio = _make_portfolio([])
        state.portfolio.pnl_decomposition = PnLDecomposition()
        state.fills = []

        runner._patch_fill_pnl(state)

        assert state.portfolio.pnl_decomposition.fill_last_hop_pnl == 0.0
        assert state.portfolio.pnl_decomposition.fees == 0.0
