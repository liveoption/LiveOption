"""Tests for AuditLogger — JSONL step logging."""

from __future__ import annotations

import json
import tempfile
from dataclasses import field
from pathlib import Path

import pytest

from trading.core.config import (
    ExperimentConfig,
    RunConfig,
    StrategyConfig,
)
from trading.core.schemas import (
    FillEvent,
    MarketEvent,
    OrderRequest,
    PortfolioSnapshot,
    RiskDecision,
    TradeIntent,
)
from trading.core.state import TradingState
from trading.logger import AuditLogger


def _make_config() -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test", start="2025-01-06", end="2025-01-10", universe=["SPY"]
        ),
        strategy=StrategyConfig(name="mock", params={}),
    )


def _make_state(step_id: int = 1, current_ts: str = "2025-01-06T09:31:00") -> TradingState:
    from trading.core.time_contract import utc_from_iso
    cfg = _make_config()
    # Accept legacy naive ET string; convert to tz-aware UTC for the contract.
    ts_iso = current_ts if current_ts.endswith("Z") or "+" in current_ts else current_ts + "+00:00"
    ts_dt = utc_from_iso(ts_iso)
    state = TradingState(
        config=cfg,
        run_id="test",
        episode_id="test",
        step_id=step_id,
        current_ts=ts_dt,
        current_date=current_ts[:10],
    )
    state.portfolio = PortfolioSnapshot(
        cash=90000, equity=100000, daily_pnl=500, daily_pnl_pct=0.5, positions=[]
    )
    state.market_event = MarketEvent(event_ts=current_ts, bars=[], bar_history={})
    state.intents = []
    state.risk_decisions = []
    state.orders = []
    state.fills = []
    state.errors = []
    return state


def test_log_step_writes_jsonl():
    """Verify a single log_step writes one valid JSONL line to portfolio.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with AuditLogger(tmpdir) as al:
            state = _make_state()
            al.log_step(state)

        # events.jsonl is no longer written; portfolio.jsonl should exist
        assert not (Path(tmpdir) / "events.jsonl").exists()
        path = Path(tmpdir) / "portfolio.jsonl"
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["step_id"] == 1


def test_log_step_includes_all_state_fields():
    """Verify trades.jsonl captures intents/fills and portfolio.jsonl captures portfolio."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with AuditLogger(tmpdir) as al:
            state = _make_state()
            state.intents = [
                TradeIntent(
                    symbol="SPY",
                    contract="O:SPY250106C00595000",
                    side="BUY",
                    qty=5,
                    order_type="MARKET",
                )
            ]
            state.fills = [
                FillEvent(
                    trade_id="t1",
                    order_id="o1",
                    contract="O:SPY250106C00595000",
                    side="BUY",
                    qty=5,
                    price=2.30,
                    fees=3.25,
                    fill_ts="2025-01-06T09:32:00",
                )
            ]
            al.log_step(state)

        trades_entry = json.loads((Path(tmpdir) / "trades.jsonl").read_text().strip())
        assert trades_entry["step_id"] == 1
        assert len(trades_entry["intents"]) == 1
        assert trades_entry["intents"][0]["contract"] == "O:SPY250106C00595000"
        assert len(trades_entry["fills"]) == 1
        assert trades_entry["fills"][0]["trade_id"] == "t1"

        portfolio_entry = json.loads((Path(tmpdir) / "portfolio.jsonl").read_text().strip())
        assert portfolio_entry["step_id"] == 1
        assert portfolio_entry["portfolio"]["equity"] == 100000


def test_multiple_steps_produce_multiple_lines():
    """Three log_step calls should produce exactly three lines in portfolio.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with AuditLogger(tmpdir) as al:
            for i in range(1, 4):
                state = _make_state(step_id=i, current_ts=f"2025-01-06T09:{30 + i}:00")
                al.log_step(state)

        path = Path(tmpdir) / "portfolio.jsonl"
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines, start=1):
            entry = json.loads(line)
            assert entry["step_id"] == i
