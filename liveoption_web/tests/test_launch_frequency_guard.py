"""Frequency × strategy guard at launch time (Bug C fix)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from liveoption_web.runs.catalog import SCENARIOS


REPO_ROOT = Path(__file__).resolve().parents[2]


def _yaml_frequency(config_path: str) -> str:
    with (REPO_ROOT / config_path).open() as f:
        return yaml.safe_load(f)["experiment"]["frequency"]


def test_all_catalog_scenarios_pass_guard():
    """Every catalog scenario's YAML frequency must be compatible with
    its strategy's REQUIRED_FREQUENCIES."""
    import trading.agents.strategy  # noqa: F401  trigger registry
    from trading.core.registry import STRATEGY_REGISTRY

    for sid, scenario in SCENARIOS.items():
        with (REPO_ROOT / scenario.config_path).open() as f:
            cfg = yaml.safe_load(f)
        strategy_name = cfg["strategy"]["name"]
        freq = cfg["experiment"]["frequency"]
        cls = STRATEGY_REGISTRY[strategy_name]
        if cls.REQUIRED_FREQUENCIES is None:
            continue
        assert freq in cls.REQUIRED_FREQUENCIES, (
            f"scenario {sid!r} declares frequency={freq!r} but "
            f"strategy {strategy_name!r} requires "
            f"{sorted(cls.REQUIRED_FREQUENCIES)}"
        )


def test_check_frequency_compatibility_passes_when_compatible():
    from liveoption_web.api.launch import _check_frequency_compatibility
    # earnings_2025 is 1m + earnings_bet (requires {"1m"}) → OK.
    _check_frequency_compatibility("earnings_bet", "1m")  # no raise


def test_check_frequency_compatibility_400s_on_mismatch():
    from liveoption_web.api.launch import _check_frequency_compatibility
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _check_frequency_compatibility("earnings_bet", "1d")
    assert exc_info.value.status_code == 400
    body = exc_info.value.detail
    assert "scenario-strategy-frequency-mismatch" in str(body)


def test_check_frequency_compatibility_unknown_strategy_is_noop():
    """Defensive: if the strategy isn't in the registry, skip the guard
    and let the subprocess fail loud at config-load time. Avoids coupling
    launcher to strategy import order."""
    from liveoption_web.api.launch import _check_frequency_compatibility
    _check_frequency_compatibility("nonexistent_strategy_zzz", "1d")  # no raise


def test_check_frequency_compatibility_unconstrained_strategy_passes():
    from liveoption_web.api.launch import _check_frequency_compatibility
    # paper_aligned has REQUIRED_FREQUENCIES = None
    _check_frequency_compatibility("paper_aligned", "1d")  # no raise
    _check_frequency_compatibility("paper_aligned", "1m")  # no raise
