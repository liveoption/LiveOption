"""RuleEngine — deterministic position exits and daily P&L limits.

Pure Python, no LLM, no PMB dependency.  Used standalone in grid search
(Phase 1) and as the enforcement layer inside HybridIntradayStrategy
(Phase 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading.core.schemas import FillEvent, PositionInfo, TradeIntent


@dataclass
class RuleEngineParams:
    """Tunable thresholds for the rule engine."""

    stop_loss_pct: float = -25.0
    take_profit_pct: float = 50.0
    trend_threshold_pct: float = 0.05
    daily_profit_target: float | None = 80.0  # $ or None = off
    daily_loss_limit: float | None = 100.0  # $ or None = off
    qty_per_trade: int = 2


class RuleEngine:
    """Deterministic exit rules + daily session limits.

    Parameters
    ----------
    params : RuleEngineParams
        The threshold configuration for this engine instance.
    """

    def __init__(self, params: RuleEngineParams | None = None) -> None:
        self.params = params or RuleEngineParams()
        self._daily_realized: float = 0.0
        self._trading_stopped: bool = False
        self._open_positions: dict[str, FillEvent] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def daily_realized(self) -> float:
        """Accumulated realized P&L for the current day ($)."""
        return self._daily_realized

    @property
    def trading_stopped(self) -> bool:
        """True once a daily limit has been hit; stays True until on_day_open()."""
        return self._trading_stopped

    # ------------------------------------------------------------------
    # Position-level exits
    # ------------------------------------------------------------------

    def check_position_exits(self, positions: list[PositionInfo]) -> list[TradeIntent]:
        """Check each position for stop-loss / take-profit.

        Returns close intents (SELL for LONG, BUY for SHORT) for positions
        that should be closed.  Uses per-share price comparison:

            pnl_pct = (mark_price - avg_entry) / avg_entry * 100
        """
        intents: list[TradeIntent] = []
        for pos in positions:
            if pos.avg_entry == 0:
                continue
            pnl_pct = (pos.mark_price - pos.avg_entry) / pos.avg_entry * 100.0

            if pnl_pct <= self.params.stop_loss_pct:
                intents.append(
                    TradeIntent(
                        contract=pos.contract,
                        side="SELL" if pos.side == "LONG" else "BUY",
                        qty=abs(pos.qty),
                        order_type="MARKET",
                        rationale=f"Stop-loss triggered: {pnl_pct:+.1f}% <= {self.params.stop_loss_pct}%",
                        strategy_name="rule_engine",
                    )
                )
            elif pnl_pct >= self.params.take_profit_pct:
                intents.append(
                    TradeIntent(
                        contract=pos.contract,
                        side="SELL" if pos.side == "LONG" else "BUY",
                        qty=abs(pos.qty),
                        order_type="MARKET",
                        rationale=f"Take-profit triggered: {pnl_pct:+.1f}% >= {self.params.take_profit_pct}%",
                        strategy_name="rule_engine",
                    )
                )
        return intents

    # ------------------------------------------------------------------
    # Daily session limits
    # ------------------------------------------------------------------

    def check_daily_limits(self) -> str | None:
        """Check whether daily realized P&L has crossed a limit.

        Returns
        -------
        str | None
            ``"profit_target"`` if daily_realized >= daily_profit_target,
            ``"loss_limit"``   if daily_realized <= -daily_loss_limit,
            ``None``           otherwise.

        Side-effect: sets ``_trading_stopped = True`` when a limit is hit.
        """
        if (
            self.params.daily_profit_target is not None
            and self._daily_realized >= self.params.daily_profit_target
        ):
            self._trading_stopped = True
            return "profit_target"

        if (
            self.params.daily_loss_limit is not None
            and self._daily_realized <= -self.params.daily_loss_limit
        ):
            self._trading_stopped = True
            return "loss_limit"

        return None

    # ------------------------------------------------------------------
    # Realized P&L accumulation
    # ------------------------------------------------------------------

    def update_realized_from_fills(
        self,
        fills: list[FillEvent],
        open_positions: dict[str, FillEvent] | None = None,
    ) -> None:
        """Accumulate realized P&L from confirmed fill events.

        For **SELL** fills the realized P&L is computed as::

            (fill_price - entry_price) * qty * 100 - fees

        where the entry price is looked up from ``_open_positions`` (tracked
        from prior BUY fills).

        For **BUY** fills the fill is recorded in ``_open_positions`` so
        that a subsequent SELL can compute realized P&L.

        Parameters
        ----------
        fills : list[FillEvent]
            New fill events from the current step.
        open_positions : dict | None
            Optional external dict to use instead of ``_open_positions``.
            Useful when the caller wants to maintain its own book.
        """
        book = open_positions if open_positions is not None else self._open_positions

        for fill in fills:
            if fill.side == "BUY":
                book[fill.contract] = fill
            elif fill.side == "SELL":
                entry = book.get(fill.contract)
                if entry is not None:
                    realized = (fill.price - entry.price) * fill.qty * 100 - fill.fees
                    self._daily_realized += realized
                    del book[fill.contract]

    # ------------------------------------------------------------------
    # Trend signal
    # ------------------------------------------------------------------

    def get_trend_signal(self, spy_prices: list[float]) -> str:
        """Derive a simple trend signal from recent SPY prices.

        Compares the **last** price to the price **15 bars ago**.  If the
        percentage change exceeds ``trend_threshold_pct`` the signal is
        ``"bullish"``; if it is below ``-trend_threshold_pct`` the signal
        is ``"bearish"``; otherwise ``"neutral"``.

        Parameters
        ----------
        spy_prices : list[float]
            Ordered close prices (oldest first).  Must have at least 16
            elements to look back 15 bars.

        Returns
        -------
        str
            One of ``"bullish"``, ``"bearish"``, ``"neutral"``.
        """
        if len(spy_prices) < 16:
            return "neutral"

        old = spy_prices[-16]
        new = spy_prices[-1]
        if old == 0:
            return "neutral"

        change_pct = (new - old) / old * 100.0
        threshold = self.params.trend_threshold_pct

        if change_pct > threshold:
            return "bullish"
        if change_pct < -threshold:
            return "bearish"
        return "neutral"

    # ------------------------------------------------------------------
    # Day lifecycle
    # ------------------------------------------------------------------

    def on_day_open(self) -> None:
        """Reset all per-day state.  Call at the start of each trading day."""
        self._daily_realized = 0.0
        self._trading_stopped = False
        self._open_positions.clear()
