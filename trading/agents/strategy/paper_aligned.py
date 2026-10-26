"""Legacy multi-agent analysis, trading and reflection strategy.

Paper Section 2.3:
- Analysis Agent: "generates high-level trading intentions and structured
  strategy proposals"
- Trader Agent: "translates structured strategy plans into executable orders,
  performs constraint verification, and applies a self-reflection mechanism"

Three-phase LLM pipeline per decision point:
1. Analysis Agent  — market assessment + high-level intent
2. Trader Agent    — concrete order plan from intent
3. Self-Reflection — validate orders before submission
"""

from __future__ import annotations

import json
import logging
import time as time_mod
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.agents.strategy.latency_aware import LatencyAwareMixin
from trading.core.registry import register_strategy
from trading.core.time_contract import to_iso_z
from trading.prompts.paper_aligned import (
    build_analysis_prompt,
    build_reflection_prompt,
    build_trader_prompt,
    parse_analysis_response,
    parse_reflection_response,
    parse_trader_response,
)

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import MarketEvent, PortfolioSnapshot, TradeIntent
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)

_CHECKPOINT_SCHEMA_VERSION = 1


@register_strategy("paper_aligned")
class PaperAlignedStrategy(BaseStrategy, LatencyAwareMixin):
    """Multi-agent strategy with a backward-compatible registration name.

    Three-phase LLM pipeline per decision point:
    1. Analysis Agent: market assessment + high-level intent
    2. Trader Agent: concrete order plan from intent
    3. Self-Reflection: validate orders before submission

    Fast path (near close): rule-based exit of losing positions, zero LLM calls.
    Slow path: full three-phase pipeline at configurable intervals.
    """

    def __init__(
        self,
        llm_provider: str = "deepseek",
        llm_model: str = "deepseek-chat",
        decision_interval_minutes: int = 15,
        enable_reflection: bool = True,
        fast_threshold_minutes: int = 15,
        max_cost_per_trade: float = 5000.0,
        max_contracts: int = 5,
        dte_min: int = 0,
        dte_max: int = 7,
        chain_sample_tickers: list[str] | None = None,
        **kwargs,
    ):
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._interval = decision_interval_minutes
        self._enable_reflection = enable_reflection
        self._max_cost = max_cost_per_trade
        self._max_contracts = max_contracts
        self._dte_min = dte_min
        self._dte_max = dte_max
        self._chain_sample_tickers = chain_sample_tickers

        # Mutable per-session state
        self._llm = None  # initialized in on_session_start
        self._bar_buffer: list[MarketEvent] = []
        self._steps_since_decision: int = 0
        self._universe: list[str] = []
        self._decision_seq: int = 0

        # Initialize mixin
        self._init_latency_aware(fast_threshold_minutes)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def on_session_start(self, config: RunConfig) -> None:
        """Initialise the LLM client from config."""
        from trading.tools.llm_client import LLMClient

        self._llm = LLMClient(
            provider=self._llm_provider,
            model=self._llm_model,
        )
        self._universe = (
            self._chain_sample_tickers
            or list(config.experiment.universe)
        )

    def on_day_open(self, date: str, state: TradingState) -> None:
        """Reset bar buffer and decision counter at each day's open."""
        self._bar_buffer = []
        self._steps_since_decision = 0

    # ------------------------------------------------------------------
    # Core decision logic
    # ------------------------------------------------------------------

    def on_step(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Main entry: choose fast/slow path and produce TradeIntents."""
        self._bar_buffer.append(market_event)

        minutes_left = self._minutes_to_close(state)
        path = self._choose_path(minutes_left, bool(portfolio.positions))

        if path == "fast":
            t0 = time_mod.monotonic()
            intents = self._fast_path(portfolio)
            elapsed = (time_mod.monotonic() - t0) * 1000
            decision = "exit" if intents else "hold"
            self._record_cost(
                step_id=state.step_id,
                timestamp=to_iso_z(state.current_ts),
                path="fast",
                llm_calls=0,
                total_tokens=0,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=elapsed,
                decision=decision,
            )
            return intents

        # SLOW PATH: full multi-agent pipeline
        self._steps_since_decision += 1

        if self._steps_since_decision < self._interval:
            self.emit("decision.skipped", {
                "trading_day": state.current_date,
                "reason": "interval_not_elapsed",
            })
            return []

        self._steps_since_decision = 0
        return self._slow_path(market_event, portfolio, state)

    # ------------------------------------------------------------------
    # Slow path — three-phase LLM pipeline
    # ------------------------------------------------------------------

    def _slow_path(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Full multi-agent pipeline: Analysis -> Trader -> Reflection."""
        from trading.core.time_contract import trading_day_from_ts
        from trading.ingest.ulid import new_ulid

        decision_id = new_ulid()
        seq = self._decision_seq
        self._decision_seq = seq + 1
        trading_day = trading_day_from_ts(state.current_ts)
        self.emit("decision.started", {
            "decision_id": decision_id,
            "trading_day": trading_day,
            "seq": seq,
        })

        t0 = time_mod.monotonic()
        llm_calls = 0
        total_tokens = 0
        prompt_tokens = 0
        completion_tokens = 0

        # Phase 1: Analysis Agent
        analysis = self._call_analysis(market_event, portfolio, state)
        llm_calls += 1
        self.emit("phase.completed", {
            "decision_id": decision_id,
            "phase": "analysis",
            "intent": analysis.get("intent"),
        })

        if analysis["intent"] == "neutral":
            elapsed = (time_mod.monotonic() - t0) * 1000
            self._record_cost(
                step_id=state.step_id,
                timestamp=to_iso_z(state.current_ts),
                path="slow",
                llm_calls=llm_calls,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=elapsed,
                decision="hold",
            )
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": False, "n_trades": 0,
                "intent": "neutral",
            })
            return []

        if analysis["intent"] == "exit_all":
            intents = self._flatten_all(portfolio, analysis["reasoning"])
            elapsed = (time_mod.monotonic() - t0) * 1000
            self._record_cost(
                step_id=state.step_id,
                timestamp=to_iso_z(state.current_ts),
                path="slow",
                llm_calls=llm_calls,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=elapsed,
                decision="exit",
            )
            self._write_paper_artifact(
                decision_id=decision_id, seq=seq, state=state,
                analysis=analysis, trade_plan=None, reflected=None,
                intents=intents, market_event=market_event, portfolio=portfolio,
            )
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": bool(intents),
                "n_trades": len(intents), "intent": "exit_all",
            })
            return intents

        # Phase 2: Trader Agent
        trade_plan = self._call_trader(analysis, portfolio, market_event, state)
        llm_calls += 1
        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "trader",
            "orders_proposed": len(trade_plan) if trade_plan else 0,
        })

        if not trade_plan:
            elapsed = (time_mod.monotonic() - t0) * 1000
            self._record_cost(
                step_id=state.step_id,
                timestamp=to_iso_z(state.current_ts),
                path="slow",
                llm_calls=llm_calls,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=elapsed,
                decision="hold",
            )
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": False, "n_trades": 0,
                "reason": "trader_no_plan",
            })
            return []

        # Phase 3: Self-Reflection (optional)
        reflected_plan = None
        if self._enable_reflection:
            reflected_plan = self._call_reflection(trade_plan, portfolio, state)
            llm_calls += 1
            self.emit("phase.completed", {
                "decision_id": decision_id, "phase": "reflection",
                "orders_validated": len(reflected_plan) if reflected_plan else 0,
            })
            trade_plan = reflected_plan

        intents = self._to_intents(trade_plan)
        elapsed = (time_mod.monotonic() - t0) * 1000
        decision = "buy" if intents else "hold"
        self._record_cost(
            step_id=state.step_id,
            timestamp=to_iso_z(state.current_ts),
            path="slow",
            llm_calls=llm_calls,
            total_tokens=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=elapsed,
            decision=decision,
        )
        self._write_paper_artifact(
            decision_id=decision_id, seq=seq, state=state,
            analysis=analysis, trade_plan=trade_plan, reflected=reflected_plan,
            intents=intents, market_event=market_event, portfolio=portfolio,
        )
        self.emit("decision.completed", {
            "decision_id": decision_id, "seq": seq,
            "has_trade": bool(intents),
            "n_trades": len(intents),
        })
        return intents

    def _write_paper_artifact(
        self, *, decision_id, seq, state, analysis, trade_plan, reflected,
        intents, market_event, portfolio,
    ) -> None:
        """Serialize prompts/responses/context and call base.write_artifact."""
        response = {
            "analysis": analysis,
            "trade_plan": trade_plan,
            "reflected_plan": reflected,
        }
        portfolio_text = self._build_portfolio_summary(portfolio)
        market_snapshot = {
            "chain_summary": self._build_chain_summary(market_event),
            "n_option_bars": sum(
                1 for b in market_event.bars if b.instrument_type == "OPTION"
            ),
        }
        context = {
            "portfolio_summary": portfolio_text,
            "universe": list(self._universe),
        }
        self.write_artifact(
            decision_id=decision_id, seq=seq, ts=state.current_ts,
            model_id=getattr(self._llm, "model", self._llm_model),
            provider=getattr(self._llm, "provider", self._llm_provider),
            prompt={"note": "multi-phase; prompts live in llm_calls/ logs"},
            response=response, context=context,
            trades=[i.model_dump() if hasattr(i, "model_dump") else dict(i.__dict__) for i in intents],
            market_snapshot=market_snapshot,
        )

    # ------------------------------------------------------------------
    # Phase 1: Analysis Agent
    # ------------------------------------------------------------------

    def _call_analysis(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> dict:
        """Call the Analysis Agent LLM and return structured intent."""
        # Try compressed chain context first; fall back to raw summary
        compressed = self._build_compressed_chain(market_event, state)
        chain_summary = compressed if compressed else self._build_chain_summary(market_event)
        portfolio_summary = self._build_portfolio_summary(portfolio)
        minutes_left = self._minutes_to_close(state)
        from trading.core.time_contract import et_time_from_ts
        time_context = (
            f"Time: {et_time_from_ts(state.current_ts)} ET | "
            f"Minutes to close: {minutes_left}"
        )

        system_prompt, user_prompt = build_analysis_prompt(
            chain_summary=chain_summary,
            portfolio_summary=portfolio_summary,
            time_context=time_context,
        )

        try:
            raw = self._llm.call_raw(system_prompt, user_prompt)
            return parse_analysis_response(raw)
        except Exception as exc:
            logger.warning("Analysis LLM call failed: %s", exc)
            return {
                "intent": "neutral",
                "confidence": 0.0,
                "reasoning": f"LLM error: {exc}",
                "target_instruments": [],
            }

    # ------------------------------------------------------------------
    # Phase 2: Trader Agent
    # ------------------------------------------------------------------

    def _call_trader(
        self,
        analysis: dict,
        portfolio: PortfolioSnapshot,
        market_event: MarketEvent,
        state: TradingState,
    ) -> list[dict]:
        """Call the Trader Agent LLM and return order plan."""
        tradable = self._build_tradable_contracts(market_event)
        positions = self._build_portfolio_summary(portfolio)

        system_prompt, user_prompt = build_trader_prompt(
            analysis=analysis,
            tradable_contracts=tradable,
            positions=positions,
            max_cost=self._max_cost,
            max_contracts=self._max_contracts,
        )

        try:
            raw = self._llm.call_raw(system_prompt, user_prompt)
            return parse_trader_response(raw)
        except Exception as exc:
            logger.warning("Trader LLM call failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Phase 3: Self-Reflection
    # ------------------------------------------------------------------

    def _call_reflection(
        self,
        trade_plan: list[dict],
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[dict]:
        """Call the Reflection Agent LLM and return validated orders."""
        positions = self._build_portfolio_summary(portfolio)

        system_prompt, user_prompt = build_reflection_prompt(
            orders=trade_plan,
            positions=positions,
            cash=portfolio.cash,
        )

        try:
            raw = self._llm.call_raw(system_prompt, user_prompt)
            result = parse_reflection_response(raw)
            valid = result.get("valid_orders", [])
            rejected = result.get("rejected", [])
            if rejected:
                logger.info(
                    "Reflection rejected %d orders: %s",
                    len(rejected),
                    result.get("notes", ""),
                )
            return valid if valid else []
        except Exception as exc:
            logger.warning("Reflection LLM call failed: %s — rejecting all orders (fail-closed)", exc)
            return []  # fail-closed: reject all on error

    # ------------------------------------------------------------------
    # Helper: flatten all positions
    # ------------------------------------------------------------------

    def _flatten_all(
        self,
        portfolio: PortfolioSnapshot,
        reasoning: str,
    ) -> list[TradeIntent]:
        """Generate intents to close every open position."""
        from trading.core.schemas import TradeIntent

        intents: list[TradeIntent] = []
        for pos in portfolio.positions:
            symbol = self._extract_symbol(pos.contract)
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    contract=pos.contract,
                    side="SELL" if pos.side == "LONG" else "BUY",
                    qty=abs(pos.qty),
                    order_type="MARKET",
                    rationale=f"exit_all: {reasoning}",
                    strategy_name="paper_aligned",
                )
            )
        return intents

    # ------------------------------------------------------------------
    # Helper: convert trade plan dicts to TradeIntents
    # ------------------------------------------------------------------

    def _to_intents(self, trade_plan: list[dict]) -> list[TradeIntent]:
        """Convert validated order dicts into TradeIntent objects."""
        from trading.core.schemas import TradeIntent

        intents: list[TradeIntent] = []
        for order in trade_plan:
            contract = order.get("contract", "")
            symbol = self._extract_symbol(contract)
            side = "BUY" if order.get("action") == "buy" else "SELL"
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    contract=contract,
                    side=side,
                    qty=int(order.get("qty", 1)),
                    order_type=order.get("order_type", "MARKET"),
                    limit_price=order.get("limit_price"),
                    rationale=order.get("rationale", ""),
                    strategy_name="paper_aligned",
                )
            )
        return intents

    # ------------------------------------------------------------------
    # Prompt-building helpers
    # ------------------------------------------------------------------

    def _build_compressed_chain(
        self,
        market_event: MarketEvent,
        state: TradingState,
    ) -> str | None:
        """Build compressed chain context (AVSR + QOCR) from market event bars."""
        from trading.prompts.chain_context import build_multi_ticker_chain_context

        # Extract option bars as contract dicts
        chain_dicts: list[dict] = []
        spot_map: dict[str, float] = {}
        recent_map: dict[str, list[float]] = {}

        for bar in market_event.bars:
            if bar.instrument_type == "STOCK":
                spot_map[bar.symbol] = bar.close
            elif bar.instrument_type == "OPTION":
                chain_dicts.append({
                    "ticker": bar.symbol,
                    "close": bar.close,
                    "delta": bar.delta or 0,
                    "iv": bar.iv or 0,
                    "vega": bar.vega or 0,
                    "volume": bar.volume,
                })

        if not chain_dicts:
            return None

        if state.market_event is not None:
            for sym, history in state.market_event.bar_history.items():
                recent_map[sym] = [b.close for b in history]

        # Group by underlying
        chains_by_ticker: dict[str, list[dict]] = {}
        for c in chain_dicts:
            sym = self._extract_symbol(c.get("ticker", ""))
            if sym:
                chains_by_ticker.setdefault(sym, []).append(c)

        return build_multi_ticker_chain_context(
            chains_by_ticker=chains_by_ticker,
            spot_map=spot_map,
            recent_map=recent_map,
        )

    def _build_chain_summary(self, market_event: MarketEvent) -> str:
        """Build a text summary of available option chains from the market event."""
        lines: list[str] = []
        for bar in market_event.bars:
            if bar.instrument_type == "OPTION":
                stale_tag = (
                    f"[STALE {bar.stale_minutes}min]" if bar.stale else "[LIVE]"
                )
                delta_str = f"{bar.delta:.2f}" if bar.delta is not None else "?"
                iv_str = f"{bar.iv:.1%}" if bar.iv is not None else "?"
                lines.append(
                    f"{bar.symbol} close={bar.close:.2f} delta={delta_str} "
                    f"IV={iv_str} vol={bar.volume} {stale_tag}"
                )
        return "\n".join(lines) if lines else "(no chain data)"

    def _build_portfolio_summary(self, portfolio: PortfolioSnapshot) -> str:
        """Build a text summary of the portfolio."""
        lines = [
            f"Cash: ${portfolio.cash:,.2f}  Equity: ${portfolio.equity:,.2f}  "
            f"Daily P&L: ${portfolio.daily_pnl:,.2f} ({portfolio.daily_pnl_pct:.2f}%)",
        ]
        if portfolio.positions:
            for p in portfolio.positions:
                stale_tag = (
                    f"[STALE {p.stale_minutes}min]" if p.stale else "[LIVE]"
                )
                lines.append(
                    f"  {p.contract} {p.side} qty={p.qty} entry={p.avg_entry:.2f} "
                    f"mark={p.mark_price:.2f} pnl={p.unrealized_pnl:+.2f} {stale_tag}"
                )
        else:
            lines.append("  No open positions.")
        return "\n".join(lines)

    def _build_tradable_contracts(self, market_event: MarketEvent) -> str:
        """List tradable option contracts from the latest market event."""
        lines: list[str] = []
        for bar in market_event.bars:
            if bar.instrument_type == "OPTION" and bar.tradeable:
                delta_str = f"{bar.delta:.2f}" if bar.delta is not None else "?"
                lines.append(
                    f"{bar.symbol} ask~{bar.close:.2f} delta={delta_str} vol={bar.volume}"
                )
        return "\n".join(lines) if lines else "(no tradable contracts)"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _minutes_to_close(state: TradingState) -> int:
        """Compute minutes remaining until market close (16:00 ET)."""
        from trading.agents.strategy._tz_utils import minutes_to_close_et

        return minutes_to_close_et(state.current_ts)

    @staticmethod
    def _extract_symbol(contract: str) -> str:
        """Extract underlying symbol from OPRA contract string."""
        if contract.startswith("O:"):
            stripped = contract[2:]
            sym_chars: list[str] = []
            for ch in stripped:
                if ch.isalpha():
                    sym_chars.append(ch)
                else:
                    break
            return "".join(sym_chars)
        return ""

    def save_checkpoint(self) -> dict:
        """Serialize cross-step state for resume.

        Persistent fields:
          - _bar_buffer: list[MarketEvent] captured this day; serialized via
            MarketEvent.model_dump(). Reset by on_day_open, so only the
            in-progress day's buffer matters for resume.
          - _steps_since_decision: int counter against _interval.

        Transient (not persisted): _llm (re-init in on_session_start),
        _universe (from config), latency-aware fast-threshold state (mixin
        has no cross-step state to persist).
        """
        from trading.core.schemas import MarketEvent  # noqa: F401 (type ref)

        return {
            "_version": _CHECKPOINT_SCHEMA_VERSION,
            "bar_buffer": [ev.model_dump() for ev in self._bar_buffer],
            "steps_since_decision": int(self._steps_since_decision),
        }

    def load_checkpoint(self, state: dict) -> None:
        from trading.core.schemas import MarketEvent

        version = state.get("_version")
        if version != _CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"PaperAlignedStrategy checkpoint schema version mismatch: "
                f"got {version}, expected {_CHECKPOINT_SCHEMA_VERSION}"
            )
        raw_buffer = state.get("bar_buffer", []) or []
        self._bar_buffer = [MarketEvent(**ev) for ev in raw_buffer]
        self._steps_since_decision = int(state.get("steps_since_decision", 0))
