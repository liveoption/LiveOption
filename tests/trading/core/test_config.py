"""Tests for RunConfig and sub-configs."""

import tempfile
from pathlib import Path

import pytest
import yaml

from trading.core.config import (
    DataPolicyConfig,
    EvaluationConfig,
    ExecutionConfig,
    ExperimentConfig,
    RiskConfig,
    RunConfig,
    StrategyConfig,
    load_config,
)


def test_experiment_config_defaults():
    cfg = ExperimentConfig(
        run_id="test", start="2025-01-06", end="2025-01-10", universe=["SPY"]
    )
    assert cfg.frequency == "1m"
    assert cfg.seed == 42
    assert cfg.timezone == "America/New_York"


def test_experiment_config_mode_default():
    cfg = ExperimentConfig(
        run_id="test", start="2025-01-06", end="2025-01-10", universe=["SPY"]
    )
    assert cfg.mode == "replay"


def test_risk_config_defaults():
    cfg = RiskConfig()
    assert cfg.max_exposure_pct == 0.5
    assert cfg.flatten_before_close is True
    assert cfg.circuit_breaker_loss_pct == 0.03
    assert cfg.max_daily_loss_pct == 0.02
    assert cfg.cooldown_seconds == 60
    assert cfg.flatten_minutes_before_close == 5
    assert cfg.max_orders_per_day == 10
    assert cfg.max_position_per_symbol == 100


def test_data_policy_defaults():
    cfg = DataPolicyConfig()
    assert cfg.max_stale_minutes == 10
    assert cfg.chain_filter_delta_min == 0.20
    assert cfg.chain_filter_delta_max == 0.60
    assert cfg.lookback_bars == 60
    assert cfg.chain_filter_dte_min == 0
    assert cfg.chain_filter_dte_max == 7
    assert cfg.chain_max_contracts_per_ticker == 10
    assert cfg.missing_bar_strategy == "forward_fill_with_stale_flag"


def test_execution_config_has_urls():
    from trading.core.endpoints import DEFAULT_PMB_URL, DEFAULT_UPQ_URL

    cfg = ExecutionConfig()
    assert cfg.pmb_base_url == DEFAULT_PMB_URL
    assert cfg.upq_base_url == DEFAULT_UPQ_URL
    assert cfg.fill_model == "next_bar"


def test_execution_config_fee_model():
    cfg = ExecutionConfig()
    assert cfg.fee_model["option_fee_per_contract"] == 0.65
    assert cfg.fee_model["slippage_bps"] == 1.0


def test_evaluation_config_defaults():
    cfg = EvaluationConfig()
    assert cfg.scales == ["1m", "5m", "15m", "1d"]
    assert cfg.output_dir == "runs"


def test_strategy_config_params_default():
    cfg = StrategyConfig(name="test")
    assert cfg.params == {}


def test_run_config_defaults():
    """RunConfig sub-configs default to their defaults when not provided."""
    cfg = RunConfig(
        experiment=ExperimentConfig(
            run_id="test", start="2025-01-06", end="2025-01-10", universe=["SPY"]
        ),
        strategy=StrategyConfig(name="test"),
    )
    from trading.core.endpoints import DEFAULT_PMB_URL

    assert cfg.risk.max_exposure_pct == 0.5
    assert cfg.execution.pmb_base_url == DEFAULT_PMB_URL
    assert cfg.data_policy.max_stale_minutes == 10
    assert cfg.evaluation.output_dir == "runs"


def test_load_config_from_yaml():
    yaml_content = {
        "experiment": {
            "run_id": "test_run",
            "start": "2025-01-06",
            "end": "2025-01-10",
            "universe": ["SPY", "QQQ"],
            "frequency": "1m",
        },
        "strategy": {
            "name": "intraday_llm",
            "params": {"decision_interval_minutes": 15},
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(yaml_content, f)
        f.flush()
        cfg = load_config(f.name)
    assert cfg.experiment.run_id == "test_run"
    assert cfg.strategy.name == "intraday_llm"
    assert cfg.strategy.params == {"decision_interval_minutes": 15}
    assert cfg.risk.max_exposure_pct == 0.5  # default
    Path(f.name).unlink()


def test_load_config_weekly():
    yaml_content = {
        "experiment": {
            "run_id": "weekly_test",
            "start": "2025-01-01",
            "end": "2025-12-31",
            "universe": ["SPY"],
            "frequency": "1d",
        },
        "strategy": {
            "name": "weekly_llm",
            "params": {"rebalance_frequency": "weekly"},
        },
        "risk": {"flatten_before_close": False},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(yaml_content, f)
        f.flush()
        cfg = load_config(f.name)
    assert cfg.experiment.frequency == "1d"
    assert cfg.risk.flatten_before_close is False
    Path(f.name).unlink()


def test_load_config_with_all_sections():
    yaml_content = {
        "experiment": {
            "run_id": "full_test",
            "start": "2025-01-06",
            "end": "2025-01-10",
            "universe": ["SPY"],
        },
        "strategy": {"name": "intraday_llm"},
        "risk": {
            "max_exposure_pct": 0.3,
            "circuit_breaker_loss_pct": 0.05,
        },
        "execution": {
            "pmb_base_url": "http://localhost:9999",
        },
        "data_policy": {
            "max_stale_minutes": 5,
            "chain_filter_delta_min": 0.15,
        },
        "evaluation": {
            "scales": ["1m", "1d"],
            "output_dir": "/tmp/test_runs",
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(yaml_content, f)
        f.flush()
        cfg = load_config(f.name)
    assert cfg.risk.max_exposure_pct == 0.3
    assert cfg.execution.pmb_base_url == "http://localhost:9999"
    assert cfg.data_policy.max_stale_minutes == 5
    assert cfg.evaluation.scales == ["1m", "1d"]
    Path(f.name).unlink()
