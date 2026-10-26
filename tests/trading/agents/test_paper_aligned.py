"""Tests for PaperAlignedStrategy: three-phase multi-agent pipeline.

7 test cases:
1. test_analysis_bullish_triggers_trader — bullish intent -> trader called -> intents
2. test_analysis_neutral_skips_trader — neutral -> no trader call, empty intents
3. test_analysis_exit_all_flattens — exit_all -> flatten all positions
4. test_reflection_removes_invalid — reflection filters bad orders
5. test_reflection_disabled — enable_reflection=False -> skip phase 3
6. test_three_phase_pipeline — full pipeline: analysis -> trader -> reflection
7. test_fast_path_overrides_slow — near close -> fast path regardless of interval
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from trading.core.schemas import (
    BarData,
    MarketEvent,
    PortfolioSnapshot,
    PositionInfo,
    TradeIntent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market_event(
    ts: str = "2025-01-06T10:00:00",
    option_bars: list[BarData] | None = None,
) -> MarketEvent:
    bars = option_bars or [
        BarData(
            symbol="O:SPY250106C00595000",
            open=2.0,
            high=2.5,
            low=1.9,
            close=2.3,
            volume=500,
            window_start=ts,
            instrument_type="OPTION",
            delta=0.45,
            iv=0.20,
        ),
    ]
    return MarketEvent(event_ts=ts, bars=bars, bar_history={})


def _make_portfolio(
    cash: float = 90_000.0,
    equity: float = 100_000.0,
    positions: list[PositionInfo] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=cash,
        equity=equity,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        positions=positions or [],
    )


def _make_position(
    contract: str = "O:SPY250106C00595000",
    side: str = "LONG",
    qty: int = 5,
    avg_entry: float = 2.30,
    mark_price: float = 2.50,
) -> PositionInfo:
    return PositionInfo(
        contract=contract,
        side=side,
        qty=qty,
        avg_entry=avg_entry,
        mark_price=mark_price,
        unrealized_pnl=(mark_price - avg_entry) * qty * 100,
        dte=0,
        stale=False,
        stale_minutes=0,
    )


def _make_state(
    current_ts: str = "2025-01-06T10:00:00",
    step_id: int = 15,
):
    """Create a minimal TradingState-like mock with market hours timestamp."""
    from trading.core.time_contract import utc_from_iso
    state = MagicMock()
    ts_iso = current_ts if current_ts.endswith("Z") or "+" in current_ts else current_ts + "+00:00"
    state.current_ts = utc_from_iso(ts_iso)
    state.current_date = current_ts[:10]
    state.step_id = step_id
    state.errors = []
    return state


class MockLLMClient:
    """Mock LLM that returns predefined responses for each successive call_raw."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._call_idx = 0
        self.call_count = 0

    def call_raw(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
            self._call_idx += 1
            return resp
        return "[]"


def _make_strategy(
    interval: int = 1,
    enable_reflection: bool = True,
    fast_threshold: int = 15,
):
    """Build a PaperAlignedStrategy with configurable parameters."""
    from trading.agents.strategy.paper_aligned import PaperAlignedStrategy

    strat = PaperAlignedStrategy(
        llm_provider="mock",
        decision_interval_minutes=interval,
        enable_reflection=enable_reflection,
        fast_threshold_minutes=fast_threshold,
    )
    strat._universe = ["SPY"]
    return strat


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPaperAlignedStrategy:

    def test_analysis_bullish_triggers_trader(self):
        """Bullish intent from analysis -> trader called -> intents returned."""
        analysis_resp = json.dumps({
            "intent": "bullish",
            "confidence": 0.8,
            "reasoning": "uptrend",
            "target_instruments": ["O:SPY250106C00595000"],
        })
        trader_resp = json.dumps([
            {
                "action": "buy",
                "contract": "O:SPY250106C00595000",
                "qty": 2,
                "order_type": "MARKET",
                "rationale": "follow bullish signal",
            },
        ])
        reflection_resp = json.dumps({
            "valid_orders": [
                {
                    "action": "buy",
                    "contract": "O:SPY250106C00595000",
                    "qty": 2,
                    "order_type": "MARKET",
                    "rationale": "follow bullish signal",
                },
            ],
            "rejected": [],
            "notes": "all ok",
        })

        strat = _make_strategy(interval=1, enable_reflection=True)
        mock_llm = MockLLMClient([analysis_resp, trader_resp, reflection_resp])
        strat._llm = mock_llm

        # Use a time far from close so slow path is chosen
        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=15)
        intents = strat.on_step(
            _make_market_event(), _make_portfolio(), state,
        )

        assert mock_llm.call_count == 3  # analysis + trader + reflection
        assert len(intents) == 1
        assert intents[0].contract == "O:SPY250106C00595000"
        assert intents[0].side == "BUY"
        assert intents[0].qty == 2

    def test_analysis_neutral_skips_trader(self):
        """Neutral intent -> no trader call, empty intents."""
        analysis_resp = json.dumps({
            "intent": "neutral",
            "confidence": 0.3,
            "reasoning": "sideways market",
            "target_instruments": [],
        })

        strat = _make_strategy(interval=1, enable_reflection=True)
        mock_llm = MockLLMClient([analysis_resp])
        strat._llm = mock_llm

        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=15)
        intents = strat.on_step(
            _make_market_event(), _make_portfolio(), state,
        )

        assert mock_llm.call_count == 1  # only analysis
        assert intents == []

    def test_analysis_exit_all_flattens(self):
        """exit_all intent -> flatten all positions (1 LLM call only)."""
        analysis_resp = json.dumps({
            "intent": "exit_all",
            "confidence": 0.9,
            "reasoning": "major risk event",
            "target_instruments": [],
        })

        pos1 = _make_position(contract="O:SPY250106C00595000", side="LONG", qty=5)
        pos2 = _make_position(contract="O:QQQ250106P00500000", side="LONG", qty=3)
        portfolio = _make_portfolio(positions=[pos1, pos2])

        strat = _make_strategy(interval=1)
        mock_llm = MockLLMClient([analysis_resp])
        strat._llm = mock_llm

        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=15)
        intents = strat.on_step(
            _make_market_event(), portfolio, state,
        )

        assert mock_llm.call_count == 1  # only analysis
        assert len(intents) == 2
        # Both should be SELL (closing LONG positions)
        for intent in intents:
            assert intent.side == "SELL"
            assert "exit_all" in intent.rationale

    def test_reflection_removes_invalid(self):
        """Reflection filters out bad orders from the trade plan."""
        analysis_resp = json.dumps({
            "intent": "bearish",
            "confidence": 0.7,
            "reasoning": "downtrend",
            "target_instruments": [],
        })
        trader_resp = json.dumps([
            {
                "action": "buy",
                "contract": "O:SPY250106P00590000",
                "qty": 2,
                "order_type": "MARKET",
                "rationale": "put for downtrend",
            },
            {
                "action": "buy",
                "contract": "O:SPY250106C00600000",
                "qty": 10,
                "order_type": "MARKET",
                "rationale": "contradictory call",
            },
        ])
        # Reflection keeps only the first order
        reflection_resp = json.dumps({
            "valid_orders": [
                {
                    "action": "buy",
                    "contract": "O:SPY250106P00590000",
                    "qty": 2,
                    "order_type": "MARKET",
                    "rationale": "put for downtrend",
                },
            ],
            "rejected": [
                {
                    "action": "buy",
                    "contract": "O:SPY250106C00600000",
                    "qty": 10,
                    "order_type": "MARKET",
                    "rationale": "contradictory call",
                },
            ],
            "notes": "Rejected contradictory call buy",
        })

        strat = _make_strategy(interval=1, enable_reflection=True)
        mock_llm = MockLLMClient([analysis_resp, trader_resp, reflection_resp])
        strat._llm = mock_llm

        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=15)
        intents = strat.on_step(
            _make_market_event(), _make_portfolio(), state,
        )

        assert mock_llm.call_count == 3
        assert len(intents) == 1
        assert intents[0].contract == "O:SPY250106P00590000"

    def test_reflection_disabled(self):
        """enable_reflection=False -> skip phase 3, only 2 LLM calls."""
        analysis_resp = json.dumps({
            "intent": "bullish",
            "confidence": 0.8,
            "reasoning": "uptrend",
            "target_instruments": [],
        })
        trader_resp = json.dumps([
            {
                "action": "buy",
                "contract": "O:SPY250106C00595000",
                "qty": 2,
                "order_type": "MARKET",
                "rationale": "follow signal",
            },
        ])

        strat = _make_strategy(interval=1, enable_reflection=False)
        mock_llm = MockLLMClient([analysis_resp, trader_resp])
        strat._llm = mock_llm

        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=15)
        intents = strat.on_step(
            _make_market_event(), _make_portfolio(), state,
        )

        assert mock_llm.call_count == 2  # analysis + trader (no reflection)
        assert len(intents) == 1
        assert intents[0].contract == "O:SPY250106C00595000"

    def test_three_phase_pipeline(self):
        """Full pipeline: analysis -> trader -> reflection, 3 LLM calls."""
        analysis_resp = json.dumps({
            "intent": "bullish",
            "confidence": 0.85,
            "reasoning": "strong uptrend",
            "target_instruments": ["O:SPY250106C00595000"],
        })
        trader_resp = json.dumps([
            {
                "action": "buy",
                "contract": "O:SPY250106C00595000",
                "qty": 3,
                "order_type": "MARKET",
                "rationale": "follow trend",
            },
        ])
        reflection_resp = json.dumps({
            "valid_orders": [
                {
                    "action": "buy",
                    "contract": "O:SPY250106C00595000",
                    "qty": 3,
                    "order_type": "MARKET",
                    "rationale": "follow trend",
                },
            ],
            "rejected": [],
            "notes": "validated",
        })

        strat = _make_strategy(interval=1, enable_reflection=True)
        mock_llm = MockLLMClient([analysis_resp, trader_resp, reflection_resp])
        strat._llm = mock_llm

        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=15)
        intents = strat.on_step(
            _make_market_event(), _make_portfolio(), state,
        )

        # Verify all three phases called
        assert mock_llm.call_count == 3
        assert len(intents) == 1
        assert isinstance(intents[0], TradeIntent)
        assert intents[0].strategy_name == "paper_aligned"

        # Verify cost was recorded
        assert len(strat._step_costs) == 1
        cost = strat._step_costs[0]
        assert cost.path == "slow"
        assert cost.llm_calls == 3

    def test_fast_path_overrides_slow(self):
        """Near close -> fast path regardless of interval, 0 LLM calls."""
        strat = _make_strategy(interval=1, enable_reflection=True, fast_threshold=15)
        mock_llm = MockLLMClient([])
        strat._llm = mock_llm

        # 20:50 UTC is 15:50 ET in January: 10 min to close.
        state = _make_state(current_ts="2025-01-06T20:50:00+00:00", step_id=15)

        # Add a losing position for fast path to exit
        losing_pos = _make_position(
            contract="O:SPY250106C00595000",
            avg_entry=3.00,
            mark_price=2.00,  # -33% loss
        )
        portfolio = _make_portfolio(positions=[losing_pos])

        intents = strat.on_step(
            _make_market_event(), portfolio, state,
        )

        assert mock_llm.call_count == 0  # no LLM calls
        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert intents[0].rationale == "fast_path: pre-close exit"

        # Verify cost was recorded as fast path
        assert len(strat._step_costs) == 1
        assert strat._step_costs[0].path == "fast"

    def test_interval_gating(self):
        """Steps before interval elapses return empty, no LLM calls."""
        strat = _make_strategy(interval=3, enable_reflection=True)
        mock_llm = MockLLMClient([])
        strat._llm = mock_llm

        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=1)
        portfolio = _make_portfolio()
        event = _make_market_event()

        # Steps 1 and 2: not at interval yet
        for i in range(2):
            state.step_id = i + 1
            result = strat.on_step(event, portfolio, state)
            assert result == []

        assert mock_llm.call_count == 0

    def test_flatten_all_short_positions(self):
        """exit_all on SHORT positions generates BUY intents."""
        analysis_resp = json.dumps({
            "intent": "exit_all",
            "confidence": 0.95,
            "reasoning": "emergency exit",
            "target_instruments": [],
        })

        short_pos = _make_position(
            contract="O:SPY250106P00590000",
            side="SHORT",
            qty=-3,
            avg_entry=2.00,
            mark_price=2.50,
        )
        portfolio = _make_portfolio(positions=[short_pos])

        strat = _make_strategy(interval=1)
        mock_llm = MockLLMClient([analysis_resp])
        strat._llm = mock_llm

        state = _make_state(current_ts="2025-01-06T15:00:00+00:00", step_id=15)
        intents = strat.on_step(
            _make_market_event(), portfolio, state,
        )

        assert len(intents) == 1
        assert intents[0].side == "BUY"  # close SHORT by buying
        assert intents[0].qty == 3

    def test_reflection_failure_rejects_all_orders(self):
        """Reflection LLM failure returns empty list (fail-closed)."""
        strat = _make_strategy(enable_reflection=True)
        trade_plan = [{"action": "buy", "contract": "O:SPY250106C00595000", "qty": 5}]
        portfolio = _make_portfolio()
        state = _make_state()

        with patch.object(strat, "_llm") as mock_llm:
            mock_llm.call_raw.side_effect = Exception("LLM service unavailable")
            result = strat._call_reflection(trade_plan, portfolio, state)

        assert result == []
