"""apply_overrides merges CLI/web-supplied values onto a loaded RunConfig."""
from datetime import date

import pytest

from trading.core.config import RunConfig, ExperimentConfig, StrategyConfig
from trading.core.overrides import apply_overrides, OverrideError


def _base_config() -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="run_base", start="2025-01-02", end="2025-01-31",
            universe=["SPY"], initial_cash=100_000.0,
        ),
        strategy=StrategyConfig(name="llm_deepseek_v1", params={"llm_model": "deepseek-chat"}),
    )


class _Args:
    """Duck-typed argparse.Namespace stand-in."""
    def __init__(self, **kw):
        for k in (
            "start", "end", "bankroll", "strategy", "llm_model", "prompt_override",
            "decision_interval_minutes", "max_concurrent_positions", "dte_range",
        ):
            setattr(self, k, kw.get(k))


def test_identity_when_no_overrides_present():
    cfg = _base_config()
    out = apply_overrides(cfg, _Args())
    assert out.experiment.start == "2025-01-02"
    assert out.experiment.end == "2025-01-31"
    assert out.experiment.initial_cash == 100_000.0
    assert out.strategy.name == "llm_deepseek_v1"
    assert out.strategy.params.get("llm_model") == "deepseek-chat"


def test_override_start_and_end():
    out = apply_overrides(_base_config(), _Args(start=date(2025, 2, 1), end=date(2025, 2, 28)))
    assert out.experiment.start == "2025-02-01"
    assert out.experiment.end == "2025-02-28"


def test_override_bankroll_updates_initial_cash():
    out = apply_overrides(_base_config(), _Args(bankroll=250_000.0))
    assert out.experiment.initial_cash == 250_000.0


def test_override_strategy_swaps_registry_key():
    out = apply_overrides(_base_config(), _Args(strategy="llm_gemini_v1"))
    assert out.strategy.name == "llm_gemini_v1"


def test_override_llm_model_flows_into_strategy_params():
    out = apply_overrides(_base_config(), _Args(llm_model="gemini-2.0-flash"))
    assert out.strategy.params.get("llm_model") == "gemini-2.0-flash"


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("gpt-5.6-terra", "openai_compatible"),
        ("gemini-3-flash", "anthropic"),
    ],
)
def test_gateway_model_override_sets_provider(model, provider):
    out = apply_overrides(_base_config(), _Args(llm_model=model))
    assert out.strategy.params["llm_model"] == model
    assert out.strategy.params["llm_provider"] == provider


def test_override_prompt_override_flows_into_strategy_params():
    out = apply_overrides(_base_config(), _Args(prompt_override="You are a trader..."))
    assert out.strategy.params.get("prompt_override") == "You are a trader..."


def test_intraday_settings_reach_strategy_and_chain_filter():
    cfg = _base_config()
    cfg.strategy.name = "intraday_llm"
    out = apply_overrides(cfg, _Args(
        decision_interval_minutes=30,
        max_concurrent_positions=2,
        dte_range=(0, 1),
    ))
    assert out.strategy.params["decision_interval_minutes"] == 30
    assert out.strategy.params["max_positions"] == 2
    assert out.strategy.params["dte_min"] == 0
    assert out.strategy.params["dte_max"] == 1
    assert out.data_policy.chain_filter_dte_max == 1


def test_rejects_llm_model_on_non_llm_strategy():
    cfg = _base_config()
    cfg.strategy = StrategyConfig(name="stock_hold_baseline", params={})
    with pytest.raises(OverrideError, match="llm_model"):
        apply_overrides(cfg, _Args(llm_model="deepseek-chat"))


@pytest.mark.parametrize(
    "strategy_name",
    [
        "weekly_llm",          # real registry key, suffix convention
        "intraday_llm",        # real registry key, suffix convention
        "llm_deepseek_v1",     # hypothetical prefix convention — must still match
        "llm_gemini_v1",
    ],
)
def test_accepts_llm_model_on_llm_naming_conventions(strategy_name: str):
    """LLM-family strategies are recognized by any `_llm` token position.

    Regression guard: the initial implementation used a strict `startswith("llm_")`
    prefix check, which silently rejected overrides on the actually-registered
    `weekly_llm` / `intraday_llm` strategies — every web-launched run with an
    llm_model override would have 400'd. See Task 4 spec review.
    """
    cfg = _base_config()
    cfg.strategy = StrategyConfig(name=strategy_name, params={})
    out = apply_overrides(cfg, _Args(llm_model="deepseek-chat"))
    assert out.strategy.params.get("llm_model") == "deepseek-chat"


@pytest.mark.parametrize(
    "strategy_name",
    [
        "stock_hold_baseline",
        "paper_aligned",
    ],
)
def test_rejects_llm_model_on_non_llm_registry_keys(strategy_name: str):
    """Non-LLM registered strategies must still reject llm_model overrides."""
    cfg = _base_config()
    cfg.strategy = StrategyConfig(name=strategy_name, params={})
    with pytest.raises(OverrideError, match="llm_model"):
        apply_overrides(cfg, _Args(llm_model="deepseek-chat"))


@pytest.mark.parametrize("name", ["overlay_profit", "overlay_hedge", "earnings_bet"])
def test_accepts_llm_model_on_llm_aware_strategies(name):
    """Overlay/earnings predate the `*_llm` naming convention but are
    LLM-driven (paper §2.2). The override layer recognizes them via the
    _LLM_AWARE_STRATEGIES allowlist."""
    cfg = _base_config()
    cfg.strategy = StrategyConfig(name=name, params={})
    out = apply_overrides(cfg, _Args(llm_model="deepseek-chat"))
    assert out.strategy.params["llm_model"] == "deepseek-chat"


def test_rejects_start_after_end():
    with pytest.raises(OverrideError, match="start .* after .* end"):
        apply_overrides(_base_config(), _Args(start=date(2025, 3, 1), end=date(2025, 2, 1)))


def test_rejects_bankroll_out_of_range():
    with pytest.raises(OverrideError, match="bankroll"):
        apply_overrides(_base_config(), _Args(bankroll=-100.0))
    with pytest.raises(OverrideError, match="bankroll"):
        apply_overrides(_base_config(), _Args(bankroll=20_000_000.0))


def test_combined_overrides():
    out = apply_overrides(
        _base_config(),
        _Args(start=date(2025, 1, 15), bankroll=150_000.0, llm_model="gpt-4o-mini"),
    )
    assert out.experiment.start == "2025-01-15"
    assert out.experiment.end == "2025-01-31"  # unchanged
    assert out.experiment.initial_cash == 150_000.0
    assert out.strategy.params.get("llm_model") == "gpt-4o-mini"


def test_rejects_partial_start_that_inverts_window():
    """Overriding only `start` must be validated against the unchanged `end`."""
    with pytest.raises(OverrideError, match="start .* after .* end"):
        apply_overrides(
            _base_config(),
            _Args(start=date(2025, 3, 1)),  # base end = 2025-01-31
        )


def test_rejects_partial_end_that_inverts_window():
    """Overriding only `end` must be validated against the unchanged `start`."""
    with pytest.raises(OverrideError, match="start .* after .* end"):
        apply_overrides(
            _base_config(),
            _Args(end=date(2024, 12, 1)),  # base start = 2025-01-02
        )
