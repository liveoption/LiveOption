"""Shared-pool concurrency cap: forks + batches share max_concurrent_runs."""
from liveoption_web.config import get_settings
from liveoption_web.runs.supervisor import RunCapExceeded


def test_setting_is_max_concurrent_runs():
    s = get_settings()
    assert hasattr(s, "max_concurrent_runs")
    assert s.max_concurrent_runs >= 1


def test_run_cap_exception_exists():
    err = RunCapExceeded("cap hit")
    assert isinstance(err, RuntimeError)
    assert str(err) == "cap hit"
