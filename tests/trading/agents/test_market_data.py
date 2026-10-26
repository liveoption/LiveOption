"""Tests for MarketDataAgent — bar extraction, staleness tracking, forward-fill."""

import pytest

from trading.agents.market_data import InstrumentState, MarketDataAgent
from trading.core.config import DataPolicyConfig


# ---------------------------------------------------------------------------
# Helpers to build mock PMB step results
# ---------------------------------------------------------------------------

def _make_step(
    ts: str = "2025-01-06T09:31:00",
    stocks: list[dict] | None = None,
    options: list[dict] | None = None,
) -> dict:
    """Build a minimal PMB step result dict."""
    payload = {}
    if stocks is not None:
        payload["stocks"] = stocks
    if options is not None:
        payload["options"] = options
    return {
        "ok": True,
        "clock": {"current_ts": ts},
        "events": [
            {"type": "MARKET_TICK", "ts": ts, "payload": payload},
        ],
    }


def _spy_stock(close: float = 595.5) -> dict:
    return {
        "symbol": "SPY",
        "open": 595.0,
        "high": 596.0,
        "low": 594.0,
        "close": close,
        "volume": 1_000_000,
    }


def _spy_option(close: float = 2.3, contract: str = "O:SPY250106C00595000") -> dict:
    return {
        "contract": contract,
        "open": 2.0,
        "high": 2.5,
        "low": 1.8,
        "close": close,
        "volume": 500,
    }


# ---------------------------------------------------------------------------
# Test 1: Stock bars extracted
# ---------------------------------------------------------------------------

def test_process_step_stock_bars_extracted():
    """Stock bars from PMB step result are correctly parsed into BarData."""
    agent = MarketDataAgent()
    step = _make_step(stocks=[_spy_stock()])
    event = agent.process_step(step)

    stock_bars = [b for b in event.bars if b.instrument_type == "STOCK"]
    assert len(stock_bars) == 1

    bar = stock_bars[0]
    assert bar.symbol == "SPY"
    assert bar.open == 595.0
    assert bar.high == 596.0
    assert bar.low == 594.0
    assert bar.close == 595.5
    assert bar.volume == 1_000_000
    assert bar.stale is False
    assert bar.stale_minutes == 0
    assert bar.tradeable is True


# ---------------------------------------------------------------------------
# Test 2: Option bars extracted
# ---------------------------------------------------------------------------

def test_process_step_option_bars_extracted():
    """Option bars parsed with instrument_type='OPTION'."""
    agent = MarketDataAgent()
    step = _make_step(options=[_spy_option()])
    event = agent.process_step(step)

    option_bars = [b for b in event.bars if b.instrument_type == "OPTION"]
    assert len(option_bars) == 1

    bar = option_bars[0]
    assert bar.symbol == "O:SPY250106C00595000"
    assert bar.close == 2.3
    assert bar.instrument_type == "OPTION"
    assert bar.stale is False


# ---------------------------------------------------------------------------
# Test 3: Staleness — fresh bar
# ---------------------------------------------------------------------------

def test_staleness_fresh_bar():
    """Option bar present for a tracked contract -> stale=False, stale_minutes=0."""
    contract = "O:SPY250106C00595000"
    agent = MarketDataAgent()
    agent.register_contracts({contract})

    step = _make_step(options=[_spy_option()])
    event = agent.process_step(step)

    bar = next(b for b in event.bars if b.symbol == contract)
    assert bar.stale is False
    assert bar.stale_minutes == 0
    assert bar.tradeable is True


# ---------------------------------------------------------------------------
# Test 4: Staleness — missing bar forward-fill
# ---------------------------------------------------------------------------

def test_staleness_missing_bar_forward_fill():
    """Option bar missing for a tracked contract -> stale=True, close=last known."""
    contract = "O:SPY250106C00595000"
    agent = MarketDataAgent()
    agent.register_contracts({contract})

    # Step 1: option bar present
    step1 = _make_step(ts="2025-01-06T09:31:00", options=[_spy_option(close=2.3)])
    agent.process_step(step1)

    # Step 2: option bar missing
    step2 = _make_step(ts="2025-01-06T09:32:00", options=[])
    event2 = agent.process_step(step2)

    bar = next(b for b in event2.bars if b.symbol == contract)
    assert bar.stale is True
    assert bar.stale_minutes == 1
    assert bar.close == 2.3  # forward-filled from last known
    assert bar.volume == 0  # forward-filled bars have zero volume
    assert bar.tradeable is True  # still within max_stale_minutes


# ---------------------------------------------------------------------------
# Test 5: Staleness accumulates
# ---------------------------------------------------------------------------

def test_staleness_accumulates():
    """3 consecutive missing bars -> stale_minutes=3."""
    contract = "O:SPY250106C00595000"
    agent = MarketDataAgent()
    agent.register_contracts({contract})

    # Step 1: bar present
    step1 = _make_step(ts="2025-01-06T09:31:00", options=[_spy_option(close=2.3)])
    agent.process_step(step1)

    # Steps 2-4: bar missing
    for minute in range(32, 35):
        step = _make_step(ts=f"2025-01-06T09:{minute}:00", options=[])
        event = agent.process_step(step)

    bar = next(b for b in event.bars if b.symbol == contract)
    assert bar.stale is True
    assert bar.stale_minutes == 3
    assert bar.close == 2.3


# ---------------------------------------------------------------------------
# Test 6: Staleness resets on fresh bar
# ---------------------------------------------------------------------------

def test_staleness_resets_on_fresh_bar():
    """Fresh bar after stale period -> stale=False, stale_minutes=0."""
    contract = "O:SPY250106C00595000"
    agent = MarketDataAgent()
    agent.register_contracts({contract})

    # Step 1: bar present
    step1 = _make_step(ts="2025-01-06T09:31:00", options=[_spy_option(close=2.3)])
    agent.process_step(step1)

    # Step 2: bar missing -> stale
    step2 = _make_step(ts="2025-01-06T09:32:00", options=[])
    event2 = agent.process_step(step2)
    bar2 = next(b for b in event2.bars if b.symbol == contract)
    assert bar2.stale is True
    assert bar2.stale_minutes == 1

    # Step 3: bar present again -> fresh
    step3 = _make_step(ts="2025-01-06T09:33:00", options=[_spy_option(close=2.5)])
    event3 = agent.process_step(step3)
    bar3 = next(b for b in event3.bars if b.symbol == contract)
    assert bar3.stale is False
    assert bar3.stale_minutes == 0
    assert bar3.close == 2.5


# ---------------------------------------------------------------------------
# Test 7: Untradeable after max stale
# ---------------------------------------------------------------------------

def test_untradeable_after_max_stale():
    """stale_minutes > max_stale_minutes -> tradeable=False."""
    contract = "O:SPY250106C00595000"
    policy = DataPolicyConfig(max_stale_minutes=3)
    agent = MarketDataAgent(data_policy=policy)
    agent.register_contracts({contract})

    # Step 1: bar present
    step1 = _make_step(ts="2025-01-06T09:31:00", options=[_spy_option()])
    agent.process_step(step1)

    # Steps 2-5: 4 consecutive missing bars (exceeds max_stale=3)
    for minute in range(32, 36):
        step = _make_step(ts=f"2025-01-06T09:{minute}:00", options=[])
        event = agent.process_step(step)

    bar = next(b for b in event.bars if b.symbol == contract)
    assert bar.stale is True
    assert bar.stale_minutes == 4
    assert bar.tradeable is False  # 4 > max_stale=3


# ---------------------------------------------------------------------------
# Test 8: register_contracts
# ---------------------------------------------------------------------------

def test_register_contracts():
    """register_contracts() starts tracking new option contracts."""
    agent = MarketDataAgent()
    contracts = {"O:SPY250106C00595000", "O:SPY250106P00590000"}
    agent.register_contracts(contracts)

    assert contracts == agent._tracked_contracts

    # After processing a step with no option bars, tracked contracts
    # should NOT produce forward-fills if they've never had a bar
    step = _make_step(ts="2025-01-06T09:31:00", options=[])
    event = agent.process_step(step)
    option_bars = [b for b in event.bars if b.instrument_type == "OPTION"]
    assert len(option_bars) == 0  # no previous bar to forward-fill


# ---------------------------------------------------------------------------
# Test 9: Bar history maintained
# ---------------------------------------------------------------------------

def test_bar_history_maintained():
    """Ring buffer keeps bars for instruments across steps."""
    agent = MarketDataAgent()

    # Process 3 steps with stock bars
    for minute in range(31, 34):
        step = _make_step(
            ts=f"2025-01-06T09:{minute}:00",
            stocks=[_spy_stock(close=595.0 + minute - 31)],
        )
        agent.process_step(step)

    # Check history via MarketEvent
    step = _make_step(ts="2025-01-06T09:34:00", stocks=[_spy_stock(close=598.0)])
    event = agent.process_step(step)

    assert "SPY" in event.bar_history
    assert len(event.bar_history["SPY"]) == 4

    # Also check via get_history
    history = agent.get_history("SPY")
    assert len(history) == 4
    assert history[0].close == 595.0
    assert history[-1].close == 598.0

    # get_history with n parameter
    last_2 = agent.get_history("SPY", n=2)
    assert len(last_2) == 2
    assert last_2[0].close == 597.0
    assert last_2[1].close == 598.0


# ---------------------------------------------------------------------------
# Test 10: Bar history trimmed
# ---------------------------------------------------------------------------

def test_bar_history_trimmed():
    """Buffer doesn't grow beyond lookback_bars."""
    policy = DataPolicyConfig(lookback_bars=5)
    agent = MarketDataAgent(data_policy=policy)

    # Process 10 steps
    for i in range(10):
        step = _make_step(
            ts=f"2025-01-06T09:{31 + i}:00",
            stocks=[_spy_stock(close=595.0 + i)],
        )
        agent.process_step(step)

    history = agent.get_history("SPY")
    assert len(history) == 5  # capped at lookback_bars=5

    # Should contain the last 5 bars (indices 5-9)
    assert history[0].close == 600.0  # 595.0 + 5
    assert history[-1].close == 604.0  # 595.0 + 9


# ---------------------------------------------------------------------------
# Test 11: unregister_contracts removes tracking
# ---------------------------------------------------------------------------

def test_unregister_contracts_removes_tracking():
    """After unregister, contract is no longer tracked or forward-filled."""
    contract = "O:SPY250106C00595000"
    agent = MarketDataAgent()
    agent.register_contracts({contract})

    # Step 1: option bar present — populates state and history
    step1 = _make_step(ts="2025-01-06T09:31:00", options=[_spy_option(close=2.3)])
    agent.process_step(step1)

    assert contract in agent._tracked_contracts
    assert contract in agent._instrument_state
    assert contract in agent._bar_history

    # Unregister the contract
    agent.unregister_contracts({contract})

    assert contract not in agent._tracked_contracts
    assert contract not in agent._instrument_state
    assert contract not in agent._bar_history

    # Step 2: no option bar — should NOT produce a forward-filled bar
    step2 = _make_step(ts="2025-01-06T09:32:00", options=[])
    event2 = agent.process_step(step2)

    option_bars = [b for b in event2.bars if b.instrument_type == "OPTION"]
    assert len(option_bars) == 0  # no forward-fill because contract was unregistered
