"""REQUIRED_FREQUENCIES class attribute on BaseStrategy + concrete strategies."""

from __future__ import annotations


def test_base_strategy_default_is_none():
    from trading.agents.strategy.base import BaseStrategy
    assert BaseStrategy.REQUIRED_FREQUENCIES is None


def test_earnings_bet_requires_1m():
    from trading.agents.strategy.earnings_bet import EarningsBetStrategy
    assert EarningsBetStrategy.REQUIRED_FREQUENCIES == frozenset({"1m"})


def test_intraday_llm_requires_1m():
    from trading.agents.strategy.intraday_llm import IntradayLLMStrategy
    assert IntradayLLMStrategy.REQUIRED_FREQUENCIES == frozenset({"1m"})


def test_overlay_profit_requires_1d():
    from trading.agents.strategy.overlay import OverlayProfitStrategy
    assert OverlayProfitStrategy.REQUIRED_FREQUENCIES == frozenset({"1d"})


def test_overlay_hedge_requires_1d():
    from trading.agents.strategy.overlay import OverlayHedgeStrategy
    assert OverlayHedgeStrategy.REQUIRED_FREQUENCIES == frozenset({"1d"})


def test_weekly_llm_requires_1d():
    from trading.agents.strategy.weekly_llm import WeeklyLLMStrategy
    assert WeeklyLLMStrategy.REQUIRED_FREQUENCIES == frozenset({"1d"})


def test_paper_aligned_unconstrained():
    from trading.agents.strategy.paper_aligned import PaperAlignedStrategy
    assert PaperAlignedStrategy.REQUIRED_FREQUENCIES is None
