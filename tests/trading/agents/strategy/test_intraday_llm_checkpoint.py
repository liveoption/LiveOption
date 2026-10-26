import pytest

from trading.agents.strategy.intraday_llm import IntradayLLMStrategy


def _make_strategy() -> IntradayLLMStrategy:
    return IntradayLLMStrategy(
        decision_interval_minutes=15,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        dte_min=0,
        dte_max=7,
        max_positions=5,
    )


def test_save_load_round_trip_empty_state():
    s = _make_strategy()
    # Pre-on_session_start: only constructor-set state. _bar_buffer / _steps_since_decision
    # initialized in on_day_open / on_session_start respectively. We must not crash.
    s._bar_buffer = []
    s._steps_since_decision = 0
    state = s.save_checkpoint()
    assert isinstance(state, dict)
    assert "schema_version" in state
    assert set(state.keys()) == {"schema_version", "bar_buffer", "steps_since_decision"}
    assert state["schema_version"] == "1"

    s2 = _make_strategy()
    s2.load_checkpoint(state)
    assert s2._steps_since_decision == 0
    assert s2._bar_buffer == []


def test_save_load_round_trip_populated_state():
    s = _make_strategy()
    s._bar_buffer = [{"ts": "2025-01-15T09:30:00", "open": 480.0}]
    s._steps_since_decision = 3
    state = s.save_checkpoint()

    s2 = _make_strategy()
    s2.load_checkpoint(state)
    assert s2._steps_since_decision == 3
    assert s2._bar_buffer == [{"ts": "2025-01-15T09:30:00", "open": 480.0}]


def test_load_checkpoint_rejects_unknown_schema_version():
    s = _make_strategy()
    with pytest.raises(ValueError, match="schema_version"):
        s.load_checkpoint({"schema_version": "999", "bar_buffer": [], "steps_since_decision": 0})
