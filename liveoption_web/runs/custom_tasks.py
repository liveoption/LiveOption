"""Independent custom task defaults; no preset config is imported."""
from __future__ import annotations

from liveoption_web.runs.catalog import Scenario
from liveoption_web.runs.families import Family
from trading.core.config import RunConfig
from trading.prompts.custom import custom_prompts


def custom_source(cadence: str) -> dict:
    if cadence not in {"intraday", "daily"}:
        raise ValueError("custom cadence must be intraday or daily")
    daily = cadence == "daily"
    params = {"dte_min": 7 if daily else 0, "dte_max": 60 if daily else 7, "max_positions": 5}
    if not daily:
        params["decision_interval_minutes"] = 15
    return RunConfig.model_validate({
        "experiment": {"run_id": f"custom_{cadence}", "start": "2025-01-02", "end": "2025-01-31",
                       "universe": ["SPY"], "frequency": "1d" if daily else "1m", "initial_cash": 100_000},
        "strategy": {"name": "custom_daily_llm" if daily else "intraday_llm", "params": params},
        "risk": {"flatten_before_close": not daily},
        "data_policy": {"chain_filter_dte_min": params["dte_min"], "chain_filter_dte_max": params["dte_max"]},
    }).model_dump(mode="json")


def custom_scenario(cadence: str) -> Scenario:
    return Scenario(id=f"custom_{cadence}", display_name="Daily" if cadence == "daily" else "Intraday",
                    description="Custom options task", config_path="", overridable=frozenset(),
                    family=None if cadence == "daily" else Family.INTRADAY)
