"""Verify day.completed and run.finished payloads include the new
greeks fields (additive — old fields preserved)."""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock


def test_day_completed_payload_carries_greeks_summary():
    from trading import runner as runner_mod
    from trading.core.schemas import PnLDecomposition

    captured: list[tuple[str, dict]] = []

    class _FakeEmitter:
        def emit(self, name, payload):
            captured.append((name, payload))

    r = runner_mod.TradingRunner.__new__(runner_mod.TradingRunner)
    r.events = _FakeEmitter()
    cfg = MagicMock()
    cfg.experiment.initial_cash = 100_000.0
    r.config = cfg

    state = MagicMock()
    state.current_ts = datetime(2025, 1, 15, 16, 0, tzinfo=timezone(timedelta(hours=-5)))
    state.portfolio = MagicMock()
    state.portfolio.equity = 100_412.0
    state.portfolio.daily_pnl = 412.0
    state.portfolio.positions = []
    state.portfolio.pnl_decomposition = PnLDecomposition(
        sod_delta_pnl=215.0, sod_gamma_pnl=42.0, sod_theta_pnl=-88.0,
        sod_vega_pnl=198.0, sod_volga_pnl=10.0, sod_vanna_pnl=5.0, sod_residual=30.0,
    )

    r._emit_day_completed(state)

    assert len(captured) == 1
    name, payload = captured[0]
    assert name == "day.completed"
    assert payload["trading_day"] == "2025-01-15"
    assert payload["pnl_delta"] == 412.0
    assert payload["cum_pnl"] == 412.0
    assert payload["portfolio_value"] == 100_412.0
    gs = payload["greeks_summary"]
    assert gs["delta_pnl"] == 215.0
    assert gs["gamma_pnl"] == 42.0
    assert gs["theta_pnl"] == -88.0
    assert gs["vega_pnl"] == 198.0
    assert gs["volga_pnl"] == 10.0
    assert gs["vanna_pnl"] == 5.0
    assert gs["residual"] == 30.0


def test_day_completed_returns_zeros_when_no_decomposition():
    from trading import runner as runner_mod

    captured = []
    class _E:
        def emit(self, name, payload):
            captured.append((name, payload))

    r = runner_mod.TradingRunner.__new__(runner_mod.TradingRunner)
    r.events = _E()
    cfg = MagicMock()
    cfg.experiment.initial_cash = 100_000.0
    r.config = cfg
    state = MagicMock()
    state.current_ts = datetime(2025, 1, 15, 16, 0, tzinfo=timezone(timedelta(hours=-5)))
    state.portfolio = MagicMock()
    state.portfolio.equity = 100_000.0
    state.portfolio.daily_pnl = 0.0
    state.portfolio.pnl_decomposition = None
    state.portfolio.positions = []

    r._emit_day_completed(state)
    payload = captured[0][1]
    assert all(payload["greeks_summary"][k] == 0.0 for k in ("delta_pnl","gamma_pnl","theta_pnl","vega_pnl","volga_pnl","vanna_pnl","residual"))


def test_run_finished_payload_carries_attribution():
    from trading import runner as runner_mod

    captured: list[tuple[str, dict]] = []

    class _FakeEmitter:
        def emit(self, name, payload):
            captured.append((name, payload))

    r = runner_mod.TradingRunner.__new__(runner_mod.TradingRunner)
    r.events = _FakeEmitter()
    r._closed_trade_ledger = []  # empty ledger acceptable

    r._emit_run_finished(exit_code=0)
    assert len(captured) == 1
    name, payload = captured[0]
    assert name == "run.finished"
    assert payload["exit_code"] == 0
    assert "greeks_attribution_total" in payload
    assert "trade_level_attribution" in payload
    assert isinstance(payload["trade_level_attribution"], list)
