"""Argv composition for the web launcher (Bug B fix tests)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from liveoption_web.api.launch import (
    LaunchRunOverrides,
    _compose_argv,
    _quarter_to_dates,
    _seed_row_fields,
)
from liveoption_web.runs.catalog import SCENARIOS
from liveoption_web.runs.families import IntradayOverrides


@pytest.fixture
def earnings_scenario():
    return SCENARIOS["earnings_2025"]


def test_overrides_accepts_tickers_and_quarter():
    ov = LaunchRunOverrides(tickers=["AAPL", "MSFT"], quarter="2025Q3")
    assert ov.tickers == ["AAPL", "MSFT"]
    assert ov.quarter == "2025Q3"


def test_overrides_rejects_invalid_quarter():
    with pytest.raises(Exception):
        LaunchRunOverrides(quarter="2025Q5")


def test_quarter_to_dates_q1():
    s, e = _quarter_to_dates("2025Q1")
    assert (s, e) == (date(2025, 1, 1), date(2025, 3, 31))


def test_quarter_to_dates_q2():
    s, e = _quarter_to_dates("2025Q2")
    assert (s, e) == (date(2025, 4, 1), date(2025, 6, 30))


def test_quarter_to_dates_q3():
    s, e = _quarter_to_dates("2025Q3")
    assert (s, e) == (date(2025, 7, 1), date(2025, 9, 30))


def test_quarter_to_dates_q4():
    s, e = _quarter_to_dates("2025Q4")
    assert (s, e) == (date(2025, 10, 1), date(2025, 12, 31))


def test_compose_argv_forwards_tickers(earnings_scenario):
    ov = LaunchRunOverrides(tickers=["AAPL", "MSFT"])
    argv = _compose_argv(earnings_scenario, ov, "run_x", None, "http://job:1/")
    assert "--tickers" in argv
    idx = argv.index("--tickers")
    assert argv[idx + 1] == "AAPL,MSFT"


def test_compose_argv_quarter_maps_to_start_end(earnings_scenario):
    ov = LaunchRunOverrides(quarter="2025Q3")
    argv = _compose_argv(earnings_scenario, ov, "run_x", None, "http://job:1/")
    assert "--start" in argv and "--end" in argv
    assert argv[argv.index("--start") + 1] == "2025-07-01"
    assert argv[argv.index("--end") + 1] == "2025-09-30"
    assert "--quarter" not in argv  # never passed to CLI


def test_compose_argv_explicit_dates_win_over_quarter(earnings_scenario):
    ov = LaunchRunOverrides(
        quarter="2025Q3",
        start=date(2025, 8, 15),
        end=date(2025, 8, 20),
    )
    argv = _compose_argv(earnings_scenario, ov, "run_x", None, "http://job:1/")
    assert argv[argv.index("--start") + 1] == "2025-08-15"
    assert argv[argv.index("--end") + 1] == "2025-08-20"


def test_compose_argv_no_overrides(earnings_scenario):
    ov = LaunchRunOverrides()
    argv = _compose_argv(earnings_scenario, ov, "run_x", None, "http://job:1/")
    # No date / ticker flags when nothing overridden.
    assert "--start" not in argv
    assert "--end" not in argv
    assert "--tickers" not in argv
    # Sanity: required scaffolding still present.
    assert "--config" in argv
    assert "--web-ingest" in argv
    assert "--run-id" in argv


def test_intraday_settings_are_forwarded_to_cli():
    overrides = IntradayOverrides(
        decision_interval_minutes=30,
        max_concurrent_positions=2,
        dte_range=(0, 1),
        llm_model="gemini-3-flash",
    )
    argv = _compose_argv(
        SCENARIOS["intraday_spy_2025_q1"], overrides,
        "run_x", None, "http://job:1/",
    )
    assert argv[argv.index("--decision-interval-minutes") + 1] == "30"
    assert argv[argv.index("--max-concurrent-positions") + 1] == "2"
    assert argv[argv.index("--dte-range") + 1] == "0,1"
    assert argv[argv.index("--llm-model") + 1] == "gemini-3-flash"


def test_model_override_updates_seeded_provider(tmp_path):
    seed = _seed_row_fields(
        SCENARIOS["intraday_spy_2025_q1"],
        IntradayOverrides(llm_model="gemini-3-flash"),
        "run_x",
        tmp_path,
    )
    assert seed["model_id"] == "gemini-3-flash"
    assert seed["provider"] == "anthropic"
