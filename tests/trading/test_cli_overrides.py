"""Tests for --tickers override on `trading.cli run`."""

from __future__ import annotations

import pytest

from trading.core.config import RunConfig, ExperimentConfig, StrategyConfig, RiskConfig, ExecutionConfig, DataPolicyConfig, EvaluationConfig
from trading.core.overrides import apply_overrides, OverrideError


class _Args:
    """Duck-typed argparse-namespace replacement for tests."""
    def __init__(self, **kw):
        for k in ("start", "end", "bankroll", "strategy", "llm_model",
                  "prompt_override", "tickers"):
            setattr(self, k, kw.get(k))


def _baseline_config(universe=None) -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="t",
            seed=1,
            start="2025-01-01",
            end="2025-12-31",
            universe=universe or ["AAPL", "MSFT", "TSLA"],
            frequency="1m",
            initial_cash=50000.0,
        ),
        strategy=StrategyConfig(name="earnings_bet", params={"llm_provider": "deepseek"}),
        risk=RiskConfig(),
        execution=ExecutionConfig(),
        data_policy=DataPolicyConfig(),
        evaluation=EvaluationConfig(output_dir="./output"),
    )


def test_apply_overrides_reduces_universe_to_tickers_list():
    cfg = _baseline_config(universe=["AAPL", "MSFT", "TSLA", "NVDA"])
    out = apply_overrides(cfg, _Args(tickers=["AAPL", "MSFT"]))
    assert out.experiment.universe == ["AAPL", "MSFT"]


def test_apply_overrides_tickers_must_be_subset_of_universe():
    cfg = _baseline_config(universe=["AAPL", "MSFT"])
    with pytest.raises(OverrideError, match="not in scenario universe"):
        apply_overrides(cfg, _Args(tickers=["AAPL", "GOOG"]))


def test_apply_overrides_empty_tickers_rejected():
    cfg = _baseline_config()
    with pytest.raises(OverrideError, match="tickers override is empty"):
        apply_overrides(cfg, _Args(tickers=[]))


def test_apply_overrides_tickers_none_is_noop():
    cfg = _baseline_config(universe=["AAPL", "MSFT"])
    out = apply_overrides(cfg, _Args(tickers=None))
    assert out.experiment.universe == ["AAPL", "MSFT"]


def test_cli_run_subparser_accepts_tickers_flag():
    """The run subparser parses --tickers as a comma-separated list."""
    from trading.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["run", "--config", "x.yaml", "--tickers", "AAPL,MSFT"])
    assert ns.tickers == ["AAPL", "MSFT"]


def test_cli_run_subparser_tickers_default_none():
    from trading.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["run", "--config", "x.yaml"])
    assert ns.tickers is None
