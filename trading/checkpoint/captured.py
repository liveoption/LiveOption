"""Pydantic schemas for trading.cli capture-checkpoint output.

Shared between trading/ (writer) and liveoption_web/checkpoint/promote.py
(reader). Schema version bumps must add a Literal to SchemaVersion and
keep older deserialization paths if the layout changes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class GenericPortfolioSummary(BaseModel):
    """Family-agnostic portfolio snapshot. Always populated."""

    cash: float
    positions: list[dict[str, Any]]
    realized_pnl: float
    unrealized_pnl: float
    gross_exposure: float
    net_delta: float


class OverlayView(BaseModel):
    kind: Literal["overlay"]
    underlying_holdings: list[dict[str, Any]]
    overlay_legs: list[dict[str, Any]]
    delta_hedge_status: dict[str, Any]


class EarningsView(BaseModel):
    kind: Literal["earnings"]
    active_event_windows: list[dict[str, Any]]
    pending_events: list[dict[str, Any]]
    post_event_realized: list[dict[str, Any]]


class IntradayView(BaseModel):
    kind: Literal["intraday"]
    minutes_to_close: int
    same_day_open_count: int
    gamma_exposure: float
    intraday_realized_today: float


FamilyView = Annotated[
    Union[OverlayView, EarningsView, IntradayView],
    Field(discriminator="kind"),
]


class CapturedCheckpoint(BaseModel):
    """Output of trading.cli capture-checkpoint.

    schema_version is mandatory. Older versions are tolerated by readers
    that branch on schema_version; current writers always emit "1".
    """

    schema_version: Literal["1"]
    run_id: str
    decision_id: str
    captured_at: datetime
    generic: GenericPortfolioSummary
    family_view: FamilyView | None = None
    runner_state: dict[str, Any] = Field(default_factory=dict)
