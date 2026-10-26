"""AuditLogger — split JSONL logging: portfolio.jsonl, trades.jsonl, market.jsonl, risk.jsonl."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)


class AuditLogger:
    """Write split JSONL logs — portfolio/trades/market/risk."""

    def __init__(self, output_dir: str) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._portfolio: IO = open(self._dir / "portfolio.jsonl", "a")
        self._trades: IO = open(self._dir / "trades.jsonl", "a")
        self._market: IO = open(self._dir / "market.jsonl", "a")
        self._risk: IO = open(self._dir / "risk.jsonl", "a")

    def log_step(self, state: TradingState, step_cost=None, snapshot_only: bool = False) -> None:
        from datetime import datetime as _dt
        from trading.core.time_contract import to_iso_z
        sc = asdict(step_cost) if step_cost is not None else None
        event_ts = (
            to_iso_z(state.current_ts)
            if isinstance(state.current_ts, _dt)
            else state.current_ts
        )

        # portfolio.jsonl — write at snapshot intervals (not every bar)
        # snapshot_only=True means "only log trades, skip portfolio snapshot"
        if state.portfolio is not None and not snapshot_only:
            self._portfolio.write(json.dumps({
                "step_id": state.step_id, "event_ts": event_ts,
                "current_date": state.current_date,
                "portfolio": state.portfolio.model_dump(), "step_cost": sc,
            }) + "\n")
            self._portfolio.flush()

        # trades.jsonl (sparse — only when activity)
        if state.intents or state.orders or state.fills or state.errors:
            self._trades.write(json.dumps({
                "step_id": state.step_id, "event_ts": event_ts,
                "current_date": state.current_date,
                "intents": [i.model_dump() for i in state.intents],
                "risk_decisions": [d.model_dump() for d in state.risk_decisions],
                "orders": [o.model_dump() for o in state.orders],
                "fills": [f.model_dump() for f in state.fills],
                "errors": state.errors,
            }) + "\n")
            self._trades.flush()

        # risk.jsonl (sparse — only when risk gate has decisions)
        if state.risk_decisions:
            risk_rows = []
            for d in state.risk_decisions:
                intent = d.original_intent
                risk_rows.append({
                    "action": d.action,          # ALLOW / ADJUST / REJECT
                    "reason": d.reason,
                    "risk_notes": d.risk_notes,
                    "contract": intent.contract if intent else None,
                    "side": intent.side if intent else None,
                    "qty": intent.qty if intent else None,
                    "adjusted_qty": d.adjusted_qty,
                    "strategy_name": intent.strategy_name if intent else None,
                })
            self._risk.write(json.dumps({
                "step_id": state.step_id,
                "event_ts": event_ts,
                "current_date": state.current_date,
                "decisions": risk_rows,
            }) + "\n")
            self._risk.flush()

        # market.jsonl (compact bar summary)
        if state.market_event is not None:
            bars = [{"symbol": b.symbol, "close": b.close, "volume": b.volume,
                     "instrument_type": b.instrument_type} for b in state.market_event.bars]
            self._market.write(json.dumps({
                "step_id": state.step_id, "event_ts": event_ts,
                "current_date": state.current_date, "bars": bars,
            }) + "\n")
            self._market.flush()

    def close(self) -> None:
        for f in (self._portfolio, self._trades, self._market, self._risk):
            if f and not f.closed:
                f.flush()
                f.close()

    def __enter__(self) -> AuditLogger:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
