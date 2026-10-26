"""Configuration models for the trading pipeline.

All config models use Pydantic v2 BaseModel with sensible defaults.
`load_config(path)` reads a YAML file and returns a validated RunConfig.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from trading.core.endpoints import (
    DEFAULT_PMB_URL,
    DEFAULT_UPQ_URL,
    service_url,
)


class ExperimentConfig(BaseModel):
    """Top-level experiment parameters."""

    run_id: str
    seed: int = 42
    mode: str = "replay"  # "replay" | "live"
    start: str  # "2025-01-06" or "2025-01-06T09:30:00"
    end: str
    universe: list[str]  # ["SPY", "QQQ", ...]
    frequency: str = "1m"  # "1m" | "1d"
    timezone: str = "America/New_York"
    initial_cash: float = 100_000.0
    initial_positions: list[dict] = Field(default_factory=list)
    # Example: [{"symbol": "QQQ", "qty": 10000}]


class StrategyConfig(BaseModel):
    """Strategy selection and parameters."""

    name: str  # registry key: "weekly_llm", "intraday_llm", ...
    params: dict = Field(default_factory=dict)  # strategy-specific parameters


class RiskConfig(BaseModel):
    """Risk gate parameters."""

    max_exposure_pct: float = 0.5
    max_daily_loss_pct: float = 0.02
    circuit_breaker_loss_pct: float = 0.03
    cooldown_seconds: int = 60
    flatten_before_close: bool = True
    flatten_minutes_before_close: int = 5
    max_orders_per_day: int = 10
    max_position_per_symbol: int = 100  # contracts
    # Greeks-aware risk limits (None = disabled)
    max_net_delta: float | None = None
    max_net_vega: float | None = None
    # Structural short-option control
    allow_naked_short_options: bool = False  # reject naked shorts unless explicitly allowed
    # Idempotent-close guard: clamp
    # closing-direction intents so cumulative approved closes can never
    # exceed the live inventory, even when the position snapshot is stale
    # across steps. Default False preserves legacy behavior.
    prevent_position_inversion: bool = False
    # Overlay stock purchase: when True, stock BUY orders from overlay_profit bypass
    # the max_position_per_symbol check (shares are not contracts; the limit is for options)
    allow_overlay_stock_buy: bool = False


class ExecutionConfig(BaseModel):
    """Execution environment configuration."""

    pmb_base_url: str = DEFAULT_PMB_URL
    upq_base_url: str = DEFAULT_UPQ_URL
    fee_model: dict = Field(
        default_factory=lambda: {
            "option_fee_per_contract": 0.65,
            "stock_fee_per_share": 0.0005,
            "slippage_bps": 1.0,
            "option_spread_pct": 0.05,
        }
    )
    fill_model: str = "next_bar"
    order_type_policy: str = "strategy"  # strategy | market | limit


class DataPolicyConfig(BaseModel):
    """Data handling and chain filtering parameters."""

    missing_bar_strategy: str = "forward_fill_with_stale_flag"
    max_stale_minutes: int = 10  # step-count based: number of consecutive steps with no bar data
    lookback_bars: int = 60
    news_lookback_days: int = 7
    chain_refresh_interval: str = "daily"  # "daily" | "session_start"
    chain_filter_dte_min: int = 0
    chain_filter_dte_max: int = 7
    chain_filter_delta_min: float = 0.20
    chain_filter_delta_max: float = 0.60
    chain_max_contracts_per_ticker: int = 10
    # News context via ESP (Events & News, formerly NPP)
    # Set news_enabled: true and npp_base_url in your run config to activate.
    news_enabled: bool = False
    npp_base_url: str = ""  # empty = QFinZero hub ESP (see trading.core.endpoints)


class AvsrConfig(BaseModel):
    """Per-run AVSR surface anchors used in option-chain prompt compression."""

    moneyness_anchors: list[float] = Field(default_factory=lambda: [0.95, 1.0, 1.05], min_length=3, max_length=3)
    expiry_anchors: list[int] = Field(default_factory=lambda: [7, 30, 90], min_length=3, max_length=3)
    distance_lambda: float = Field(default=0.01, gt=0, le=1)

    @model_validator(mode="after")
    def validate_anchors(self) -> "AvsrConfig":
        if not (0.5 <= self.moneyness_anchors[0] < self.moneyness_anchors[1] < self.moneyness_anchors[2] <= 1.5):
            raise ValueError("moneyness_anchors must be three increasing ratios in [0.5, 1.5]")
        if not (1 <= self.expiry_anchors[0] < self.expiry_anchors[1] < self.expiry_anchors[2] <= 365):
            raise ValueError("expiry_anchors must be three increasing days in [1, 365]")
        return self


class EvaluationConfig(BaseModel):
    """Evaluation and output parameters."""

    scales: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m", "1d"])
    output_dir: str = "runs"


class RunConfig(BaseModel):
    """Top-level configuration combining all sub-configs."""

    experiment: ExperimentConfig
    strategy: StrategyConfig
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    data_policy: DataPolicyConfig = Field(default_factory=DataPolicyConfig)
    avsr: AvsrConfig = Field(default_factory=AvsrConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)


def load_config(path: str | Path) -> RunConfig:
    """Read a YAML config file and return a validated RunConfig.

    Stashes the source path on the model as ``_source_path`` so web-layer
    code (capture_checkpoint, resume.py) can round-trip a fork back to the
    original config file without embedding the full config in the
    checkpoint JSON.
    """
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    cfg = RunConfig(**raw)
    # Pydantic v2: use __dict__ to bypass the schema (_source_path is not a
    # declared field). Safe since Pydantic models allow dynamic attr set.
    object.__setattr__(cfg, "_source_path", str(path))
    return cfg


# ---------------------------------------------------------------------------
# Multi-model / multi-service / batch-case config classes
# ---------------------------------------------------------------------------

def _resolve_env(value: str) -> str:
    """Replace {env:VAR_NAME} placeholders with actual environment variables."""
    return re.sub(
        r"\{env:([^}]+)\}",
        lambda m: os.environ.get(m.group(1), ""),
        value,
    )


class ModelEntry(BaseModel):
    """One LLM model definition from models.yaml."""

    provider: str = "openai_compatible"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.5
    max_tokens: int = 4096
    reasoning_effort: str | None = None

    def resolved_api_key(self) -> str:
        return _resolve_env(self.api_key)

    def resolved_base_url(self) -> str:
        return _resolve_env(self.base_url)


class ModelsConfig(BaseModel):
    """Full models.yaml: a dict of model-name → ModelEntry."""

    models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    defaults: dict[str, str] = Field(default_factory=dict)

    def get_entry(self, name: str) -> ModelEntry:
        raw = self.models.get(name)
        if raw is None:
            raise KeyError(f"Model '{name}' not found in models config")
        return ModelEntry(**raw)

    def default_model(self) -> str:
        return self.defaults.get("model", "")


class ServiceEndpoint(BaseModel):
    host: str = "127.0.0.1"
    port: int = 0
    base_url: str = ""


class ServicesConfig(BaseModel):
    """Full services.yaml: pmb, upq, esp endpoints (npp accepted as legacy alias for esp)."""

    services: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def get(self, name: str) -> ServiceEndpoint:
        raw = self.services.get(name, {})
        return ServiceEndpoint(**raw)

    @property
    def pmb_base_url(self) -> str:
        return service_url("pmb", self.get("pmb").base_url)

    @property
    def upq_base_url(self) -> str:
        return service_url("upq", self.get("upq").base_url)

    @property
    def esp_base_url(self) -> str:
        return service_url("esp", self.get("esp").base_url or self.get("npp").base_url)

    @property
    def npp_base_url(self) -> str:
        # Legacy name — NPP is now ESP in the QFinZero unified hub.
        return self.esp_base_url


class CaseEntry(BaseModel):
    """One experiment case from cases YAML."""

    ticker: str | None = None
    mode: str | None = None          # "hedge" | "profit"
    start: str | None = None
    end: str | None = None
    date: str | None = None          # for intraday
    earnings_date: str | None = None
    fiscal_period: str | None = None
    stock_qty: int | None = None     # override initial stock position size


class CasesConfig(BaseModel):
    """Full cases YAML: list of CaseEntry.

    Optional `risk_overrides` applies to every case in this file, merging into
    the base strategy config's risk section.  Supported keys mirror RiskConfig
    fields (e.g. circuit_breaker_loss_pct, max_daily_loss_pct).
    """

    cases: list[CaseEntry] = Field(default_factory=list)
    risk_overrides: dict = Field(default_factory=dict)


def load_models_config(path: str | Path) -> ModelsConfig:
    with open(path, "r") as f:
        return ModelsConfig(**yaml.safe_load(f))


def load_services_config(path: str | Path) -> ServicesConfig:
    with open(path, "r") as f:
        return ServicesConfig(**yaml.safe_load(f))


def load_cases_config(path: str | Path) -> CasesConfig:
    with open(path, "r") as f:
        return CasesConfig(**yaml.safe_load(f))


def resolve_model_entry(
    config: RunConfig,
    models: ModelsConfig,
    override: str | None = None,
) -> ModelEntry:
    """Pick the ModelEntry for this run (override > config.model > default)."""
    name = override or getattr(config, "model", None) or models.default_model()
    if not name:
        raise ValueError("No model name specified and no default in models config")
    return models.get_entry(name)
