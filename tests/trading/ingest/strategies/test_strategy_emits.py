"""Unit tests for strategy-level decision/phase emits (Plan A Task 7).

These tests use a FakeEmitter to capture emit calls without running the full
runner/engine. They focus on verifying that:

1. Strategies emit decision.started + decision.completed around each decision.
2. Multi-phase strategies emit phase.completed after each phase.
3. decision.skipped is emitted when a decision is short-circuited.
4. write_artifact is a safe no-op when _runner_ref is None.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class FakeEmitter:
    """Captures emit calls; mirrors NoopEmitter.emit signature."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


def test_base_write_artifact_noop_without_runner_ref():
    """write_artifact is a safe no-op when _runner_ref is None."""
    from datetime import datetime, timezone

    from trading.agents.strategy.base import BaseStrategy

    class Dummy(BaseStrategy):
        def on_step(self, market_event, portfolio, state):
            return []

        def save_checkpoint(self):
            return {}

        def load_checkpoint(self, state):
            pass

    d = Dummy()
    assert d._runner_ref is None
    d.write_artifact(
        decision_id="01TEST",
        seq=0,
        ts=datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc),
        model_id="m",
        provider="p",
        prompt={},
        response={},
        context={},
        trades=[],
        market_snapshot={},
    )


def test_weekly_llm_emits_skip_on_non_rebalance_day():
    """Non-rebalance day emits decision.skipped with reason=not_rebalance_day."""
    from datetime import datetime, timezone

    from trading.agents.strategy.weekly_llm import WeeklyLLMStrategy

    s = WeeklyLLMStrategy()
    s._rebalance_dates = {"2025-01-06"}  # Monday only
    s.events = FakeEmitter()

    state = MagicMock()
    state.current_ts = datetime(2025, 1, 7, 20, 0, tzinfo=timezone.utc)  # Tuesday

    intents = s.on_step(market_event=MagicMock(), portfolio=MagicMock(), state=state)
    assert intents == []
    kinds = s.events.kinds()
    assert "decision.skipped" in kinds
    payload = dict(s.events.events[0][1])
    assert payload["reason"] == "not_rebalance_day"


def test_weekly_llm_emits_started_and_completed_on_success():
    """Successful LLM decision emits started + completed with has_trade/n_trades."""
    from datetime import datetime, timezone

    from trading.agents.strategy.weekly_llm import WeeklyLLMStrategy

    s = WeeklyLLMStrategy()
    s._rebalance_dates = {"2025-01-07"}
    s.events = FakeEmitter()
    # Stub out _call_llm to avoid any network/LLM dependencies
    s._call_llm = MagicMock(return_value=([], {}, {}, {}, {}))

    state = MagicMock()
    state.current_ts = datetime(2025, 1, 7, 20, 0, tzinfo=timezone.utc)

    intents = s.on_step(market_event=MagicMock(), portfolio=MagicMock(), state=state)
    assert intents == []
    kinds = s.events.kinds()
    assert "decision.started" in kinds
    assert "decision.completed" in kinds
    started_payload = next(p for k, p in s.events.events if k == "decision.started")
    completed_payload = next(p for k, p in s.events.events if k == "decision.completed")
    assert started_payload["decision_id"] == completed_payload["decision_id"]
    assert started_payload["seq"] == 0
    assert completed_payload["has_trade"] is False
    assert completed_payload["n_trades"] == 0


def test_weekly_llm_decision_seq_monotonic():
    """seq counter increments across successive decisions."""
    from datetime import datetime, timezone

    from trading.agents.strategy.weekly_llm import WeeklyLLMStrategy

    s = WeeklyLLMStrategy()
    s._rebalance_dates = {"2025-01-06", "2025-01-13"}
    s.events = FakeEmitter()
    s._call_llm = MagicMock(return_value=([], {}, {}, {}, {}))

    state1 = MagicMock()
    state1.current_ts = datetime(2025, 1, 6, 20, 0, tzinfo=timezone.utc)
    state2 = MagicMock()
    state2.current_ts = datetime(2025, 1, 13, 20, 0, tzinfo=timezone.utc)

    s.on_step(MagicMock(), MagicMock(), state1)
    s.on_step(MagicMock(), MagicMock(), state2)

    started_payloads = [p for k, p in s.events.events if k == "decision.started"]
    assert len(started_payloads) == 2
    assert started_payloads[0]["seq"] == 0
    assert started_payloads[1]["seq"] == 1


def test_paper_aligned_emits_skip_when_interval_not_elapsed():
    """paper_aligned: emits decision.skipped when interval not yet elapsed."""
    from datetime import datetime, timezone

    from trading.agents.strategy.paper_aligned import PaperAlignedStrategy

    s = PaperAlignedStrategy(decision_interval_minutes=15)
    s.events = FakeEmitter()
    # Force slow path with no positions + minutes_left large
    s._minutes_to_close = lambda state: 120  # far from close → slow
    s._steps_since_decision = 0  # below interval=15

    portfolio = MagicMock(positions=[])
    state = MagicMock()
    state.current_ts = datetime(2025, 1, 6, 18, 0, tzinfo=timezone.utc)
    state.current_date = "2025-01-06"
    state.step_id = 1
    me = MagicMock()
    me.bars = []

    intents = s.on_step(market_event=me, portfolio=portfolio, state=state)
    assert intents == []
    assert "decision.skipped" in s.events.kinds()


def test_paper_aligned_emits_phases_and_complete_on_neutral_intent():
    """paper_aligned: analysis phase emit + decision.completed when intent=neutral."""
    from datetime import datetime, timezone

    from trading.agents.strategy.paper_aligned import PaperAlignedStrategy

    s = PaperAlignedStrategy(decision_interval_minutes=0)
    s.events = FakeEmitter()
    s._minutes_to_close = lambda state: 120
    s._steps_since_decision = 0  # with interval=0 this passes >=
    s._record_cost = lambda **_: None
    s._call_analysis = MagicMock(
        return_value={"intent": "neutral", "reasoning": "", "confidence": 0.0},
    )

    portfolio = MagicMock(positions=[])
    state = MagicMock()
    state.current_ts = datetime(2025, 1, 6, 18, 0, tzinfo=timezone.utc)
    state.current_date = "2025-01-06"
    state.step_id = 1
    me = MagicMock()
    me.bars = []

    intents = s.on_step(market_event=me, portfolio=portfolio, state=state)
    assert intents == []
    kinds = s.events.kinds()
    assert "decision.started" in kinds
    assert "phase.completed" in kinds
    assert "decision.completed" in kinds
    # Only analysis phase should fire (neutral short-circuits)
    phase_events = [p for k, p in s.events.events if k == "phase.completed"]
    assert len(phase_events) == 1
    assert phase_events[0]["phase"] == "analysis"
