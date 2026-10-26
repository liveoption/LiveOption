from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from liveoption_web.runs.protocol import RunSpecV1, materialize
from trading.agents.strategy.intraday_llm import CustomDailyLLMStrategy


@pytest.mark.parametrize("day,utc_time", [("2025-01-06", "15:30"), ("2025-07-07", "14:30")])
def test_daily_schedule_et_dst_exact_minute_and_resume(day, utc_time):
    config = materialize(RunSpecV1(mode="custom", cadence="daily", decision_time_et="10:30", model_id="mock"), {},
                         SimpleNamespace(api_model="mock"), SimpleNamespace(provider="mock"), "scheduled")
    assert config.experiment.frequency == "1m"
    assert config.strategy.params["decision_time_et"] == "10:30"
    strategy = CustomDailyLLMStrategy(**config.strategy.params)
    strategy._call_llm = Mock(return_value=[])
    make_state = lambda clock: SimpleNamespace(current_ts=datetime.fromisoformat(f"{day}T{clock}:00+00:00"))
    state = make_state(utc_time)
    strategy.on_day_open(day, state)
    earlier = state.current_ts.replace(minute=29)
    strategy.on_step("earlier-visible-event", None, SimpleNamespace(current_ts=earlier))
    assert strategy._call_llm.call_count == 0
    strategy.on_step("decision-event", None, state)
    assert strategy._call_llm.call_count == 1
    assert strategy._call_llm.call_args.args[0] == ["earlier-visible-event", "decision-event"]
    strategy.on_step("duplicate-event", None, state)
    assert strategy._call_llm.call_count == 1
    saved = strategy.save_checkpoint()
    resumed = CustomDailyLLMStrategy(**config.strategy.params)
    resumed._call_llm = Mock(return_value=[])
    resumed.load_checkpoint(saved)
    resumed.on_step("resume-same-minute", None, state)
    resumed._call_llm.assert_not_called()
    resumed.on_day_open("2025-01-07", state)
    next_state = SimpleNamespace(current_ts=state.current_ts.replace(day=state.current_ts.day + 1))
    resumed.on_step("new-day-event", None, next_state)
    resumed._call_llm.assert_called_once()


@pytest.mark.parametrize("clock", ["09:29", "16:00", "25:00", "9:30", "10:30:00", "", "invalid"])
def test_reject_bad_scheduled_times(clock):
    with pytest.raises(ValueError, match="decision_time_et"):
        RunSpecV1(mode="custom", cadence="daily", decision_time_et=clock, model_id="mock")


def test_time_only_for_daily_and_missing_bar_does_not_shift_decision():
    with pytest.raises(ValueError, match="decision_time_et"):
        RunSpecV1(mode="custom", cadence="intraday", decision_time_et="10:30", model_id="mock")
    strategy = CustomDailyLLMStrategy(decision_time_et="15:45")
    strategy._call_llm = Mock()
    for clock in ("17:00", "19:44", "19:46"):
        strategy.on_step(None, None, SimpleNamespace(current_ts=datetime.fromisoformat(f"2025-07-07T{clock}:00+00:00")))
    strategy._call_llm.assert_not_called()
