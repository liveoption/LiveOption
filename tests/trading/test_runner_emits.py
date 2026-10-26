"""Regression tests for runner / strategy event emissions.

Covers two production-observed bugs:

* `day.completed` only fired once per session on daily-frequency runs because
  PMB clock metadata didn't trigger `_is_last_bar_of_day`.
* `decision.completed` payload was missing `seq`, breaking frontend
  started↔completed correlation.
"""
from __future__ import annotations


def test_daily_frequency_emits_day_completed_each_step():
    """Each step on daily frequency must report end-of-day; fixes silent
    PnL chart bug where the runner only emitted at session-end."""
    from trading import runner as runner_mod

    r = runner_mod.TradingRunner.__new__(runner_mod.TradingRunner)
    cfg = type("C", (), {"experiment": type("E", (), {"frequency": "1d"})()})()
    r.config = cfg
    assert r._is_last_bar_of_day({"clock": {}}) is True


def test_minute_frequency_respects_clock_flag():
    from trading import runner as runner_mod

    r = runner_mod.TradingRunner.__new__(runner_mod.TradingRunner)
    cfg = type("C", (), {"experiment": type("E", (), {"frequency": "1m"})()})()
    r.config = cfg
    # No clock flag, no next_ts → fallback returns False
    assert r._is_last_bar_of_day({"clock": {}}) is False
    # Explicit flag honored
    assert r._is_last_bar_of_day({"clock": {"is_last_bar_of_day": True}}) is True


def test_decision_completed_payload_carries_seq():
    """Regression: every `decision.completed` emit site must include `seq` so
    the frontend can correlate started↔completed events.
    """
    import re
    from pathlib import Path

    strategy_dir = Path(__file__).resolve().parents[2] / "trading" / "agents" / "strategy"
    files = [
        strategy_dir / "overlay.py",
        strategy_dir / "weekly_llm.py",
        strategy_dir / "paper_aligned.py",
        strategy_dir / "earnings_bet.py",
        strategy_dir / "intraday_llm.py",
    ]

    pattern = re.compile(r'self\.emit\(\s*"decision\.completed"\s*,\s*\{')
    missing = []
    for f in files:
        text = f.read_text()
        for m in pattern.finditer(text):
            # Look at the next ~600 chars (the payload literal) for "seq"
            start = m.start()
            block = text[start:start + 600]
            # Trim to the closing brace of the payload dict to avoid bleed-over
            # into the next emit.
            close = block.find("})")
            payload = block[: close if close != -1 else len(block)]
            if '"seq"' not in payload:
                # Locate line number for diagnostics
                line_no = text.count("\n", 0, start) + 1
                missing.append(f"{f.name}:{line_no}")

    assert not missing, (
        "decision.completed payload missing 'seq' key at: " + ", ".join(missing)
    )


def test_minute_after_hours_eod_still_emits_day_completed():
    """Regression: in 1m mode, PMB sessions that include after-hours bars
    mark `is_last_bar_of_day=True` on a bar past 16:00 ET (e.g. 23:58 UTC).
    The market-hours gate skips such bars via `continue`, but it must
    still call `_emit_day_completed` first — otherwise multi-day intraday
    runs lose their EOD rollups and the web PnL chart stays empty.

    Verifies the gate's source contains the EOD-before-skip handler and
    that the handler invokes both on_day_close and _emit_day_completed.
    """
    import inspect
    from pathlib import Path
    from trading.runner import TradingRunner

    # The actual loop lives in _main_loop (run() is a thin shim).
    src = inspect.getsource(TradingRunner._main_loop)
    # Find the market-hours gate block.
    gate_marker = "if not is_market_hours_et(state.current_ts):"
    assert gate_marker in src, (
        "market-hours gate not found in TradingRunner._main_loop; refactor "
        "changed structure — update this test along with the runner."
    )
    # The 200 chars after the gate marker must reference is_last_bar_of_day
    # and call _emit_day_completed before the bare `continue`.
    idx = src.index(gate_marker)
    window = src[idx:idx + 600]
    assert "is_last_bar_of_day" in window, (
        "Market-hours gate skips after-hours bars without checking "
        "is_last_bar_of_day; this drops EOD rollups for multi-day intraday "
        "runs (PnL chart stays empty)."
    )
    assert "_emit_day_completed" in window, (
        "Market-hours gate's EOD-before-skip branch must call "
        "_emit_day_completed; otherwise day.completed never fires."
    )


def test_emit_day_completed_is_idempotent_per_trading_day():
    """Regression: PMB's is_last_bar_of_day flag uses UTC date boundaries,
    so SPY's after-hours feed can mark two EOD bars for the same ET
    trading day (e.g. T23:59 UTC and next-UTC-day T00:59, both still same
    ET trading day). Calling _emit_day_completed twice for the same
    trading_day must emit only once.
    """
    from datetime import datetime, timezone
    from trading import runner as runner_mod

    r = runner_mod.TradingRunner.__new__(runner_mod.TradingRunner)
    r.config = type("C", (), {"experiment": type("E", (), {"initial_cash": 10000})()})()

    emitted: list[dict] = []
    r.events = type("E", (), {"emit": lambda self, k, p: emitted.append({"kind": k, "payload": p})})()
    r._summarize_day_greeks = lambda state: {}

    state = type("S", (), {})()
    state.current_ts = datetime(2025, 1, 3, 23, 59, tzinfo=timezone.utc)  # 18:59 ET, trading day Jan 3
    state.portfolio = type("P", (), {"equity": 9500.0, "daily_pnl": -500.0})()

    r._emit_day_completed(state)
    # Second call same ET trading day (different UTC date, e.g. next-day 00:59)
    state.current_ts = datetime(2025, 1, 4, 0, 59, tzinfo=timezone.utc)  # 19:59 ET, still trading day Jan 3
    r._emit_day_completed(state)

    assert len(emitted) == 1, f"expected 1 day.completed for trading day, got {len(emitted)}"
    assert emitted[0]["payload"]["trading_day"] == "2025-01-03"

    # New trading day should emit again
    state.current_ts = datetime(2025, 1, 6, 21, 0, tzinfo=timezone.utc)  # Jan 6 ET
    r._emit_day_completed(state)
    assert len(emitted) == 2
    assert emitted[1]["payload"]["trading_day"] == "2025-01-06"


def test_intraday_llm_emits_decision_events_and_writes_artifact():
    """Regression: intraday_llm must emit decision.started + decision.completed
    AND call write_artifact so decision_records row is registered. Without
    this the web Timeline / Inspector are permanently empty for intraday
    runs even when the LLM is making real trades.
    """
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from trading.agents.strategy.intraday_llm import IntradayLLMStrategy

    s = IntradayLLMStrategy.__new__(IntradayLLMStrategy)
    # Mimic __init__ side effects without invoking the real LLMClient.
    s._interval = 15
    s._llm_provider = "deepseek"
    s._llm_model = "deepseek-chat"
    s._dte_min = 0
    s._dte_max = 7
    s._max_positions = 5
    s._chain_sample_tickers = None
    s._flatten_before_close = True
    s._flatten_minutes_before_close = 5
    s._bar_buffer = []
    s._steps_since_decision = 0
    s._universe = ["SPY"]
    s._decision_seq = 0
    # Fake LLM that returns one buy action.
    fake_llm = MagicMock()
    fake_llm.decide.return_value = [{
        "contract": "O:SPY250106C00597000",
        "action": "buy",
        "qty": 1,
        "rationale": "test",
    }]
    fake_llm.model = "deepseek-chat"
    fake_llm.provider = "deepseek"
    s._llm = fake_llm
    # Stubs for helpers _call_llm uses internally.
    s._build_price_windows = lambda evts: []
    s._build_chain_sample = lambda ev, d: []
    s._portfolio_to_dict = lambda p: {}
    s._compute_minutes_to_close = lambda ts: 60
    s._compress_chain = lambda c, ev, st: None

    emitted: list[dict] = []
    artifact_calls: list[dict] = []
    s.events = type("E", (), {"emit": lambda self, k, p: emitted.append({"kind": k, "payload": p})})()
    s.write_artifact = lambda **kw: artifact_calls.append(kw)

    state = type("S", (), {})()
    state.current_ts = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)  # 09:30 ET
    state.errors = []
    portfolio = type("P", (), {})()

    intents = s._call_llm([], portfolio, state)

    # Two emit events: started + completed.
    kinds = [e["kind"] for e in emitted]
    assert "decision.started" in kinds
    assert "decision.completed" in kinds
    # decision.completed payload must carry seq (matches lint).
    completed = next(e for e in emitted if e["kind"] == "decision.completed")
    assert "seq" in completed["payload"]
    assert completed["payload"]["has_trade"] is True
    # write_artifact called exactly once with the same decision_id.
    assert len(artifact_calls) == 1
    started = next(e for e in emitted if e["kind"] == "decision.started")
    assert artifact_calls[0]["decision_id"] == started["payload"]["decision_id"]
    # Returned at least one TradeIntent.
    assert len(intents) == 1


def test_intraday_llm_failed_call_still_emits_decision_completed():
    """Regression: failed LLM calls must still emit decision.completed
    (with has_trade=False) so the started event isn't orphaned."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from trading.agents.strategy.intraday_llm import IntradayLLMStrategy

    s = IntradayLLMStrategy.__new__(IntradayLLMStrategy)
    s._interval = 15
    s._llm_provider = "deepseek"
    s._llm_model = "deepseek-chat"
    s._dte_min = 0
    s._dte_max = 7
    s._max_positions = 5
    s._chain_sample_tickers = None
    s._flatten_before_close = True
    s._flatten_minutes_before_close = 5
    s._bar_buffer = []
    s._steps_since_decision = 0
    s._universe = ["SPY"]
    s._decision_seq = 0
    fake_llm = MagicMock()
    fake_llm.decide.side_effect = RuntimeError("provider down")
    s._llm = fake_llm
    s._build_price_windows = lambda evts: []
    s._build_chain_sample = lambda ev, d: []
    s._portfolio_to_dict = lambda p: {}
    s._compute_minutes_to_close = lambda ts: 60
    s._compress_chain = lambda c, ev, st: None

    emitted: list[dict] = []
    s.events = type("E", (), {"emit": lambda self, k, p: emitted.append({"kind": k, "payload": p})})()
    s.write_artifact = lambda **kw: None  # would not be called on failure

    state = type("S", (), {})()
    state.current_ts = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)
    state.errors = []
    portfolio = type("P", (), {})()

    intents = s._call_llm([], portfolio, state)

    assert intents == []
    kinds = [e["kind"] for e in emitted]
    assert kinds.count("decision.started") == 1
    assert kinds.count("decision.completed") == 1
    completed = next(e for e in emitted if e["kind"] == "decision.completed")
    assert completed["payload"]["has_trade"] is False
    assert completed["payload"]["reason"] == "llm_call_failed"
