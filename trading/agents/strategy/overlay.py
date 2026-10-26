"""Overlay strategies — profit (covered call + CSP) and hedge (protective put).

Uses analyzer-executor two-agent pipeline via MultiAgentMixin.
Both overlay_profit and overlay_hedge are registered HERE.
"""

from __future__ import annotations

import logging
import re
from datetime import date as _date, datetime, timedelta
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.agents.strategy.multi_agent import MultiAgentMixin
from trading.core.registry import register_strategy
from trading.core.schemas import TradeIntent

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import MarketEvent, PortfolioSnapshot
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)

_CHECKPOINT_SCHEMA_VERSION = 1


def _parse_opra_expiry(contract: str) -> str | None:
    m = re.search(r"(\d{6})[CP]\d{8}$", contract)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%y%m%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_opra_right(contract: str) -> str | None:
    m = re.search(r"\d{6}([CP])\d{8}$", contract)
    return m.group(1) if m else None


def compute_covered_call_capacity(portfolio, underlying: str) -> tuple[int, int, int]:
    if portfolio is None:
        return 0, 0, 0
    long_shares = 0
    existing_short_calls = 0
    for pos in portfolio.positions:
        if pos.contract == underlying and pos.qty > 0:
            long_shares += pos.qty
        elif pos.contract.startswith(f"O:{underlying}") and pos.qty < 0:
            if _parse_opra_right(pos.contract) == "C":
                existing_short_calls += abs(pos.qty)
    max_covered = long_shares // 100
    available = max(0, max_covered - existing_short_calls)
    return long_shares, existing_short_calls, available


def compute_csp_capacity(portfolio, underlying_price: float) -> int:
    if portfolio is None or underlying_price <= 0 or portfolio.cash <= 0:
        return 0
    return max(0, int(portfolio.cash / (underlying_price * 100)))


class _OverlayBase(BaseStrategy, MultiAgentMixin):
    DEFAULT_TRADE_EQUITY_PCT = 0.02

    from trading.agents.qocr import qocr_config_overlay, get_executor_chain_config
    _QOCR_CONFIG = qocr_config_overlay()
    _exec_cfg = get_executor_chain_config("overlay")
    _EXECUTOR_PRICE_RANGE = _exec_cfg["price_range"]
    _EXECUTOR_OTM_RANGE   = _exec_cfg["otm_range"]
    _EXECUTOR_ITM_RANGE   = _exec_cfg["itm_range"]
    _EXECUTOR_DENSE_RANGE = _exec_cfg["dense_range"]
    _EXECUTOR_SPARSE_GAP  = _exec_cfg["sparse_gap"]
    _EXECUTOR_MAX_PER_DTE = _exec_cfg["max_per_dte"]

    def __init__(self, underlying: str = "", dte_min: int = 7, dte_max: int = 45,
                 trade_equity_pct: float = DEFAULT_TRADE_EQUITY_PCT,
                 stock_qty: int = 0, cash_buffer_pct: float = 0.0,
                 otm_pct: float = 0.0, near_otm_pct: float = 0.0,
                 far_otm_pct: float = 0.0, mode: str = "",
                 rebalance_weekday: int = 0, upq_base_url: str = "",
                 llm_provider: str = "", llm_model: str = "",
                 llm_api_key: str = "", llm_base_url: str = "",
                 llm_reasoning_effort: str | None = None,
                 news_lookback_days: int = 7,
                 prompt_override: str = "", prompt_addenda: dict[str, str] | None = None,
                 prompt_templates: dict[str, str] | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._underlying = underlying
        self._dte_min = dte_min
        self._dte_max = dte_max
        self._trade_equity_pct = trade_equity_pct
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_reasoning_effort = llm_reasoning_effort
        self._custom_context = prompt_override.strip()
        self._prompt_addenda = prompt_addenda or {}
        self._prompt_templates = prompt_templates or {}
        self._llm = None
        self._last_rebalance: str | None = None
        self._stock_qty: int = stock_qty
        self._rebalance_weekday = rebalance_weekday
        # Track share qty step-to-step to detect option assignment events
        self._last_share_qty: int | None = None
        # Force-buy tracking: if agent skips buying puts after assignment for 2+ rebalances, force it
        self._assignment_pending: bool = False
        self._rebalances_without_put_buy: int = 0
        self._decision_seq: int = 0

    def _begin_decision(self, state, strategy_name: str) -> tuple[str, int]:
        from trading.core.time_contract import trading_day_from_ts
        from trading.ingest.ulid import new_ulid
        decision_id = new_ulid()
        seq = self._decision_seq
        self._decision_seq = seq + 1
        try:
            trading_day = trading_day_from_ts(state.current_ts)
        except Exception:
            trading_day = state.current_date
        self.emit("decision.started", {
            "decision_id": decision_id, "trading_day": trading_day, "seq": seq,
            "strategy": strategy_name,
        })
        return decision_id, seq

    def _emit_overlay_artifact_and_complete(
        self, *, decision_id, seq, state, intents, extra_ctx, strategy_name,
    ) -> None:
        calls = getattr(self, "_last_pipeline_calls", [])
        self.write_artifact(
            decision_id=decision_id, seq=seq, ts=state.current_ts,
            model_id=getattr(self._llm, "model", self._llm_model),
            provider=getattr(self._llm, "provider", self._llm_provider),
            prompt={"calls": [
                {"phase": call["phase"], "system": call["system_prompt"],
                 "user": call["user_prompt"]}
                for call in calls
            ]},
            response={"calls": [
                {"phase": call["phase"], "raw": call["response"]}
                for call in calls
            ], "intents_count": len(intents or [])},
            context=dict(extra_ctx or {}),
            trades=[
                i.model_dump() if hasattr(i, "model_dump") else dict(i.__dict__)
                for i in (intents or [])
            ],
            market_snapshot={"underlying": self._underlying},
        )
        self.emit("decision.completed", {
            "decision_id": decision_id,
            "seq": seq,
            "has_trade": bool(intents),
            "n_trades": len(intents or []),
            "strategy": strategy_name,
        })

    def on_session_start(self, config: RunConfig) -> None:
        self._avsr_config = config.avsr
        if not self._underlying and config.experiment.universe:
            self._underlying = config.experiment.universe[0]
        if config.experiment.initial_positions:
            for pos in config.experiment.initial_positions:
                if pos.get("symbol") == self._underlying:
                    self._stock_qty = pos.get("qty", 0)
        if self._llm_provider:
            from pathlib import Path
            from trading.tools.llm_client import LLMClient
            llm_log_dir = Path(config.evaluation.output_dir) / "llm_calls"
            self._llm = LLMClient(
                provider=self._llm_provider,
                model=self._llm_model or "deepseek-chat",
                api_key=self._llm_api_key,
                base_url=self._llm_base_url,
                log_dir=llm_log_dir,
                reasoning_effort=self._llm_reasoning_effort,
            )
            logger.info("Overlay LLM: provider=%s model=%s log=%s",
                        self._llm_provider, self._llm_model, llm_log_dir)

    def _should_rebalance(self, state) -> bool:
        if self._last_rebalance is None:
            return True
        d = _date.fromisoformat(state.current_date)
        if state.current_date == self._last_rebalance:
            return False
        # Rebalance on Monday(0) and Wednesday(2)
        return d.weekday() in (0, 2)

    def _compute_sizing(self, portfolio) -> tuple[float, int]:
        if portfolio is None or portfolio.equity <= 0:
            return 5000.0, 10
        budget = portfolio.equity * self._trade_equity_pct
        underlying_price = 100.0
        for pos in portfolio.positions:
            if pos.contract == self._underlying:
                underlying_price = pos.mark_price
                break
        est = underlying_price * 0.03 * 100
        mc = max(1, int(budget / est)) if est > 0 else 10
        return budget, mc

    def _get_underlying_price(self, portfolio) -> float:
        if portfolio is None:
            return 0.0
        for pos in portfolio.positions:
            if pos.contract == self._underlying:
                return pos.mark_price
        return 0.0

    def _detect_and_log_assignment(self, portfolio, strategy_name: str) -> dict | None:
        """Detect when shares changed vs last step (option assignment occurred).

        Returns a dict with assignment info to inject into LLM context, or None
        if no assignment event detected.
        """
        if portfolio is None:
            return None
        current_shares = sum(
            p.qty for p in portfolio.positions
            if p.contract == self._underlying and p.qty > 0
        )
        prev = self._last_share_qty
        self._last_share_qty = current_shares

        if prev is None:
            return None  # first step — no baseline yet

        delta = current_shares - prev
        if delta == 0:
            return None

        if delta < 0:
            # Shares decreased → short call assigned (shares called away)
            logger.warning(
                "[%s] ASSIGNMENT DETECTED: %d shares called away by short call "
                "(prev=%d, now=%d). Cash received = ~$%.0f",
                strategy_name, abs(delta), prev, current_shares,
                abs(delta) * self._get_underlying_price(portfolio),
            )
            # Arm force-buy: agent has 2 rebalance days to buy puts before we force it
            self._assignment_pending = True
            self._rebalances_without_put_buy = 0
            return {
                "type": "call_assignment",
                "shares_lost": abs(delta),
                "current_shares": current_shares,
                "target_shares": self._stock_qty,
                "deficit": max(0, self._stock_qty - current_shares),
            }
        else:
            # Shares increased → short put assigned (shares put to us)
            logger.warning(
                "[%s] ASSIGNMENT DETECTED: %d shares put to us by short put "
                "(prev=%d, now=%d). Cash paid = ~$%.0f",
                strategy_name, delta, prev, current_shares,
                delta * self._get_underlying_price(portfolio),
            )
            return {
                "type": "put_assignment",
                "shares_gained": delta,
                "current_shares": current_shares,
                "target_shares": self._stock_qty,
                "surplus": max(0, current_shares - self._stock_qty),
            }

    def _generate_expiry_close_orders(self, current_date: str, portfolio, strategy_name: str) -> list[TradeIntent]:
        """Auto-close expiring options on expiry day (or Friday before weekend expiry).

        Rules:
        - SHORT call/put ITM on expiry day → BUY to close (last-resort assignment guard)
        - SHORT call/put OTM → let expire worthless (keep premium)
        - LONG put/call ITM → SELL to close (capture intrinsic value)
        - LONG put/call OTM → let expire worthless (cost already paid)

        NOTE: Near-expiry warnings (DTE <= 3) are injected into the LLM context so the
        agent can decide to roll or close early. This function only fires on the actual
        expiry day as a safety backstop for whatever the LLM didn't catch.
        """
        if portfolio is None or not current_date:
            return []
        try:
            today = _date.fromisoformat(current_date[:10])
        except ValueError:
            return []
        is_friday = today.weekday() == 4
        spot = self._get_underlying_price(portfolio)
        intents: list[TradeIntent] = []

        for pos in portfolio.positions:
            if not pos.contract.startswith("O:") or pos.qty == 0:
                continue
            expiry = _parse_opra_expiry(pos.contract)
            if expiry is None:
                continue
            exp_date = _date.fromisoformat(expiry)

            # Only act on actual expiry day (or Friday before a weekend expiry)
            expires_today = exp_date == today
            expires_weekend = is_friday and today < exp_date <= today + timedelta(days=2)
            if not (expires_today or expires_weekend):
                continue

            dte = (exp_date - today).days
            right = _parse_opra_right(pos.contract)
            mark = pos.mark_price

            # Determine ITM status from delta or strike
            is_itm = False
            if pos.delta is not None:
                is_itm = abs(pos.delta) > 0.5
            else:
                import re
                m = re.search(r"[CP](\d{8})$", pos.contract)
                if m and spot > 0:
                    strike = int(m.group(1)) / 1000.0
                    is_itm = (spot > strike) if right == "C" else (spot < strike)

            is_short = pos.qty < 0
            is_long = pos.qty > 0

            if is_short and is_itm:
                intents.append(TradeIntent(
                    symbol=self._underlying, contract=pos.contract,
                    side="BUY", qty=abs(pos.qty), order_type="MARKET",
                    rationale=f"expiry backstop buy-to-close: SHORT ITM {right} mark=${mark:.2f}",
                    strategy_name=strategy_name,
                ))
                logger.info("[%s] EXPIRY BACKSTOP BUY-TO-CLOSE %s qty=%d (SHORT ITM, DTE=%d)",
                            strategy_name, pos.contract, abs(pos.qty), dte)
            elif is_short:
                logger.info("[%s] EXPIRY let-expire SHORT OTM %s qty=%d mark=$%.2f",
                            strategy_name, pos.contract, pos.qty, mark)
            elif is_long and is_itm and mark >= 0.10:
                intents.append(TradeIntent(
                    symbol=self._underlying, contract=pos.contract,
                    side="SELL", qty=abs(pos.qty), order_type="MARKET",
                    rationale=f"expiry sell-to-close: LONG ITM {right} mark=${mark:.2f}",
                    strategy_name=strategy_name,
                ))
                logger.info("[%s] EXPIRY SELL-TO-CLOSE %s qty=%d (LONG ITM, capture value)",
                            strategy_name, pos.contract, abs(pos.qty))
            else:
                logger.info("[%s] EXPIRY let-expire LONG OTM %s qty=%d mark=$%.2f",
                            strategy_name, pos.contract, pos.qty, mark)

        return intents

    def _collect_near_expiry_warnings(self, current_date: str, portfolio) -> list[str]:
        """Return warning strings for positions within 3 DTE that are ITM.

        These are injected into the LLM context so the agent can decide whether
        to roll, close, or hold — rather than forcing a hard close.
        """
        if portfolio is None or not current_date:
            return []
        try:
            today = _date.fromisoformat(current_date[:10])
        except ValueError:
            return []
        spot = self._get_underlying_price(portfolio)
        warnings = []
        for pos in portfolio.positions:
            if not pos.contract.startswith("O:") or pos.qty == 0:
                continue
            expiry = _parse_opra_expiry(pos.contract)
            if expiry is None:
                continue
            dte = (_date.fromisoformat(expiry) - today).days
            if dte > 3:
                continue
            right = _parse_opra_right(pos.contract)
            is_itm = False
            if pos.delta is not None:
                is_itm = abs(pos.delta) > 0.5
            else:
                import re
                m = re.search(r"[CP](\d{8})$", pos.contract)
                if m and spot > 0:
                    strike = int(m.group(1)) / 1000.0
                    is_itm = (spot > strike) if right == "C" else (spot < strike)
            direction = "SHORT" if pos.qty < 0 else "LONG"
            itm_str = "ITM" if is_itm else "OTM"
            delta_str = f" delta={pos.delta:.2f}" if pos.delta is not None else ""
            urgency = "⚠️ URGENT" if is_itm and pos.qty < 0 else "ℹ️ NOTE"
            warnings.append(
                f"{urgency}: {pos.contract} {direction} qty={pos.qty} DTE={dte} {itm_str}"
                f"{delta_str} mark=${pos.mark_price:.2f}"
                + (" — consider rolling or closing to avoid assignment" if is_itm and pos.qty < 0 else "")
            )
        return warnings

    def save_checkpoint(self) -> dict:
        """Serialize cross-step state for resume.

        Persistent fields (participate in cross-step decisions):
          - _last_rebalance: YYYY-MM-DD str or None
          - _last_share_qty: int (for assignment detection) or None
          - _assignment_pending: bool (force-buy latch, hedge only effectively)
          - _rebalances_without_put_buy: int (counter)

        Transient (not persisted): _llm (re-init in on_session_start),
        _underlying/_stock_qty (re-derived from config), thresholds/knobs
        (set by constructor).
        """
        return {
            "_version": _CHECKPOINT_SCHEMA_VERSION,
            "last_rebalance": self._last_rebalance,
            "last_share_qty": self._last_share_qty,
            "assignment_pending": self._assignment_pending,
            "rebalances_without_put_buy": self._rebalances_without_put_buy,
        }

    def load_checkpoint(self, state: dict) -> None:
        version = state.get("_version")
        if version != _CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"{self.__class__.__name__} checkpoint schema version mismatch: "
                f"got {version}, expected {_CHECKPOINT_SCHEMA_VERSION}"
            )
        self._last_rebalance = state.get("last_rebalance")
        self._last_share_qty = state.get("last_share_qty")
        self._assignment_pending = bool(state.get("assignment_pending", False))
        self._rebalances_without_put_buy = int(state.get("rebalances_without_put_buy", 0))

    def project_family_view(self, portfolio, state) -> dict:
        """Project an overlay-family view from real PositionInfo fields.

        Shared by OverlayProfitStrategy and OverlayHedgeStrategy — both
        produce a legitimate overlay-family view (stock + option legs).
        """
        positions = list(getattr(portfolio, "positions", []) or [])
        underlying_holdings: list[dict] = []
        overlay_legs: list[dict] = []
        net_delta = 0.0
        for pos in positions:
            contract = getattr(pos, "contract", "") or ""
            qty = float(getattr(pos, "qty", 0) or 0)
            avg_entry = float(getattr(pos, "avg_entry", 0.0) or 0.0)
            mark = float(getattr(pos, "mark_price", 0.0) or 0.0)
            delta = getattr(pos, "delta", None)
            if delta is not None:
                net_delta += float(delta) * qty
            is_option = contract.startswith("O:")
            if is_option:
                overlay_legs.append({
                    "contract": contract,
                    "income_to_date": float(getattr(pos, "unrealized_pnl", 0.0) or 0.0),
                    "days_to_expiry": int(getattr(pos, "dte", 0) or 0),
                })
            else:
                shares = qty
                cost_basis = avg_entry * qty
                current_mv = mark * qty
                underlying_holdings.append({
                    "symbol": contract,
                    "shares": shares,
                    "cost_basis": cost_basis,
                    "current_mv": current_mv,
                })
        return {
            "kind": "overlay",
            "underlying_holdings": underlying_holdings,
            "overlay_legs": overlay_legs,
            "delta_hedge_status": {"net_delta": net_delta},
        }


@register_strategy("overlay_profit")
class OverlayProfitStrategy(_OverlayBase):
    """Covered call + cash-secured put — multi-agent analyzer->executor."""

    REQUIRED_FREQUENCIES = frozenset({"1d"})

    def _check_assignment_risk(self, portfolio) -> list[TradeIntent]:
        """Daily check: buy-to-close short calls with high assignment risk.

        Triggers when delta > 0.70 (deep ITM) — don't wait for expiry.
        """
        if portfolio is None:
            return []
        intents: list[TradeIntent] = []
        for pos in portfolio.positions:
            if not pos.contract.startswith(f"O:{self._underlying}"):
                continue
            if pos.qty >= 0:  # only care about short positions
                continue
            if _parse_opra_right(pos.contract) != "C":
                continue
            # High assignment risk: delta > 0.70 means deep ITM
            if pos.delta is not None and abs(pos.delta) > 0.70:
                intents.append(TradeIntent(
                    symbol=self._underlying, contract=pos.contract,
                    side="BUY", qty=abs(pos.qty), order_type="MARKET",
                    rationale=f"assignment risk close: delta={pos.delta:.2f} (>0.70) mark=${pos.mark_price:.2f}",
                    strategy_name="overlay_profit",
                ))
                logger.info("[overlay_profit] ASSIGNMENT RISK: buy-to-close %s qty=%d delta=%.2f",
                            pos.contract, abs(pos.qty), abs(pos.delta))
        return intents

    def _get_actual_stock_qty(self, portfolio) -> int:
        if portfolio is None:
            return 0
        return sum(p.qty for p in portfolio.positions
                   if p.contract == self._underlying and p.qty > 0)

    def _check_share_deficit(self, portfolio) -> list[TradeIntent]:
        """Daily check: rebuy shares if below target (called away by assignment)."""
        if portfolio is None or self._stock_qty <= 0:
            return []
        actual = self._get_actual_stock_qty(portfolio)
        if actual < self._stock_qty:
            deficit = self._stock_qty - actual
            logger.info("[overlay_profit] SHARE DEFICIT: actual=%d target=%d → rebuy %d",
                        actual, self._stock_qty, deficit)
            return [TradeIntent(
                symbol=self._underlying, contract=self._underlying,
                side="BUY", qty=deficit, order_type="MARKET",
                rationale=f"rebuy {deficit} shares (target={self._stock_qty}, actual={actual})",
                strategy_name="overlay_profit",
            )]
        return []

    def on_step(self, market_event, portfolio, state) -> list[TradeIntent]:
        # Detect assignment events (share count changed vs last step) — log + context
        assignment_event = self._detect_and_log_assignment(portfolio, "overlay_profit")

        # 1. Expiry backstop (fires on actual expiry day only)
        expiry_intents = self._generate_expiry_close_orders(state.current_date, portfolio, "overlay_profit")
        if expiry_intents:
            return expiry_intents

        # 2. Daily assignment risk check — close deep ITM short calls (delta > 0.70)
        risk_intents = self._check_assignment_risk(portfolio)
        if risk_intents:
            return risk_intents

        # 3. LLM rebalance (Mon + Wed)
        if self._llm is None or not self._should_rebalance(state):
            self.emit("decision.skipped", {
                "trading_day": state.current_date,
                "reason": "no_llm" if self._llm is None else "not_rebalance_day",
                "strategy": "overlay_profit",
            })
            return []
        decision_id, seq = self._begin_decision(state, "overlay_profit")
        max_cost, max_contracts = self._compute_sizing(portfolio)
        long_shares, short_calls, avail_calls = compute_covered_call_capacity(portfolio, self._underlying)
        logger.info("[overlay_profit] Capacity: %d shares, %d short calls, %d covered calls available",
                    long_shares, short_calls, avail_calls)

        # Collect near-expiry warnings (DTE <= 3) for LLM context
        near_expiry_warnings = self._collect_near_expiry_warnings(state.current_date, portfolio)

        # Build extra context — share deficit + near-expiry warnings are passed to LLM
        extra_ctx: dict = {
            "covered_call_capacity": avail_calls,
            "long_shares": long_shares,
            "existing_short_calls": short_calls,
        }
        if self._custom_context:
            extra_ctx["custom_context"] = self._custom_context
        if assignment_event:
            extra_ctx["assignment_event"] = assignment_event
        if near_expiry_warnings:
            extra_ctx["near_expiry_warnings"] = near_expiry_warnings

        # Compute share deficit for LLM context (LLM decides whether to rebuy)
        actual_shares = self._get_actual_stock_qty(portfolio)
        if self._stock_qty > 0 and actual_shares < self._stock_qty:
            extra_ctx["share_deficit"] = {
                "current_shares": actual_shares,
                "target_shares": self._stock_qty,
                "deficit": self._stock_qty - actual_shares,
            }

        # Claude-specific: when holding 0 shares, prompt Claude to restore the original
        # share position so covered calls can be written again. Other providers are unaffected.
        _is_claude = self._llm_provider and self._llm_provider.lower() in ("claude", "anthropic")
        if long_shares == 0 and _is_claude and portfolio is not None:
            extra_ctx["claude_buy_shares_prompt"] = {
                "cash_available": portfolio.cash,
                "underlying": self._underlying,
                "target_shares": self._stock_qty,  # restore to original level (e.g. 10,000)
            }
            logger.info(
                "[overlay_profit] Claude: 0 shares held — injecting buy-shares prompt "
                "(target=%d)", self._stock_qty,
            )

        # Skip if no covered call capacity AND no deficit/assignment/share-buy prompt to handle
        if (
            avail_calls <= 0
            and "share_deficit" not in extra_ctx
            and "assignment_event" not in extra_ctx
            and "claude_buy_shares_prompt" not in extra_ctx
        ):
            logger.info("[overlay_profit] No covered call capacity — skipping")
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": False, "n_trades": 0,
                "strategy": "overlay_profit", "reason": "no_capacity",
            })
            return []

        # Cap to max 50% of coverable contracts per trade
        max_per_trade = max(1, (long_shares // 100) // 2) if long_shares > 0 else 1
        effective_max = min(max_contracts, max(avail_calls, 1), max_per_trade)

        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "analyzer",
            "strategy": "overlay_profit",
        })
        intents = self._run_multi_agent_pipeline(
            llm=self._llm, market_event=market_event,
            portfolio=portfolio, state=state,
            strategy_name="overlay_profit", underlying=self._underlying,
            max_cost=max_cost, max_contracts=effective_max,
            extra_context=extra_ctx,
        )
        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "executor",
            "strategy": "overlay_profit",
            "n_intents": len(intents or []),
        })
        if intents is not None:
            self._last_rebalance = state.current_date
        self._emit_overlay_artifact_and_complete(
            decision_id=decision_id, seq=seq, state=state,
            intents=intents or [], extra_ctx=extra_ctx,
            strategy_name="overlay_profit",
        )
        return intents


@register_strategy("overlay_hedge")
class OverlayHedgeStrategy(_OverlayBase):
    """Protective put — multi-agent analyzer->executor."""

    REQUIRED_FREQUENCIES = frozenset({"1d"})

    # Hard limit: max long put contracts regardless of shares
    MAX_LONG_PUTS = 100
    # Target per trade: never suggest more than this in a single trade
    MAX_PER_TRADE = 50

    def _check_short_put_risk(self, portfolio) -> list[TradeIntent]:
        """Daily check: buy-to-close short puts with high assignment risk.

        Short puts in put debit spreads must be closed before they expire ITM
        and trigger involuntary share assignment. Close when delta < -0.70.
        """
        if portfolio is None:
            return []
        intents: list[TradeIntent] = []
        for pos in portfolio.positions:
            if not pos.contract.startswith(f"O:{self._underlying}"):
                continue
            if pos.qty >= 0:  # only care about short positions
                continue
            if _parse_opra_right(pos.contract) != "P":
                continue
            # High assignment risk: |delta| > 0.70 means deep ITM
            if pos.delta is not None and abs(pos.delta) > 0.70:
                intents.append(TradeIntent(
                    symbol=self._underlying, contract=pos.contract,
                    side="BUY", qty=abs(pos.qty), order_type="MARKET",
                    rationale=f"short put risk close: delta={pos.delta:.2f} (>0.70) mark=${pos.mark_price:.2f}",
                    strategy_name="overlay_hedge",
                ))
                logger.info("[overlay_hedge] SHORT PUT RISK: buy-to-close %s qty=%d delta=%.2f",
                            pos.contract, abs(pos.qty), abs(pos.delta))
        return intents

    def on_step(self, market_event, portfolio, state) -> list[TradeIntent]:
        # Detect assignment events (short put assigned → unexpected share increase)
        assignment_event = self._detect_and_log_assignment(portfolio, "overlay_hedge")

        # 1. Expiry backstop (actual expiry day only)
        expiry_intents = self._generate_expiry_close_orders(state.current_date, portfolio, "overlay_hedge")
        if expiry_intents:
            return expiry_intents

        # 2. Daily short put risk check — close deep ITM short puts (delta > 0.70)
        risk_intents = self._check_short_put_risk(portfolio)
        if risk_intents:
            return risk_intents

        if self._llm is None or not self._should_rebalance(state):
            self.emit("decision.skipped", {
                "trading_day": state.current_date,
                "reason": "no_llm" if self._llm is None else "not_rebalance_day",
                "strategy": "overlay_hedge",
            })
            return []
        decision_id, seq = self._begin_decision(state, "overlay_hedge")
        max_cost, max_contracts = self._compute_sizing(portfolio)

        # Compute hedge capacity
        long_shares = 0
        existing_long_puts = 0
        if portfolio:
            for pos in portfolio.positions:
                if pos.contract == self._underlying and pos.qty > 0:
                    long_shares += pos.qty
                elif pos.contract.startswith(f"O:{self._underlying}") and pos.qty > 0:
                    if _parse_opra_right(pos.contract) == "P":
                        existing_long_puts += pos.qty

        # When assignment is pending (shares called away), use target stock_qty as
        # capacity basis so the agent still gets consulted and can reestablish the hedge.
        effective_long_shares = long_shares
        if self._assignment_pending and long_shares == 0 and self._stock_qty > 0:
            effective_long_shares = self._stock_qty
            logger.info(
                "[overlay_hedge] Assignment pending — using target stock_qty=%d for capacity",
                self._stock_qty,
            )

        # Hard cap: never exceed MAX_LONG_PUTS total long contracts
        hard_cap = min(self.MAX_LONG_PUTS, effective_long_shares // 100)
        remaining_puts = max(0, hard_cap - existing_long_puts)

        # Per-trade cap: don't open more than MAX_PER_TRADE in one rebalance
        per_trade_cap = self.MAX_PER_TRADE

        # Recommended range for the prompt: always target 50 contracts
        rec_min = max(5, min(per_trade_cap // 5, remaining_puts))
        rec_max = min(per_trade_cap, remaining_puts)

        logger.info(
            "[overlay_hedge] Capacity: %d shares, %d existing puts, %d remaining (hard_cap=%d), "
            "per_trade_cap=%d, recommend %d-%d",
            long_shares, existing_long_puts, remaining_puts, hard_cap,
            per_trade_cap, rec_min, rec_max,
        )

        if remaining_puts <= 0:
            if self._assignment_pending:
                logger.info("[overlay_hedge] Fully hedged (assignment pending but capacity=0) — skipping")
            else:
                logger.info("[overlay_hedge] Fully hedged — skipping")
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": False, "n_trades": 0,
                "strategy": "overlay_hedge", "reason": "fully_hedged",
            })
            return []

        # Effective max for this trade: min of all caps
        effective_max = min(max_contracts, remaining_puts, per_trade_cap)

        # Collect near-expiry warnings for LLM context
        near_expiry_warnings = self._collect_near_expiry_warnings(state.current_date, portfolio)

        extra_ctx: dict = {
            "long_shares": effective_long_shares,
            "existing_long_puts": existing_long_puts,
            "remaining_puts": remaining_puts,
            "recommended_min": rec_min,
            "recommended_max": rec_max,
        }
        if self._custom_context:
            extra_ctx["custom_context"] = self._custom_context
        if assignment_event:
            extra_ctx["assignment_event"] = assignment_event
        if near_expiry_warnings:
            extra_ctx["near_expiry_warnings"] = near_expiry_warnings
        if self._assignment_pending:
            extra_ctx["assignment_pending"] = True
            extra_ctx["rebalances_without_buy"] = self._rebalances_without_put_buy
            if self._rebalances_without_put_buy >= 2:
                extra_ctx["force_buy"] = True
                logger.warning(
                    "[overlay_hedge] Force-buy armed: %d rebalances without put purchase since assignment",
                    self._rebalances_without_put_buy,
                )

        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "analyzer",
            "strategy": "overlay_hedge",
        })
        intents = self._run_multi_agent_pipeline(
            llm=self._llm, market_event=market_event,
            portfolio=portfolio, state=state,
            strategy_name="overlay_hedge", underlying=self._underlying,
            max_cost=max_cost, max_contracts=effective_max,
            extra_context=extra_ctx,
        )
        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "executor",
            "strategy": "overlay_hedge",
            "n_intents": len(intents or []),
        })
        if intents is not None:
            intents = self._enforce_hedge_capacity(intents, remaining_puts, per_trade_cap)
            self._last_rebalance = state.current_date
            # Track put-buy activity to manage force-buy state
            has_put_buy = any(
                i.side == "BUY" and _parse_opra_right(i.contract) == "P"
                for i in intents
            )
            if has_put_buy:
                self._assignment_pending = False
                self._rebalances_without_put_buy = 0
                logger.info("[overlay_hedge] Put buy detected — clearing assignment_pending state")
            elif self._assignment_pending:
                self._rebalances_without_put_buy += 1
                logger.info(
                    "[overlay_hedge] No put buy (rebalance %d/2 before force-buy)",
                    self._rebalances_without_put_buy,
                )
        self._emit_overlay_artifact_and_complete(
            decision_id=decision_id, seq=seq, state=state,
            intents=intents or [], extra_ctx=extra_ctx,
            strategy_name="overlay_hedge",
        )
        return intents

    def _enforce_hedge_capacity(
        self, intents: list[TradeIntent], remaining_puts: int, per_trade_cap: int,
    ) -> list[TradeIntent]:
        """Hard-enforce buy put qty cap. Adjusts spread sell-put legs to match."""
        buy_cap = min(remaining_puts, per_trade_cap)
        buy_used = 0
        spread_group_qty: dict[str, int] = {}
        result = []
        for intent in intents:
            if intent.side == "BUY" and _parse_opra_right(intent.contract) == "P":
                qty = intent.qty
                available = buy_cap - buy_used
                if available <= 0:
                    logger.warning("[overlay_hedge] Blocked BUY put — capacity exhausted")
                    continue
                if qty > available:
                    logger.warning("[overlay_hedge] Capped BUY put qty %d → %d", qty, available)
                    intent.qty = available
                buy_used += intent.qty
                if intent.spread_group:
                    spread_group_qty[intent.spread_group] = intent.qty
                result.append(intent)
            elif intent.side == "SELL" and _parse_opra_right(intent.contract) == "P":
                # Sync sell leg qty to capped buy leg in same spread group
                sg = intent.spread_group
                if sg and sg in spread_group_qty:
                    intent.qty = spread_group_qty[sg]
                result.append(intent)
            else:
                result.append(intent)
        return result


class _HybridSleeve:
    """Namespace spread groups before artifacts and execution see the plan."""

    _sleeve: str

    def _plan_to_intents(self, *args, **kwargs):
        intents = super()._plan_to_intents(*args, **kwargs)
        for intent in intents:
            if intent.spread_group:
                intent.spread_group = f"{self._sleeve}:{intent.spread_group}"
        return intents


class _HybridCoveredCall(_HybridSleeve, OverlayProfitStrategy):
    _sleeve = "covered_call"


class _HybridHedge(_HybridSleeve, OverlayHedgeStrategy):
    _sleeve = "hedge"


@register_strategy("overlay_hybrid")
class OverlayHybridStrategy(_OverlayBase):
    """Run the existing covered-call and protective-put sleeves together.

    Each sleeve sees stocks plus its own option side, so assignment/expiry
    management cannot close the other sleeve's positions. Both share the same
    account equity/cash and the runner's risk gate and simulated broker. The
    per-rebalance model budget is divided equally; decisions are sequential.
    """

    REQUIRED_FREQUENCIES = frozenset({"1d"})

    def __init__(self, **params):
        super().__init__(**params)
        sleeve_params = {**params, "trade_equity_pct": self._trade_equity_pct / 2}
        self._sleeves = [(_HybridCoveredCall(**sleeve_params), "C"),
                         (_HybridHedge(**sleeve_params), "P")]

    def _attach_sleeves(self):
        for sleeve, _ in self._sleeves:
            sleeve.events = self.events
            sleeve._runner_ref = self._runner_ref

    def on_session_start(self, config):
        # Initialize each existing strategy once, without creating an unused
        # third LLM client for the wrapper.
        self._underlying = self._underlying or config.experiment.universe[0]
        self._attach_sleeves()
        for sleeve, _ in self._sleeves:
            sleeve.on_session_start(config)

    def on_step(self, market_event, portfolio, state):
        self._attach_sleeves()
        intents = []
        for sleeve, right in self._sleeves:
            view = portfolio.model_copy(update={"positions": [
                position for position in portfolio.positions
                if not position.contract.startswith("O:") or _parse_opra_right(position.contract) == right
            ]}) if portfolio is not None else None
            sleeve._decision_seq = self._decision_seq
            try:
                intents.extend(sleeve.on_step(market_event, view, state) or [])
            finally:
                self._decision_seq = sleeve._decision_seq
        return intents

    def save_checkpoint(self):
        return {"_version": 1, "decision_seq": self._decision_seq,
                "covered_call": self._sleeves[0][0].save_checkpoint(),
                "hedge": self._sleeves[1][0].save_checkpoint()}

    def load_checkpoint(self, state):
        if state.get("_version") != 1 or not all(isinstance(state.get(key), dict) for key in ("covered_call", "hedge")):
            raise ValueError("Invalid overlay hybrid checkpoint")
        self._decision_seq = int(state.get("decision_seq", 0))
        for (sleeve, _), key in zip(self._sleeves, ("covered_call", "hedge")):
            sleeve.load_checkpoint(state[key])
