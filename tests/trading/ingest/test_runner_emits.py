"""Tests for runner-level web events: run.started/finished, day.completed,
fill.executed (Task 5 + Task 7 Step 6)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from trading.core.config import (
    DataPolicyConfig,
    EvaluationConfig,
    ExecutionConfig,
    ExperimentConfig,
    RiskConfig,
    RunConfig,
    StrategyConfig,
)
from trading.core.registry import STRATEGY_REGISTRY
from trading.runner import TradingRunner


class _Stub:
    def __init__(self, **kwargs):
        pass
    def on_session_start(self, config): pass
    def on_session_end(self, state): pass
    def on_day_open(self, date, state): pass
    def on_day_close(self, date, state): pass
    def on_step(self, me, pf, st): return []


STRATEGY_REGISTRY["runner_emits_stub"] = _Stub


def _make_config(output_dir: str) -> RunConfig:
    return RunConfig(
        experiment=ExperimentConfig(
            run_id="test_emits",
            start="2025-01-06",
            end="2025-01-10",
            universe=["SPY"],
            frequency="1m",
        ),
        strategy=StrategyConfig(
            name="runner_emits_stub",
            params={"llm_model": "unit-test-model"},
        ),
        risk=RiskConfig(flatten_before_close=False),
        execution=ExecutionConfig(),
        data_policy=DataPolicyConfig(),
        evaluation=EvaluationConfig(output_dir=output_dir),
    )


def _read_events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_main_loop_emits_run_started_and_finished():
    """run.started must be the first event, run.finished the last, for a
    happy-path _main_loop (session ends immediately)."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTEMITSBASIC0000001")
        # Skip bootstrap; wire a minimal state ourselves.
        from trading.core.state import TradingState
        runner._state = TradingState(
            config=cfg, run_id="test_emits", episode_id="test_emits",
        )
        # Make session terminate on the first step: return a clock that
        # has no current_ts (which causes `continue`) then flip session_running
        # off via a second PMB step. Simpler: have the state loop exit
        # immediately by returning session_running=False via the clock.
        step_result = {"clock": {"current_ts": "", "status": "FINISHED"}, "events": []}
        runner.pmb = MagicMock()
        # First call: empty ts → `continue` loops back; session_running still True
        # but we want to exit. So push status=FINISHED after setting current_ts.
        # Set session_running=False on state so the while-loop is skipped entirely.
        runner._state.session_running = False

        try:
            result = runner._main_loop()
        except Exception:
            pass  # we don't care about summary completion here

        events = _read_events(Path(tmp))
        kinds = [e["kind"] for e in events]
        assert kinds, f"no events emitted: {events}"
        assert kinds[0] == "run.started", f"first event was {kinds[0]}"
        assert kinds[-1] == "run.finished", f"last event was {kinds[-1]}"
        started = events[0]["payload"]
        assert started["scenario"] == "runner_emits_stub"
        assert started["model_id"] == "unit-test-model"
        assert started["date_start"] == "2025-01-06"
        assert started["date_end"] == "2025-01-10"
        assert started["kind"] == "batch"
        finished = events[-1]["payload"]
        assert finished["exit_code"] == 0


def test_main_loop_emits_run_finished_with_error_exit_code():
    """If _main_loop raises, run.finished must still fire with exit_code=1."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTEMITSERRORCODE001")
        from trading.core.state import TradingState
        runner._state = TradingState(
            config=cfg, run_id="test_emits", episode_id="test_emits",
        )
        # Force a crash inside the loop by having pmb.step raise.
        runner.pmb = MagicMock()
        runner.pmb.step.side_effect = RuntimeError("boom")

        try:
            runner._main_loop()
        except RuntimeError:
            pass

        events = _read_events(Path(tmp))
        kinds = [e["kind"] for e in events]
        assert "run.started" in kinds
        assert "run.finished" in kinds
        finished = next(e for e in events if e["kind"] == "run.finished")
        assert finished["payload"]["exit_code"] == 1


def test_emit_day_completed_fields():
    """_emit_day_completed should produce all required payload fields."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTEMITSDAYCOMPLETE1")
        try:
            from trading.core.schemas import PortfolioSnapshot
            from trading.core.state import TradingState
            from trading.core.time_contract import utc_from_iso

            state = TradingState(
                config=cfg, run_id="test_emits", episode_id="test_emits",
            )
            state.current_ts = utc_from_iso("2025-01-06T20:00:00Z")
            state.portfolio = PortfolioSnapshot(
                cash=50_000.0, equity=105_000.0,
                daily_pnl=2_500.0, daily_pnl_pct=0.025, positions=[],
            )
            runner._emit_day_completed(state)
            # Flush queue
            runner.events.close(timeout=5)
            events = _read_events(Path(tmp))
            day_evts = [e for e in events if e["kind"] == "day.completed"]
            assert len(day_evts) == 1
            p = day_evts[0]["payload"]
            assert p["trading_day"] == "2025-01-06"
            assert p["pnl_delta"] == 2_500.0
            # cum_pnl = equity - initial_cash (default 100_000)
            assert p["cum_pnl"] == 5_000.0
            assert p["portfolio_value"] == 105_000.0
        finally:
            # already closed above, but safe to ensure
            try:
                runner.events.close()
            except Exception:
                pass


def test_fill_executed_emitted_in_main_loop():
    """Fills produced each step should each yield a fill.executed event."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _make_config(tmp)
        runner = TradingRunner(cfg, run_id="01HTESTEMITSFILLEXECUTED")
        from trading.core.state import TradingState
        from trading.core.time_contract import utc_from_iso
        from trading.core.schemas import PortfolioSnapshot

        runner._state = TradingState(
            config=cfg, run_id="test_emits", episode_id="test_emits",
        )

        # Build a fake Fill-like object with contract/qty/price/side/fees.
        class _Fill:
            contract = "O:SPY250110C00600000"
            symbol = contract
            qty = 1
            price = 2.5
            side = "BUY"
            fees = 0.65

        # Patch agents to produce one fill, one portfolio, then end session.
        runner.pmb = MagicMock()
        runner.pmb.step.return_value = {
            "clock": {
                "current_ts": "2025-01-06T15:00:00Z",
                "next_ts": "2025-01-06T15:01:00Z",
                "status": "RUNNING",
            },
            "events": [],
        }

        runner.execution = MagicMock()
        runner.execution.extract_fills.return_value = [_Fill()]
        runner.execution.submit_orders.return_value = []

        runner.market_data = MagicMock()
        runner.market_data.process_step.return_value = None

        runner.risk_gate = MagicMock()
        runner.risk_gate.evaluate.return_value = []
        runner.risk_gate.on_new_day = MagicMock()

        runner.audit_logger = MagicMock()

        # _extract_portfolio short-circuits via _last_portfolio
        runner._last_portfolio = PortfolioSnapshot(
            cash=100_000.0, equity=100_000.0,
            daily_pnl=0.0, daily_pnl_pct=0.0, positions=[],
        )

        # After first iteration, stop the loop.
        original_step = runner.pmb.step
        call_count = {"n": 0}
        def _stepping():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "clock": {
                        "current_ts": "2025-01-06T15:00:00Z",
                        "next_ts": "2025-01-06T15:01:00Z",
                        "status": "RUNNING",
                    },
                    "events": [],
                }
            return {"clock": {"current_ts": "2025-01-06T16:00:00Z", "status": "FINISHED"}, "events": []}
        runner.pmb.step.side_effect = _stepping

        # Skip market-hours gate and last-bar-of-day complications
        with patch("trading.agents.strategy._tz_utils.is_market_hours_et", return_value=True):
            try:
                runner._main_loop()
            except Exception:
                pass

        events = _read_events(Path(tmp))
        fill_evts = [e for e in events if e["kind"] == "fill.executed"]
        assert fill_evts, f"no fill.executed emitted; kinds={[e['kind'] for e in events]}"
        p = fill_evts[0]["payload"]
        assert p["symbol"] == "O:SPY250110C00600000"
        assert p["qty"] == 1
        assert p["price"] == 2.5
        assert p["trading_day"] == "2025-01-06"
