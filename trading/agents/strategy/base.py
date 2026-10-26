"""BaseStrategy abstract interface with lifecycle hooks.

All concrete strategies subclass BaseStrategy and implement on_step().
Registration into STRATEGY_REGISTRY happens via the @register_strategy
decorator from trading.core.registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Union


def _to_iso_with_offset(ts: datetime) -> str:
    """Format ts as ISO with explicit +HH:MM offset.

    decision_records.decision_ts CHECK rejects 'Z' suffix; backfill uses
    +00:00. If ts is naive, treat as UTC.
    """
    if ts.tzinfo is None:
        from datetime import timezone
        ts = ts.replace(tzinfo=timezone.utc)
    iso = ts.isoformat(timespec="milliseconds")
    # Python emits +00:00; the CHECK only requires [-+]HH:MM so this is fine.
    return iso

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import MarketEvent, PortfolioSnapshot, TradeIntent
    from trading.core.state import TradingState
    from trading.ingest.event_emitter import EventEmitter, NoopEmitter


class BaseStrategy(ABC):
    """All strategies implement this interface."""

    # Web-layer event emitter. Injected by runner when --web-ingest is enabled;
    # remains None otherwise and emit() becomes a no-op.
    events: "Optional[Union[EventEmitter, NoopEmitter]]" = None

    # Web-layer back-reference to the owning TradingRunner, injected by runner.
    # Remains None in CLI/test contexts; write_artifact becomes a no-op.
    _runner_ref: "Any" = None

    # Class-level declaration of which experiment.frequency values this
    # strategy supports. None = strategy is frequency-agnostic. Concrete
    # subclasses set this to constrain. Web launcher validates against
    # scenario YAML's experiment.frequency before subprocess spawn
    # (liveoption_web/api/launch.py::handle_launch_run); CLI does not
    # validate (callers are expected to know what they're doing).
    REQUIRED_FREQUENCIES: "frozenset[str] | None" = None

    @abstractmethod
    def on_step(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Called every PMB step. Return intents or empty list."""
        ...

    def on_session_start(self, config: RunConfig) -> None:
        """Optional: initialize state at session start."""
        pass

    def on_session_end(self, state: TradingState) -> None:
        """Optional: cleanup at session end."""
        pass

    def on_day_open(self, date: str, state: TradingState) -> None:
        """Optional: called at the first bar of each new trading day.

        Use to reset per-day state (bar buffers, decision counters, etc.).
        """
        pass

    def on_day_close(self, date: str, state: TradingState) -> None:
        """Optional: called at the last bar of each trading day."""
        pass

    def emit(self, kind: str, payload: dict) -> None:
        """Emit a web-layer event via injected EventEmitter."""
        if self.events is not None:
            self.events.emit(kind, payload)

    def write_artifact(
        self,
        *,
        decision_id: str,
        seq: int,
        ts: datetime,
        model_id: str,
        provider: str,
        prompt: dict,
        response: dict,
        context: dict,
        trades: list,
        market_snapshot: dict,
    ) -> None:
        """Write a decision artifact via the runner's run_dir.

        No-op when no runner reference is injected (CLI/test contexts).
        Errors are swallowed to avoid breaking the trading loop.
        """
        if self._runner_ref is None:
            return
        try:
            from trading.core.time_contract import trading_day_from_ts
            from trading.ingest.artifact import write_decision_artifact

            artifact = write_decision_artifact(
                run_dir=self._runner_ref.run_dir,
                decision_id=decision_id,
                seq=seq,
                trading_day=trading_day_from_ts(ts),
                decision_ts=ts,
                model_id=model_id,
                provider=provider,
                prompt=prompt,
                response=response,
                context=context,
                trades_executed=trades,
                market_snapshot=market_snapshot,
            )
            # Register the decision_records row so the /v1/runs/:id/timeline
            # endpoint and decision inspector pick it up.
            if self.events is not None:
                # decision_ts must include a +HH:MM offset (CHECK constraint
                # on decision_records.decision_ts). Use UTC offset.
                self.events.post_decision_record({
                    "id": decision_id,
                    "seq": seq,
                    "trading_day": trading_day_from_ts(ts),
                    "decision_ts": _to_iso_with_offset(ts),
                    "model_id": model_id,
                    "provider": provider,
                    "prompt_sha256": artifact["prompt_sha256"],
                    "response_sha256": artifact["response_sha256"],
                    "storage_dir": artifact["storage_dir"],
                    "has_trade": bool(trades),
                })
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "write_artifact failed for decision_id=%s", decision_id, exc_info=True,
            )

    @abstractmethod
    def save_checkpoint(self) -> dict:
        """Return JSON-serializable dict with all private strategy state."""
        raise NotImplementedError

    @abstractmethod
    def load_checkpoint(self, state: dict) -> None:
        """Restore private state from dict produced by save_checkpoint()."""
        raise NotImplementedError

    def project_family_view(
        self,
        portfolio: "PortfolioSnapshot",
        state: "TradingState",
    ) -> dict | None:
        """Project current strategy + portfolio state into a family_view dict.

        Default returns None (no family-specific view). Strategies that
        belong to a supported family (overlay, earnings, intraday)
        override to return a dict matching `trading.checkpoint.captured`
        FamilyView shape (with the `kind` discriminator field).

        Called by trading.cli capture-checkpoint at promote time. Pure
        projection — no new strategy state, no I/O.
        """
        return None
