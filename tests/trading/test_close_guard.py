"""Idempotent-close guard (RiskConfig.prevent_position_inversion).

An agent can re-emit an identical
closing intent while its position snapshot is stale, and the matcher fills
every emission, inverting the position. The guard keeps a per-contract
ledger of approved closes that resets whenever the snapshot qty changes.
"""
from __future__ import annotations

import pytest

from trading.agents.risk_manager import RiskGateAgent
from trading.core.config import DataPolicyConfig, RiskConfig
from trading.core.schemas import PortfolioSnapshot, PositionInfo, TradeIntent


class _State:
    current_ts = "2025-04-23T14:00:00+00:00"
    market_event = None


def _gate(**risk_kwargs) -> RiskGateAgent:
    defaults = dict(
        prevent_position_inversion=True,
        flatten_before_close=False,
        max_daily_loss_pct=0,
        max_exposure_pct=10.0,
        max_orders_per_day=999,
    )
    defaults.update(risk_kwargs)
    gate = RiskGateAgent(RiskConfig(**defaults), DataPolicyConfig(max_stale_minutes=10**6))
    gate._get_stale_minutes = lambda *a: 0  # market-data staleness not under test
    return gate


def _pos(qty: int) -> PositionInfo:
    return PositionInfo(
        contract="O:TSLA250425C00230000",
        side="LONG" if qty > 0 else "SHORT",
        qty=qty, avg_entry=25.9, mark_price=30.0,
        unrealized_pnl=4100.0, dte=2, stale=False, stale_minutes=0,
    )


def _pf(qty: int, daily_pnl_pct: float = 0.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=24741, equity=52951, daily_pnl=0,
        daily_pnl_pct=daily_pnl_pct, positions=[_pos(qty)],
    )


def _sell(qty: int) -> TradeIntent:
    return TradeIntent(
        symbol="TSLA", contract="O:TSLA250425C00230000", side="SELL",
        qty=qty, order_type="MARKET",
        rationale="Closing profitable call leg", strategy_name="earnings_bet",
    )


def test_duplicate_close_rejected_across_steps():
    gate = _gate()
    pf = _pf(10)  # snapshot stays stale across steps (the M1 setup)
    assert gate.evaluate([_sell(10)], pf, _State())[0].action == "ALLOW"
    d2 = gate.evaluate([_sell(10)], pf, _State())[0]
    assert d2.action == "REJECT"
    assert "close_guard_duplicate" in d2.risk_notes
    assert gate.evaluate([_sell(10)], pf, _State())[0].action == "REJECT"


def test_oversized_close_clamped_to_inventory():
    gate = _gate()
    d = gate.evaluate([_sell(15)], _pf(10), _State())[0]
    assert d.action in ("ALLOW", "ADJUST")
    qty = d.adjusted_qty or d.original_intent.qty
    assert qty == 10  # cannot invert 10-lot long into a short


def test_circuit_breaker_close_path_is_guarded():
    gate = _gate(circuit_breaker_loss_pct=0.05)
    pf = _pf(10, daily_pnl_pct=-0.26)  # breaker active
    c1 = gate.evaluate([_sell(10)], pf, _State())[0]
    assert c1.action == "ALLOW"
    c2 = gate.evaluate([_sell(10)], pf, _State())[0]
    assert c2.action == "REJECT"
    assert "close_guard_duplicate" in c2.risk_notes


def test_snapshot_change_resets_budget():
    gate = _gate()
    assert gate.evaluate([_sell(10)], _pf(10), _State())[0].action == "ALLOW"
    # fills landed: qty 10 -> 4; a fresh close of 4 is legitimate
    assert gate.evaluate([_sell(4)], _pf(4), _State())[0].action == "ALLOW"
    assert gate.evaluate([_sell(4)], _pf(4), _State())[0].action == "REJECT"


def test_guard_off_preserves_paper_behavior():
    gate = _gate(prevent_position_inversion=False)
    pf = _pf(10)
    assert gate.evaluate([_sell(10)], pf, _State())[0].action == "ALLOW"
    assert gate.evaluate([_sell(10)], pf, _State())[0].action == "ALLOW"
