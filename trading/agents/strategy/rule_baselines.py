"""Rule-based baseline strategies (no LLM).

Calibration baselines for the benchmark: do the LLM agents actually beat
simple deterministic rules? Every strategy here is fully deterministic,
consumes only the option chain the runner already preloads into
``market_event.bars`` (per ``data_policy.chain_filter_*``), and mirrors the
risk conventions of its LLM counterpart scenario.

Registered baselines:

- ``buy_and_hold``        (any freq) — hold ``initial_positions``, never trade
- ``intraday_momentum``   (1m) — opening-range breakout -> target-delta 0DTE
  option, rule SL/TP, flat before close
- ``intraday_delta_rule`` (1m) — 15-bar trend signal -> target-delta option,
  reverse-on-flip, rule SL/TP, flat before close
- ``intraday_vertical``   (1m) — trend signal -> defined-risk debit vertical
  (buy ~0.40Δ, sell ~0.20Δ), held to EOD flatten
- ``covered_call_rule``   (1d) — overlay: sell target-delta OTM calls against
  held shares on rebalance days; expiry/assignment guards from overlay base
- ``protective_put_rule`` (1d) — overlay: keep held shares hedged with
  target-delta OTM puts; roll via expiry backstop
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.agents.strategy.multi_agent import MultiAgentMixin
from trading.agents.strategy.overlay import (
    OverlayHedgeStrategy,
    OverlayProfitStrategy,
)
from trading.agents.strategy.rule_engine import RuleEngine, RuleEngineParams
from trading.core.registry import register_strategy
from trading.core.schemas import TradeIntent

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import MarketEvent, PortfolioSnapshot
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)

_CHECKPOINT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Shared contract selection
# ---------------------------------------------------------------------------

def pick_by_delta(
    chain_dicts: list[dict],
    right: str,
    target_delta: float,
    dte_min: int | None = None,
    dte_max: int | None = None,
    min_price: float = 0.05,
    max_delta_error: float = 0.15,
) -> dict | None:
    """Pick the chain entry of ``right`` whose |delta| is closest to target.

    Returns None when the closest match is further than ``max_delta_error``
    from the target (the loaded chain may simply not contain a suitable
    contract — better to skip the trade than to trade the wrong delta).
    Falls back to the max-volume entry only when no delta is populated.
    Entries without a positive close price are skipped.
    """
    candidates = []
    for c in chain_dicts:
        if c.get("right") != right:
            continue
        if (c.get("close") or 0.0) < min_price:
            continue
        dte = c.get("dte")
        if dte is None:
            continue
        if dte_min is not None and dte < dte_min:
            continue
        if dte_max is not None and dte > dte_max:
            continue
        candidates.append(c)
    if not candidates:
        return None

    with_delta = [c for c in candidates if c.get("delta") is not None]
    if with_delta:
        best = min(with_delta, key=lambda c: abs(abs(c["delta"]) - target_delta))
        if abs(abs(best["delta"]) - target_delta) > max_delta_error:
            return None
        return best
    # Delta unavailable: nearest strike to max-volume proxy for ATM
    return max(candidates, key=lambda c: c.get("volume") or 0)


def _budget_qty(price: float, cash: float, qty: int, budget_pct: float = 0.5) -> int:
    """Clamp qty so premium cost stays within budget_pct of available cash."""
    if price <= 0:
        return 0
    affordable = int((cash * budget_pct) / (price * 100))
    return max(0, min(qty, affordable))


_KNOWN_PASSTHROUGH_KEYS = {
    "llm_provider", "llm_model", "llm_api_key", "llm_base_url",
    "llm_reasoning_effort", "mode",
}


def _warn_unused_kwargs(cls_name: str, kwargs: dict) -> None:
    """Baselines are deterministic — a typo'd YAML param silently falling
    back to a default would corrupt the benchmark. Make it loud."""
    unknown = {k: v for k, v in kwargs.items() if k not in _KNOWN_PASSTHROUGH_KEYS}
    if unknown:
        logger.warning("[%s] ignoring unknown strategy params: %s", cls_name, unknown)


# ---------------------------------------------------------------------------
# buy_and_hold — the do-nothing passive baseline
# ---------------------------------------------------------------------------

@register_strategy("buy_and_hold")
class BuyAndHoldStrategy(BaseStrategy):
    """Hold whatever ``experiment.initial_positions`` seeded; never trade.

    With stock in ``initial_positions`` this is the classic buy-and-hold
    baseline; with cash only it is the all-cash floor.
    """

    def __init__(self, **kwargs):
        super().__init__()

    def on_step(self, market_event, portfolio, state) -> list[TradeIntent]:
        return []

    def save_checkpoint(self) -> dict:
        return {"_version": _CHECKPOINT_SCHEMA_VERSION}

    def load_checkpoint(self, state: dict) -> None:
        pass


# ---------------------------------------------------------------------------
# Intraday rule baselines (1m)
# ---------------------------------------------------------------------------

class _IntradayRuleBase(BaseStrategy, MultiAgentMixin):
    """Shared plumbing for 1m rule baselines: fills → RuleEngine SL/TP,
    daily limits, EOD flatten, chain parsing, underlying price history."""

    REQUIRED_FREQUENCIES = frozenset({"1m"})

    def __init__(
        self,
        qty_per_trade: int = 2,
        stop_loss_pct: float = -25.0,
        take_profit_pct: float = 50.0,
        daily_profit_target: float = 200.0,
        daily_loss_limit: float = 200.0,
        trend_threshold_pct: float = 0.05,
        delta_target: float = 0.40,
        dte_max: int = 0,
        max_entries_per_day: int = 2,
        flatten_minutes_before_close: int = 10,
        **kwargs,
    ):
        super().__init__()
        _warn_unused_kwargs(self.__class__.__name__, kwargs)
        self._rule_engine = RuleEngine(RuleEngineParams(
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trend_threshold_pct=trend_threshold_pct,
            daily_profit_target=daily_profit_target,
            daily_loss_limit=daily_loss_limit,
            qty_per_trade=qty_per_trade,
        ))
        self._qty_per_trade = qty_per_trade
        self._delta_target = delta_target
        self._dte_max = dte_max
        self._max_entries = max_entries_per_day
        self._flatten_before_close = flatten_minutes_before_close
        self._underlying: str = ""
        self._entries_today: int = 0
        self._flattened_today: bool = False
        self._steps_since_flatten: int = 0
        self._close_attempts: dict[str, int] = {}

    # -- lifecycle ------------------------------------------------------

    def on_session_start(self, config: RunConfig) -> None:
        if config.experiment.universe:
            self._underlying = config.experiment.universe[0]

    def on_day_open(self, date: str, state: TradingState) -> None:
        self._rule_engine.on_day_open()
        self._entries_today = 0
        self._flattened_today = False
        self._steps_since_flatten = 0
        self._close_attempts = {}

    # -- shared step frame ---------------------------------------------

    def on_step(self, market_event, portfolio, state) -> list[TradeIntent]:
        self._track_fills(state)

        # Exercise-residue cleanup: an option that expired ITM (because a
        # close order after the final bar can never fill) is auto-exercised
        # by PMB into a STOCK leg. Intraday baselines never hold stock, so
        # any stock position is residue — close it, with the same per-
        # contract attempt cap as the flatten path.
        residue = self._close_stock_residue(portfolio)
        if residue:
            return residue

        # EOD flatten has absolute priority. Retry a bounded number of
        # times: after the final bar of a 0DTE contract a MARKET close can
        # NEVER fill, and every re-submitted order stays pending — an
        # unbounded retry loop accumulates pending closes that later fill
        # together and flip the position massively short.
        minutes_left = self._minutes_to_close(state)
        if minutes_left <= self._flatten_before_close:
            open_opts = self._option_positions(portfolio)
            if open_opts and (not self._flattened_today
                              or self._steps_since_flatten >= 3):
                self._flattened_today = True
                self._steps_since_flatten = 0
                return self._flatten(portfolio, "EOD flatten")
            self._steps_since_flatten += 1
            return []

        # Per-position SL/TP + daily limits
        exit_intents = self._rule_engine.check_position_exits(portfolio.positions)
        if exit_intents and self._manage_exits_per_leg():
            return exit_intents
        limit_hit = self._check_daily_limit(portfolio)
        if limit_hit:
            self._rule_engine._trading_stopped = True
            return self._flatten(portfolio, f"Daily {limit_hit}")
        if self._rule_engine.trading_stopped:
            return []

        return self._decide(market_event, portfolio, state)

    def _close_stock_residue(self, portfolio) -> list[TradeIntent]:
        intents = []
        for pos in portfolio.positions:
            if pos.contract.startswith("O:") or pos.qty == 0:
                continue
            if not self._may_attempt_close(pos.contract):
                continue
            intents.append(TradeIntent(
                contract=pos.contract,
                side="SELL" if pos.qty > 0 else "BUY",
                qty=abs(pos.qty),
                order_type="MARKET",
                rationale="close exercise-residue stock leg",
                strategy_name=self.__class__.STRATEGY_NAME,
            ))
        return intents

    def _may_attempt_close(self, contract: str) -> bool:
        """At most 2 close submissions per contract per day. An unfillable
        close (expired contract) must not be re-submitted forever."""
        n = self._close_attempts.get(contract, 0)
        if n >= 2:
            return False
        self._close_attempts[contract] = n + 1
        return True

    def _track_fills(self, state) -> None:
        """Feed fills into the RuleEngine long-only book (BUY=open, SELL=
        close). Strategies whose fills violate that convention override."""
        if state.fills:
            self._rule_engine.update_realized_from_fills(state.fills)

    def _check_daily_limit(self, portfolio) -> str | None:
        return self._rule_engine.check_daily_limits()

    def _decide(self, market_event, portfolio, state) -> list[TradeIntent]:
        raise NotImplementedError

    def _manage_exits_per_leg(self) -> bool:
        """Whether RuleEngine per-position exits apply (spreads opt out)."""
        return True

    # -- helpers --------------------------------------------------------

    def _minutes_to_close(self, state) -> int:
        from trading.agents.strategy._tz_utils import minutes_to_close_et
        return minutes_to_close_et(state.current_ts)

    def _underlying_closes(self, market_event) -> list[float]:
        bars = (market_event.bar_history or {}).get(self._underlying, [])
        return [b.close for b in bars if b.instrument_type == "STOCK"]

    def _spot(self, market_event) -> float:
        bar = self._stock_bar(market_event)
        return bar.close if bar is not None else 0.0

    def _stock_bar(self, market_event):
        for bar in market_event.bars:
            if bar.instrument_type == "STOCK" and bar.symbol == self._underlying:
                return bar
        return None

    def _option_positions(self, portfolio) -> list:
        return [p for p in portfolio.positions
                if p.contract.startswith("O:") and p.qty != 0]

    def _flatten(self, portfolio, reason: str) -> list[TradeIntent]:
        intents = []
        for pos in self._option_positions(portfolio):
            if not self._may_attempt_close(pos.contract):
                continue
            intents.append(TradeIntent(
                contract=pos.contract,
                side="SELL" if pos.side == "LONG" else "BUY",
                qty=abs(pos.qty),
                order_type="MARKET",
                rationale=reason,
                strategy_name=self.__class__.STRATEGY_NAME,
            ))
        return intents

    def _entry_intent(self, contract: dict, qty: int, reason: str) -> TradeIntent:
        return TradeIntent(
            contract=contract["ticker"],
            side="BUY",
            qty=qty,
            order_type="MARKET",
            symbol=self._underlying,
            rationale=reason,
            strategy_name=self.__class__.STRATEGY_NAME,
        )

    # -- checkpoint -----------------------------------------------------

    def save_checkpoint(self) -> dict:
        book = {
            contract: fill.model_dump()
            for contract, fill in self._rule_engine._open_positions.items()
        }
        return {
            "_version": _CHECKPOINT_SCHEMA_VERSION,
            "entries_today": self._entries_today,
            "flattened_today": self._flattened_today,
            "steps_since_flatten": self._steps_since_flatten,
            "close_attempts": dict(self._close_attempts),
            "daily_realized": self._rule_engine.daily_realized,
            "trading_stopped": self._rule_engine.trading_stopped,
            "open_position_book": book,
        }

    def load_checkpoint(self, state: dict) -> None:
        from trading.core.schemas import FillEvent
        self._entries_today = state.get("entries_today", 0)
        self._flattened_today = state.get("flattened_today", False)
        self._steps_since_flatten = state.get("steps_since_flatten", 0)
        self._close_attempts = dict(state.get("close_attempts") or {})
        self._rule_engine._daily_realized = state.get("daily_realized", 0.0)
        self._rule_engine._trading_stopped = state.get("trading_stopped", False)
        self._rule_engine._open_positions = {
            contract: FillEvent(**d)
            for contract, d in (state.get("open_position_book") or {}).items()
        }


@register_strategy("intraday_momentum")
class IntradayMomentumStrategy(_IntradayRuleBase):
    """Opening-range breakout: track the underlying's high/low over the first
    ``open_range_minutes``; on a close beyond the range (± buffer), buy a
    target-delta option in the breakout direction. Rule SL/TP, EOD flat."""

    STRATEGY_NAME = "intraday_momentum"

    def __init__(self, open_range_minutes: int = 30,
                 breakout_buffer_pct: float = 0.05, **kwargs):
        super().__init__(**kwargs)
        self._range_minutes = open_range_minutes
        self._buffer_pct = breakout_buffer_pct
        self._bars_seen = 0
        self._range_high: float | None = None
        self._range_low: float | None = None

    def save_checkpoint(self) -> dict:
        state = super().save_checkpoint()
        state.update(bars_seen=self._bars_seen, range_high=self._range_high,
                     range_low=self._range_low)
        return state

    def load_checkpoint(self, state: dict) -> None:
        super().load_checkpoint(state)
        self._bars_seen = state.get("bars_seen", 0)
        self._range_high = state.get("range_high")
        self._range_low = state.get("range_low")

    def on_day_open(self, date: str, state: TradingState) -> None:
        super().on_day_open(date, state)
        self._bars_seen = 0
        self._range_high = None
        self._range_low = None

    def _decide(self, market_event, portfolio, state) -> list[TradeIntent]:
        bar = self._stock_bar(market_event)
        if bar is None or bar.close <= 0:
            return []
        spot = bar.close

        self._bars_seen += 1
        if self._bars_seen <= self._range_minutes:
            # True intrabar extremes, not closes — a close-based range is
            # systematically narrower and fires spurious breakouts.
            self._range_high = max(self._range_high or bar.high, bar.high)
            self._range_low = min(self._range_low or bar.low, bar.low)
            return []
        if self._range_high is None or self._range_low is None:
            return []
        if self._option_positions(portfolio) or self._entries_today >= self._max_entries:
            return []

        buffer = self._buffer_pct / 100.0
        if spot > self._range_high * (1 + buffer):
            right, label = "C", "breakout above opening range"
        elif spot < self._range_low * (1 - buffer):
            right, label = "P", "breakdown below opening range"
        else:
            return []

        chain, _ = self._parse_chain_dicts(market_event, self._underlying, state.current_date)
        pick = pick_by_delta(chain, right, self._delta_target, dte_max=self._dte_max)
        if pick is None:
            return []
        qty = _budget_qty(pick["close"], portfolio.cash, self._qty_per_trade)
        if qty <= 0:
            return []
        self._entries_today += 1
        return [self._entry_intent(
            pick, qty,
            f"{label}: spot={spot:.2f} range=[{self._range_low:.2f},{self._range_high:.2f}]",
        )]


@register_strategy("intraday_delta_rule")
class IntradayDeltaRuleStrategy(_IntradayRuleBase):
    """Pure delta rule: 15-bar trend signal on the underlying decides
    direction; hold one target-delta option in that direction, close on
    signal flip. Rule SL/TP, EOD flat."""

    STRATEGY_NAME = "intraday_delta_rule"

    def __init__(self, decision_interval_minutes: int = 15, **kwargs):
        super().__init__(**kwargs)
        self._interval = decision_interval_minutes
        self._steps_since_decision = 0

    def save_checkpoint(self) -> dict:
        state = super().save_checkpoint()
        state["steps_since_decision"] = self._steps_since_decision
        return state

    def load_checkpoint(self, state: dict) -> None:
        super().load_checkpoint(state)
        self._steps_since_decision = state.get("steps_since_decision", 0)

    def on_day_open(self, date: str, state: TradingState) -> None:
        super().on_day_open(date, state)
        self._steps_since_decision = 0

    def _decide(self, market_event, portfolio, state) -> list[TradeIntent]:
        self._steps_since_decision += 1
        if self._steps_since_decision < self._interval:
            return []
        self._steps_since_decision = 0

        signal = self._rule_engine.get_trend_signal(self._underlying_closes(market_event))
        held = self._option_positions(portfolio)

        # Signal flip → close opposing position first
        if held:
            from trading.agents.strategy.overlay import _parse_opra_right
            for pos in held:
                right = _parse_opra_right(pos.contract)
                opposing = (right == "C" and signal == "bearish") or (
                    right == "P" and signal == "bullish")
                if opposing:
                    return [TradeIntent(
                        contract=pos.contract,
                        side="SELL" if pos.side == "LONG" else "BUY",
                        qty=abs(pos.qty), order_type="MARKET",
                        rationale=f"trend flipped to {signal}",
                        strategy_name=self.STRATEGY_NAME,
                    )]
            return []

        if signal == "neutral" or self._entries_today >= self._max_entries:
            return []
        right = "C" if signal == "bullish" else "P"
        chain, _ = self._parse_chain_dicts(market_event, self._underlying, state.current_date)
        pick = pick_by_delta(chain, right, self._delta_target, dte_max=self._dte_max)
        if pick is None:
            return []
        qty = _budget_qty(pick["close"], portfolio.cash, self._qty_per_trade)
        if qty <= 0:
            return []
        self._entries_today += 1
        return [self._entry_intent(pick, qty, f"trend signal: {signal}")]


@register_strategy("intraday_vertical")
class IntradayVerticalStrategy(_IntradayRuleBase):
    """Defined-risk debit vertical: on a trend signal buy the ~0.40Δ option
    and sell the ~0.20Δ option of the same right/expiry. Held to the EOD
    flatten (0DTE spread), no per-leg SL/TP — max loss is the debit paid."""

    STRATEGY_NAME = "intraday_vertical"

    def __init__(self, decision_interval_minutes: int = 15,
                 short_delta_target: float = 0.20, **kwargs):
        super().__init__(**kwargs)
        self._interval = decision_interval_minutes
        self._short_delta = short_delta_target
        self._steps_since_decision = 0
        self._spread_seq = 0

    def save_checkpoint(self) -> dict:
        state = super().save_checkpoint()
        state.update(steps_since_decision=self._steps_since_decision,
                     spread_seq=self._spread_seq)
        return state

    def load_checkpoint(self, state: dict) -> None:
        super().load_checkpoint(state)
        self._steps_since_decision = state.get("steps_since_decision", 0)
        self._spread_seq = state.get("spread_seq", 0)

    def on_day_open(self, date: str, state: TradingState) -> None:
        super().on_day_open(date, state)
        self._steps_since_decision = 0

    def _manage_exits_per_leg(self) -> bool:
        return False  # legs are a unit; only EOD flatten closes them

    def _track_fills(self, state) -> None:
        # RuleEngine's BUY=open/SELL=close book cannot represent a spread's
        # opening SELL leg — it would drop the short leg's P&L and corrupt
        # daily limits. The spread is defined-risk; limits use daily_pnl.
        pass

    def _check_daily_limit(self, portfolio) -> str | None:
        pnl = portfolio.daily_pnl
        if pnl >= self._rule_engine.params.daily_profit_target:
            return "profit_target"
        if pnl <= -self._rule_engine.params.daily_loss_limit:
            return "loss_limit"
        return None

    def _decide(self, market_event, portfolio, state) -> list[TradeIntent]:
        self._steps_since_decision += 1
        if self._steps_since_decision < self._interval:
            return []
        self._steps_since_decision = 0

        if self._option_positions(portfolio) or self._entries_today >= self._max_entries:
            return []
        signal = self._rule_engine.get_trend_signal(self._underlying_closes(market_event))
        if signal == "neutral":
            return []

        right = "C" if signal == "bullish" else "P"
        chain, _ = self._parse_chain_dicts(market_event, self._underlying, state.current_date)
        long_leg = pick_by_delta(chain, right, self._delta_target, dte_max=self._dte_max)
        if long_leg is None:
            return []
        same_expiry = [c for c in chain if c.get("expiry") == long_leg.get("expiry")
                       and c.get("ticker") != long_leg.get("ticker")]
        short_leg = pick_by_delta(same_expiry, right, self._short_delta, dte_max=self._dte_max)
        if short_leg is None or short_leg["ticker"] == long_leg["ticker"]:
            return []

        debit = max(0.01, long_leg["close"] - short_leg["close"])
        qty = _budget_qty(debit, portfolio.cash, self._qty_per_trade)
        if qty <= 0:
            return []
        self._entries_today += 1
        self._spread_seq += 1
        group = f"{self.STRATEGY_NAME}-{state.current_date}-{self._spread_seq}"
        reason = f"debit vertical on {signal} trend (net debit ~${debit:.2f})"
        return [
            TradeIntent(contract=long_leg["ticker"], side="BUY", qty=qty,
                        order_type="MARKET", spread_group=group,
                        symbol=self._underlying, rationale=reason,
                        strategy_name=self.STRATEGY_NAME),
            TradeIntent(contract=short_leg["ticker"], side="SELL", qty=qty,
                        order_type="MARKET", spread_group=group,
                        symbol=self._underlying, rationale=reason,
                        strategy_name=self.STRATEGY_NAME),
        ]


# ---------------------------------------------------------------------------
# Overlay rule baselines (1d) — reuse the overlay guards, drop the LLM
# ---------------------------------------------------------------------------

@register_strategy("covered_call_rule")
class CoveredCallRuleStrategy(OverlayProfitStrategy):
    """Systematic covered call: on every rebalance day sell OTM calls at
    ``call_delta_target`` (7-45 DTE window) against all uncovered shares.
    Inherits the deterministic guards from the LLM overlay (expiry backstop,
    deep-ITM assignment close, share-deficit rebuy) — only the decision
    layer is replaced by a fixed rule."""

    STRATEGY_NAME = "covered_call_rule"

    def __init__(self, call_delta_target: float = 0.30, **kwargs):
        kwargs.pop("llm_provider", None)
        kwargs.pop("llm_model", None)
        super().__init__(llm_provider="", llm_model="", **kwargs)
        self._call_delta_target = call_delta_target

    def on_step(self, market_event, portfolio, state) -> list[TradeIntent]:
        from trading.agents.strategy.overlay import compute_covered_call_capacity

        self._detect_and_log_assignment(portfolio, self.STRATEGY_NAME)

        # 1. Deterministic guards (same order as the LLM overlay)
        expiry_intents = self._generate_expiry_close_orders(
            state.current_date, portfolio, self.STRATEGY_NAME)
        if expiry_intents:
            return expiry_intents
        risk_intents = self._check_assignment_risk(portfolio)
        if risk_intents:
            return risk_intents
        deficit_intents = self._check_share_deficit(portfolio)
        if deficit_intents:
            return deficit_intents

        # 2. Rule rebalance: write calls on all uncovered shares
        if not self._should_rebalance(state):
            return []
        _, _, avail_calls = compute_covered_call_capacity(portfolio, self._underlying)
        if avail_calls <= 0:
            self._last_rebalance = state.current_date
            return []

        chain, _ = self._parse_chain_dicts(market_event, self._underlying, state.current_date)
        pick = pick_by_delta(chain, "C", self._call_delta_target,
                             dte_min=self._dte_min, dte_max=self._dte_max)
        self._last_rebalance = state.current_date
        if pick is None:
            logger.info("[%s] no call in delta/DTE window — skip", self.STRATEGY_NAME)
            return []
        return [TradeIntent(
            contract=pick["ticker"], side="SELL", qty=avail_calls,
            order_type="MARKET", symbol=self._underlying,
            rationale=(f"covered call: delta={pick.get('delta')} "
                       f"dte={pick.get('dte')} premium=${pick['close']:.2f}"),
            strategy_name=self.STRATEGY_NAME,
        )]


@register_strategy("protective_put_rule")
class ProtectivePutRuleStrategy(OverlayHedgeStrategy):
    """Systematic protective put: keep held shares hedged with long OTM puts
    at ``put_delta_target`` (7-45 DTE). Buys the missing coverage on
    rebalance days; expiry backstop sells ITM puts / lets OTM expire, after
    which coverage is re-bought on the next rebalance."""

    STRATEGY_NAME = "protective_put_rule"

    def __init__(self, put_delta_target: float = 0.30, **kwargs):
        kwargs.pop("llm_provider", None)
        kwargs.pop("llm_model", None)
        super().__init__(llm_provider="", llm_model="", **kwargs)
        self._put_delta_target = put_delta_target

    def _long_put_coverage(self, portfolio) -> int:
        from trading.agents.strategy.overlay import _parse_opra_right
        total = 0
        for pos in portfolio.positions:
            if (pos.contract.startswith(f"O:{self._underlying}") and pos.qty > 0
                    and _parse_opra_right(pos.contract) == "P"):
                total += pos.qty
        return total

    def on_step(self, market_event, portfolio, state) -> list[TradeIntent]:
        self._detect_and_log_assignment(portfolio, self.STRATEGY_NAME)

        expiry_intents = self._generate_expiry_close_orders(
            state.current_date, portfolio, self.STRATEGY_NAME)
        if expiry_intents:
            return expiry_intents

        if not self._should_rebalance(state):
            return []
        shares = sum(p.qty for p in portfolio.positions
                     if p.contract == self._underlying and p.qty > 0)
        needed = shares // 100 - self._long_put_coverage(portfolio)
        self._last_rebalance = state.current_date
        if needed <= 0:
            return []

        chain, _ = self._parse_chain_dicts(market_event, self._underlying, state.current_date)
        pick = pick_by_delta(chain, "P", self._put_delta_target,
                             dte_min=self._dte_min, dte_max=self._dte_max)
        if pick is None:
            logger.info("[%s] no put in delta/DTE window — skip", self.STRATEGY_NAME)
            return []
        qty = _budget_qty(pick["close"], portfolio.cash, needed, budget_pct=0.9)
        if qty <= 0:
            return []
        return [TradeIntent(
            contract=pick["ticker"], side="BUY", qty=qty,
            order_type="MARKET", symbol=self._underlying,
            rationale=(f"protective put: delta={pick.get('delta')} "
                       f"dte={pick.get('dte')} cost=${pick['close']:.2f} "
                       f"coverage {qty}/{max(needed, 0)}"),
            strategy_name=self.STRATEGY_NAME,
        )]


# ---------------------------------------------------------------------------
# Earnings: mechanical ATM straddle baseline
# ---------------------------------------------------------------------------

from trading.agents.strategy.earnings_bet import (  # noqa: E402
    EarningsBetStrategy,
    build_event_windows,
)


@register_strategy("earnings_straddle")
class EarningsStraddleBaseline(EarningsBetStrategy):
    """Mechanical earnings baseline: buy an ATM straddle at T-1 close, hold
    through the print, T+2 mechanical force close. No LLM calls.

    Reuses the full ``EarningsBetStrategy`` event-window machinery (ESP
    calendar discovery, phase handling, T+2 auto close) so the protocol is
    identical to the LLM earnings agents; only the decision layer differs:

    - Last close before the print (BMO: last trading day before T; AMC: T
      itself, 15:30-15:55 ET): buy the ATM straddle — nearest expiry in the
      configured [dte_min, dte_max] window, strike minimising |C - P|
      (put-call-parity ATM pick, needs no spot quote), qty from the same
      ``max_position_cost`` budget the LLM agents get.
    - Post-earnings days: hold (no adjustment, no stop-loss, no EOD flatten).
    - T+2: inherited mechanical force close.
    """

    STRATEGY_NAME = "earnings_straddle"

    # NYSE full-closure dates 2025 (t_minus_1 from _compute_trading_days is
    # weekend-aware only; entry-date resolution must also skip these).
    _MARKET_HOLIDAYS = frozenset({
        "2025-01-01", "2025-01-09",  # Jan 9: national day of mourning
        "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
        "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    })

    def _entry_date(self, window) -> str:
        """Last tradable close strictly before results are known.

        AMC: results at ~16:00 ET on T -> enter at T 15:30-15:55.
        BMO: results before open on T -> enter the last trading day before T.
        """
        from datetime import date, timedelta

        if not window.is_bmo:
            return window.earnings_date
        d = date.fromisoformat(window.earnings_date)
        while True:
            d -= timedelta(days=1)
            if d.weekday() < 5 and d.isoformat() not in self._MARKET_HOLIDAYS:
                return d.isoformat()

    def _handle_event_step(self, window, current_date, portfolio, state):
        # The entry day may be the AMC "skip" day, which the parent routes to
        # hold — intercept it (and holiday-shifted BMO T-1) here.
        if (not window.positions_opened
                and current_date == self._entry_date(window)
                and not (current_date >= window.t_plus_2 and self.auto_close_at_t2)):
            return self._enter_straddle(window, current_date, portfolio, state)
        return super()._handle_event_step(window, current_date, portfolio, state)

    def on_session_start(self, config) -> None:
        from pathlib import Path

        from trading.tools.earnings_loader import EarningsLoader

        self._universe = config.experiment.universe
        self._output_dir = Path(config.evaluation.output_dir)
        self._predictions_dir = self._output_dir / "predictions"
        self._predictions_dir.mkdir(parents=True, exist_ok=True)
        self._llm = None  # rule baseline: no LLM client
        self._earnings_loader = EarningsLoader(npp_base_url=self.npp_base_url)
        events = self._earnings_loader.get_earnings_calendar(
            tickers=self._universe,
            start=config.experiment.start[:10],
            end=config.experiment.end[:10],
        )
        self._event_windows = build_event_windows(events)
        logger.info(
            "EarningsStraddle: loaded %d earnings events for %d tickers",
            len(self._event_windows), len(self._universe),
        )

    # -- decision layer overrides (everything else inherited) --

    def _handle_pre_earnings(self, window, current_date, portfolio, state):
        return []  # entry handled by _handle_event_step -> _enter_straddle

    def _enter_straddle(self, window, current_date, portfolio, state):
        from trading.agents.strategy._tz_utils import minutes_to_close_et

        if minutes_to_close_et(state.current_ts) > 30:
            return []
        win_key = f"{window.ticker}|{window.earnings_date}"
        acted = self._pre_acted_dates.setdefault(win_key, set())
        if current_date in acted:
            return []
        acted.add(current_date)
        window.phase = "pre"

        pick = self._pick_atm_straddle(window.upq_ticker, current_date, portfolio)
        if pick is None:
            logger.warning(
                "EarningsStraddle: no ATM straddle found for %s on %s — skip",
                window.upq_ticker, current_date,
            )
            return []
        call_row, put_row, qty = pick
        window.entry_decided = True
        window.entry_date = current_date
        window.positions_opened = True
        cost = (call_row["close"] + put_row["close"]) * 100 * qty
        logger.info(
            "EarningsStraddle: %s T-1 entry %dx %s/%s (K=%s exp=%s) cost=$%.0f",
            window.upq_ticker, qty, call_row["ticker"], put_row["ticker"],
            call_row["strike"], call_row["expiry"], cost,
        )
        mk = dict(order_type="MARKET", symbol=window.upq_ticker,
                  spread_group="STRADDLE", strategy_name=self.STRATEGY_NAME)
        return [
            TradeIntent(
                contract=call_row["ticker"], side="BUY", qty=qty,
                rationale=(f"rule baseline: ATM straddle call leg K={call_row['strike']} "
                           f"exp={call_row['expiry']} px={call_row['close']:.2f}"),
                **mk),
            TradeIntent(
                contract=put_row["ticker"], side="BUY", qty=qty,
                rationale=(f"rule baseline: ATM straddle put leg K={put_row['strike']} "
                           f"exp={put_row['expiry']} px={put_row['close']:.2f}"),
                **mk),
        ]

    def _pick_atm_straddle(self, ticker, trade_date, portfolio):
        """Nearest-expiry, |C-P|-minimising strike; budgeted qty (or None)."""
        from trading.tools.upq_tools import query_option_chain

        rows: dict[tuple, dict] = {}
        for right in ("C", "P"):
            try:
                for r in query_option_chain(
                    upq_base_url=self.upq_base_url, underlying=ticker,
                    trade_date=trade_date, right=right,
                    dte_min=self.dte_min, dte_max=self.dte_max,
                ):
                    if r.get("close") and r["close"] > 0:
                        rows.setdefault((r["expiry"], r["strike"]), {})[right] = r
            except Exception as e:  # chain outage -> skip case
                logger.warning("EarningsStraddle chain query failed %s %s: %s",
                               ticker, right, e)
        pairs = {k: v for k, v in rows.items() if "C" in v and "P" in v}
        if not pairs:
            return None
        nearest_expiry = min(exp for exp, _ in pairs)
        atm_key = min(
            (k for k in pairs if k[0] == nearest_expiry),
            key=lambda k: abs(pairs[k]["C"]["close"] - pairs[k]["P"]["close"]),
        )
        call_row, put_row = pairs[atm_key]["C"], pairs[atm_key]["P"]
        unit_cost = (call_row["close"] + put_row["close"]) * 100
        budget = min(self.max_position_cost, portfolio.cash)
        qty = int(budget // unit_cost)
        if qty < 1:
            return None
        return call_row, put_row, min(qty, self.max_contracts)

    def _handle_post_earnings(self, window, current_date, portfolio, state):
        return []  # hold through the print — no adjustment

    def _eod_flatten(self, portfolio, current_date):
        return []  # hold overnight; T+2 force close is the only exit

    def _check_stop_loss(self, portfolio, already_closing):
        return []  # transparent baseline: no stop-loss
