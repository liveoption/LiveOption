"""Project_family_view tests using real PositionInfo / EventWindow fixtures
so attribute-shape bugs are caught (not masked by MagicMock)."""
from types import SimpleNamespace

from trading.agents.strategy.intraday_llm import IntradayLLMStrategy
from trading.agents.strategy.overlay import OverlayProfitStrategy
from trading.agents.strategy.earnings_bet import EarningsBetStrategy, EventWindow
from trading.checkpoint.captured import OverlayView, EarningsView, IntradayView
from trading.core.schemas import PositionInfo


def _stock_position(contract: str, qty: int, avg_entry: float, mark: float) -> PositionInfo:
    return PositionInfo(
        contract=contract, side="LONG", qty=qty, avg_entry=avg_entry,
        mark_price=mark, unrealized_pnl=(mark - avg_entry) * qty,
        dte=0, stale=False, stale_minutes=0,
        delta=1.0, gamma=0.0, vega=0.0, theta=0.0,
    )


def _option_position(contract: str, qty: int, avg_entry: float, mark: float, dte: int, gamma: float = 0.05) -> PositionInfo:
    return PositionInfo(
        contract=contract, side="LONG", qty=qty, avg_entry=avg_entry,
        mark_price=mark, unrealized_pnl=(mark - avg_entry) * qty * 100,
        dte=dte, stale=False, stale_minutes=0,
        delta=0.5, gamma=gamma, vega=0.1, theta=-0.02,
    )


def _portfolio(positions, realized_pnl=0.0):
    return SimpleNamespace(positions=positions, cash=0.0, realized_pnl=realized_pnl)


def _state(trading_day=None, current_ts="2025-01-15T10:00:00-05:00"):
    return SimpleNamespace(trading_day=trading_day, current_ts=current_ts)


def test_intraday_projection_shape_and_validation():
    strat = IntradayLLMStrategy(
        decision_interval_minutes=15, llm_provider="deepseek",
        llm_model="deepseek-chat", dte_min=0, dte_max=7, max_positions=5,
    )
    pf = _portfolio([_option_position("O:SPY250115C00480000", 2, 1.5, 2.0, dte=0, gamma=0.07)], realized_pnl=120.0)
    view = strat.project_family_view(pf, _state())
    IntradayView.model_validate(view)
    assert view["kind"] == "intraday"
    # gamma_exposure = 0.07 * 2 = 0.14
    assert abs(view["gamma_exposure"] - 0.14) < 1e-9
    assert view["intraday_realized_today"] == 120.0
    # same_day_open_count is 0 by design (PositionInfo has no open-time field)
    assert view["same_day_open_count"] == 0


def test_overlay_classifies_stock_vs_option():
    strat = OverlayProfitStrategy()
    stock = _stock_position("SPY", 100, 480.0, 500.0)
    opt = _option_position("O:SPY250115C00500000", 1, 2.0, 3.5, dte=14)
    view = strat.project_family_view(_portfolio([stock, opt]), _state())
    OverlayView.model_validate(view)
    assert view["kind"] == "overlay"
    assert len(view["underlying_holdings"]) == 1
    assert view["underlying_holdings"][0]["symbol"] == "SPY"
    assert view["underlying_holdings"][0]["shares"] == 100
    assert view["underlying_holdings"][0]["cost_basis"] == 48000.0
    assert view["underlying_holdings"][0]["current_mv"] == 50000.0
    assert len(view["overlay_legs"]) == 1
    assert view["overlay_legs"][0]["contract"] == "O:SPY250115C00500000"
    assert view["overlay_legs"][0]["days_to_expiry"] == 14
    # net_delta: stock(1.0)*100 + option(0.5)*1 = 100.5
    assert abs(view["delta_hedge_status"]["net_delta"] - 100.5) < 1e-9


def _event_window(ticker, earnings_date, phase):
    """Build an EventWindow with t-2..t+2 around earnings_date."""
    from datetime import date, timedelta
    d = date.fromisoformat(earnings_date)
    return EventWindow(
        ticker=ticker, upq_ticker=ticker, earnings_date=earnings_date, is_bmo=False, event_data={},
        t_minus_2=(d - timedelta(days=2)).isoformat(),
        t_minus_1=(d - timedelta(days=1)).isoformat(),
        t_plus_1=(d + timedelta(days=1)).isoformat(),
        t_plus_2=(d + timedelta(days=2)).isoformat(),
        phase=phase,
    )


def test_earnings_filters_by_phase_via_stored_field():
    strat = EarningsBetStrategy()
    strat._event_windows = [
        _event_window("AAPL", "2025-01-30", "pre"),       # active
        _event_window("MSFT", "2025-01-29", "post"),      # active
        _event_window("GOOG", "2025-02-05", "pending"),   # pending
        _event_window("AMZN", "2024-12-15", "closed"),    # post-event
    ]
    view = strat.project_family_view(_portfolio([]), _state())
    EarningsView.model_validate(view)
    assert view["kind"] == "earnings"
    actives = {e["ticker"] for e in view["active_event_windows"]}
    pendings = {e["ticker"] for e in view["pending_events"]}
    posts = {e["ticker"] for e in view["post_event_realized"]}
    assert actives == {"AAPL", "MSFT"}
    assert pendings == {"GOOG"}
    assert posts == {"AMZN"}


def test_earnings_uses_get_phase_for_date_when_state_has_trading_day():
    strat = EarningsBetStrategy()
    # Window phase stored as 'pending' but get_phase_for_date should compute 'pre' on T-1.
    from datetime import date, timedelta
    d = date.fromisoformat("2025-01-30")
    w = EventWindow(
        ticker="AAPL", upq_ticker="AAPL", earnings_date="2025-01-30", is_bmo=False, event_data={},
        t_minus_2=(d - timedelta(days=2)).isoformat(),
        t_minus_1=(d - timedelta(days=1)).isoformat(),
        t_plus_1=(d + timedelta(days=1)).isoformat(),
        t_plus_2=(d + timedelta(days=2)).isoformat(),
        phase="pending",
    )
    strat._event_windows = [w]
    view = strat.project_family_view(_portfolio([]), _state(trading_day="2025-01-29"))
    EarningsView.model_validate(view)
    # On 2025-01-29 (T-1), get_phase_for_date returns "pre" → active.
    actives = {e["ticker"] for e in view["active_event_windows"]}
    assert actives == {"AAPL"}, view
