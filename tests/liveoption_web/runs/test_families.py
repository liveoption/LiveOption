"""Phase C1 — Family registry + Pydantic override models.

Verifies extra='forbid' rejection of unknown fields, and that LLM and
family-specific fields are scoped to the right override models per
spec §2.3.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from liveoption_web.runs.families import (
    FAMILY_REGISTRY,
    Family,
    FamilySpec,
    EarningsOverrides,
    IntradayOverrides,
    NullFamilyOverrides,
    OverlayOverrides,
)


# ---------------------------------------------------------------------------
# extra='forbid' on every override model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model_cls",
    [OverlayOverrides, EarningsOverrides, IntradayOverrides, NullFamilyOverrides],
)
def test_unknown_field_rejected(model_cls):
    with pytest.raises(ValidationError):
        model_cls.model_validate({"definitely_not_a_field": 1})


# ---------------------------------------------------------------------------
# llm_model and prompt_override are accepted on overlay & earnings
# (paper §3: model is selectable at launch time for all families)
# ---------------------------------------------------------------------------

def test_overlay_accepts_llm_model():
    m = OverlayOverrides.model_validate({"llm_model": "deepseek-chat"})
    assert m.llm_model == "deepseek-chat"


def test_overlay_accepts_prompt_override():
    m = OverlayOverrides.model_validate({"prompt_override": "hi"})
    assert m.prompt_override == "hi"


def test_earnings_accepts_llm_model():
    m = EarningsOverrides.model_validate({"llm_model": "deepseek-chat"})
    assert m.llm_model == "deepseek-chat"


def test_earnings_accepts_prompt_override():
    m = EarningsOverrides.model_validate({"prompt_override": "hi"})
    assert m.prompt_override == "hi"


# ---------------------------------------------------------------------------
# family-specific fields don't bleed into other families
# ---------------------------------------------------------------------------

def test_overlay_rejects_tickers():
    with pytest.raises(ValidationError):
        OverlayOverrides.model_validate({"tickers": ["AAPL"]})


def test_intraday_rejects_tickers():
    with pytest.raises(ValidationError):
        IntradayOverrides.model_validate({"tickers": ["AAPL"]})


def test_earnings_rejects_underlying_holdings():
    with pytest.raises(ValidationError):
        EarningsOverrides.model_validate(
            {"underlying_holdings": [{"symbol": "SPY", "shares": 1, "cost_basis": 1.0}]}
        )


def test_intraday_rejects_underlying_holdings():
    with pytest.raises(ValidationError):
        IntradayOverrides.model_validate(
            {"underlying_holdings": [{"symbol": "SPY", "shares": 1, "cost_basis": 1.0}]}
        )


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------

def test_overlay_accepts_valid_overrides():
    m = OverlayOverrides.model_validate(
        {
            "start": "2025-01-02",
            "end": "2025-03-31",
            "bankroll": 100000,
            "underlying_holdings": [
                {"symbol": "SPY", "shares": 100, "cost_basis": 500.0}
            ],
            "overlay_dte_range": [7, 45],
        }
    )
    assert m.underlying_holdings[0].symbol == "SPY"
    assert m.overlay_dte_range == (7, 45)


def test_earnings_accepts_valid_overrides():
    m = EarningsOverrides.model_validate(
        {
            "tickers": ["AAPL", "MSFT"],
            "event_window": [-2, 2],
            "quarter": "2025Q1",
        }
    )
    assert m.quarter == "2025Q1"


def test_intraday_accepts_llm_and_intraday_fields():
    m = IntradayOverrides.model_validate(
        {
            "llm_model": "deepseek-chat",
            "decision_interval_minutes": 15,
            "max_concurrent_positions": 5,
            "dte_range": [0, 7],
        }
    )
    assert m.llm_model == "deepseek-chat"
    assert m.decision_interval_minutes == 15


def test_null_family_accepts_llm_fields():
    m = NullFamilyOverrides.model_validate({"llm_model": "deepseek-chat"})
    assert m.llm_model == "deepseek-chat"


# ---------------------------------------------------------------------------
# SUPPORTED_UNDERLYINGS allowlist validators (paper §E)
# ---------------------------------------------------------------------------

def test_holding_spec_rejects_unsupported_symbol():
    from liveoption_web.runs.families import HoldingSpec
    with pytest.raises(ValueError, match="not in supported underlyings"):
        HoldingSpec(symbol="ZZZZ", shares=100, cost_basis=10.0)


def test_holding_spec_accepts_paper_symbols():
    from liveoption_web.runs.families import HoldingSpec
    for sym in ["QQQ", "NVDA", "SPY", "AAPL"]:
        HoldingSpec(symbol=sym, shares=100, cost_basis=10.0)


def test_earnings_overrides_rejects_unsupported_ticker():
    from liveoption_web.runs.families import EarningsOverrides
    with pytest.raises(ValueError, match="not in supported underlyings"):
        EarningsOverrides(tickers=["AAPL", "ZZZZ"])


def test_earnings_overrides_accepts_paper_subset():
    from liveoption_web.runs.families import EarningsOverrides
    EarningsOverrides(tickers=["AAPL", "MSFT", "NVDA"])


# ---------------------------------------------------------------------------
# registry shape
# ---------------------------------------------------------------------------

def test_family_registry_has_all_three():
    assert set(FAMILY_REGISTRY.keys()) == {Family.OVERLAY, Family.EARNINGS, Family.INTRADAY}
    for spec in FAMILY_REGISTRY.values():
        assert isinstance(spec, FamilySpec)
        assert spec.evaluator_module
