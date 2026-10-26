"""Versioned replay task protocol shared by browser and CLI.

Only research inputs are editable. Strategy implementation, execution endpoints,
output paths and live-mode switches remain server-owned.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from liveoption_web.runs.catalog import SCENARIOS, SUPPORTED_UNDERLYINGS
from liveoption_web.runs.families import Family
from trading.core.config import AvsrConfig, DataPolicyConfig, RiskConfig, RunConfig


class ExperimentInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date | None = None
    end: date | None = None
    initial_cash: float | None = Field(default=None, ge=1000, lt=10_000_000)
    universe: list[str] | None = Field(default=None, min_length=1, max_length=24)


class ExecutionInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_fee_per_contract: float | None = Field(default=None, ge=0, le=100)
    stock_fee_per_share: float | None = Field(default=None, ge=0, le=10)
    slippage_bps: float | None = Field(default=None, ge=0, le=1000)
    option_spread_pct: float | None = Field(default=None, ge=0, le=1)
    order_type_policy: Literal["strategy", "market", "limit"] | None = None


class RunSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: Literal["liveoption.run.v1"] = "liveoption.run.v1"
    preset_id: str | None = None
    mode: Literal["preset", "custom"] = "preset"
    cadence: Literal["intraday", "daily"] | None = None
    decision_time_et: str | None = None
    overlay_mode: Literal["hedge", "covered_call", "hybrid"] | None = None
    title: str | None = Field(default=None, max_length=200)
    model_id: str
    context: str = Field(default="", max_length=16_000)
    prompt_addenda: dict[str, str] = Field(default_factory=dict)
    prompt_templates: dict[str, str] = Field(default_factory=dict)
    experiment: ExperimentInputs = Field(default_factory=ExperimentInputs)
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    data_policy: dict[str, Any] = Field(default_factory=dict)
    avsr: AvsrConfig = Field(default_factory=AvsrConfig)
    execution: ExecutionInputs = Field(default_factory=ExecutionInputs)

    @property
    def scenario(self):
        if self.mode == "custom" and self.cadence:
            from liveoption_web.runs.custom_tasks import custom_scenario
            return custom_scenario(self.cadence)
        return SCENARIOS.get(self.preset_id)

    @model_validator(mode="after")
    def validate_inputs(self) -> "RunSpecV1":
        if self.cadence and (self.mode != "custom" or self.preset_id is not None):
            raise ValueError("custom cadence is independent of preset_id")
        if self.decision_time_et is not None:
            if self.cadence != "daily" or not re.fullmatch(r"(?:09:[3-5][0-9]|1[0-5]:[0-5][0-9])", self.decision_time_et):
                raise ValueError("decision_time_et requires custom daily cadence and HH:MM between 09:30 and 15:59 ET")
        scenario = self.scenario
        if scenario is None:
            raise ValueError("choose a preset_id or custom mode with cadence")
        family = Family.INTRADAY if self.cadence else scenario.family
        if self.overlay_mode is not None and family != Family.OVERLAY:
            raise ValueError("overlay_mode requires an overlay preset")
        if re.search(r"(?i)\b(?:sk|pk)[-_][A-Za-z0-9]{12,}", self.context):
            raise ValueError("context appears to contain an API secret")
        prompt_keys: dict[Family, set[str]] = {
            Family.INTRADAY: {"system", "user"},
            Family.OVERLAY: {"analyzer_system", "analyzer_user", "executor_system", "executor_user"},
            Family.EARNINGS: {"pre_system", "pre_user", "post_system", "post_user", "executor_system", "executor_user"},
        }
        if set(self.prompt_addenda) - prompt_keys[family]:
            raise ValueError("unsupported prompt addendum stage")
        if set(self.prompt_templates) - prompt_keys[family]:
            raise ValueError("unsupported prompt template stage")
        required_market_fields = {
            "user": "chain_sample_table", "analyzer_user": "CHAIN_CONTEXT",
            "executor_user": "TRADABLE_CONTRACTS", "pre_user": "CHAIN_CONTEXT",
            "post_user": "CHAIN_CONTEXT",
        }
        from trading.prompts.intraday import INTRADAY_SYSTEM_TEMPLATE, INTRADAY_USER_TEMPLATE
        from trading.prompts.loader import get_loader
        if self.cadence:
            from liveoption_web.runs.custom_tasks import custom_prompts
            defaults = custom_prompts(self.cadence)
        elif scenario.family == Family.INTRADAY:
            defaults = {"system": INTRADAY_SYSTEM_TEMPLATE, "user": INTRADAY_USER_TEMPLATE}
        else:
            names = ("analyzer", "executor") if scenario.family == Family.OVERLAY else ("earnings_pre", "earnings_post", "executor")
            loader = get_loader()
            defaults = {f"{name.removeprefix('earnings_')}_{role}":
                        loader.load_system(name) if role == "system" else loader.load_user(name)
                        for name in names for role in ("system", "user")}
        for stage, template in self.prompt_templates.items():
            if not isinstance(template, str) or not template.strip() or len(template) > 16_000 or re.search(r"(?i)\b(?:sk|pk)[-_][A-Za-z0-9]{12,}", template):
                raise ValueError("prompt template must be nonempty text under 16000 characters and contain no API secret")
            placeholder_pattern = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]+)?\}(?!\})")
            placeholders = set(placeholder_pattern.findall(template))
            allowed_fields = set(placeholder_pattern.findall(defaults[stage]))
            if placeholders - allowed_fields:
                raise ValueError(f"unsupported placeholders in {stage}: {sorted(placeholders - allowed_fields)}")
            required = required_market_fields.get(stage)
            if required and required not in placeholders:
                raise ValueError(f"{stage} must contain {{{required}}} so market data reaches the model")
        for value in self.prompt_addenda.values():
            if not isinstance(value, str) or len(value) > 4_000 or re.search(r"(?i)\b(?:sk|pk)[-_][A-Za-z0-9]{12,}", value):
                raise ValueError("prompt addendum must be text under 4000 characters and contain no API secret")
        allowed: dict[Family, set[str]] = {
            Family.INTRADAY: {"decision_interval_minutes", "max_positions", "dte_min", "dte_max"},
            Family.OVERLAY: {"stock_qty", "cash_buffer_pct", "dte_min", "dte_max", "otm_pct", "rebalance_weekday"},
            Family.EARNINGS: {"max_position_cost", "max_contracts", "auto_close_at_t2", "dte_min", "dte_max", "post_decision_interval_minutes"},
        }
        permitted = allowed[family] - ({"decision_interval_minutes"} if self.cadence == "daily" else set())
        unknown = set(self.strategy_params) - permitted
        if unknown:
            raise ValueError(f"unsupported strategy params: {sorted(unknown)}")
        for key, value in self.strategy_params.items():
            if key == "auto_close_at_t2":
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be boolean")
            elif not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{key} must be numeric")
            elif key in {"decision_interval_minutes", "max_positions", "dte_min", "dte_max", "stock_qty", "rebalance_weekday", "max_contracts", "post_decision_interval_minutes"} and not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            elif abs(value) > 1_000_000:
                raise ValueError(f"{key} is out of range")
        for key in ("dte_min", "dte_max"):
            if key in self.strategy_params and not 0 <= self.strategy_params[key] <= 365:
                raise ValueError(f"{key} must be 0–365")
        ranges = {
            "decision_interval_minutes": (1, 390), "post_decision_interval_minutes": (1, 390),
            "max_positions": (1, 50), "stock_qty": (0, 1_000_000),
            "cash_buffer_pct": (0, 1), "otm_pct": (0, 1),
            "rebalance_weekday": (0, 6), "max_contracts": (1, 1000),
            "max_position_cost": (1, 1_000_000),
        }
        for key, (minimum, maximum) in ranges.items():
            if key in self.strategy_params and not minimum <= self.strategy_params[key] <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
        if self.strategy_params.get("dte_min", 0) > self.strategy_params.get("dte_max", 365):
            raise ValueError("dte_min must be <= dte_max")
        if self.experiment.universe is not None:
            # Custom tasks may use the operator's imported or synthetic data.
            # A source-code ticker list cannot certify coverage on their hub.
            if self.cadence:
                bad = [ticker for ticker in self.experiment.universe
                       if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", ticker)]
            else:
                bad = set(self.experiment.universe) - SUPPORTED_UNDERLYINGS
            if bad:
                raise ValueError(f"invalid or unsupported underlying symbols: {sorted(bad)}")
            if not self.cadence and scenario.family == Family.INTRADAY and self.experiment.universe != ["SPY"]:
                raise ValueError("intraday preset currently supports SPY only")
        if set(self.risk) - set(RiskConfig.model_fields):
            raise ValueError("unknown risk setting")
        if self.cadence == "daily" and self.risk.get("flatten_before_close"):
            raise ValueError("daily tasks do not support before-close flattening; use intraday cadence")
        if set(self.data_policy) - (set(DataPolicyConfig.model_fields) - {"npp_base_url"}):
            raise ValueError("unknown or server-owned data policy setting")
        for key in ("max_orders_per_day", "max_position_per_symbol"):
            if key in self.risk and (not isinstance(self.risk[key], int) or self.risk[key] < 1):
                raise ValueError(f"{key} must be a positive integer")
        for key in ("chain_filter_dte_min", "chain_filter_dte_max"):
            if key in self.data_policy and (not isinstance(self.data_policy[key], int) or not 0 <= self.data_policy[key] <= 365):
                raise ValueError(f"{key} must be 0–365")
        if "news_enabled" in self.data_policy and not isinstance(self.data_policy["news_enabled"], bool):
            raise ValueError("news_enabled must be boolean")
        if "news_lookback_days" in self.data_policy and (not isinstance(self.data_policy["news_lookback_days"], int) or isinstance(self.data_policy["news_lookback_days"], bool) or not 1 <= self.data_policy["news_lookback_days"] <= 30):
            raise ValueError("news_lookback_days must be 1–30")
        return self


def materialize(spec: RunSpecV1, source: dict[str, Any], model, group, run_id: str) -> RunConfig:
    """Merge validated edits onto a preset, preserving server-owned fields."""
    if spec.cadence:
        from liveoption_web.runs.custom_tasks import custom_source
        source = custom_source(spec.cadence)
    config = RunConfig.model_validate(source)
    edits = spec.experiment
    if edits.start:
        config.experiment.start = edits.start.isoformat()
    if edits.end:
        config.experiment.end = edits.end.isoformat()
    if config.experiment.start > config.experiment.end:
        raise ValueError("start must be <= end")
    if edits.initial_cash is not None:
        config.experiment.initial_cash = edits.initial_cash
    if edits.universe is not None:
        config.experiment.universe = edits.universe
    if spec.scenario.family == Family.OVERLAY:
        if edits.universe is not None or "stock_qty" in spec.strategy_params:
            qty = int(spec.strategy_params.get("stock_qty", config.strategy.params.get("stock_qty", 10000)))
            config.experiment.initial_positions = [{"symbol": ticker, "qty": qty} for ticker in config.experiment.universe]
        if spec.overlay_mode is not None:
            config.strategy.name = {"hedge": "overlay_hedge", "covered_call": "overlay_profit", "hybrid": "overlay_hybrid"}[spec.overlay_mode]
    config.experiment.run_id = run_id
    config.experiment.mode = "replay"
    config.strategy.params.update(spec.strategy_params)
    if spec.decision_time_et is not None:
        config.experiment.frequency = "1m"
        config.strategy.params["decision_time_et"] = spec.decision_time_et
    config.strategy.params["llm_provider"] = group.provider
    config.strategy.params["llm_model"] = model.api_model
    config.strategy.params["prompt_override"] = spec.context.strip()
    config.strategy.params["prompt_addenda"] = {key: value.strip() for key, value in spec.prompt_addenda.items() if value.strip()}
    if spec.cadence:
        from liveoption_web.runs.custom_tasks import custom_prompts
        config.strategy.params["prompt_templates"] = {**custom_prompts(spec.cadence), **spec.prompt_templates}
    else:
        config.strategy.params["prompt_templates"] = spec.prompt_templates
    config.risk = RiskConfig.model_validate({**config.risk.model_dump(), **spec.risk})
    data_edits = dict(spec.data_policy)
    for key in ("dte_min", "dte_max"):
        if key in spec.strategy_params:
            data_edits.setdefault(f"chain_filter_{key}", spec.strategy_params[key])
    config.data_policy = DataPolicyConfig.model_validate({**config.data_policy.model_dump(), **data_edits})
    config.avsr = spec.avsr
    execution = spec.execution.model_dump(exclude_none=True)
    if "order_type_policy" in execution:
        config.execution.order_type_policy = execution.pop("order_type_policy")
    config.execution.fee_model.update(execution)
    # URL and output location are intentionally inherited from server-side defaults.
    config.execution.pmb_base_url = source.get("execution", {}).get("pmb_base_url", config.execution.pmb_base_url)
    config.execution.upq_base_url = source.get("execution", {}).get("upq_base_url", config.execution.upq_base_url)
    return config
