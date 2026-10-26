"""Web run launcher — scenario catalog + allowlist.

Single source of truth for what the web UI can launch. Adding a scenario:
  1. Commit a YAML under configs/scenarios/<id>.yaml.
  2. Add an entry to SCENARIOS below with id, display_name, description,
     and config_path.
  3. Test the preset, review and deploy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from liveoption_web.runs.families import Family


REPO_ROOT = Path(__file__).resolve().parents[2]

# Underlyings verified to have UPQ option chains AND Benzinga news/earnings
# coverage. Expansion requires data
# verification (UPQ chain_query 200 + Benzinga news ticker hits).
SUPPORTED_UNDERLYINGS: frozenset[str] = frozenset({
    # Intraday
    "SPY",
    # Overlay
    "QQQ", "NVDA",
    # Earnings universe
    "AAPL", "MSFT", "TSLA", "AMZN", "META",
    "AMD", "INTC", "MU", "AVGO",
    "NFLX", "DIS", "NKE",
    "CRM", "SNOW", "PLTR",
    "HOOD", "COIN", "SOFI", "PYPL",
    "SHOP", "RKLB", "SMCI", "CELH",
})
# Fail loudly at import time if catalog.py gets moved and REPO_ROOT resolves
# to the wrong ancestor — scenario config_paths are resolved against it.
assert (REPO_ROOT / "pyproject.toml").is_file(), (
    f"catalog REPO_ROOT={REPO_ROOT!s} is not the repo root (missing pyproject.toml). "
    "Was catalog.py moved? Update the parents[N] depth."
)

OVERRIDABLE: frozenset[str] = frozenset(
    {
        # Core (every family + null-family)
        "start", "end", "bankroll",
        # LLM mixin (intraday + null-family)
        "llm_model", "prompt_override",
        # Overlay-specific
        "underlying_holdings", "overlay_dte_range",
        # Earnings-specific
        "tickers", "event_window", "quarter",
        # Intraday-specific
        "decision_interval_minutes", "max_concurrent_positions", "dte_range",
    }
)


class Scenario(BaseModel):
    """A web-launcher-managed scenario.

    Fields:
      - ``id``: stable snake_case identifier; used by the API and must
        equal the ``SCENARIOS`` dict key.
      - ``display_name``: shown in the launcher UI's scenario picker.
      - ``description``: one-line summary rendered under the name.
      - ``config_path``: repo-relative path to the base YAML loaded by
        ``trading.core.config.load_config``. Overrides are merged on top
        at CLI parse time via ``trading.core.overrides.apply_overrides``.
      - ``overridable``: subset of ``OVERRIDABLE`` this scenario permits
        the UI to override. Defaults to all; set a smaller set to lock
        down specific scenarios (e.g. the ``dryrun_smoke`` fixture).
    """

    id: str
    display_name: str
    description: str
    config_path: str
    overridable: frozenset[str] = Field(default=OVERRIDABLE)
    family: Family | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "intraday_spy_2025_q1": Scenario(
        id="intraday_spy_2025_q1",
        display_name="Intraday",
        description=(
            "Intraday options trading with configurable decision intervals and risk limits."
        ),
        config_path="configs/scenarios/intraday_spy_2025_q1.yaml",
        family=Family.INTRADAY,
        overridable=frozenset(
            {
                "start", "end", "bankroll", "llm_model", "prompt_override",
                "decision_interval_minutes", "max_concurrent_positions",
                "dte_range",
            }
        ),
        defaults={"decision_interval_minutes": 15, "dte_range": [0, 7]},
    ),
    "overlay_spy_qqq_2025_q1": Scenario(
        id="overlay_spy_qqq_2025_q1",
        display_name="Overlay",
        description=(
            "Portfolio overlay using covered calls and protective puts."
        ),
        config_path="configs/scenarios/overlay_spy_qqq_2025_q1.yaml",
        family=Family.OVERLAY,
        overridable=frozenset(
            {"start", "end", "bankroll", "llm_model", "prompt_override",
             "underlying_holdings", "overlay_dte_range"}
        ),
        defaults={"overlay_dte_range": [7, 45]},
    ),
    "earnings_2025": Scenario(
        id="earnings_2025",
        display_name="Earnings",
        description=(
            "Event-driven options trading around earnings releases."
        ),
        config_path="configs/scenarios/earnings_2025.yaml",
        family=Family.EARNINGS,
        overridable=frozenset(
            {"start", "end", "bankroll", "llm_model", "prompt_override",
             "tickers", "event_window", "quarter"}
        ),
        defaults={"event_window": [-2, 2]},
    ),
}


LAUNCH_ALLOWLIST: frozenset[str] = frozenset()


# Legacy model identifiers. Provider is auto-derived from the model id —
# see _provider_for_model in trading/core/overrides.py.
LLM_MODEL_CHOICES: frozenset[str] = frozenset({
    "gpt-4.1-mini",
    "deepseek-chat",         # DeepSeek-V3 chat alias
    "gemini-2.5-flash",
    "claude-sonnet-4-5",
    "qwen2.5-max",
    "gemini-3-flash",
    "gpt-5.6-terra",
})


# Must be a subset of trading.core.registry.STRATEGY_REGISTRY.
STRATEGY_CHOICES: frozenset[str] = frozenset({
    "weekly_llm",
    "intraday_llm",
    "paper_aligned",
    "overlay_profit",
    "overlay_hedge",
    "overlay_hybrid",
    "earnings_bet",
})
