"""Exercise the real overlay analyzer → executor path, not a mocked executor."""
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest

from trading.agents.strategy.overlay import OverlayHedgeStrategy, OverlayProfitStrategy, OverlayHybridStrategy
from trading.core.config import RunConfig
from trading.core.schemas import BarData, MarketEvent, PortfolioSnapshot, PositionInfo
from trading.core.state import TradingState


@pytest.fixture
def market(monkeypatch):
    # Only external data lookups are stubbed; both prompt builders and phases run.
    monkeypatch.setattr("trading.tools.earnings_loader.build_daily_price_summary", lambda *_: "QQQ recent closes: 499, 500")
    timestamp = "2025-01-06T21:00:00Z"
    def bar(symbol, price, **extra):
        return BarData(symbol=symbol, open=price, high=price, low=price, close=price,
                       volume=1000, window_start=timestamp, **extra)
    event = MarketEvent(event_ts=timestamp, bars=[bar("QQQ", 500),
        bar("O:QQQ250221C00520000", 2, instrument_type="OPTION", iv=0.3, delta=0.3),
        bar("O:QQQ250221P00480000", 3, instrument_type="OPTION", iv=0.35, delta=-0.3)], bar_history={})
    portfolio = PortfolioSnapshot(cash=100000, equity=5100000, daily_pnl=0, daily_pnl_pct=0,
        positions=[PositionInfo(contract="QQQ", side="LONG", qty=10000, avg_entry=500,
                                mark_price=500, unrealized_pnl=0, dte=0, stale=False, stale_minutes=0)])
    config = RunConfig.model_validate({"experiment": {"run_id": "executor-regression", "start": "2025-01-06",
        "end": "2025-01-07", "universe": ["QQQ"], "frequency": "1d"},
        "strategy": {"name": "overlay_profit"}, "data_policy": {"news_enabled": False}})
    state = TradingState(config=config, run_id="executor-regression", episode_id="test",
                         current_date="2025-01-06", current_ts=datetime(2025, 1, 6, 21, tzinfo=timezone.utc))
    return event, portfolio, state


@pytest.mark.parametrize("strategy_class,right,action", [(OverlayProfitStrategy, "C", "sell"), (OverlayHedgeStrategy, "P", "buy")])
@pytest.mark.parametrize("custom", [False, True])
def test_real_overlay_executor_renders_default_and_custom_prompts(market, strategy_class, right, action, custom):
    templates = {"executor_system": "Custom executor: budget {MAX_COST}; contracts {MAX_CONTRACTS}.",
                 "executor_user": "Custom plan\n{ANALYSIS_JSON}\n{TRADABLE_CONTRACTS}\n{POSITIONS_SUMMARY}"} if custom else {}
    strategy = strategy_class(underlying="QQQ", stock_qty=10000, prompt_templates=templates,
        prompt_override="Avoid illiquid options.", prompt_addenda={"executor_user": "Explain the chosen contract.",
                                                                 "executor_system": "Use conservative sizing."})
    contract = f"O:QQQ250221{right}{'00520000' if right == 'C' else '00480000'}"
    llm = Mock(model="test-model", provider="mock")
    llm.call_raw.side_effect = [json.dumps({"intent": "bearish", "confidence": 0.64, "reasoning": "Risk has risen."}),
                               json.dumps([{"action": action, "contract": contract, "qty": 1, "order_type": "MARKET", "rationale": "Test plan"}])]
    strategy._llm = llm
    event, portfolio, state = market
    intents = strategy.on_step(event, portfolio, state)
    assert len(intents) == 1
    assert intents[0].contract == contract and intents[0].side == action.upper()
    assert llm.call_raw.call_count == 2
    system, user = llm.call_raw.call_args.args
    assert ("Custom executor" in system) is custom
    assert ("Custom plan" in user) is custom
    assert all(f"{{{key}}}" not in system + user for key in ("MAX_COST", "MAX_CONTRACTS", "ANALYSIS_JSON", "TRADABLE_CONTRACTS", "POSITIONS_SUMMARY"))
    assert "QQQ" in user and "bearish" in user and contract in user
    assert "Avoid illiquid options." in user and "Explain the chosen contract." in user
    assert "Use conservative sizing." in system and "Strategy Constraints" in system
    assert [call["phase"] for call in strategy._last_pipeline_calls] == ["analyzer", "executor"]
    assert strategy._prompt_templates == templates


@pytest.mark.parametrize("strategy_class", [OverlayProfitStrategy, OverlayHedgeStrategy])
def test_neutral_analysis_still_skips_executor(market, strategy_class):
    strategy = strategy_class(underlying="QQQ", stock_qty=10000)
    strategy._llm = Mock(model="test-model", provider="mock")
    strategy._llm.call_raw.return_value = '{"intent":"neutral","confidence":0.5,"reasoning":"No signal"}'
    assert strategy.on_step(*market) == []
    assert strategy._llm.call_raw.call_count == 1


def test_hybrid_runs_both_real_pipelines_with_distinct_artifacts_and_spread_groups(market):
    strategy = OverlayHybridStrategy(underlying="QQQ", stock_qty=10000)
    artifacts = []
    event, portfolio, state = market
    assert [s._trade_equity_pct for s, _ in strategy._sleeves] == [0.01, 0.01]
    for sleeve, right in strategy._sleeves:
        contract = f"O:QQQ250221{right}{'00520000' if right == 'C' else '00480000'}"
        sleeve._llm = Mock(model="test-model", provider="mock")
        sleeve._llm.call_raw.side_effect = [json.dumps({"intent": "bearish", "confidence": 0.64, "reasoning": "Risk."}),
            json.dumps([{"action": "sell" if right == "C" else "buy", "contract": contract,
                         "qty": 1, "order_type": "MARKET", "spread_group": "A"}])]
        sleeve.write_artifact = lambda **record: artifacts.append(record)
    intents = strategy.on_step(event, portfolio, state)
    assert len(intents) == 2
    assert {i.strategy_name for i in intents} == {"overlay_profit", "overlay_hedge"}
    assert {i.spread_group for i in intents} == {"covered_call:A", "hedge:A"}
    assert [a["seq"] for a in artifacts] == [0, 1]
    assert len({a["decision_id"] for a in artifacts}) == 2
    assert {a["trades"][0]["spread_group"] for a in artifacts} == {"covered_call:A", "hedge:A"}
    assert all(len(a["prompt"]["calls"]) == 2 for a in artifacts)
    saved = strategy.save_checkpoint()
    restored = OverlayHybridStrategy(underlying="QQQ", stock_qty=10000)
    restored.load_checkpoint(saved)
    assert restored.save_checkpoint() == saved
    assert restored._decision_seq == 2


def test_hybrid_expiry_management_does_not_close_the_other_sleeve_twice(market):
    event, portfolio, state = market
    expired = [PositionInfo(contract=f"O:QQQ250106{right}00500000", side="LONG", qty=1,
        avg_entry=1, mark_price=1, unrealized_pnl=0, dte=0, stale=False, stale_minutes=0,
        delta=0.8 if right == "C" else -0.8)
        for right in ("C", "P")]
    portfolio = portfolio.model_copy(update={"positions": portfolio.positions + expired})
    strategy = OverlayHybridStrategy(underlying="QQQ", stock_qty=10000)
    orders = strategy.on_step(event, portfolio, state)
    assert len(orders) == 2
    assert {order.contract for order in orders} == {position.contract for position in expired}
    assert len(portfolio.positions) == 3


def test_hybrid_preserves_both_sleeves_constraints_when_model_proposes_wrong_option_type(market):
    strategy = OverlayHybridStrategy(underlying="QQQ", stock_qty=10000)
    for sleeve, right in strategy._sleeves:
        # Deliberately send the wrong option side to both existing hard filters.
        contract = "O:QQQ250221P00480000" if right == "C" else "O:QQQ250221C00520000"
        sleeve._llm = Mock(model="test-model", provider="mock")
        sleeve._llm.call_raw.side_effect = ['{"intent":"bearish","confidence":0.6}',
            json.dumps([{"action":"buy", "contract":contract, "qty":1}])]
    assert strategy.on_step(*market) == []
