"""Shared mutable state passed through the trading pipeline each step.

Uses a Python dataclass (not Pydantic BaseModel) because this is mutable
state that agents read from and write to, not a validation model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from trading.core.time_contract import UTC_TZ

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import (
        FillEvent,
        MarketEvent,
        OrderRequest,
        PortfolioSnapshot,
        RiskDecision,
        TradeIntent,
    )


@dataclass
class TradingState:
    """Flat state object passed through the pipeline each step.

    Every agent reads from and writes to well-defined fields.

    Fill Timing Model:
        PMB uses next-bar fills: an order submitted at step N fills against the
        bar at step N+1 (or later if no bar exists for that instrument). This
        means:
        1. ExecutionAgent submits orders at step N.
        2. PMB processes fills when stepping to step N+1.
        3. The ACCOUNT_SNAPSHOT at step N+1 already reflects those fills.
        4. ExecutionAgent extracts FillEvents from step N+1's result.
        5. The strategy's portfolio at step N+1 includes the filled positions.
    """

    # -- Immutable per run --
    config: RunConfig
    run_id: str
    episode_id: str  # one trading day or session segment

    # -- Updated by StepMarket --
    step_id: int = 0
    # Tz-aware UTC datetime of the current step's clock. Use
    # ``trading.core.time_contract.trading_day_from_ts`` /
    # ``et_time_from_ts`` / ``to_iso_z`` for display or serialization.
    current_ts: datetime = field(default_factory=lambda: datetime(1970, 1, 1, tzinfo=UTC_TZ))
    current_date: str = ""  # YYYY-MM-DD in ET, derived from current_ts
    session_running: bool = True
    raw_step_result: dict = field(default_factory=dict)
    is_new_day: bool = False  # True when current_date changes from prev step

    # -- Updated by TradingRunner (from PMB ACCOUNT_SNAPSHOT event) --
    portfolio: PortfolioSnapshot | None = None

    # -- Updated by MarketDataAgent --
    market_event: MarketEvent | None = None

    # -- Updated by ExecutionAgent (fills from PREVIOUS step's orders) --
    fills: list[FillEvent] = field(default_factory=list)

    # -- Updated by StrategyAgent --
    intents: list[TradeIntent] = field(default_factory=list)

    # -- Updated by RiskGateAgent --
    risk_decisions: list[RiskDecision] = field(default_factory=list)

    # -- Updated by ExecutionAgent (new orders from THIS step) --
    orders: list[OrderRequest] = field(default_factory=list)

    # -- Accumulated --
    errors: list[str] = field(default_factory=list)
