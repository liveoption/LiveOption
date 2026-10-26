"""Shared fixtures for strategy checkpoint round-trip tests."""
from __future__ import annotations

import pytest

from trading.core.config import (
    ExperimentConfig,
    RunConfig,
    StrategyConfig,
)


@pytest.fixture
def fake_config(tmp_path) -> RunConfig:
    """Minimal RunConfig sufficient for strategy construction / on_session_start.

    Strategies should not need network access because their checkpoint methods
    operate on already-populated state fields (we set those directly in tests).
    """
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="checkpoint-test",
            start="2025-01-06",
            end="2025-01-10",
            universe=["SPY", "QQQ"],
        ),
        strategy=StrategyConfig(name="dummy", params={}),
    )
