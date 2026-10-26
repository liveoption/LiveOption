"""Latency-aware design mixin: fast/slow path and per-step cost tracking.

Paper Section 2.3: "The MAS incorporates: a fast path for reactive, safe
adjustments using cached summaries; a slow path for deeper reasoning when
time permits.  We log context length, token usage, and step-level decision
cost proxies."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.core.schemas import PortfolioSnapshot, TradeIntent


@dataclass
class StepCost:
    """Per-step decision cost metrics for evaluation."""

    step_id: int
    timestamp: str
    path: str  # "fast" | "slow" | "skip"
    llm_calls: int
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    decision: str  # "hold" | "buy" | "sell" | "exit"


class LatencyAwareMixin:
    """Mixin that adds fast/slow path logic and cost tracking to any strategy.

    Concrete strategies mix this in alongside :class:`BaseStrategy` and call
    ``_choose_path`` at each step to decide which decision pipeline to use.
    """

    _step_costs: list[StepCost]
    _fast_threshold_minutes: int = 15  # use fast path when < 15 min to close

    def _init_latency_aware(self, fast_threshold_minutes: int = 15) -> None:
        """Initialise mixin state.  Call from __init__ of the concrete class."""
        self._step_costs: list[StepCost] = []
        self._fast_threshold_minutes = fast_threshold_minutes

    # ------------------------------------------------------------------
    # Path selection
    # ------------------------------------------------------------------

    def _choose_path(self, minutes_to_close: int, has_position: bool) -> str:
        """Decide fast vs slow path based on time remaining.

        Returns ``"fast"`` when close is imminent, ``"slow"`` otherwise.
        """
        if minutes_to_close <= 5:
            return "fast"  # always fast in last 5 min
        if minutes_to_close <= self._fast_threshold_minutes:
            return "fast"
        return "slow"

    # ------------------------------------------------------------------
    # Fast path — rule-based, zero LLM calls
    # ------------------------------------------------------------------

    def _fast_path(self, portfolio: PortfolioSnapshot) -> list[TradeIntent]:
        """Quick rule-based check: close losing positions, no new trades.

        Emits SELL (for longs) or BUY (for shorts) intents for positions
        whose mark-to-market loss exceeds 15%.
        """
        from trading.core.schemas import TradeIntent

        intents: list[TradeIntent] = []
        for pos in portfolio.positions:
            if pos.avg_entry > 0:
                pnl_pct = (pos.mark_price - pos.avg_entry) / pos.avg_entry * 100
            else:
                pnl_pct = 0.0

            if pnl_pct < -15:  # quick stop-loss
                # Extract underlying symbol from OPRA contract
                symbol = ""
                contract = pos.contract
                if contract.startswith("O:"):
                    stripped = contract[2:]
                    sym_chars: list[str] = []
                    for ch in stripped:
                        if ch.isalpha():
                            sym_chars.append(ch)
                        else:
                            break
                    symbol = "".join(sym_chars)

                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        contract=contract,
                        side="SELL" if pos.side == "LONG" else "BUY",
                        qty=abs(pos.qty),
                        order_type="MARKET",
                        rationale="fast_path: pre-close exit",
                        strategy_name="latency_aware",
                    )
                )
        return intents

    # ------------------------------------------------------------------
    # Cost recording
    # ------------------------------------------------------------------

    def _record_cost(
        self,
        step_id: int,
        timestamp: str,
        path: str,
        llm_calls: int,
        total_tokens: int,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        decision: str,
    ) -> None:
        """Record step cost for evaluation."""
        self._step_costs.append(
            StepCost(
                step_id=step_id,
                timestamp=timestamp,
                path=path,
                llm_calls=llm_calls,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                decision=decision,
            )
        )

    def get_cost_summary(self) -> dict:
        """Aggregate cost metrics for evaluation report."""
        if not self._step_costs:
            return {
                "total_steps": 0,
                "fast_path_steps": 0,
                "slow_path_steps": 0,
                "total_llm_calls": 0,
                "total_tokens": 0,
                "avg_latency_ms": 0.0,
                "cost_per_step": [],
            }

        return {
            "total_steps": len(self._step_costs),
            "fast_path_steps": sum(1 for c in self._step_costs if c.path == "fast"),
            "slow_path_steps": sum(1 for c in self._step_costs if c.path == "slow"),
            "total_llm_calls": sum(c.llm_calls for c in self._step_costs),
            "total_tokens": sum(c.total_tokens for c in self._step_costs),
            "avg_latency_ms": mean(c.latency_ms for c in self._step_costs),
            "cost_per_step": self._step_costs,
        }
