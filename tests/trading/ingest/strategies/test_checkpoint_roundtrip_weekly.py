"""Round-trip checkpoint tests for WeeklyLLMStrategy."""
from __future__ import annotations

import pytest

from trading.agents.strategy.weekly_llm import WeeklyLLMStrategy


def test_weekly_llm_checkpoint_roundtrip(fake_config):
    s1 = WeeklyLLMStrategy()
    s1._rebalance_dates = {"2025-01-06", "2025-01-13", "2025-01-20"}
    s1._last_decision_date = "2025-01-13"

    snap = s1.save_checkpoint()

    s2 = WeeklyLLMStrategy()
    s2.load_checkpoint(snap)

    assert s2._rebalance_dates == s1._rebalance_dates
    assert s2._last_decision_date == s1._last_decision_date


def test_weekly_llm_checkpoint_version_mismatch(fake_config):
    s = WeeklyLLMStrategy()
    with pytest.raises(ValueError, match="schema version"):
        s.load_checkpoint({"_version": 99})
