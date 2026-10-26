"""Catalog sanity — every scenario has a real YAML; enums are in sync with code."""
from pathlib import Path

import pytest

from liveoption_web.runs.catalog import (
    LAUNCH_ALLOWLIST,
    LLM_MODEL_CHOICES,
    OVERRIDABLE,
    SCENARIOS,
    STRATEGY_CHOICES,
    Scenario,
)
from trading.core.config import load_config
from trading.core.registry import STRATEGY_REGISTRY


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_scenarios_is_nonempty():
    assert len(SCENARIOS) >= 1


def test_each_scenario_has_real_config_yaml():
    for sc in SCENARIOS.values():
        yaml_path = REPO_ROOT / sc.config_path
        assert yaml_path.is_file(), f"{sc.id}: {yaml_path} missing"


def test_each_scenario_config_parses():
    # Import strategy module so the registry is populated before config parses.
    import trading.agents.strategy  # noqa: F401
    for sc in SCENARIOS.values():
        cfg = load_config(str(REPO_ROOT / sc.config_path))
        assert cfg.experiment.run_id, sc.id
        assert cfg.strategy.name in STRATEGY_REGISTRY, (
            f"{sc.id} strategy {cfg.strategy.name!r} not in registry; "
            "add it to trading/agents/strategy.py or fix the YAML."
        )


def test_strategy_choices_nonempty_and_subset_of_registry():
    import trading.agents.strategy  # noqa: F401
    assert STRATEGY_CHOICES, "STRATEGY_CHOICES must not be empty (empty set is trivially a subset)"
    unknown = STRATEGY_CHOICES - set(STRATEGY_REGISTRY.keys())
    assert not unknown, f"STRATEGY_CHOICES contains unregistered names: {unknown}"


def test_overridable_fields_are_known():
    # Phase C: OVERRIDABLE is the union of every family's permitted fields.
    # Family-specific subsets are picked per-scenario via Scenario.overridable.
    expected_subset = {"start", "end", "bankroll", "llm_model", "prompt_override"}
    assert expected_subset.issubset(OVERRIDABLE)
    for sc in SCENARIOS.values():
        unknown = sc.overridable - OVERRIDABLE
        assert not unknown, f"{sc.id}: unknown overridable fields {unknown}"


def test_release_has_no_hardcoded_personal_email_allowlist():
    assert LAUNCH_ALLOWLIST == frozenset()


def test_llm_model_choices_nonempty():
    assert LLM_MODEL_CHOICES
    assert "deepseek-chat" in LLM_MODEL_CHOICES


def test_scenario_id_matches_key():
    for key, sc in SCENARIOS.items():
        assert key == sc.id, f"SCENARIOS[{key!r}].id = {sc.id!r} mismatch"
