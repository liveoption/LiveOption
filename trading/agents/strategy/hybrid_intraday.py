"""HybridIntradayStrategy — rule-enforced exits + LLM direction/stop decisions.

Combines a deterministic RuleEngine (stop-loss, take-profit, daily limits)
with optional LLM calls for market direction and session stop decisions.
Registered as ``"hybrid_intraday"`` in the strategy registry.

When both LLM flags are off (``enable_llm_direction=False``,
``enable_llm_stop=False``), the strategy behaves as a pure rule engine
using trend signals for direction — identical to grid search mode.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.agents.strategy.rule_engine import RuleEngine, RuleEngineParams
from trading.core.registry import register_strategy
from trading.core.schemas import TradeIntent
from trading.prompts.hybrid import (
    build_direction_prompt,
    build_stop_prompt,
    parse_direction_response,
    parse_stop_response,
)

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import (
        BarData,
        FillEvent,
        MarketEvent,
        PortfolioSnapshot,
    )
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)


@register_strategy("hybrid_intraday")
class HybridIntradayStrategy(BaseStrategy):
    """Hybrid intraday strategy: rule exits + LLM direction/stop.

    The rule engine runs every step to enforce position-level stop-loss
    and take-profit, plus daily realized P&L limits.  LLM is called only
    at decision intervals for direction and stop-trading decisions.

    Parameters
    ----------
    stop_loss_pct : float
        Per-position stop-loss threshold (negative, e.g. -25).
    take_profit_pct : float
        Per-position take-profit threshold (positive, e.g. 50).
    trend_threshold_pct : float
        SPY 15-bar change threshold for trend signal (rule-only mode).
    daily_profit_target : float
        Dollar realized P&L to trigger daily stop (profit side).
    daily_loss_limit : float
        Dollar realized P&L to trigger daily stop (loss side).
    qty_per_trade : int
        Default quantity for rule-engine trend entries.
    llm_provider : str
        LLM provider name (e.g. "deepseek", "claude", "mock").
    llm_model : str
        Model identifier for the LLM provider.
    decision_interval_minutes : int
        Steps between LLM calls (1 step ~ 1 minute).
    enable_llm_direction : bool
        If True, use LLM for entry direction; if False, use trend signal.
    enable_llm_stop : bool
        If True, ask LLM whether to stop trading at decision intervals.
    """

    def __init__(
        self,
        # Rule engine params
        stop_loss_pct: float = -25,
        take_profit_pct: float = 50,
        trend_threshold_pct: float = 0.05,
        daily_profit_target: float = 80,
        daily_loss_limit: float = 100,
        qty_per_trade: int = 2,
        # LLM params
        llm_provider: str = "deepseek",
        llm_model: str = "deepseek-chat",
        decision_interval_minutes: int = 15,
        # Feature flags
        enable_llm_direction: bool = True,
        enable_llm_stop: bool = True,
        **kwargs,
    ):
        self._rule_engine = RuleEngine(
            RuleEngineParams(
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                trend_threshold_pct=trend_threshold_pct,
                daily_profit_target=daily_profit_target,
                daily_loss_limit=daily_loss_limit,
                qty_per_trade=qty_per_trade,
            )
        )
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._decision_interval = decision_interval_minutes
        self._enable_llm_direction = enable_llm_direction
        self._enable_llm_stop = enable_llm_stop
        self._qty_per_trade = qty_per_trade
        self._daily_profit_target = daily_profit_target
        self._daily_loss_limit = daily_loss_limit

        # Mutable per-session state
        self._llm = None  # initialized in on_session_start
        self._steps_since_decision: int = 0
        self._bar_buffer: list[MarketEvent] = []
        self._trading_stopped: bool = False

        # Trade tracking for LLM stop prompt
        self._n_trades: int = 0
        self._n_wins: int = 0

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def on_session_start(self, config: RunConfig) -> None:
        """Initialize LLM client if LLM features are enabled."""
        if self._enable_llm_direction or self._enable_llm_stop:
            from trading.tools.llm_client import LLMClient

            self._llm = LLMClient(
                provider=self._llm_provider,
                model=self._llm_model,
            )

    def on_day_open(self, date: str, state: TradingState) -> None:
        """Reset rule engine + strategy state at each day's open."""
        self._rule_engine.on_day_open()
        self._steps_since_decision = 0
        self._bar_buffer = []
        self._trading_stopped = False
        self._n_trades = 0
        self._n_wins = 0

    # ------------------------------------------------------------------
    # Core decision logic
    # ------------------------------------------------------------------

    def on_step(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Execute one step of the hybrid strategy.

        Flow:
        1. Update realized P&L from confirmed fills
        2. Rule engine: check per-position exits (every step)
        3. Rule engine: check daily limits
        4. At decision intervals: LLM stop check
        5. At decision intervals: LLM direction (only if flat)
        6. If both LLM flags off: pure rule mode (trend signal)
        """
        self._bar_buffer.append(market_event)

        # 0. Update realized P&L from confirmed fills
        self._update_realized_from_fills(state)

        # 1. RULE ENGINE: check per-position exits (every step)
        exit_intents = self._rule_engine.check_position_exits(portfolio.positions)

        # 2. RULE ENGINE: daily limits
        if self._rule_engine.daily_realized >= self._daily_profit_target:
            exit_intents += self._flatten_remaining(
                portfolio, "Daily profit target reached"
            )
            self._trading_stopped = True
            self._rule_engine._trading_stopped = True
            return exit_intents

        if self._rule_engine.daily_realized <= -self._daily_loss_limit:
            exit_intents += self._flatten_remaining(
                portfolio, "Daily loss limit reached"
            )
            self._trading_stopped = True
            self._rule_engine._trading_stopped = True
            return exit_intents

        if exit_intents:
            return exit_intents  # rule exits only, no daily limit yet

        if self._trading_stopped:
            return []

        # 3. Decision interval gating
        self._steps_since_decision += 1
        if self._steps_since_decision < self._decision_interval:
            return []
        self._steps_since_decision = 0

        # 4. LLM: should we stop trading?
        if self._enable_llm_stop:
            if self._call_llm_stop_check(state):
                self._trading_stopped = True
                return self._flatten_all(portfolio, "LLM: enough for today")

        # 5. Direction decision (only if no position)
        if portfolio.positions:
            return []

        if self._enable_llm_direction:
            return self._call_llm_direction(market_event, portfolio, state)
        else:
            # Pure rule mode: use trend signal
            return self._rule_trend_entry(market_event, portfolio)

    # ------------------------------------------------------------------
    # Realized P&L tracking
    # ------------------------------------------------------------------

    def _update_realized_from_fills(self, state: TradingState) -> None:
        """Update realized P&L from confirmed fill events in state.fills."""
        if not state.fills:
            return

        old_realized = self._rule_engine.daily_realized
        self._rule_engine.update_realized_from_fills(state.fills)
        new_realized = self._rule_engine.daily_realized

        # Track trades for the LLM stop prompt
        for fill in state.fills:
            if fill.side == "SELL":
                self._n_trades += 1
                # A win if realized increased
                fill_pnl = new_realized - old_realized
                if fill_pnl > 0:
                    self._n_wins += 1

    # ------------------------------------------------------------------
    # Rule engine exits
    # ------------------------------------------------------------------

    def _flatten_remaining(
        self,
        portfolio: PortfolioSnapshot,
        reason: str,
    ) -> list[TradeIntent]:
        """Generate close intents (SELL for LONG, BUY for SHORT) for all open positions."""
        intents = []
        for pos in portfolio.positions:
            intents.append(
                TradeIntent(
                    contract=pos.contract,
                    side="SELL" if pos.side == "LONG" else "BUY",
                    qty=abs(pos.qty),
                    order_type="MARKET",
                    rationale=reason,
                    strategy_name="hybrid_intraday",
                )
            )
        return intents

    def _flatten_all(
        self,
        portfolio: PortfolioSnapshot,
        reason: str,
    ) -> list[TradeIntent]:
        """Flatten all positions (alias for flatten_remaining)."""
        return self._flatten_remaining(portfolio, reason)

    # ------------------------------------------------------------------
    # Rule-only trend entry (pure rule mode)
    # ------------------------------------------------------------------

    def _rule_trend_entry(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
    ) -> list[TradeIntent]:
        """Use trend signal to decide direction and enter a position.

        Only used when ``enable_llm_direction=False`` (pure rule mode).
        """
        # Build SPY price history from bar buffer
        spy_prices = self._extract_spy_prices()
        signal = self._rule_engine.get_trend_signal(spy_prices)

        if signal == "neutral":
            return []

        # Find the appropriate option contract from market event
        contract, price = self._pick_option_from_event(market_event, signal)
        if not contract:
            return []

        return [
            TradeIntent(
                contract=contract,
                side="BUY",
                qty=self._qty_per_trade,
                order_type="MARKET",
                rationale=f"Trend signal: {signal}",
                strategy_name="hybrid_intraday",
            )
        ]

    def _extract_spy_prices(self) -> list[float]:
        """Extract SPY close prices from the bar buffer."""
        prices = []
        for event in self._bar_buffer:
            for bar in event.bars:
                if bar.symbol == "SPY" and bar.instrument_type == "STOCK":
                    prices.append(bar.close)
                    break
        return prices

    def _pick_option_from_event(
        self,
        market_event: MarketEvent,
        signal: str,
    ) -> tuple[str, float]:
        """Pick a call (bullish) or put (bearish) from the market event bars.

        Returns (contract, price) or ("", 0.0) if nothing suitable found.
        """
        target_type = "C" if signal == "bullish" else "P"
        for bar in market_event.bars:
            if bar.instrument_type == "OPTION" and target_type in bar.symbol:
                return bar.symbol, bar.close
        return "", 0.0

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    def _call_llm_stop_check(self, state: TradingState) -> bool:
        """Ask LLM whether to stop trading for today.

        Returns True if LLM says stop.
        """
        if self._llm is None:
            return False

        minutes_left = self._compute_minutes_to_close(state.current_ts)

        system_prompt, user_prompt = build_stop_prompt(
            realized=self._rule_engine.daily_realized,
            n_trades=self._n_trades,
            n_wins=self._n_wins,
            minutes_left=minutes_left,
            target=self._daily_profit_target,
            limit=self._daily_loss_limit,
        )

        try:
            raw = self._llm.call_raw(system_prompt, user_prompt)
            result = parse_stop_response(raw)
            logger.info(
                "LLM stop check: stop=%s, rationale=%s",
                result.get("stop"),
                result.get("rationale", ""),
            )
            return result.get("stop", False)
        except Exception as exc:
            logger.warning("LLM stop check failed: %s", exc)
            return False

    def _call_llm_direction(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Ask LLM for market direction and contract selection.

        Returns a list with a single BUY TradeIntent, or empty list.
        """
        if self._llm is None:
            return []

        spy_price, change_pct = self._get_spy_stats()
        available_options = self._get_available_options(market_event)

        system_prompt, user_prompt = build_direction_prompt(
            spy_price=spy_price,
            change_pct=change_pct,
            available_options=available_options,
        )

        try:
            raw = self._llm.call_raw(system_prompt, user_prompt)
            result = parse_direction_response(raw)
            logger.info(
                "LLM direction: %s, contract=%s, rationale=%s",
                result.get("direction"),
                result.get("contract", ""),
                result.get("rationale", ""),
            )

            direction = result.get("direction", "neutral")
            if direction == "neutral":
                return []

            contract = result.get("contract", "")
            qty = result.get("qty", self._qty_per_trade)
            if not contract:
                return []

            return [
                TradeIntent(
                    contract=contract,
                    side="BUY",
                    qty=int(qty),
                    order_type="MARKET",
                    rationale=result.get("rationale", f"LLM direction: {direction}"),
                    strategy_name="hybrid_intraday",
                )
            ]
        except Exception as exc:
            logger.warning("LLM direction call failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_spy_stats(self) -> tuple[float, float]:
        """Get current SPY price and 15-bar change percentage."""
        spy_prices = self._extract_spy_prices()
        if not spy_prices:
            return 0.0, 0.0

        current = spy_prices[-1]
        if len(spy_prices) >= 16:
            old = spy_prices[-16]
        elif len(spy_prices) >= 2:
            old = spy_prices[0]
        else:
            return current, 0.0

        if old == 0:
            return current, 0.0

        change_pct = (current - old) / old * 100.0
        return current, change_pct

    def _get_available_options(self, market_event: MarketEvent) -> list[dict]:
        """Extract available options from the market event for the LLM prompt."""
        options = []
        for bar in market_event.bars:
            if bar.instrument_type == "OPTION":
                opt_type = "call" if "C" in bar.symbol else "put"
                options.append({
                    "contract": bar.symbol,
                    "price": bar.close,
                    "type": opt_type,
                })
        return options[:4]

    @staticmethod
    def _compute_minutes_to_close(current_ts: str) -> int:
        """Estimate minutes remaining until market close (16:00 ET).

        Properly converts the timestamp to ET before extracting HH:MM,
        since PMB timestamps are UTC.  Returns 0 if past close.
        """
        from trading.agents.strategy._tz_utils import minutes_to_close_et
        return minutes_to_close_et(current_ts)

    def save_checkpoint(self) -> dict:
        raise NotImplementedError(f"{self.__class__.__name__} does not support checkpoint yet (Phase 1 scope or Phase 2 scenario)")

    def load_checkpoint(self, state: dict) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support checkpoint yet")
