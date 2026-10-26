"""TradingRunner — pipeline orchestration for the multi-agent framework.

Wires config -> tools -> agents -> pipeline -> run.  The runner owns the
main loop that steps the PMB engine and dispatches to each agent in the
correct order:

    step_market -> extract_portfolio -> market_data -> extract_fills
    -> strategy -> risk_gate -> submit_orders -> logger
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import trading.agents.strategy  # noqa: F401 — trigger @register_strategy decorators

from trading.agents.execution import ExecutionAgent
from trading.agents.market_data import MarketDataAgent
from trading.agents.risk_manager import RiskGateAgent
from trading.core.config import RunConfig
from trading.core.greeks_math import (
    compute_higher_order_greeks,
    compute_step_pnl_attribution,
    compute_time_to_expiry,
    parse_opra_contract,
    _SECONDS_PER_YEAR,
)
from trading.core.time_contract import (
    et_time_from_ts,
    to_iso_z,
    trading_day_from_ts,
    utc_from_iso,
    utc_now,
)
from trading.core.registry import get_strategy
from trading.core.schemas import (
    MarketEvent,
    PnLDecomposition,
    PortfolioSnapshot,
    PositionInfo,
    PositionPnLAttribution,
)
from trading.core.state import TradingState
from trading.logger import AuditLogger
from trading.tools.pmb_client import PMBClient
from trading.tools.upq_tools import query_option_chain
from trading.ingest.event_emitter import EventEmitter, NoopEmitter
from trading.ingest.ulid import new_ulid

logger = logging.getLogger(__name__)


@dataclass
class _PositionGreeksSnapshot:
    """Internal snapshot of a position's Greeks state at one step."""
    contract: str
    mark_price: float
    underlying_price: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    volga: float | None
    vanna: float | None
    qty: int
    timestamp: datetime


class TradingRunner:
    """Orchestrate the full trading pipeline from config to completion.

    Args:
        config: A validated :class:`RunConfig` instance driving all parameters.
    """

    def __init__(
        self,
        config: RunConfig,
        *,
        web_ingest: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.config = config

        # Web-layer run identity + event emitter. Falls back to
        # config.experiment.run_id (if set) or a freshly generated ULID.
        cfg_run_id = getattr(getattr(config, "experiment", None), "run_id", None)
        self.run_id = run_id or cfg_run_id or new_ulid()
        self.run_dir = Path(config.evaluation.output_dir)
        # Always write JSONL sidecar; HTTP POST only if ingest URL was provided.
        self.events = EventEmitter(
            run_dir=self.run_dir,
            run_id=self.run_id,
            ingest_url=web_ingest,
        )

        # Tools
        self.pmb = PMBClient(config.execution.pmb_base_url)
        self.upq_base_url = config.execution.upq_base_url

        # Strategy (via registry)
        self.strategy = get_strategy(config.strategy.name, config.strategy.params)
        # Web-layer event emitter injection.
        self.strategy.events = self.events
        self.strategy._runner_ref = self

        # Agents
        self.risk_gate = RiskGateAgent(config.risk, config.data_policy)
        self.market_data = MarketDataAgent(config.data_policy)
        self.execution = ExecutionAgent(self.pmb, order_type_policy=config.execution.order_type_policy)

        # Logger
        self.audit_logger = AuditLogger(config.evaluation.output_dir)

        # Internal bookkeeping
        self._last_portfolio: PortfolioSnapshot | None = None
        self._start_of_day_equity: float | None = None

        # PnL decomposition bookkeeping
        self._prev_step_snapshot: dict[str, _PositionGreeksSnapshot] | None = None
        self._current_step_snapshot: dict[str, _PositionGreeksSnapshot] | None = None
        self._sod_accumulator: PnLDecomposition | None = None

        self._closed_trade_ledger: list[dict] = []
        # TODO(A5-followup): populate this from execution close events. Currently
        # left empty so run.finished emits a structurally-valid attribution roll-up
        # with count_total=0; trade-close wiring is tracked separately.

        # Bootstrap guard: set True by either _bootstrap() or
        # _restore_from_checkpoint().  Prevents double-bootstrap when
        # _main_loop is invoked after a restore path.
        self._bootstrapped: bool = False

    def run(self) -> dict[str, Any]:
        """Execute the full trading pipeline and return a summary dict.

        Returns:
            A dict with keys like ``total_steps``, ``run_id``, etc.
        """
        self._bootstrap()
        return self._main_loop()

    def _bootstrap(self) -> None:
        """PMB account setup, session creation, initial state build,
        chain refresh, and ``on_session_start`` hook.

        Safe to call exactly once per runner; subsequent paths (e.g.
        ``_restore_from_checkpoint``) will be the alternative entry point
        for resume flows.  Bootstrap failures propagate immediately — we
        deliberately do NOT emit ``run.started`` here, so an aborted
        bootstrap won't produce a mismatched started/finished pair.
        """
        if self._bootstrapped:
            return
        cfg = self.config.experiment

        # 1. Create PMB account + session
        self.pmb.create_account(initial_cash=cfg.initial_cash, start_date=cfg.start[:10])

        # For minute-frequency sessions, pre-query option contracts and pass them
        # at session creation so PMB can align minute-level option timestamps
        # correctly.  add_contracts() after the fact has a bug where it collapses
        # all option minute bars to a single daily timestamp.
        initial_options: list[str] = []
        if cfg.frequency == "1m":
            initial_options = self._prefetch_chain_contracts(cfg.start[:10])

        self._create_session(initial_options=initial_options)

        if cfg.initial_positions:
            self._bootstrap_initial_positions(cfg.initial_positions)

        # Build initial state
        self._state = TradingState(
            config=self.config,
            run_id=cfg.run_id,
            episode_id=cfg.run_id,
        )

        # 2. Initial chain refresh (registers contracts with MarketDataAgent;
        #    for minute sessions, contracts are already in PMB so skip add_contracts)
        self._refresh_chains(cfg.start[:10], skip_pmb_load=cfg.frequency == "1m")

        # 3. Strategy session start hook
        self.strategy.on_session_start(self.config)

        self._bootstrapped = True

    def _restore_from_checkpoint(self, ckpt) -> None:
        """Alternative to ``_bootstrap``: restore runner/state from a
        persisted ``RunnerCheckpoint`` (Task 12).

        Phase 1 Plan A scope:
          - Rehydrates ``TradingState`` and bookkeeping accumulators.
          - Invokes the strategy's ``load_checkpoint`` to restore its
            internal bookkeeping (rebalance dates, last decision date,
            etc.).  Does NOT call ``on_session_start`` — the strategy is
            expected to resume mid-session from persisted state.
          - PMB restoration is explicitly deferred: this method does NOT
            create a PMB session or account.  Callers running a real
            engine loop after restore must either (a) reuse an existing
            PMB session identified by ``ckpt.pmb_session_id`` via their
            own wiring, or (b) accept that the engine will start from a
            fresh PMB context.  Phase 1 tests exercise the orchestration
            without spinning up PMB.
        """
        from trading.core.schemas import PortfolioSnapshot
        from trading.core.state import TradingState

        # Rehydrate TradingState.  Use config/run_id from this runner.
        ts_raw = ckpt.trading_state.get("current_ts")
        if isinstance(ts_raw, str) and ts_raw:
            current_ts = utc_from_iso(ts_raw)
        else:
            current_ts = None

        state = TradingState(
            config=self.config,
            run_id=self.config.experiment.run_id or self.run_id,
            episode_id=ckpt.trading_state.get("episode_id")
            or self.config.experiment.run_id
            or self.run_id,
        )
        state.step_id = int(ckpt.trading_state.get("step_id", 0))
        if current_ts is not None:
            state.current_ts = current_ts
        state.current_date = ckpt.trading_state.get("current_date", "") or ""
        state.session_running = bool(
            ckpt.trading_state.get("session_running", True)
        )
        state.is_new_day = bool(ckpt.trading_state.get("is_new_day", False))
        state.errors = list(ckpt.trading_state.get("errors", []) or [])
        self._state = state

        # Accumulators
        acc = ckpt.accumulators or {}
        lp = acc.get("last_portfolio")
        if lp:
            try:
                self._last_portfolio = PortfolioSnapshot(**lp)
            except Exception:
                # Lossy reconstruction may produce dicts that don't match
                # the schema; fall back to None rather than crashing the
                # resume path.
                self._last_portfolio = None
        else:
            self._last_portfolio = None
        self._start_of_day_equity = acc.get("start_of_day_equity")
        # prev/current step snapshots are dicts of dataclasses; not restored
        # in Phase 1 (capture_checkpoint sets them to None explicitly).
        self._prev_step_snapshot = None
        self._current_step_snapshot = None
        self._sod_accumulator = None

        # PMB restoration: deferred (see docstring).  No-op here.
        # Strategy internal state
        try:
            self.strategy.load_checkpoint(ckpt.strategy_checkpoint or {})
        except NotImplementedError:
            # Strategy doesn't support resume; leave as-is.
            pass

        self._bootstrapped = True

    def _main_loop(self) -> dict[str, Any]:
        """Bar-by-bar main loop.

        Requires either ``_bootstrap()`` or ``_restore_from_checkpoint()``
        to have been called first.  Emits ``run.started`` / ``run.finished``
        event bookends and drains the event queue on exit.
        """
        cfg = self.config.experiment
        state = self._state

        # Strategy-layer params may be a pydantic model on some configs; use
        # dict-style access with a safe fallback.
        params = self.config.strategy.params
        model_id = (
            params.get("llm_model", "unknown")
            if isinstance(params, dict)
            else getattr(params, "llm_model", "unknown")
        )
        # Fork runs (from trading.ingest.resume) stash context on the runner to
        # override kind/fork_mode/model_id without needing a second emit.
        fork_ctx = getattr(self, "_fork_context", None)
        if fork_ctx:
            started_payload = {
                "scenario": self.config.strategy.name,
                "model_id": fork_ctx.get("model_override") or model_id,
                "date_start": cfg.start,
                "date_end": cfg.end,
                "kind": fork_ctx.get("kind", "batch"),
                "fork_mode": fork_ctx.get("fork_mode"),
            }
        else:
            started_payload = {
                "scenario": self.config.strategy.name,
                "model_id": model_id,
                "date_start": cfg.start,
                "date_end": cfg.end,
                "kind": "batch",
            }
        self.events.emit("run.started", started_payload)

        prev_date: str | None = None
        _progress_freq = 5 if cfg.frequency == "1m" else 1  # print every N steps

        exit_code = 0
        # 4. Main loop — one iteration per PMB step
        try:
            while state.session_running:
                state.step_id += 1
                state.errors = []

                # -- Step market --
                step_result = self.pmb.step()
                state.raw_step_result = step_result

                clock = step_result.get("clock", {})
                raw_ts = clock.get("current_ts", "")
                if not raw_ts:
                    # PMB has no clock yet (pre-first-bar); skip this iteration
                    # rather than let the epoch-1970 default drive downstream
                    # decisions (trading_day, market-hours gate, etc.).
                    continue
                state.current_ts = utc_from_iso(raw_ts)
                state.current_date = trading_day_from_ts(state.current_ts)
                state.session_running = clock.get("status") == "RUNNING"

                if not state.session_running:
                    # Session finished — extract final portfolio snapshot,
                    # market data, and fills. Run decomposition but do NOT
                    # run strategy/risk/execution pipeline.
                    state.portfolio = self._extract_portfolio(step_result)
                    state.market_event = self.market_data.process_step(step_result)
                    self._enrich_position_greeks(state.portfolio, state.market_event)

                    # Final decomposition Pass 1
                    try:
                        self._current_step_snapshot = self._build_greeks_snapshot(
                            state.portfolio, state.market_event, state.current_ts
                        )
                        decomp = self._compute_step_decomposition(
                            self._current_step_snapshot,
                            self._prev_step_snapshot,
                            state.current_ts,
                        )
                        if self._sod_accumulator is None:
                            self._sod_accumulator = PnLDecomposition()
                        self._sod_accumulator.sod_delta_pnl += decomp.step_delta_pnl
                        self._sod_accumulator.sod_gamma_pnl += decomp.step_gamma_pnl
                        self._sod_accumulator.sod_theta_pnl += decomp.step_theta_pnl
                        self._sod_accumulator.sod_vega_pnl += decomp.step_vega_pnl
                        self._sod_accumulator.sod_volga_pnl += decomp.step_volga_pnl
                        self._sod_accumulator.sod_vanna_pnl += decomp.step_vanna_pnl
                        self._sod_accumulator.sod_residual += decomp.step_residual
                        self._sod_accumulator.sod_total_pnl += decomp.step_total_pnl
                        decomp.sod_delta_pnl = self._sod_accumulator.sod_delta_pnl
                        decomp.sod_gamma_pnl = self._sod_accumulator.sod_gamma_pnl
                        decomp.sod_theta_pnl = self._sod_accumulator.sod_theta_pnl
                        decomp.sod_vega_pnl = self._sod_accumulator.sod_vega_pnl
                        decomp.sod_volga_pnl = self._sod_accumulator.sod_volga_pnl
                        decomp.sod_vanna_pnl = self._sod_accumulator.sod_vanna_pnl
                        decomp.sod_residual = self._sod_accumulator.sod_residual
                        decomp.sod_total_pnl = self._sod_accumulator.sod_total_pnl
                        state.portfolio.pnl_decomposition = decomp
                    except Exception:
                        logger.warning("PnL decomposition failed (final step)", exc_info=True)

                    # Final fill extraction + Pass 2
                    state.fills = self.execution.extract_fills(step_result)
                    self._patch_fill_pnl(state)

                    # Emit fill.executed for each final-step fill
                    for fill in state.fills or []:
                        self.events.emit("fill.executed", {
                            "symbol": getattr(fill, "contract", None)
                                      or getattr(fill, "symbol", ""),
                            "qty": fill.qty,
                            "price": fill.price,
                            "trading_day": trading_day_from_ts(state.current_ts),
                        })

                    self.audit_logger.log_step(state)
                    # Final day rolled up before loop exit
                    self._emit_day_completed(state)
                    break

                # -- Market hours gate (1m frequency only) --
                # Skip the strategy/risk/execution pipeline for pre-market and
                # after-hours steps (outside 09:30–16:00 ET).  PMB has already
                # advanced the clock for this bar; we just continue the loop.
                #
                # Important: PMB marks `is_last_bar_of_day` on the bar whose
                # *next* bar is on a different calendar date — for sessions
                # that include after-hours bars this can land at e.g. 23:58
                # UTC (18:58 ET), well past `is_market_hours_et`. Emit the
                # day.completed event before skipping, otherwise the web PnL
                # chart misses the EOD rollup for every multi-day intraday
                # run.
                if self.config.experiment.frequency == "1m":
                    from trading.agents.strategy._tz_utils import is_market_hours_et
                    if not is_market_hours_et(state.current_ts):
                        if step_result.get("clock", {}).get("is_last_bar_of_day"):
                            self.strategy.on_day_close(state.current_date, state)
                            self._emit_day_completed(state)
                            prev_date = state.current_date
                        continue

                # -- Console progress (market hours only) --
                self._print_step_progress(state, _progress_freq)

                # -- Day boundary detection --
                # Also treat the very first step as a new day so that
                # on_day_open fires for the first trading day.
                state.is_new_day = (
                    prev_date is None or state.current_date != prev_date
                )

                if state.is_new_day:
                    if prev_date is not None and self.config.data_policy.chain_refresh_interval == "daily":
                        # Only refresh chains on subsequent days; initial
                        # refresh already happened before the loop.
                        self._refresh_chains(state.current_date)
                    self.risk_gate.on_new_day()
                    self.strategy.on_day_open(state.current_date, state)
                    # Carry previous day's closing equity for daily P&L
                    # (resetting to None makes P&L=0 for daily frequency)
                    if self._last_portfolio is not None:
                        self._start_of_day_equity = self._last_portfolio.equity
                    else:
                        self._start_of_day_equity = None
                    self._prev_step_snapshot = None
                    self._current_step_snapshot = None
                    self._sod_accumulator = None

                # -- Portfolio extraction --
                state.portfolio = self._extract_portfolio(step_result)

                # Record start-of-day equity on the first snapshot of the day
                if self._start_of_day_equity is None:
                    self._start_of_day_equity = state.portfolio.equity

                # -- Market data extraction + staleness --
                state.market_event = self.market_data.process_step(step_result)

                # -- Enrich portfolio positions with Greeks from market bars --
                self._enrich_position_greeks(state.portfolio, state.market_event)

                # -- PnL decomposition Pass 1 (before fill extraction) --
                try:
                    self._current_step_snapshot = self._build_greeks_snapshot(
                        state.portfolio, state.market_event, state.current_ts
                    )
                    decomp = self._compute_step_decomposition(
                        self._current_step_snapshot,
                        self._prev_step_snapshot,
                        state.current_ts,
                    )
                    # Accumulate SOD
                    if self._sod_accumulator is None:
                        self._sod_accumulator = PnLDecomposition()
                    self._sod_accumulator.sod_delta_pnl += decomp.step_delta_pnl
                    self._sod_accumulator.sod_gamma_pnl += decomp.step_gamma_pnl
                    self._sod_accumulator.sod_theta_pnl += decomp.step_theta_pnl
                    self._sod_accumulator.sod_vega_pnl += decomp.step_vega_pnl
                    self._sod_accumulator.sod_volga_pnl += decomp.step_volga_pnl
                    self._sod_accumulator.sod_vanna_pnl += decomp.step_vanna_pnl
                    self._sod_accumulator.sod_residual += decomp.step_residual
                    self._sod_accumulator.sod_total_pnl += decomp.step_total_pnl
                    # Merge SOD into decomp
                    decomp.sod_delta_pnl = self._sod_accumulator.sod_delta_pnl
                    decomp.sod_gamma_pnl = self._sod_accumulator.sod_gamma_pnl
                    decomp.sod_theta_pnl = self._sod_accumulator.sod_theta_pnl
                    decomp.sod_vega_pnl = self._sod_accumulator.sod_vega_pnl
                    decomp.sod_volga_pnl = self._sod_accumulator.sod_volga_pnl
                    decomp.sod_vanna_pnl = self._sod_accumulator.sod_vanna_pnl
                    decomp.sod_residual = self._sod_accumulator.sod_residual
                    decomp.sod_total_pnl = self._sod_accumulator.sod_total_pnl
                    state.portfolio.pnl_decomposition = decomp
                except Exception:
                    logger.warning("PnL decomposition failed", exc_info=True)

                # -- Fill extraction (previous step's orders) --
                state.fills = self.execution.extract_fills(step_result)

                # -- Emit fill.executed events (web layer) --
                for fill in state.fills or []:
                    self.events.emit("fill.executed", {
                        "symbol": getattr(fill, "contract", None)
                                  or getattr(fill, "symbol", ""),
                        "qty": fill.qty,
                        "price": fill.price,
                        "trading_day": trading_day_from_ts(state.current_ts),
                    })

                # -- PnL decomposition Pass 2: patch fill last-hop --
                self._patch_fill_pnl(state)

                # -- Update prev snapshot (AFTER Pass 2 consumes it) --
                self._prev_step_snapshot = self._current_step_snapshot

                # -- Strategy decision --
                state.intents = self.strategy.on_step(
                    state.market_event, state.portfolio, state
                )

                # -- Dynamic contract loading --
                # Clear fresh set each step so staleness bypass is per-step only
                state._freshly_loaded_contracts = set()
                state._freshly_loaded_prices = {}
                # Ensure all contracts referenced in intents are loaded into PMB
                self._ensure_contracts_loaded(state.intents, state)

                # -- Risk gate --
                state.risk_decisions = self.risk_gate.evaluate(
                    state.intents, state.portfolio, state
                )

                # -- Submit orders --
                state.orders = self.execution.submit_orders(
                    state.risk_decisions, state
                )

                # -- Audit log --
                # For strategies with snapshot interval (e.g. earnings_bet: 5-min),
                # only log portfolio at the configured interval; always log trades.
                _should_snapshot = True
                if hasattr(self.strategy, 'should_log_snapshot'):
                    _should_snapshot = self.strategy.should_log_snapshot(state.current_ts)
                # Always log when there are trades/fills/intents regardless of snapshot
                if _should_snapshot or state.intents or state.fills or state.orders:
                    self.audit_logger.log_step(state, snapshot_only=not _should_snapshot)

                # -- Day-close hook --
                if self._is_last_bar_of_day(step_result):
                    self.strategy.on_day_close(state.current_date, state)
                    self._emit_day_completed(state)

                prev_date = state.current_date

        except Exception:
            exit_code = 1
            raise
        finally:
            try:
                self.strategy.on_session_end(state)
            finally:
                self.audit_logger.close()
                # Event bookends: run.finished BEFORE close() so it enters the
                # queue; close() then drains and flushes the JSONL.
                self._emit_run_finished(exit_code)
                # Expose exit code for trading.ingest.resume callers bypassing run().
                self._last_exit_code = exit_code
                self.events.close(timeout=10)

        equity = state.portfolio.equity if state.portfolio else 0.0
        pnl = equity - cfg.initial_cash
        print(
            f"[DONE] {cfg.run_id}  steps={state.step_id}"
            f"  equity=${equity:,.2f}  pnl=${pnl:+,.2f}",
            flush=True,
        )
        return {
            "run_id": cfg.run_id,
            "total_steps": state.step_id,
        }

    # ------------------------------------------------------------------
    # Event emission helpers
    # ------------------------------------------------------------------

    def _emit_day_completed(self, state) -> None:
        """Emit a ``day.completed`` event with per-day PnL deltas plus a
        Greek attribution summary.

        Greek numbers come from the same per-position Greeks the runner
        already enriches per step (`_enrich_position_greeks`,
        `_build_greeks_snapshot`); this method aggregates them at day
        boundary so web-layer evaluators don't need to re-run BSM.

        Idempotent per trading day: PMB's ``is_last_bar_of_day`` flag
        uses UTC calendar boundaries, so a single ET trading day can
        produce multiple EOD-marked bars (e.g. SPY's after-hours feed
        marks both ``T23:59 UTC`` and the next-UTC-day's ``T00:59`` while
        both are still the same ET trading day). Track the last emitted
        trading_day and skip duplicates so the web PnL chart doesn't get
        repeated points.
        """
        trading_day = trading_day_from_ts(state.current_ts)
        if getattr(self, "_last_emitted_day_completed", None) == trading_day:
            return
        self._last_emitted_day_completed = trading_day
        equity = state.portfolio.equity if state.portfolio else 0.0
        # pnl_delta: day's equity change (daily_pnl when we have it).
        if state.portfolio is not None:
            pnl_delta = state.portfolio.daily_pnl
        else:
            pnl_delta = 0.0
        # cum_pnl: equity minus initial cash (cumulative since run start).
        initial_cash = self.config.experiment.initial_cash
        cum_pnl = equity - initial_cash
        greeks_summary = self._summarize_day_greeks(state)
        self.events.emit("day.completed", {
            "trading_day": trading_day,
            "pnl_delta": pnl_delta,
            "cum_pnl": cum_pnl,
            "portfolio_value": equity,
            "greeks_summary": greeks_summary,
        })

    def _summarize_day_greeks(self, state) -> dict:
        """Project the portfolio's day-cumulative PnL decomposition into a
        7-key dict matching spec §5.1.1.

        Reads `state.portfolio.pnl_decomposition.sod_*` — the runner already
        accumulates these per step and assigns the snapshot to the portfolio
        at decision boundaries (`trading/runner.py:366,468`). Day-completed
        emits the SOD-cumulative (start-of-day → end-of-day) attribution.
        """
        zero = {
            "delta_pnl": 0.0,
            "gamma_pnl": 0.0,
            "theta_pnl": 0.0,
            "vega_pnl": 0.0,
            "volga_pnl": 0.0,
            "vanna_pnl": 0.0,
            "residual": 0.0,
        }
        if state is None or state.portfolio is None:
            return zero
        decomp = getattr(state.portfolio, "pnl_decomposition", None)
        if decomp is None:
            return zero
        return {
            "delta_pnl": float(getattr(decomp, "sod_delta_pnl", 0.0) or 0.0),
            "gamma_pnl": float(getattr(decomp, "sod_gamma_pnl", 0.0) or 0.0),
            "theta_pnl": float(getattr(decomp, "sod_theta_pnl", 0.0) or 0.0),
            "vega_pnl": float(getattr(decomp, "sod_vega_pnl", 0.0) or 0.0),
            "volga_pnl": float(getattr(decomp, "sod_volga_pnl", 0.0) or 0.0),
            "vanna_pnl": float(getattr(decomp, "sod_vanna_pnl", 0.0) or 0.0),
            "residual": float(getattr(decomp, "sod_residual", 0.0) or 0.0),
        }

    def _emit_run_finished(self, exit_code: int) -> None:
        """Emit ``run.finished`` with run-level Greek attribution roll-up.

        Aggregates closed-trade Greek decomposition across the entire
        ledger so web-layer evaluators read greeks attribution from the
        events table without re-running BSM.
        """
        ledger = list(getattr(self, "_closed_trade_ledger", []) or [])
        attribution_total: dict = {}
        per_trade: list = []
        try:
            from trading.evaluation.greeks_decomposition import (
                aggregate_greeks,
                decompose_trade_greeks,
            )
            attribution_total = aggregate_greeks(ledger) or {}
            per_trade = [decompose_trade_greeks(t) for t in ledger]
        except Exception:  # defensive: never fail run-finish on attribution
            import logging
            logging.getLogger(__name__).warning(
                "aggregate_greeks failed; emitting empty attribution",
                exc_info=True,
            )
            attribution_total = {}
            per_trade = []
        self.events.emit("run.finished", {
            "exit_code": exit_code,
            "greeks_attribution_total": dict(attribution_total),
            "trade_level_attribution": list(per_trade),
        })

    # ------------------------------------------------------------------
    # Progress printing
    # ------------------------------------------------------------------

    def _print_step_progress(self, state, freq: int = 5) -> None:
        """Print a one-line progress update to stdout.

        For minute-frequency sessions prints every `freq` steps.
        For daily frequency prints every step (each day is significant).
        Always prints on day boundaries.
        """
        if state.is_new_day:
            equity = state.portfolio.equity if state.portfolio else 0.0
            pnl = state.portfolio.daily_pnl if state.portfolio else 0.0
            pos_count = len([p for p in (state.portfolio.positions if state.portfolio else [])
                             if p.qty != 0])
            print(
                f"[{state.current_date}] day open"
                f"  equity=${equity:,.2f}  daily_pnl=${pnl:+,.2f}"
                f"  positions={pos_count}",
                flush=True,
            )
            return

        if state.step_id % freq != 0:
            return

        if self.config.experiment.frequency == "1m":
            from trading.agents.strategy._tz_utils import ts_to_et_display
            ts_display = ts_to_et_display(state.current_ts) + " ET"
        else:
            ts_display = et_time_from_ts(state.current_ts)
        equity = state.portfolio.equity if state.portfolio else 0.0
        pnl = state.portfolio.daily_pnl if state.portfolio else 0.0
        pos_count = len([p for p in (state.portfolio.positions if state.portfolio else [])
                         if p.qty != 0])
        print(
            f"  [{state.current_date} {ts_display}] step={state.step_id}"
            f"  equity=${equity:,.2f}  daily_pnl=${pnl:+,.2f}"
            f"  positions={pos_count}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _create_session(self, initial_options: list[str] | None = None) -> str:
        """Create a PMB session with config-driven frequency and universe.

        For minute-frequency sessions, ``initial_options`` should contain the
        full contract list so PMB can align minute-level timestamps correctly
        during session creation (the add_contracts endpoint collapses minute
        timestamps for options, which breaks per-minute option bar delivery).
        """
        cfg = self.config.experiment
        start_ts = cfg.start
        end_ts = cfg.end

        # For minute frequency, PMB/UPQ use UTC timestamps.  Convert the
        # ET market hours (09:30-16:00 ET) to UTC so UPQ returns trading-
        # hours data.  If a full UTC timestamp was already provided, use as-is.
        if cfg.frequency == "1m":
            from trading.agents.strategy._tz_utils import (
                et_market_open_utc, et_market_close_utc,
            )
            date_str = start_ts[:10]
            if "T" not in start_ts:
                start_ts = et_market_open_utc(date_str)
            elif start_ts[11:13] in ("09", "10", "11", "12"):
                # Looks like an ET time was passed — convert to UTC
                start_ts = et_market_open_utc(date_str)
            if "T" not in end_ts:
                end_date = end_ts[:10]
                end_ts = et_market_close_utc(end_date)
            elif end_ts[11:13] in ("14", "15", "16"):
                end_date = end_ts[:10]
                end_ts = et_market_close_utc(end_date)

        return self.pmb.create_session(
            start_ts=start_ts,
            end_ts=end_ts,
            stocks=cfg.universe,
            options=initial_options or [],
            frequency=cfg.frequency,
            execution_config={
                "slippage_bps": self.config.execution.fee_model.get("slippage_bps", 1.0),
                "option_spread_pct": self.config.execution.fee_model.get("option_spread_pct", 0.05),
                "fee_model": {
                    "stock_fee_per_share": self.config.execution.fee_model.get("stock_fee_per_share", 0.0005),
                    "option_fee_per_contract": self.config.execution.fee_model.get("option_fee_per_contract", 0.65),
                },
            },
        )

    def _prefetch_chain_contracts(self, trade_date: str) -> list[str]:
        """Query UPQ for option contracts to include at session creation.

        Used for minute-frequency sessions where contracts must be loaded
        upfront so PMB can align minute-level option bar timestamps correctly.
        Returns a flat list of OPRA contract tickers.
        """
        dp = self.config.data_policy
        contracts: list[str] = []
        for ticker in self.config.experiment.universe:
            for right in ("C", "P"):
                try:
                    chain = query_option_chain(
                        upq_base_url=self.upq_base_url,
                        underlying=ticker,
                        trade_date=trade_date,
                        right=right,
                        dte_min=dp.chain_filter_dte_min,
                        dte_max=dp.chain_filter_dte_max,
                    )
                except Exception as exc:
                    logger.warning("Prefetch chain failed %s %s: %s", ticker, right, exc)
                    continue

                filtered = [
                    c for c in chain
                    if c.get("delta") is None
                    or dp.chain_filter_delta_min <= abs(c["delta"]) <= dp.chain_filter_delta_max
                ]
                filtered.sort(key=lambda c: c.get("volume", 0), reverse=True)
                for c in filtered[:dp.chain_max_contracts_per_ticker]:
                    t = c.get("ticker") or c.get("contract", "")
                    if t:
                        contracts.append(t)

        logger.info("Prefetched %d option contracts for session creation", len(contracts))
        return contracts

    def _bootstrap_initial_positions(self, positions: list[dict]) -> None:
        """Bootstrap stock positions following PMB demo pattern: step → buy → step."""
        self.pmb.step()  # get first market tick
        for pos_cfg in positions:
            self.pmb.place_order({
                "action": "buy",
                "contract": pos_cfg["symbol"],
                "qty": pos_cfg["qty"],
                "order_type": "MARKET",
                "instrument_type": "STOCK",
            })
        self.pmb.step()  # fill the stock orders

    # ------------------------------------------------------------------
    # Chain refresh
    # ------------------------------------------------------------------

    def _refresh_chains(self, trade_date: str, skip_pmb_load: bool = False) -> None:
        """Query option chains, filter, load into PMB, register with MarketDataAgent.

        Args:
            trade_date: The date to query chains for (YYYY-MM-DD).
            skip_pmb_load: If True, skip calling add_contracts (contracts already
                in PMB via session creation).  Still registers with MarketDataAgent.
        """
        dp = self.config.data_policy

        # Unregister old contracts before loading new ones
        old_contracts = set(self.market_data._instrument_state.keys())
        if old_contracts:
            self.market_data.unregister_contracts(old_contracts)

        contracts_to_load: list[str] = []

        for ticker in self.config.experiment.universe:
            for right in ("C", "P"):
                try:
                    chain = query_option_chain(
                        upq_base_url=self.upq_base_url,
                        underlying=ticker,
                        trade_date=trade_date,
                        right=right,
                        dte_min=dp.chain_filter_dte_min,
                        dte_max=dp.chain_filter_dte_max,
                    )
                except Exception as e:
                    logger.warning(
                        "Chain query failed for %s %s on %s: %s",
                        ticker, right, trade_date, e,
                    )
                    continue

                # Filter by delta range (skip filter if delta not in chain data)
                filtered = [
                    c for c in chain
                    if c.get("delta") is None
                    or dp.chain_filter_delta_min <= abs(c["delta"]) <= dp.chain_filter_delta_max
                ]
                # Sort by volume descending for liquidity
                filtered.sort(key=lambda c: c.get("volume", 0), reverse=True)
                for c in filtered[:dp.chain_max_contracts_per_ticker]:
                    ticker_key = c.get("ticker") or c.get("contract", "")
                    if ticker_key:
                        contracts_to_load.append(ticker_key)

        if not contracts_to_load:
            logger.warning("Chain refresh %s: no contracts found", trade_date)
            return

        if skip_pmb_load:
            # Contracts already loaded at session creation — just register
            self.market_data.register_contracts(set(contracts_to_load))
            logger.info("Chain refresh %s: registered %d contracts (already in PMB)",
                        trade_date, len(contracts_to_load))
            return

        try:
            result = self.pmb.add_contracts(contracts_to_load)
            if result.get("ok"):
                self.market_data.register_contracts(set(contracts_to_load))
            loaded_count = result.get("loaded", 0)
            logger.info("Chain refresh %s: loaded %d/%d contracts",
                        trade_date, loaded_count, len(contracts_to_load))
        except Exception as e:
            logger.warning("add_contracts failed: %s", e)

    # ------------------------------------------------------------------
    # Dynamic contract loading
    # ------------------------------------------------------------------

    def _ensure_contracts_loaded(self, intents: list, state=None) -> None:
        """Dynamically load any option contracts from intents into PMB.

        LLM-based strategies may reference contracts that were not loaded
        during the initial chain refresh.  This method extracts all OPRA
        contract IDs from the current intents and adds them to the PMB
        session so orders can be filled.

        Also fetches last-known close prices from the UPQ chain for each
        freshly-loaded contract and stores them on
        ``state._freshly_loaded_prices`` so the risk manager can use a
        trusted price instead of the LLM's limit price or the $1M sentinel.
        """
        if not intents:
            return
        contracts = [
            i.contract for i in intents
            if i.contract and i.contract.startswith("O:")
        ]
        if not contracts:
            return
        try:
            result = self.pmb.add_contracts(contracts)
            # PMB returns {"ok": true, "loaded": N, "skipped": M}
            # NOT a list of contract IDs — so if ok, treat all as loaded.
            loaded_count = result.get("loaded", 0)
            skipped_count = result.get("skipped", 0)
            if result.get("ok") and (loaded_count + skipped_count) > 0:
                self.market_data.register_contracts(set(contracts))
                # Tag freshly-requested contracts so the risk gate bypasses
                # staleness for this step.  Both newly loaded AND skipped
                # (already cached in PMB) contracts need the bypass — a
                # skipped contract is new to the *market-data stream* and
                # won't have bar data until the next PMB step.
                if state is not None and (loaded_count + skipped_count) > 0:
                    if not hasattr(state, "_freshly_loaded_contracts"):
                        state._freshly_loaded_contracts = set()
                    state._freshly_loaded_contracts.update(contracts)
                    # Fetch trusted close prices from UPQ chain data so the
                    # risk manager doesn't fall back to LLM limit prices or
                    # the $1M sentinel for freshly-loaded contracts.
                    self._fetch_freshly_loaded_prices(contracts, state)
                    trusted = getattr(state, "_freshly_loaded_prices", {})
                    # Staleness bypass: contracts that have a trusted UPQ price.
                    # For "skipped" contracts (already in PMB session), also allow
                    # bypass when they don't need a UPQ price lookup because the
                    # risk gate will find their bars in the market event stream.
                    # Only exclude hallucinated contracts that PMB accepted but
                    # UPQ completely doesn't know about (close=None from wide query).
                    if skipped_count > 0 and loaded_count == 0:
                        # All were "skipped" = already in PMB = streaming bars.
                        # If we still have no trusted price after wide fallback,
                        # grant the bypass anyway so MARKET orders can fill.
                        # The risk gate staleness check will validate via bars.
                        state._freshly_loaded_contracts = set(contracts)
                    else:
                        # Newly loaded contracts: only bypass if UPQ confirmed price.
                        state._freshly_loaded_contracts = {
                            c for c in state._freshly_loaded_contracts
                            if c in trusted
                        }
                logger.info(
                    "Dynamic contract load: %d loaded, %d skipped (already in PMB), %d with trusted prices",
                    loaded_count, skipped_count, len(getattr(state, "_freshly_loaded_prices", {})),
                )
            else:
                logger.warning(
                    "Dynamic contract load failed: PMB response=%s",
                    result,
                )
        except Exception as e:
            logger.warning("Dynamic contract load failed: %s", e)

    def _fetch_freshly_loaded_prices(
        self, contracts: list[str], state,
    ) -> None:
        """Query UPQ chain to get last close prices for freshly-loaded contracts.

        Groups contracts by underlying, queries the chain for each, and
        stores matching close prices in ``state._freshly_loaded_prices``.

        Falls back to querying a wider DTE window if the config DTE range misses
        contracts that the LLM chose (e.g. strikes outside the filtered set).
        """
        if not hasattr(state, "_freshly_loaded_prices"):
            state._freshly_loaded_prices = {}

        # Group contracts by underlying to minimise UPQ queries
        by_underlying: dict[str, list[str]] = {}
        for contract in contracts:
            parsed = parse_opra_contract(contract)
            if parsed is None:
                continue
            underlying = parsed[0]
            by_underlying.setdefault(underlying, []).append(contract)

        dp = self.config.data_policy
        trade_date = getattr(state, "current_date", None)
        if not trade_date:
            return

        for underlying, tickers in by_underlying.items():
            for right in ("C", "P"):
                need = [
                    t for t in tickers
                    if t not in state._freshly_loaded_prices
                ]
                if not need:
                    continue

                # Try configured DTE range first, then a wider range as fallback
                for dte_min, dte_max in [
                    (dp.chain_filter_dte_min, dp.chain_filter_dte_max),
                    (0, 60),  # wide fallback to catch any strike in 0–60 DTE
                ]:
                    still_need = [t for t in need if t not in state._freshly_loaded_prices]
                    if not still_need:
                        break
                    try:
                        chain = query_option_chain(
                            upq_base_url=self.upq_base_url,
                            underlying=underlying,
                            trade_date=trade_date,
                            right=right,
                            dte_min=dte_min,
                            dte_max=dte_max,
                        )
                    except Exception as e:
                        logger.debug(
                            "Chain price lookup failed for %s %s DTE %d-%d: %s",
                            underlying, right, dte_min, dte_max, e,
                        )
                        continue

                    # Build a ticker -> close map from the chain response
                    chain_prices = {}
                    for c in chain:
                        tk = c.get("ticker") or c.get("contract", "")
                        close = c.get("close")
                        if tk and close is not None:
                            chain_prices[tk] = float(close)

                    for ticker in still_need:
                        if ticker in chain_prices:
                            state._freshly_loaded_prices[ticker] = chain_prices[ticker]

        if state._freshly_loaded_prices:
            logger.debug(
                "Freshly-loaded prices from chain: %s",
                state._freshly_loaded_prices,
            )

    # ------------------------------------------------------------------
    # Portfolio extraction
    # ------------------------------------------------------------------

    def _extract_portfolio(self, step_result: dict) -> PortfolioSnapshot:
        """Extract portfolio from PMB's ACCOUNT_SNAPSHOT event.

        Falls back to the last known portfolio if no snapshot is present.
        """
        events = step_result.get("events", [])
        snap = next(
            (e["payload"] for e in events if e.get("type") == "ACCOUNT_SNAPSHOT"),
            None,
        )
        if snap is None:
            if self._last_portfolio is not None:
                return self._last_portfolio
            # Degenerate: no snapshot and no prior — return empty
            return PortfolioSnapshot(
                cash=0.0, equity=0.0, daily_pnl=0.0, daily_pnl_pct=0.0, positions=[]
            )

        positions: list[PositionInfo] = []
        for pos in snap.get("positions", []):
            # Map PMB field names to PositionInfo field names.
            # PMB Position uses: instrument_id, type, qty (signed), avg_price
            # PositionInfo expects: contract, side, qty, avg_entry
            raw_qty = pos.get("qty", 0)
            # PMB instrument_id has "OPTION:" prefix; strip it for OPRA contract
            instrument_id = pos.get("instrument_id", "")
            contract = pos.get("contract", "")
            if not contract and instrument_id:
                contract = instrument_id.split(":", 1)[-1] if ":" in instrument_id else instrument_id
            # Derive side from signed qty if not provided
            side = pos.get("side", "")
            if not side:
                side = "SHORT" if raw_qty < 0 else "LONG"
            positions.append(
                PositionInfo(
                    contract=contract,
                    side=side,
                    qty=raw_qty,
                    avg_entry=pos.get("avg_entry", pos.get("avg_price", 0.0)),
                    mark_price=pos.get("mark_price", 0.0),
                    unrealized_pnl=pos.get("unrealized_pnl", 0.0),
                    dte=pos.get("dte", 0),
                    stale=pos.get("stale", False),
                    stale_minutes=pos.get("stale_minutes", 0),
                )
            )

        equity = snap.get("equity", 0.0)

        # PMB does not provide daily_pnl; compute from start-of-day equity.
        sod = self._start_of_day_equity
        if sod is not None and sod > 0:
            daily_pnl = equity - sod
            daily_pnl_pct = daily_pnl / sod
        else:
            daily_pnl = snap.get("daily_pnl", 0.0)
            daily_pnl_pct = snap.get("daily_pnl_pct", 0.0)

        portfolio = PortfolioSnapshot(
            cash=snap.get("cash_available", snap.get("cash", 0.0)),
            equity=equity,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            positions=positions,
        )
        self._last_portfolio = portfolio
        return portfolio

    # ------------------------------------------------------------------
    # Greeks enrichment
    # ------------------------------------------------------------------

    @staticmethod
    def _enrich_position_greeks(
        portfolio: PortfolioSnapshot, market_event: MarketEvent | None
    ) -> None:
        """Populate Greeks + BSM inputs on positions from current market bars."""
        if market_event is None:
            return

        option_bar_map: dict[str, Any] = {}
        stock_bar_map: dict[str, Any] = {}
        for bar in market_event.bars:
            if bar.instrument_type == "OPTION":
                option_bar_map[bar.symbol] = bar
            elif bar.instrument_type == "STOCK":
                stock_bar_map[bar.symbol] = bar

        for pos in portfolio.positions:
            parsed = parse_opra_contract(pos.contract)
            if parsed is not None:
                underlying, _expiry, right, strike = parsed
                pos.strike = strike
                pos.right = right
                stock_bar = stock_bar_map.get(underlying)
                if stock_bar is not None:
                    pos.underlying_price = stock_bar.close

            bar = option_bar_map.get(pos.contract)
            if bar is None:
                continue
            if bar.delta is not None:
                pos.delta = bar.delta
            if bar.vega is not None:
                pos.vega = bar.vega
            if bar.gamma is not None:
                pos.gamma = bar.gamma
            if bar.theta is not None:
                pos.theta = bar.theta
            if bar.iv is not None:
                pos.iv = bar.iv

    # ------------------------------------------------------------------
    # Greeks snapshot + PnL decomposition
    # ------------------------------------------------------------------

    @staticmethod
    def _build_greeks_snapshot(
        portfolio: PortfolioSnapshot,
        market_event: MarketEvent | None,
        current_ts: datetime,
    ) -> dict[str, _PositionGreeksSnapshot]:
        """Build a snapshot of all positions' Greeks for decomposition."""
        stock_prices: dict[str, float] = {}
        if market_event:
            for bar in market_event.bars:
                if bar.instrument_type == "STOCK":
                    stock_prices[bar.symbol] = bar.close

        snapshot: dict[str, _PositionGreeksSnapshot] = {}
        for pos in portfolio.positions:
            is_option = pos.contract.startswith("O:")

            if is_option:
                volga, vanna = None, None
                parsed = parse_opra_contract(pos.contract)
                if (parsed is not None and pos.iv is not None
                        and pos.underlying_price is not None and pos.strike is not None):
                    T = compute_time_to_expiry(parsed[1], current_ts)
                    ho = compute_higher_order_greeks(
                        S=pos.underlying_price, K=pos.strike, T=T, sigma=pos.iv,
                    )
                    volga = ho["volga"]
                    vanna = ho["vanna"]

                snapshot[pos.contract] = _PositionGreeksSnapshot(
                    contract=pos.contract,
                    mark_price=pos.mark_price,
                    underlying_price=pos.underlying_price or 0.0,
                    iv=pos.iv or 0.0,
                    delta=pos.delta or 0.0,
                    gamma=pos.gamma or 0.0,
                    theta=pos.theta or 0.0,
                    vega=pos.vega or 0.0,
                    volga=volga,
                    vanna=vanna,
                    qty=pos.qty,
                    timestamp=current_ts,
                )
            else:
                ul_price = stock_prices.get(pos.contract, pos.mark_price)
                snapshot[pos.contract] = _PositionGreeksSnapshot(
                    contract=pos.contract,
                    mark_price=pos.mark_price,
                    underlying_price=ul_price,
                    iv=0.0, delta=1.0, gamma=0.0, theta=0.0, vega=0.0,
                    volga=None, vanna=None,
                    qty=pos.qty,
                    timestamp=current_ts,
                )
        return snapshot

    @staticmethod
    def _compute_step_decomposition(
        current_snap: dict[str, _PositionGreeksSnapshot],
        prev_snap: dict[str, _PositionGreeksSnapshot] | None,
        current_ts: datetime,
    ) -> PnLDecomposition:
        """Compute step-level PnL decomposition by diffing two snapshots."""
        if prev_snap is None:
            return PnLDecomposition()

        pos_attribs: list[PositionPnLAttribution] = []

        for contract, curr in current_snap.items():
            prev = prev_snap.get(contract)
            if prev is None:
                continue  # new position this step

            is_option = contract.startswith("O:")
            multiplier = 100 if is_option else 1

            dS = curr.underlying_price - prev.underlying_price
            d_sigma = curr.iv - prev.iv
            dt_seconds = (curr.timestamp - prev.timestamp).total_seconds()
            dt = dt_seconds / _SECONDS_PER_YEAR
            actual_pnl = (curr.mark_price - prev.mark_price) * curr.qty * multiplier

            # Use PREV step's Greeks for attribution (what we held during the move)
            result = compute_step_pnl_attribution(
                contract=contract,
                qty=curr.qty,
                multiplier=multiplier,
                delta=prev.delta,
                gamma=prev.gamma,
                theta=prev.theta,
                vega=prev.vega,
                volga=prev.volga,
                vanna=prev.vanna,
                dS=dS,
                d_sigma=d_sigma,
                dt=dt,
                actual_pnl=actual_pnl,
            )

            pos_attribs.append(PositionPnLAttribution(
                contract=contract,
                delta_pnl=result.delta_pnl,
                gamma_pnl=result.gamma_pnl,
                theta_pnl=result.theta_pnl,
                vega_pnl=result.vega_pnl,
                volga_pnl=result.volga_pnl,
                vanna_pnl=result.vanna_pnl,
                residual=result.residual,
                total_pnl=result.total_pnl,
            ))

        def _sum_field(field: str) -> float:
            vals = [getattr(a, field) for a in pos_attribs if getattr(a, field) is not None]
            return sum(vals)

        return PnLDecomposition(
            step_delta_pnl=_sum_field("delta_pnl"),
            step_gamma_pnl=_sum_field("gamma_pnl"),
            step_theta_pnl=_sum_field("theta_pnl"),
            step_vega_pnl=_sum_field("vega_pnl"),
            step_volga_pnl=_sum_field("volga_pnl"),
            step_vanna_pnl=_sum_field("vanna_pnl"),
            step_residual=_sum_field("residual"),
            step_total_pnl=_sum_field("total_pnl"),
            positions=pos_attribs,
        )

    def _patch_fill_pnl(self, state) -> None:
        """Pass 2: patch fill last-hop PnL onto decomposition.

        Computes the price movement from prev step's mark-to-market to the
        fill price for positions that were closed or reduced. This captures
        the gap that Pass 1 misses because those positions disappeared from
        the current snapshot.
        """
        if state.portfolio is None or state.portfolio.pnl_decomposition is None:
            return
        if not state.fills:
            return

        decomp = state.portfolio.pnl_decomposition
        prev = self._prev_step_snapshot or {}
        total_last_hop = 0.0
        total_fees = 0.0

        for fill in state.fills:
            total_fees += fill.fees
            prev_snap = prev.get(fill.contract)
            if prev_snap is None:
                continue  # new position opened by this fill

            multiplier = 100 if fill.contract.startswith("O:") else 1

            if fill.side == "SELL" and prev_snap.qty > 0:
                # Closing long (possibly cross-zero). Only closing portion.
                closing_qty = min(fill.qty, prev_snap.qty)
                total_last_hop += (fill.price - prev_snap.mark_price) * closing_qty * multiplier
            elif fill.side == "BUY" and prev_snap.qty < 0:
                # Covering short (possibly cross-zero). Only covering portion.
                closing_qty = min(fill.qty, abs(prev_snap.qty))
                total_last_hop += (prev_snap.mark_price - fill.price) * closing_qty * multiplier

        decomp.fill_last_hop_pnl = total_last_hop
        decomp.fees = total_fees

    # ------------------------------------------------------------------
    # Day boundary helpers
    # ------------------------------------------------------------------

    def _is_last_bar_of_day(self, step_result: dict) -> bool:
        """Return True if this step is the last bar of the current trading day.

        For daily-frequency runs every step *is* a full trading day, so this
        is unconditionally True. For minute frequency we consult PMB clock
        metadata: first an ``is_last_bar_of_day`` flag, otherwise comparing
        the next step's date against the current step's date.
        """
        if self.config.experiment.frequency == "1d":
            return True
        clock = step_result.get("clock", {})
        # PMB may expose a direct flag
        if "is_last_bar_of_day" in clock:
            return bool(clock["is_last_bar_of_day"])
        # Fallback: check if next_ts crosses a day boundary (ET trading day)
        next_ts = clock.get("next_ts", "")
        current_ts = clock.get("current_ts", "")
        if next_ts and current_ts:
            return trading_day_from_ts(utc_from_iso(next_ts)) != trading_day_from_ts(
                utc_from_iso(current_ts)
            )
        return False

    # ------------------------------------------------------------------
    # Checkpoint capture (web layer)
    # ------------------------------------------------------------------

    def capture_checkpoint(self):
        """Capture current runner state into a :class:`RunnerCheckpoint`.

        Call AFTER :meth:`_bootstrap` and typically between steps or at
        end-of-day.  The underlying strategy must support
        ``save_checkpoint`` (Phase 1: weekly_llm, paper_aligned, overlay,
        earnings_bet); otherwise its ``save_checkpoint`` will raise
        ``NotImplementedError`` and propagate to the caller.
        """
        from trading.ingest.runner_checkpoint import RunnerCheckpoint

        # -- Serialize TradingState (dataclass with embedded pydantic/datetime) --
        state = getattr(self, "_state", None)
        if state is not None:
            ts_dict = {
                "run_id": state.run_id,
                "episode_id": state.episode_id,
                "step_id": state.step_id,
                "current_ts": (
                    to_iso_z(state.current_ts) if state.current_ts is not None else ""
                ),
                "current_date": state.current_date,
                "session_running": state.session_running,
                "is_new_day": state.is_new_day,
                "errors": list(state.errors),
            }
        else:
            ts_dict = {}

        # -- Accumulators --
        last_pf = self._last_portfolio
        accumulators = {
            "last_portfolio": last_pf.model_dump() if last_pf is not None else None,
            "start_of_day_equity": self._start_of_day_equity,
            # Snapshot dicts carry datetimes + non-JSON state; omit for Phase 1.
            "prev_step_snapshot": None,
            "current_step_snapshot": None,
        }

        # -- Strategy checkpoint (may raise NotImplementedError) --
        strat_ckpt = self.strategy.save_checkpoint()

        # -- Minimal run_config snapshot --
        # _config_path lets trading.ingest.resume._reconstruct_run_config load
        # the full original config (preserving LLM/PMB/evaluation settings).
        # Falls back silently to the inline-minimal reconstruction path
        # (strategy.name + experiment dates only) if this attr isn't set.
        run_cfg_dict = {
            "strategy": {"name": self.config.strategy.name},
            "experiment": {
                "start": self.config.experiment.start,
                "end": self.config.experiment.end,
                "run_id": getattr(self.config.experiment, "run_id", ""),
            },
            "_config_path": getattr(self.config, "_source_path", None),
        }

        return RunnerCheckpoint(
            version=1,
            trading_state=ts_dict,
            accumulators=accumulators,
            pmb_session_id=getattr(self.pmb, "session_id", None) or "",
            pmb_account_id=getattr(self.pmb, "account_id", None) or "",
            strategy_checkpoint=strat_ckpt,
            llm_client_state={},
            run_config=run_cfg_dict,
        )
