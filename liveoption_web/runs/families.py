"""Scenario family registry and validated override models.

Each family pins an `overridable_model` (Pydantic, with
`extra='forbid'`) used by the launch endpoint to validate POST bodies,
plus an `evaluator_module` referencing `liveoption_web.eval.<name>`.

The LLM model is selectable at launch time for all families.
Overlay and earnings override models now include LLMOverridesMixin so the
web UI can select configured models. The trading-layer
apply_overrides derives llm_provider from the model id automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    condecimal,
    constr,
    field_validator,
)


class Family(str, Enum):
    """Paper-level scenario families. Fixed enum, not a runtime extension point."""

    OVERLAY = "overlay"
    EARNINGS = "earnings"
    INTRADAY = "intraday"


class CoreOverrides(BaseModel):
    """Truly-shared override fields (every family + null-family)."""

    model_config = ConfigDict(extra="forbid")

    start: date | None = None
    end: date | None = None
    bankroll: condecimal(gt=Decimal(0), lt=Decimal("10_000_000")) | None = None


class LLMOverridesMixin(BaseModel):
    """LLM-strategy mixin — only mixed into families with an LLM strategy."""

    llm_model: (
        Literal[
            "gpt-4.1-mini",
            "deepseek-chat",
            "gemini-2.5-flash",
            "claude-sonnet-4-5",
            "qwen2.5-max",
            "gemini-3-flash",
            "gpt-5.6-terra",
        ] | None
    ) = None
    prompt_override: constr(max_length=16_000) | None = None


class HoldingSpec(BaseModel):
    """One leg of an underlying portfolio (overlay only)."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    shares: int
    cost_basis: float

    @field_validator("symbol")
    @classmethod
    def _symbol_in_allowlist(cls, v: str) -> str:
        from liveoption_web.runs.catalog import SUPPORTED_UNDERLYINGS
        if v not in SUPPORTED_UNDERLYINGS:
            raise ValueError(
                f"symbol {v!r} not in supported underlyings; "
                f"data coverage requires it be one of: {sorted(SUPPORTED_UNDERLYINGS)}"
            )
        return v


class OverlayOverrides(CoreOverrides, LLMOverridesMixin):
    """Overlay family — strategy is overlay_profit; LLM model selectable at launch."""

    underlying_holdings: list[HoldingSpec] | None = None
    overlay_dte_range: tuple[int, int] | None = None


class EarningsOverrides(CoreOverrides, LLMOverridesMixin):
    """Earnings family — strategy is earnings_bet; LLM model selectable at launch."""

    tickers: list[str] | None = None
    event_window: tuple[int, int] | None = None
    quarter: Literal["2025Q1", "2025Q2", "2025Q3", "2025Q4"] | None = None

    @field_validator("tickers")
    @classmethod
    def _tickers_in_allowlist(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        from liveoption_web.runs.catalog import SUPPORTED_UNDERLYINGS
        bad = [t for t in v if t not in SUPPORTED_UNDERLYINGS]
        if bad:
            raise ValueError(
                f"tickers {bad!r} not in supported underlyings; "
                f"must be subset of: {sorted(SUPPORTED_UNDERLYINGS)}"
            )
        return v


class IntradayOverrides(CoreOverrides, LLMOverridesMixin):
    """Intraday family — strategy is intraday_llm (LLM-driven)."""

    decision_interval_minutes: Literal[1, 5, 15, 30] | None = None
    max_concurrent_positions: PositiveInt | None = None
    dte_range: tuple[int, int] | None = None

    @field_validator("max_concurrent_positions")
    @classmethod
    def _position_limit(cls, value: int | None) -> int | None:
        if value is not None and value > 50:
            raise ValueError("max_concurrent_positions must be <= 50")
        return value

    @field_validator("dte_range")
    @classmethod
    def _valid_dte_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and not 0 <= value[0] <= value[1] <= 365:
            raise ValueError("dte_range must satisfy 0 <= min <= max <= 365")
        return value


class NullFamilyOverrides(CoreOverrides, LLMOverridesMixin):
    """Used by null-family scenarios (baseline weekly_llm, dryrun smoke)."""

    pass


@dataclass(frozen=True)
class FamilySpec:
    """Static binding of family enum → override model + evaluator module name."""

    overridable_model: type[BaseModel]
    evaluator_module: str  # e.g., "overlay" → liveoption_web.eval.overlay


FAMILY_REGISTRY: dict[Family, FamilySpec] = {
    Family.OVERLAY: FamilySpec(OverlayOverrides, "overlay"),
    Family.EARNINGS: FamilySpec(EarningsOverrides, "earnings"),
    Family.INTRADAY: FamilySpec(IntradayOverrides, "intraday"),
}


def pick_override_model(family: Family | None) -> type[BaseModel]:
    """Return the override model for ``family``; ``None`` → NullFamilyOverrides."""

    if family is None:
        return NullFamilyOverrides
    return FAMILY_REGISTRY[family].overridable_model
