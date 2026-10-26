"""Event and message schemas for the trading pipeline.

All schemas use Pydantic v2 BaseModel for validation and serialization.
Field names and types match the design spec's Event Schemas section.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BarData(BaseModel):
    """A single OHLCV bar for a stock or option instrument."""

    symbol: str  # ticker or OPRA contract (clean, no prefix)
    open: float
    high: float
    low: float
    close: float
    volume: int
    window_start: str  # ISO timestamp
    instrument_type: str = "STOCK"  # "STOCK" | "OPTION"
    stale: bool = False
    stale_minutes: int = 0
    tradeable: bool = True
    # Optional Greeks (options only)
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


class PositionPnLAttribution(BaseModel):
    """Per-position step-level PnL attribution."""

    contract: str
    delta_pnl: float | None = None
    gamma_pnl: float | None = None
    theta_pnl: float | None = None
    vega_pnl: float | None = None
    volga_pnl: float | None = None
    vanna_pnl: float | None = None
    residual: float | None = None
    total_pnl: float | None = None


class PnLDecomposition(BaseModel):
    """Portfolio-level PnL decomposition with step and SOD views."""

    step_delta_pnl: float = 0.0
    step_gamma_pnl: float = 0.0
    step_theta_pnl: float = 0.0
    step_vega_pnl: float = 0.0
    step_volga_pnl: float = 0.0
    step_vanna_pnl: float = 0.0
    step_residual: float = 0.0
    step_total_pnl: float = 0.0

    sod_delta_pnl: float = 0.0
    sod_gamma_pnl: float = 0.0
    sod_theta_pnl: float = 0.0
    sod_vega_pnl: float = 0.0
    sod_volga_pnl: float = 0.0
    sod_vanna_pnl: float = 0.0
    sod_residual: float = 0.0
    sod_total_pnl: float = 0.0

    fill_last_hop_pnl: float = 0.0
    fees: float = 0.0

    positions: list[PositionPnLAttribution] = Field(default_factory=list)


class MarketEvent(BaseModel):
    """Emitted by MarketDataAgent each step with all bars and history."""

    event_ts: str
    bars: list[BarData]
    bar_history: dict[str, list[BarData]]  # symbol -> last N bars (ring buffer)


class PositionInfo(BaseModel):
    """A single position in the portfolio."""

    contract: str
    side: str  # "LONG" | "SHORT"
    qty: int
    avg_entry: float
    mark_price: float
    unrealized_pnl: float
    dte: int
    stale: bool
    stale_minutes: int
    # Optional Greeks (populated when available from market data)
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    # Additional fields for BSM volga/vanna computation
    iv: float | None = None
    underlying_price: float | None = None
    strike: float | None = None
    right: str | None = None


class PortfolioSnapshot(BaseModel):
    """Account state extracted from PMB ACCOUNT_SNAPSHOT event."""

    cash: float
    equity: float
    daily_pnl: float
    daily_pnl_pct: float
    positions: list[PositionInfo]
    pnl_decomposition: PnLDecomposition | None = None


class TradeIntent(BaseModel):
    """A strategy's desired trade, before risk evaluation."""

    symbol: str = ""
    contract: str  # OPRA format
    side: str  # "BUY" | "SELL"
    qty: int
    order_type: str  # "MARKET" | "LIMIT"
    limit_price: float | None = None
    spread_group: str | None = None
    rationale: str = ""
    confidence: float = 0.0
    strategy_name: str = ""


class RiskDecision(BaseModel):
    """Risk gate output for a single TradeIntent."""

    original_intent: TradeIntent
    action: str  # "ALLOW" | "REJECT" | "ADJUST"
    adjusted_qty: int | None = None
    reason: str = ""
    risk_notes: list[str] = Field(default_factory=list)
    exposure_after: float = 0.0
    loss_after: float = 0.0


class FillEvent(BaseModel):
    """A confirmed trade fill from PMB."""

    trade_id: str
    order_id: str
    contract: str
    side: str
    qty: int
    price: float
    fees: float
    fill_ts: str


class OrderRequest(BaseModel):
    """An order submitted to PMB by the ExecutionAgent."""

    order_id: str = ""
    contract: str
    side: str  # "BUY" | "SELL"
    qty: int
    order_type: str  # "MARKET" | "LIMIT"
    limit_price: float | None = None
    spread_id: str | None = None
