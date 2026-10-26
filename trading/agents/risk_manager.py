"""RiskGateAgent — hard risk gate that evaluates every TradeIntent before execution.

Sequential checks (first failure rejects):
1. Circuit breaker: daily loss > circuit_breaker_loss_pct → reject ALL
2. Daily loss limit: daily_pnl < -max_daily_loss_pct → reject opening, allow closing
3. Max exposure: position value / equity > max_exposure_pct → REJECT
4. Max position per symbol: per-underlying cap
5. Order frequency: max_orders_per_day
6. Staleness: stale_minutes > max_stale_minutes → REJECT
7. Delta concentration: net portfolio delta > max_net_delta → REJECT
8. Vega concentration: net portfolio vega > max_net_vega → REJECT
9. Flatten before close: auto-generate SELL intents for all positions
"""

from __future__ import annotations

import logging
from datetime import datetime, time

from trading.core.config import DataPolicyConfig, RiskConfig
from trading.core.schemas import (
    PortfolioSnapshot,
    RiskDecision,
    TradeIntent,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)  # 4:00 PM ET


class RiskGateAgent:
    """Hard risk gate that evaluates every TradeIntent before execution.

    Args:
        risk_config: Risk parameters (exposure limits, circuit breaker, etc.).
        data_policy_config: Data policy parameters — staleness threshold
            (``max_stale_minutes``) is the single source of truth here.
    """

    def __init__(
        self,
        risk_config: RiskConfig,
        data_policy_config: DataPolicyConfig,
    ) -> None:
        self._config = risk_config
        self._max_stale_minutes = data_policy_config.max_stale_minutes

        # Internal mutable state (reset daily)
        self._daily_pnl: float = 0.0
        self._circuit_breaker_active: bool = False
        self._orders_today: int = 0

        # Idempotent-close guard ledger (prevent_position_inversion):
        # approved close qty per contract since the position snapshot last
        # changed. A change in observed qty means fills landed — reset.
        self._close_ledger: dict[str, int] = {}
        self._ledger_qty: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        intents: list[TradeIntent],
        portfolio: PortfolioSnapshot,
        state,  # TradingState — import avoided to prevent circular deps
    ) -> list[RiskDecision]:
        """Evaluate a batch of intents against all risk checks.

        Returns one ``RiskDecision`` per intent, plus any auto-generated
        flatten decisions if we are within the EOD flatten window.
        """
        # Sync daily P&L from portfolio
        self._daily_pnl = portfolio.daily_pnl_pct
        # Store state reference for helpers that need market event data
        self._current_state = state

        # Idempotent-close guard: refresh the ledger against the snapshot
        self._sync_close_ledger(portfolio)

        # 0. Circuit breaker check — blocks new entries but ALWAYS allows closing trades
        if self._check_circuit_breaker():
            decisions = []
            for intent in intents:
                if self._is_closing_trade(intent, portfolio):
                    guarded = self._guarded_close_decision(intent, portfolio)
                    if guarded is not None:
                        decisions.append(guarded)
                        continue
                    # Closing an existing position reduces risk — always allow
                    self._record_close(intent, portfolio, intent.qty)
                    decisions.append(RiskDecision(
                        original_intent=intent,
                        action="ALLOW",
                        reason="Circuit breaker active but closing trade allowed",
                        risk_notes=["circuit_breaker_close_allowed"],
                    ))
                else:
                    decisions.append(RiskDecision(
                        original_intent=intent,
                        action="REJECT",
                        reason="Circuit breaker active — daily loss exceeds "
                        f"{self._config.circuit_breaker_loss_pct:.1%}",
                        risk_notes=["circuit_breaker"],
                    ))
            return decisions

        # 1. EOD flatten overrides strategy intents
        if self._should_flatten(state):
            return self._generate_flatten_decisions(portfolio, state)

        # 2. Evaluate each intent individually, accumulating exposure
        # Track cumulative exposure for this batch so that previously-approved
        # intents are reflected when evaluating subsequent ones.
        projected_positions = {p.contract: p.qty for p in portfolio.positions}

        # Initialize projected delta/vega from existing portfolio positions
        projected_delta = sum(
            pos.qty * pos.delta * 100
            for pos in portfolio.positions
            if pos.delta is not None
        )
        projected_vega = sum(
            pos.qty * pos.vega * 100
            for pos in portfolio.positions
            if pos.vega is not None
        )

        # Pre-compute spread groups: map spread_group -> list of intents
        # A defined-risk spread must have exactly 2 option legs, same underlying,
        # same expiry, both BUY and SELL, and matching quantities.
        spread_group_intents: dict[str, list[TradeIntent]] = {}
        for intent in intents:
            if intent.spread_group:
                spread_group_intents.setdefault(intent.spread_group, []).append(intent)

        defined_risk_groups: set[str] = set()
        for group, legs in spread_group_intents.items():
            if self._is_defined_risk_spread(legs):
                defined_risk_groups.add(group)

        # Track which spread groups have already been counted for order frequency,
        # so that a 2-leg spread costs only 1 order slot, not 2.
        counted_spread_groups: set[str] = set()

        decisions: list[RiskDecision] = []
        for intent in intents:
            # Idempotent-close guard: reject duplicate closes outright,
            # pre-clamp over-sized ones before the standard checks run.
            remaining = self._close_guard_remaining(intent, portfolio)
            if remaining is not None and remaining <= 0:
                decisions.append(RiskDecision(
                    original_intent=intent,
                    action="REJECT",
                    reason="Idempotent-close guard: inventory already fully "
                    "closed by previously approved orders",
                    risk_notes=["close_guard_duplicate"],
                ))
                continue
            if remaining is not None and intent.qty > remaining:
                intent = intent.model_copy(update={"qty": remaining})
            decision = self._evaluate_single(
                intent, portfolio, state, projected_positions,
                projected_delta=projected_delta, projected_vega=projected_vega,
                defined_risk_groups=defined_risk_groups,
                counted_spread_groups=counted_spread_groups,
            )
            decisions.append(decision)
            if decision.action in ("ALLOW", "ADJUST"):
                qty = decision.adjusted_qty or intent.qty
                self._record_close(intent, portfolio, qty)
                contract = intent.contract
                if intent.side == "BUY":
                    projected_positions[contract] = projected_positions.get(contract, 0) + qty
                else:
                    projected_positions[contract] = projected_positions.get(contract, 0) - qty

                # Count spread groups as 1 order, not 1 per leg
                if (
                    intent.spread_group is not None
                    and intent.spread_group in defined_risk_groups
                ):
                    if intent.spread_group not in counted_spread_groups:
                        counted_spread_groups.add(intent.spread_group)
                        self._orders_today += 1
                else:
                    self._orders_today += 1

                # Update projected delta/vega accumulators
                sign = 1 if intent.side == "BUY" else -1
                bar_delta = self._get_bar_delta(contract, state)
                if bar_delta is not None:
                    projected_delta += sign * qty * bar_delta * 100
                bar_vega = self._get_bar_vega(contract, state)
                if bar_vega is not None:
                    projected_vega += sign * qty * bar_vega * 100

        # ── Spread leg quantity normalization ─────────────────────────────────
        # When ADJUST reduces one leg of a spread independently, the other leg
        # may retain a larger quantity, creating unintended naked short exposure.
        # After all individual evaluations, normalize every spread group so that
        # all approved legs share the same (minimum) quantity.
        # Example: BUY 8 adjusted→4, SELL 8 allowed→8 would leave 4 naked shorts.
        # After normalization: both legs are set to 4.
        for group in defined_risk_groups:
            group_decisions = [
                d for d in decisions
                if d.original_intent.spread_group == group
                and d.action in ("ALLOW", "ADJUST")
            ]
            if len(group_decisions) < 2:
                continue
            effective_qtys = [d.adjusted_qty or d.original_intent.qty for d in group_decisions]
            min_qty = min(effective_qtys)
            for d in group_decisions:
                current_qty = d.adjusted_qty or d.original_intent.qty
                if current_qty != min_qty:
                    logger.info(
                        "Spread leg normalized: %s qty %d→%d to match paired leg "
                        "(prevents naked exposure in group=%s)",
                        d.original_intent.contract, current_qty, min_qty, group,
                    )
                    d.adjusted_qty = min_qty
                    d.action = "ADJUST"
                    d.reason = (
                        f"{d.reason}; spread qty normalized {current_qty}→{min_qty} "
                        f"to match paired leg (group={group})"
                    )
                    if "spread_normalized" not in d.risk_notes:
                        d.risk_notes.append("spread_normalized")

        return decisions

    def on_new_day(self) -> None:
        """Reset daily accumulators at the start of a new trading day."""
        self._daily_pnl = 0.0
        self._circuit_breaker_active = False
        self._orders_today = 0

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _check_circuit_breaker(self) -> bool:
        """Activate and return True if daily loss exceeds circuit breaker threshold.

        Setting circuit_breaker_loss_pct <= 0 disables the circuit breaker entirely.
        """
        if self._config.circuit_breaker_loss_pct <= 0:
            return False
        # daily_pnl_pct is negative for losses, e.g. -0.04 means -4%
        if self._daily_pnl < -self._config.circuit_breaker_loss_pct:
            self._circuit_breaker_active = True
        return self._circuit_breaker_active

    def _is_closing_trade(self, intent: TradeIntent, portfolio: PortfolioSnapshot) -> bool:
        """Return True if this intent is closing an existing position.

        A SELL on a long position or a BUY on a short position is closing.
        """
        for pos in portfolio.positions:
            if pos.contract == intent.contract:
                if intent.side == "SELL" and pos.side == "LONG" and pos.qty > 0:
                    return True
                if intent.side == "BUY" and pos.side == "SHORT" and pos.qty < 0:
                    return True
        return False

    # ------------------------------------------------------------------
    # Idempotent-close guard (prevent_position_inversion)
    # ------------------------------------------------------------------

    def _sync_close_ledger(self, portfolio: PortfolioSnapshot) -> None:
        """Reset per-contract close budgets whenever the snapshot qty moves
        (fills have landed); drop contracts that left the book."""
        if not self._config.prevent_position_inversion:
            return
        current = {p.contract: p.qty for p in portfolio.positions}
        for c, q in current.items():
            if self._ledger_qty.get(c) != q:
                self._ledger_qty[c] = q
                self._close_ledger[c] = 0
        for c in list(self._ledger_qty):
            if c not in current:
                self._ledger_qty.pop(c, None)
                self._close_ledger.pop(c, None)

    def _close_guard_remaining(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot
    ) -> int | None:
        """Remaining closable qty for a closing-direction intent, or None
        when the guard is off / the intent is not closing-direction."""
        if not self._config.prevent_position_inversion:
            return None
        pos = next(
            (p for p in portfolio.positions if p.contract == intent.contract), None
        )
        if pos is None:
            return None
        closing = (intent.side == "SELL" and pos.qty > 0) or (
            intent.side == "BUY" and pos.qty < 0
        )
        if not closing:
            return None
        return max(abs(pos.qty) - self._close_ledger.get(intent.contract, 0), 0)

    def _record_close(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot, qty: int
    ) -> None:
        if self._close_guard_remaining(intent, portfolio) is None:
            return
        self._close_ledger[intent.contract] = (
            self._close_ledger.get(intent.contract, 0) + qty
        )

    def _guarded_close_decision(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot
    ) -> RiskDecision | None:
        """Guard verdict for the circuit-breaker close-allow path: REJECT a
        duplicate close, ADJUST an over-sized one, None = full qty fine."""
        remaining = self._close_guard_remaining(intent, portfolio)
        if remaining is None:
            return None
        if remaining <= 0:
            return RiskDecision(
                original_intent=intent,
                action="REJECT",
                reason="Idempotent-close guard: inventory already fully "
                "closed by previously approved orders",
                risk_notes=["close_guard_duplicate"],
            )
        if intent.qty > remaining:
            self._record_close(intent, portfolio, remaining)
            return RiskDecision(
                original_intent=intent,
                action="ADJUST",
                adjusted_qty=remaining,
                reason="Idempotent-close guard: close clamped to remaining "
                f"inventory {remaining}",
                risk_notes=["close_guard_clamped", "circuit_breaker_close_allowed"],
            )
        return None

    def _evaluate_single(
        self,
        intent: TradeIntent,
        portfolio: PortfolioSnapshot,
        state,
        projected_positions: dict[str, int] | None = None,
        projected_delta: float = 0.0,
        projected_vega: float = 0.0,
        defined_risk_groups: set[str] | None = None,
        counted_spread_groups: set[str] | None = None,
    ) -> RiskDecision:
        """Run sequential risk checks on a single intent."""
        risk_notes: list[str] = []

        # 1. Staleness / no-market-data check — must be FIRST so that unknown
        #    contracts are rejected before any exposure calculations run.
        #    Untracked contracts return max_stale+1 from _get_stale_minutes.
        stale_minutes = self._get_stale_minutes(intent, state)
        if stale_minutes > self._max_stale_minutes:
            return RiskDecision(
                original_intent=intent,
                action="REJECT",
                reason=f"Instrument {intent.contract} has no market data or is stale "
                f"({stale_minutes}min > {self._max_stale_minutes}min)",
                risk_notes=["stale_data"],
            )

        # 2. Daily loss limit — reject opening trades, allow closing
        # Setting max_daily_loss_pct <= 0 disables this check entirely.
        if self._config.max_daily_loss_pct > 0 and self._daily_pnl < -self._config.max_daily_loss_pct:
            if not self._is_closing_trade(intent, portfolio):
                return RiskDecision(
                    original_intent=intent,
                    action="REJECT",
                    reason="Daily loss limit exceeded "
                    f"({self._daily_pnl:.2%} < -{self._config.max_daily_loss_pct:.2%})",
                    risk_notes=["daily_loss_limit"],
                )
            else:
                risk_notes.append("daily_loss_exceeded_but_closing_allowed")

        # 2b. Naked short option check — reject undefined-risk short options
        if (
            not self._config.allow_naked_short_options
            and intent.side == "SELL"
            and self._is_option(intent.contract)
            and not self._is_closing_trade(intent, portfolio)
        ):
            # Check if part of a validated defined-risk spread
            is_defined_risk = (
                intent.spread_group is not None
                and defined_risk_groups is not None
                and intent.spread_group in defined_risk_groups
            )

            if not is_defined_risk and not self._is_covered_call(intent, portfolio):
                return RiskDecision(
                    original_intent=intent,
                    action="REJECT",
                    reason="Undefined-risk naked short option is not permitted",
                    risk_notes=["naked_short_option"],
                )

        # 3. Max exposure — position value / equity
        # For defined-risk spread legs: the SELL (short) leg's max loss is
        # bounded by the BUY leg's premium, so we exclude it from the abs
        # notional sum to avoid double-counting spread exposure.
        is_spread_short_leg = (
            intent.side == "SELL"
            and intent.spread_group is not None
            and defined_risk_groups is not None
            and intent.spread_group in defined_risk_groups
        )
        exposure_after = self._calc_exposure_after(
            intent, portfolio, exclude_notional=is_spread_short_leg
        )
        if exposure_after > self._config.max_exposure_pct:
            # Allow trades that reduce absolute exposure (de-risking)
            exposure_before = self._calc_current_exposure(portfolio)
            if exposure_after < exposure_before:
                risk_notes.append("exposure_over_limit_but_reducing")
            else:
                # Try to adjust qty down
                adjusted_qty = self._calc_max_allowed_qty(intent, portfolio)
                if adjusted_qty is not None and adjusted_qty > 0:
                    return RiskDecision(
                        original_intent=intent,
                        action="ADJUST",
                        adjusted_qty=adjusted_qty,
                        reason=f"Qty reduced from {intent.qty} to {adjusted_qty} "
                        f"to stay within {self._config.max_exposure_pct:.0%} exposure",
                        risk_notes=["exposure_adjusted"],
                        exposure_after=self._calc_exposure_with_qty(
                            adjusted_qty, intent, portfolio
                        ),
                        loss_after=self._daily_pnl,
                    )
                return RiskDecision(
                    original_intent=intent,
                    action="REJECT",
                    reason=f"Max exposure exceeded ({exposure_after:.2%} > "
                    f"{self._config.max_exposure_pct:.2%})",
                    risk_notes=["max_exposure"],
                    exposure_after=exposure_after,
                )

        # 4. Max position per symbol — use projected_positions for cumulative check
        if projected_positions is not None:
            current_symbol_qty = self._projected_qty_for_symbol(
                intent.symbol, portfolio, projected_positions
            )
        else:
            current_symbol_qty = self._current_qty_for_symbol(intent.symbol, portfolio)
        if intent.side == "BUY":
            projected_qty = current_symbol_qty + intent.qty
        else:
            projected_qty = current_symbol_qty - intent.qty
        if abs(projected_qty) > self._config.max_position_per_symbol:
            # Overlay profit: stock buys for covered-call coverage are exempt from the
            # per-symbol limit (which is designed for option contracts, not share counts).
            is_overlay_stock_buy = (
                self._config.allow_overlay_stock_buy
                and intent.strategy_name == "overlay_profit"
                and not self._is_option(intent.contract)
                and intent.side == "BUY"
            )
            if is_overlay_stock_buy:
                risk_notes.append("overlay_stock_buy_position_limit_exempt")
            else:
                return RiskDecision(
                    original_intent=intent,
                    action="REJECT",
                    reason=f"Max position per symbol exceeded for {intent.symbol} "
                    f"(projected {projected_qty} vs limit "
                    f"{self._config.max_position_per_symbol})",
                    risk_notes=["max_position_per_symbol"],
                )

        # 5. Order frequency limit
        # Spread legs that share a group already counted (by the BUY leg) do not
        # consume an additional slot — the whole spread costs 1 order slot.
        is_already_counted_spread_leg = (
            intent.spread_group is not None
            and defined_risk_groups is not None
            and intent.spread_group in defined_risk_groups
            and counted_spread_groups is not None
            and intent.spread_group in counted_spread_groups
        )
        if not is_already_counted_spread_leg and self._orders_today >= self._config.max_orders_per_day:
            return RiskDecision(
                original_intent=intent,
                action="REJECT",
                reason=f"Order frequency limit reached "
                f"({self._orders_today} >= {self._config.max_orders_per_day})",
                risk_notes=["order_frequency"],
            )

        # 6. Delta concentration check
        delta_reason = self._check_delta_concentration(
            intent, portfolio, state, projected_delta=projected_delta,
        )
        if delta_reason is not None:
            return RiskDecision(
                original_intent=intent,
                action="REJECT",
                reason=delta_reason,
                risk_notes=["delta_concentration"],
            )

        # 7. Vega concentration check
        vega_reason = self._check_vega_concentration(
            intent, portfolio, state, projected_vega=projected_vega,
        )
        if vega_reason is not None:
            return RiskDecision(
                original_intent=intent,
                action="REJECT",
                reason=vega_reason,
                risk_notes=["vega_concentration"],
            )

        # All checks passed
        return RiskDecision(
            original_intent=intent,
            action="ALLOW",
            reason="All risk checks passed",
            risk_notes=risk_notes if risk_notes else ["all_checks_ok"],
            exposure_after=exposure_after,
            loss_after=self._daily_pnl,
        )

    # ------------------------------------------------------------------
    # Flatten logic
    # ------------------------------------------------------------------

    def _should_flatten(self, state) -> bool:
        """Check if we are within the EOD flatten window."""
        if not self._config.flatten_before_close:
            return False
        minutes_to_close = self._minutes_to_close(state.current_ts)
        return minutes_to_close <= self._config.flatten_minutes_before_close

    def _minutes_to_close(self, current_ts) -> float:
        """Calculate minutes remaining until 16:00 ET.

        Accepts a tz-aware ``datetime`` (preferred) or an ISO string for
        legacy callers.
        """
        if isinstance(current_ts, datetime):
            dt = current_ts if current_ts.tzinfo else current_ts.replace(tzinfo=ET)
            dt = dt.astimezone(ET)
        else:
            dt = datetime.fromisoformat(str(current_ts).replace("Z", "+00:00"))
            # If naive, assume ET
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ET)
            else:
                dt = dt.astimezone(ET)

        close_dt = datetime.combine(dt.date(), MARKET_CLOSE, tzinfo=ET)
        delta = (close_dt - dt).total_seconds() / 60.0
        return delta

    def _generate_flatten_decisions(
        self,
        portfolio: PortfolioSnapshot,
        state,
    ) -> list[RiskDecision]:
        """Auto-generate close intents for all open positions (EOD flatten).

        Long positions (qty > 0) are closed with SELL; short positions (qty < 0)
        are closed with BUY.
        """
        decisions: list[RiskDecision] = []
        for pos in portfolio.positions:
            if pos.qty == 0:
                continue
            # Determine side and absolute qty for closing
            if pos.qty > 0:
                close_side = "SELL"
                close_qty = pos.qty
            else:
                close_side = "BUY"
                close_qty = abs(pos.qty)
            # Create a synthetic TradeIntent to close this position
            close_intent = TradeIntent(
                symbol=self._extract_underlying(pos.contract),
                contract=pos.contract,
                side=close_side,
                qty=close_qty,
                order_type="MARKET",
                rationale="EOD flatten — auto-generated by RiskGateAgent",
                strategy_name="risk_gate_flatten",
            )
            decisions.append(
                RiskDecision(
                    original_intent=close_intent,
                    action="ALLOW",
                    reason=f"EOD flatten — {self._minutes_to_close(state.current_ts):.0f}min "
                    f"to close",
                    risk_notes=["eod_flatten"],
                )
            )
        return decisions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calc_current_exposure(self, portfolio: PortfolioSnapshot) -> float:
        """Return current absolute exposure ratio (no intent applied).

        Options use a 100x multiplier; stock positions use 1x.
        """
        if portfolio.equity <= 0:
            return 1.0
        total = 0.0
        for pos in portfolio.positions:
            mult = 100 if self._is_option(pos.contract) else 1
            total += abs(pos.qty) * pos.mark_price * mult
        return total / portfolio.equity

    def _calc_exposure_after(
        self,
        intent: TradeIntent,
        portfolio: PortfolioSnapshot,
        exclude_notional: bool = False,
    ) -> float:
        """Project total position exposure after this intent fills.

        Exposure = sum(abs(position_value)) / equity.
        For options, position value = qty * mark_price * 100 (multiplier).

        Uses absolute position values so that both long and short positions
        contribute positively to exposure.  A SELL that closes a long reduces
        exposure, while a SELL that opens a short increases it.

        Args:
            exclude_notional: When True the intent's own notional contribution
                is omitted from the sum.  Used for the SELL leg of a
                defined-risk spread: its max loss is already captured by the
                BUY leg's premium, so adding it again would double-count the
                spread's exposure.
        """
        if portfolio.equity <= 0:
            return 1.0  # degenerate case

        # Build a position map: contract -> signed qty
        pos_map: dict[str, int] = {}
        price_map: dict[str, float] = {}
        for pos in portfolio.positions:
            pos_map[pos.contract] = pos.qty
            price_map[pos.contract] = pos.mark_price

        intent_mark = self._get_intent_mark_price(intent, portfolio, self._current_state)

        # Apply the intent to projected positions
        contract = intent.contract
        current_qty = pos_map.get(contract, 0)
        if intent.side == "BUY":
            new_qty = current_qty + intent.qty
        else:
            new_qty = current_qty - intent.qty
        pos_map[contract] = new_qty
        price_map.setdefault(contract, intent_mark)

        # Compute total absolute exposure, optionally excluding this intent's leg
        total_abs_value = 0.0
        for c, qty in pos_map.items():
            if exclude_notional and c == contract:
                continue
            mark = price_map.get(c, 0.0)
            mult = 100 if self._is_option(c) else 1
            total_abs_value += abs(qty) * mark * mult

        return total_abs_value / portfolio.equity

    def _calc_exposure_with_qty(
        self, qty: int, intent: TradeIntent, portfolio: PortfolioSnapshot
    ) -> float:
        """Project exposure if we trade ``qty`` instead of ``intent.qty``."""
        if portfolio.equity <= 0:
            return 1.0

        pos_map: dict[str, int] = {}
        price_map: dict[str, float] = {}
        for pos in portfolio.positions:
            pos_map[pos.contract] = pos.qty
            price_map[pos.contract] = pos.mark_price

        intent_mark = self._get_intent_mark_price(intent, portfolio, self._current_state)
        contract = intent.contract
        current_qty = pos_map.get(contract, 0)
        if intent.side == "BUY":
            new_qty = current_qty + qty
        else:
            new_qty = current_qty - qty
        pos_map[contract] = new_qty
        price_map.setdefault(contract, intent_mark)

        total_abs_value = 0.0
        for c, q in pos_map.items():
            mark = price_map.get(c, 0.0)
            mult = 100 if self._is_option(c) else 1
            total_abs_value += abs(q) * mark * mult

        return total_abs_value / portfolio.equity

    def _calc_max_allowed_qty(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot
    ) -> int | None:
        """Compute the largest qty that stays within the exposure limit.

        Returns ``None`` if even qty=1 would exceed the limit.
        Works for both BUY (opening long) and SELL (opening short).
        """
        if portfolio.equity <= 0:
            return None

        # Current absolute exposure
        current_abs_value = 0.0
        for pos in portfolio.positions:
            mult = 100 if self._is_option(pos.contract) else 1
            current_abs_value += abs(pos.qty) * pos.mark_price * mult
        intent_mark = self._get_intent_mark_price(intent, portfolio, self._current_state)
        if intent_mark <= 0:
            return None

        intent_mult = 100 if self._is_option(intent.contract) else 1
        max_total_value = self._config.max_exposure_pct * portfolio.equity
        remaining = max_total_value - current_abs_value
        if remaining <= 0:
            return None

        max_qty = int(remaining / (intent_mark * intent_mult))
        return max_qty if max_qty > 0 else None

    def _get_intent_mark_price(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot, state=None,
    ) -> float:
        """Get a reasonable mark price for an intent's contract.

        Priority order:
          1. Existing portfolio positions (most current mark).
          2. Current market-event bars.
          3. UPQ chain close price for freshly-loaded contracts (trusted).
          4. LLM-provided limit price.
          5. $1,000,000 sentinel (defense-in-depth).

        Step 3 was added to close a trust gap: freshly-loaded contracts
        bypass the staleness check but have no bar data yet.  Without a
        trusted price the risk manager would either accept the LLM's own
        limit price (letting the LLM price its own trades) or fall through
        to the $1M sentinel (blocking legitimate trades).
        """
        # 1. Check existing positions
        for pos in portfolio.positions:
            if pos.contract == intent.contract:
                return pos.mark_price
        # 2. Check current market event bars
        if state is not None and state.market_event is not None:
            for bar in state.market_event.bars:
                if bar.symbol == intent.contract:
                    return bar.close
        # 3. Check chain close price for freshly-loaded contracts (trusted UPQ data)
        if state is not None:
            freshly_loaded_prices = getattr(state, "_freshly_loaded_prices", {})
            if intent.contract in freshly_loaded_prices:
                price = freshly_loaded_prices[intent.contract]
                if price > 0:
                    return price
        # 4. Use limit price if provided
        if intent.limit_price is not None:
            return intent.limit_price
        # 5. Defense-in-depth sentinel for unknown contracts.
        #    A $1,000,000 mark ensures any unknown contract instantly
        #    breaches the exposure limit, even if the staleness check
        #    somehow failed to catch it.
        logger.debug(
            "No mark price found for %s, using sentinel $1,000,000 (defense-in-depth)",
            intent.contract,
        )
        return 1_000_000.0

    def _current_qty_for_symbol(
        self, symbol: str, portfolio: PortfolioSnapshot
    ) -> int:
        """Sum option-contract position qty for a given underlying symbol.

        Only counts option positions (contracts starting with ``"O:"``), not
        stock positions, because ``max_position_per_symbol`` is measured in
        option contracts.
        """
        total = 0
        for pos in portfolio.positions:
            if not pos.contract.startswith("O:"):
                continue
            underlying = self._extract_underlying(pos.contract)
            if underlying == symbol:
                total += pos.qty
        return total

    def _projected_qty_for_symbol(
        self,
        symbol: str,
        portfolio: PortfolioSnapshot,
        projected_positions: dict[str, int],
    ) -> int:
        """Sum projected option-contract qty for a given underlying symbol.

        Uses ``projected_positions`` (which accumulates approved intents from the
        current batch) instead of the original portfolio positions.  Only counts
        option contracts (starting with ``"O:"``) since
        ``max_position_per_symbol`` is measured in option contracts.
        """
        total = 0
        for contract, qty in projected_positions.items():
            if not contract.startswith("O:"):
                continue
            underlying = self._extract_underlying(contract)
            if underlying == symbol:
                total += qty
        return total

    def _extract_underlying(self, contract: str) -> str:
        """Extract underlying ticker from an OPRA contract string.

        E.g. ``"O:SPY250106C00595000"`` → ``"SPY"``.
        If the contract doesn't match OPRA format, return it as-is.
        """
        if contract.startswith("O:"):
            # OPRA format: O:<TICKER><YYMMDD><C/P><STRIKE>
            # Ticker is between "O:" and the first digit
            rest = contract[2:]
            ticker = ""
            for ch in rest:
                if ch.isdigit():
                    break
                ticker += ch
            return ticker if ticker else contract
        return contract

    def _get_stale_minutes(self, intent: TradeIntent, state) -> int:
        """Get staleness minutes for the intent's contract from market event.

        Checks the latest bars in ``state.market_event`` for the contract.
        Returns ``max_stale_minutes + 1`` if the contract is not tracked at all
        (i.e. not present in the market event), so that trades on untracked
        instruments are also blocked by the staleness check.

        Contracts freshly loaded this step (via dynamic chain loading) are
        exempt from the staleness check since they won't have bar data until
        the next PMB step.
        """
        # Bypass staleness for contracts just loaded this step, but only
        # if they also have a trusted UPQ price (defence-in-depth against
        # hallucinated contracts that PMB accepted but UPQ doesn't know).
        freshly_loaded = getattr(state, "_freshly_loaded_contracts", set())
        freshly_priced = getattr(state, "_freshly_loaded_prices", {})
        if intent.contract in freshly_loaded and intent.contract in freshly_priced:
            return 0
        if state.market_event is None:
            return self._max_stale_minutes + 1
        for bar in state.market_event.bars:
            if bar.symbol == intent.contract:
                return bar.stale_minutes
        # Contract not tracked — treat as stale to prevent trading unknown instruments
        return self._max_stale_minutes + 1

    @staticmethod
    def _is_option(contract: str) -> bool:
        """Return True if the contract is an OPRA option (starts with 'O:')."""
        return contract.startswith("O:")

    @staticmethod
    def _is_call(contract: str) -> bool:
        """Return True if the contract is a call option.

        OPRA format: O:<TICKER><YYMMDD><C/P><8-digit-strike>
        The option type character is at position -9 from the end of the string.
        """
        if not contract.startswith("O:") or len(contract) < 10:
            return False
        return contract[-9] == "C"

    def _is_defined_risk_spread(self, legs: list[TradeIntent]) -> bool:
        """Validate that a spread group forms a defined-risk vertical spread.

        Requirements:
        1. Exactly 2 option legs
        2. One BUY and one SELL
        3. Both are options (not stock)
        4. Same underlying
        5. Same expiry date
        6. Same option type (both calls or both puts)
        7. Same quantity
        """
        if len(legs) != 2:
            return False

        a, b = legs

        # Both must be options
        if not self._is_option(a.contract) or not self._is_option(b.contract):
            return False

        # One BUY, one SELL
        sides = {a.side, b.side}
        if sides != {"BUY", "SELL"}:
            return False

        # Same quantity
        if a.qty != b.qty:
            return False

        # Same underlying
        if self._extract_underlying(a.contract) != self._extract_underlying(b.contract):
            return False

        # Same option type (both calls or both puts)
        if self._is_call(a.contract) != self._is_call(b.contract):
            return False

        # Same expiry — extract YYMMDD from OPRA: O:<TICKER><YYMMDD><C/P><STRIKE>
        expiry_a = self._extract_expiry(a.contract)
        expiry_b = self._extract_expiry(b.contract)
        if expiry_a is None or expiry_b is None or expiry_a != expiry_b:
            return False

        return True

    @staticmethod
    def _extract_expiry(contract: str) -> str | None:
        """Extract YYMMDD expiry from OPRA contract string.

        Format: O:<TICKER><YYMMDD><C/P><STRIKE_8_DIGITS>
        The expiry is the 6 digits before the C/P character (which is at position -9).
        """
        if not contract.startswith("O:") or len(contract) < 16:
            return None
        # C/P is at position -9, expiry is the 6 chars before that
        try:
            return contract[-15:-9]
        except IndexError:
            return None

    def _is_covered_call(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot
    ) -> bool:
        """Check if a SELL call is covered by a LONG stock position in the underlying.

        A call is covered when the portfolio holds at least ``intent.qty * 100``
        shares of the underlying as a LONG position (1 option contract = 100 shares).
        """
        if not self._is_option(intent.contract) or not self._is_call(intent.contract):
            return False
        underlying = self._extract_underlying(intent.contract)
        stock_held = 0
        for pos in portfolio.positions:
            if pos.contract == underlying and pos.side == "LONG":
                stock_held += pos.qty  # qty is positive for LONG
        return stock_held >= intent.qty * 100

    def _check_delta_concentration(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot, state,
        projected_delta: float = 0.0,
    ) -> str | None:
        """Reject if portfolio net delta would exceed max_net_delta.

        Returns a rejection reason string, or None if the check passes
        (or is disabled / data unavailable).

        Uses ``projected_delta`` as the baseline (pre-accumulated across this
        batch) instead of re-computing from ``portfolio.positions``, so that
        previously-approved intents in the same batch are accounted for.
        """
        if self._config.max_net_delta is None:
            return None

        # Determine whether any delta data exists (positions or intent bar)
        has_any_delta = any(pos.delta is not None for pos in portfolio.positions)

        # Look up intent contract delta from market event bars
        intent_delta_per_contract = self._get_bar_delta(intent.contract, state)
        if intent_delta_per_contract is None:
            # No delta data for this contract — skip check (don't crash)
            if not has_any_delta:
                return None
            # Use 0 for intent delta if portfolio has delta data but intent doesn't
            intent_delta_per_contract = 0.0
        else:
            # Intent has delta data — mark as having delta
            has_any_delta = True

        if not has_any_delta:
            return None

        # Calculate delta contribution of intent
        sign = 1 if intent.side == "BUY" else -1
        intent_delta = sign * intent.qty * intent_delta_per_contract * 100

        projected = projected_delta + intent_delta
        if abs(projected) > self._config.max_net_delta:
            return (
                f"Net delta {projected:.0f} exceeds limit "
                f"{self._config.max_net_delta:.0f}"
            )
        return None

    def _check_vega_concentration(
        self, intent: TradeIntent, portfolio: PortfolioSnapshot, state,
        projected_vega: float = 0.0,
    ) -> str | None:
        """Reject if portfolio vega exposure exceeds max_net_vega.

        Returns a rejection reason string, or None if the check passes
        (or is disabled / data unavailable).

        Uses ``projected_vega`` as the baseline (pre-accumulated across this
        batch) instead of re-computing from ``portfolio.positions``, so that
        previously-approved intents in the same batch are accounted for.
        """
        if self._config.max_net_vega is None:
            return None

        # Determine whether any vega data exists (positions or intent bar)
        has_any_vega = any(pos.vega is not None for pos in portfolio.positions)

        # Look up intent contract vega from market event bars
        intent_vega_per_contract = self._get_bar_vega(intent.contract, state)
        if intent_vega_per_contract is None:
            if not has_any_vega:
                return None
            intent_vega_per_contract = 0.0
        else:
            # Intent has vega data — mark as having vega
            has_any_vega = True

        if not has_any_vega:
            return None

        sign = 1 if intent.side == "BUY" else -1
        intent_vega = sign * intent.qty * intent_vega_per_contract * 100

        projected = projected_vega + intent_vega
        if abs(projected) > self._config.max_net_vega:
            return (
                f"Net vega {projected:.0f} exceeds limit "
                f"{self._config.max_net_vega:.0f}"
            )
        return None

    def _get_bar_delta(self, contract: str, state) -> float | None:
        """Extract delta for a contract from current market event bars."""
        if state.market_event is None:
            return None
        for bar in state.market_event.bars:
            if bar.symbol == contract:
                return bar.delta
        return None

    def _get_bar_vega(self, contract: str, state) -> float | None:
        """Extract vega for a contract from current market event bars."""
        if state.market_event is None:
            return None
        for bar in state.market_event.bars:
            if bar.symbol == contract:
                return bar.vega
        return None
