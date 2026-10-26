"""MultiAgentMixin — shared analyzer-executor pipeline for all strategies.

Two-phase LLM pipeline:
1. Analyzer: market assessment → intent (bullish/bearish/neutral/exit_all)
2. Executor: intent + chain → concrete order plan

If analyzer returns "neutral" → skip executor, return [] immediately.
If analyzer returns "exit_all" → close all positions without calling executor.

AVSR + QOCR chain compression is used when contract data includes
strike/DTE/IV fields; falls back to raw bar table otherwise.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from trading.core.endpoints import DEFAULT_ESP_URL, DEFAULT_UPQ_URL
from trading.core.schemas import TradeIntent
from trading.prompts.context import (
    build_positions_table,
    build_price_trend,
    build_time_context,
    compute_minutes_to_close,
    get_underlying_price,
    has_option_bars,
)
from trading.prompts.loader import get_loader
from trading.prompts.parsers import parse_json_array_response, parse_json_object_response

if TYPE_CHECKING:
    from trading.core.schemas import MarketEvent, PortfolioSnapshot
    from trading.core.state import TradingState
    from trading.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


class MultiAgentMixin:
    """Two-agent (analyzer → executor) pipeline mixin.

    Provides `_run_multi_agent_pipeline()` that strategies call from on_step.
    """

    # Subclasses override to pass a strategy-specific QOCRConfig to build_chain_context.
    # None = use the default QOCRConfig.
    _QOCR_CONFIG = None  # subclasses override; None = default QOCRConfig

    # Strategy-specific intent filters: which analyzer intents trigger the executor.
    # Intents not listed are treated as "hold" (skip executor, return []).
    _ALLOWED_INTENTS: dict[str, set[str]] = {
        "overlay_hedge": {"bearish"},       # only protect when bearish
        "overlay_profit": {"bullish", "bearish"},  # sell premium in either direction
    }

    # Strategy-specific executor constraints injected into the system prompt.
    _STRATEGY_CONSTRAINTS: dict[str, str] = {
        "overlay_hedge": (
            "\n## Strategy Constraints (STRICT — violations will be rejected)\n"
            "- This is a HEDGE strategy using puts only. No calls allowed.\n"
            "- Allowed actions:\n"
            "  * buy put — open new protective put (single-leg or spread long leg)\n"
            "  * sell put — close/roll existing put OR sell lower-strike leg of a put spread\n"
            "  * [] — no action\n"
            "\n## PREFERRED: Put Debit Spreads (Bear Put Spread)\n"
            "A put debit spread buys a higher-strike put and sells a lower-strike put at the same expiry.\n"
            "- Net cost = long premium - short premium (typically 30-60% cheaper than single-leg put)\n"
            "- Provides protection over the range [lower_strike, upper_strike]\n"
            "- Example for 10K shares NVDA @ $138:\n"
            "  BUY 20x O:NVDA250221P00135000 (the protection leg, delta~-0.40)\n"
            "  SELL 20x O:NVDA250221P00120000 (the cost-reduction leg, delta~-0.15)\n"
            "  Both legs: qty=20, spread_group='A'\n"
            "- Width: 5-15% OTM for the long leg; sell 10-20% further OTM\n"
            "\n## Scale: Size Proportional to Shares\n"
            "- See 'Long shares' in the Position Capacity block below.\n"
            "- For 10,000 shares → buy 10-50 contracts (covers 1K-5K shares, 10%-50%)\n"
            "- For 1,000 shares → buy 1-5 contracts\n"
            "- Each contract = 100 shares of protection. Don't buy fewer than 5 contracts for 10K shares.\n"
            "\n## Rolling & Closing\n"
            "- Near expiry (DTE < 3) or deep OTM: sell to close, then buy new spread (roll)\n"
            "- Put spread gained >50%: sell to lock gains, redeploy capital\n"
            "- Can do roll as single set of orders: sell old spread + buy new spread\n"
        ),
        "overlay_profit": (
            "\n## Strategy Constraints (STRICT — violations will be rejected)\n"
            "- This is a COVERED CALL strategy. Calls only for options. No puts.\n"
            "- Allowed actions:\n"
            "  * sell call — open new covered call for income (single-leg or spread short leg)\n"
            "  * buy call — close/roll existing short call OR buy upper-strike leg of a call spread\n"
            "  * buy <TICKER> (stock) — rebuy shares if assignment_event or share_deficit detected\n"
            "  * [] — no action\n"
            "\n## PREFERRED: Call Credit Spreads\n"
            "A call credit spread sells a lower-strike call and buys a higher-strike call at the same expiry.\n"
            "- Net credit = short premium - long premium (captures most income, caps max loss)\n"
            "- Preferred when IV is elevated (>30%) — lock in the spread credit\n"
            "- Example for 10K shares NVDA @ $138:\n"
            "  SELL 20x O:NVDA250221C00145000 (income leg, delta~0.25)\n"
            "  BUY  20x O:NVDA250221C00155000 (protection leg, delta~0.10)\n"
            "  Both legs: qty=20, spread_group='A'\n"
            "- Width: sell 5-15% OTM; buy 10-20% further OTM\n"
            "- Alternatively use single-leg covered calls when IV is moderate and you want simplicity.\n"
            "\n## Covered Call Playbook\n"
            "**Bearish / High IV:** Sell ATM-near OTM calls (delta 0.30-0.50), short expiry (1-2W).\n"
            "  Higher premium = better cushion. Use call spread to cap downside risk.\n"
            "**Flat / Range-bound:** Sell OTM calls (delta 0.15-0.25), medium expiry (2-4W).\n"
            "  Theta farming. Stock stays below strike = pure profit.\n"
            "**Bullish / Trending Up:** Sell deep OTM (delta 0.10-0.15) or skip.\n"
            "  Don't cap the upside in a strong rally. Close ITM calls to avoid assignment.\n"
            "\n## Scale: Size Proportional to Shares\n"
            "- See 'Available covered calls' and 'Long shares' in Position Capacity block below.\n"
            "- For 10,000 shares → sell 10-50 contracts (covers 1K-5K shares, 10%-50%)\n"
            "- For 1,000 shares → sell 1-5 contracts\n"
            "- NEVER exceed 'Available covered calls' limit.\n"
            "\n## Rolling & Closing\n"
            "- Short call ITM (delta > 0.50): buy to close → sell new at higher strike (roll up)\n"
            "- Short call decayed >80%: buy to close → free capacity for new sell\n"
            "- Short call near expiry + ITM: buy to close immediately (assignment risk)\n"
            "- Multi-leg roll: close old + open new in same order set\n"
        ),
    }

    def _run_multi_agent_pipeline(
        self,
        llm: LLMClient,
        market_event: MarketEvent | None,
        portfolio: PortfolioSnapshot | None,
        state: TradingState,
        strategy_name: str = "",
        max_cost: float = 5000.0,
        max_contracts: int = 10,
        underlying: str | None = None,
        extra_context: dict | None = None,
    ) -> list[TradeIntent]:
        """Run the full analyzer → executor pipeline.

        Returns:
            List of TradeIntents, or [] if analyzer says hold/neutral.
        """
        self._last_pipeline_calls = []
        if not has_option_bars(market_event, underlying):
            return []

        # Phase 1: Analyzer
        analysis = self._call_analyzer(
            llm, market_event, portfolio, state, underlying,
            custom_context=(extra_context or {}).get("custom_context", ""),
        )

        intent = analysis.get("intent", "neutral")

        # Neutral → skip executor entirely
        if intent == "neutral":
            logger.info("[%s] Analyzer: neutral (hold) — skipping executor", strategy_name)
            return []

        # Exit all → close positions without executor
        # Overlay strategies MUST never exit the underlying stock — treat exit_all as neutral.
        if intent == "exit_all":
            if strategy_name in ("overlay_profit", "overlay_hedge"):
                logger.warning(
                    "[%s] Analyzer returned exit_all — BLOCKED for overlay strategy "
                    "(stock is a permanent holding). Treating as neutral/hold.",
                    strategy_name,
                )
                return []
            logger.info("[%s] Analyzer: exit_all — closing all positions", strategy_name)
            return self._flatten_all(portfolio, analysis.get("reasoning", "exit_all"), strategy_name)

        # Strategy-specific intent filter
        # If intent doesn't match open-new direction, still call executor
        # when there are existing option positions (agent may want to close/roll).
        allowed = self._ALLOWED_INTENTS.get(strategy_name)
        has_option_positions = portfolio is not None and any(
            p.contract.startswith("O:") and p.qty != 0 for p in portfolio.positions
        )
        if allowed and intent not in allowed:
            if has_option_positions:
                # Override intent to signal "manage existing only, don't open new"
                logger.info("[%s] Analyzer: %s (not in %s) — calling executor for position management",
                            strategy_name, intent, allowed)
                analysis["_manage_only"] = True
            else:
                logger.info("[%s] Analyzer: %s — skipped (no positions to manage)",
                            strategy_name, intent)
                return []

        # Phase 2: Executor
        logger.info("[%s] Analyzer: %s (conf=%.2f) — calling executor",
                     strategy_name, intent, analysis.get("confidence", 0))
        trade_plan = self._call_executor(
            llm, analysis, market_event, portfolio, state,
            max_cost=max_cost, max_contracts=max_contracts,
            underlying=underlying,
            strategy_name=strategy_name,
            extra_context=extra_context,
        )

        if not trade_plan:
            logger.info("[%s] Executor returned empty plan", strategy_name)
            return []

        # Hard-validate actions against strategy rules
        trade_plan = self._filter_disallowed_actions(trade_plan, strategy_name)
        if not trade_plan:
            logger.warning("[%s] All executor actions rejected by strategy filter", strategy_name)
            return []

        # Enforce covered call capacity for profit strategy
        if extra_context and strategy_name == "overlay_profit":
            trade_plan = self._enforce_capacity(trade_plan, extra_context, strategy_name)
            if not trade_plan:
                logger.warning("[%s] All orders exceeded capacity", strategy_name)
                return []

        intents = self._plan_to_intents(trade_plan, strategy_name, underlying)
        for ti in intents:
            logger.info(
                "[%s] ORDER: %s %s qty=%d %s — %s",
                strategy_name, ti.side, ti.contract, ti.qty,
                ti.order_type, ti.rationale,
            )
        return intents

    # ------------------------------------------------------------------
    # Phase 1: Analyzer
    # ------------------------------------------------------------------

    def _call_analyzer(
        self,
        llm: LLMClient,
        market_event: MarketEvent | None,
        portfolio: PortfolioSnapshot | None,
        state: TradingState,
        underlying: str | None = None,
        custom_context: str = "",
    ) -> dict:
        """Call the Analysis Agent. Returns dict with intent, confidence, reasoning."""
        loader = get_loader()

        # Build context components (full chain: calls + puts, with AVSR + QOCR)
        chain_context = self._build_compressed_chain(market_event, underlying, state.current_date)
        portfolio_summary = self._build_portfolio_summary(portfolio)
        frequency = state.config.experiment.frequency
        time_context = build_time_context(state.current_ts, frequency)

        # Price trend with TA metrics (MA5, MA15, MA30)
        trend_tickers = [underlying] if underlying else state.config.experiment.universe
        price_trend = build_price_trend(market_event, trend_tickers)

        # Daily price summary (MAs, trend, 20-bar range, last 10 OHLC)
        upq_base_url = getattr(state.config.execution, "upq_base_url", "") or DEFAULT_UPQ_URL
        daily_price_context = "(no daily data)"
        for _tkr in trend_tickers[:1]:
            try:
                from trading.tools.earnings_loader import build_daily_price_summary
                daily_price_context = build_daily_price_summary(
                    upq_base_url, _tkr, state.current_date,
                )
            except Exception as _exc:
                logger.debug("Daily price summary failed for %s: %s", _tkr, _exc)

        # News context (optional — loaded from NPP/Benzinga if enabled)
        news_context, earnings_context = self._load_news_context(state, underlying)

        templates = getattr(self, "_prompt_templates", {})
        system_prompt = templates.get("analyzer_system") or loader.load_system("analyzer")
        user_prompt = loader.render(
            templates.get("analyzer_user") or loader.load_user("analyzer"),
            TIME_CONTEXT=time_context,
            PORTFOLIO_SUMMARY=portfolio_summary,
            PRICE_TREND=price_trend,
            DAILY_PRICE_CONTEXT=daily_price_context,
            CHAIN_CONTEXT=chain_context,
            NEWS_CONTEXT=news_context,
            EARNINGS_CONTEXT=earnings_context,
        )
        if custom_context:
            user_prompt += f"\n\nUser-defined trading context:\n{custom_context}"
        addenda = getattr(self, "_prompt_addenda", {})
        if addenda.get("analyzer_system"):
            system_prompt += "\n\nAdditional system instructions:\n" + addenda["analyzer_system"]
        if addenda.get("analyzer_user"):
            user_prompt += "\n\nAdditional user instructions:\n" + addenda["analyzer_user"]

        try:
            raw = llm.call_raw(system_prompt, user_prompt)
            self._last_pipeline_calls.append({
                "phase": "analyzer", "system_prompt": system_prompt,
                "user_prompt": user_prompt, "response": raw,
            })
            return self._parse_analysis(raw)
        except Exception as exc:
            logger.warning("Analyzer LLM call failed: %s", exc)
            return {"intent": "neutral", "confidence": 0.0, "reasoning": f"LLM error: {exc}", "target_instruments": []}

    # ------------------------------------------------------------------
    # Phase 2: Executor
    # ------------------------------------------------------------------

    def _call_executor(
        self,
        llm: LLMClient,
        analysis: dict,
        market_event: MarketEvent | None,
        portfolio: PortfolioSnapshot | None,
        state: TradingState,
        max_cost: float = 5000.0,
        max_contracts: int = 10,
        underlying: str | None = None,
        strategy_name: str = "",
        extra_context: dict | None = None,
    ) -> list[dict]:
        """Call the Executor Agent with detailed chain context + capacity info."""
        loader = get_loader()
        templates = getattr(self, "_prompt_templates", {})

        tradable = self._build_executor_chain(market_event, underlying, state.current_date)
        positions_summary = self._build_portfolio_summary(portfolio)

        base_prompt = loader.render(
            templates.get("executor_system") or loader.load_system("executor"),
            MAX_COST=f"{max_cost:,.0f}",
            MAX_CONTRACTS=str(max_contracts),
        )
        constraints = self._STRATEGY_CONSTRAINTS.get(strategy_name, "")
        system_prompt = base_prompt + constraints

        user_prompt = loader.render(
            templates.get("executor_user") or loader.load_user("executor"),
            ANALYSIS_JSON=json.dumps(analysis, indent=2),
            TRADABLE_CONTRACTS=tradable,
            POSITIONS_SUMMARY=positions_summary,
        )
        if extra_context and extra_context.get("custom_context"):
            user_prompt += f"\n\nUser-defined trading context:\n{extra_context['custom_context']}"
        addenda = getattr(self, "_prompt_addenda", {})
        if addenda.get("executor_system"):
            system_prompt += "\n\nAdditional system instructions:\n" + addenda["executor_system"]
        if addenda.get("executor_user"):
            user_prompt += "\n\nAdditional user instructions:\n" + addenda["executor_user"]

        # Inject capacity info + event warnings into executor prompt
        if extra_context:
            ls = extra_context.get("long_shares", 0)
            scale_note = f"Scale = {ls} shares / 100 = {ls // 100} contract-equivalents" if ls else ""
            if "covered_call_capacity" in extra_context:
                # Profit strategy — covered call limits
                cc = extra_context["covered_call_capacity"]
                esc = extra_context.get("existing_short_calls", 0)
                target_min = max(1, (ls // 100) // 10)   # 10% of shares
                target_max = max(5, (ls // 100) // 2)    # 50% of shares, capped at avail
                target_max = min(target_max, cc)
                user_prompt += (
                    f"\n\n== Position Capacity (HARD LIMITS) ==\n"
                    f"Long shares: {ls:,}  ({scale_note})\n"
                    f"Existing short calls: {esc} contracts\n"
                    f"Available covered calls: {cc} contracts (MAXIMUM — do NOT exceed)\n"
                    f"Recommended this trade: {target_min}-{target_max} contracts "
                    f"(10%-50% coverage of your {ls:,} shares)\n"
                    f"TIP: Use a call credit spread (sell lower-strike + buy higher-strike, same expiry + spread_group) "
                    f"to cap risk while maintaining income.\n"
                )
            elif "remaining_puts" in extra_context:
                # Hedge strategy — put sizing guidance
                elp = extra_context.get("existing_long_puts", 0)
                rem = extra_context["remaining_puts"]
                rmin = extra_context.get("recommended_min", 10)
                rmax = extra_context.get("recommended_max", 50)
                asgn_pending = extra_context.get("assignment_pending", False)
                force_buy = extra_context.get("force_buy", False)
                days_without_buy = extra_context.get("rebalances_without_buy", 0)
                shares_note = (
                    f"(target position — shares were recently called away by assignment)"
                    if asgn_pending and ls > 0
                    else scale_note
                )
                user_prompt += (
                    f"\n\n== Hedge Sizing Guide ==\n"
                    f"Long shares: {ls:,}  ({shares_note})\n"
                    f"Existing long puts: {elp} contracts\n"
                    f"Remaining capacity: {rem} contracts (max before over-hedged)\n"
                    f"Recommended qty this trade: {rmin}-{rmax} contracts (10%-50% of {ls:,} shares)\n"
                    f"IMPORTANT: Buy a meaningful amount ({rmin}+ contracts). "
                    f"Buying 1-3 contracts for {ls:,} shares is negligible protection.\n"
                    f"TIP: Use a put debit spread (buy higher-strike put + sell lower-strike put, same expiry + spread_group) "
                    f"to cut cost by 30-60% while maintaining meaningful protection.\n"
                )
                if force_buy:
                    user_prompt += (
                        f"\n\n== ⛔ FORCE BUY REQUIRED ==\n"
                        f"You have skipped buying puts for {days_without_buy} rebalances since the last assignment. "
                        f"You MUST place a put buy order this rebalance (at minimum {rmin} contracts). "
                        f"Holding an unhedged position is not acceptable. Choose the best available put or put spread.\n"
                    )
                elif asgn_pending and days_without_buy > 0:
                    user_prompt += (
                        f"\n\n== ⚠️ HEDGE REBUILD NEEDED ==\n"
                        f"Shares were recently assigned away. You have gone {days_without_buy} rebalance(s) "
                        f"without reestablishing the put hedge. Please buy puts this rebalance "
                        f"(force-buy will trigger if you skip again).\n"
                    )

            # Near-expiry warnings (DTE <= 3) — LLM decides whether to roll/close
            near_expiry = extra_context.get("near_expiry_warnings", [])
            if near_expiry:
                user_prompt += "\n\n== Near-Expiry Positions (DTE ≤ 3) ==\n"
                for w in near_expiry:
                    user_prompt += f"{w}\n"
                user_prompt += (
                    "ACTION REQUIRED: Review these positions. For SHORT ITM options, "
                    "strongly consider rolling (buy to close + sell new) or closing to avoid assignment. "
                    "For LONG ITM options, consider selling to capture intrinsic value.\n"
                )

            # Assignment event — LLM decides whether to rebuy shares / adjust options
            asgn = extra_context.get("assignment_event")
            if asgn:
                atype = asgn.get("type", "")
                curr = asgn.get("current_shares", 0)
                tgt = asgn.get("target_shares", 0)
                if atype == "call_assignment":
                    lost = asgn.get("shares_lost", 0)
                    deficit = asgn.get("deficit", 0)
                    user_prompt += (
                        f"\n\n== ⚠️ SHARE ASSIGNMENT EVENT ==\n"
                        f"SHORT CALL was assigned: {lost:,} shares called away.\n"
                        f"Current shares: {curr:,} | Target: {tgt:,} | Deficit: {deficit:,} shares\n"
                        f"DECISION NEEDED: Should you rebuy {deficit:,} shares to restore the position? "
                        f"If yes, include a BUY {tgt} (stock, not option) order.\n"
                        f"Also consider whether existing option coverage needs adjustment.\n"
                    )
                elif atype == "put_assignment":
                    gained = asgn.get("shares_gained", 0)
                    surplus = asgn.get("surplus", 0)
                    user_prompt += (
                        f"\n\n== ⚠️ SHARE ASSIGNMENT EVENT ==\n"
                        f"SHORT PUT was assigned: {gained:,} shares put to us.\n"
                        f"Current shares: {curr:,} | Target: {tgt:,} | Surplus: {surplus:,} shares\n"
                        f"DECISION NEEDED: Should you sell {surplus:,} surplus shares, or hold them? "
                        f"Consider adjusting option coverage for the new share count.\n"
                    )

            # Share deficit (without fresh assignment) — LLM decides
            deficit_ctx = extra_context.get("share_deficit")
            if deficit_ctx and not asgn:
                curr = deficit_ctx["current_shares"]
                tgt = deficit_ctx["target_shares"]
                defn = deficit_ctx["deficit"]
                user_prompt += (
                    f"\n\n== ⚠️ SHARE DEFICIT ==\n"
                    f"Current shares: {curr:,} | Target: {tgt:,} | Missing: {defn:,} shares\n"
                    f"This likely occurred from a prior option assignment. "
                    f"DECISION: Should you rebuy {defn:,} shares (stock) to restore the target position? "
                    f"Factor in cash balance and market outlook.\n"
                )

            # Claude-specific: no shares held — prompt to restore original share position
            buy_shares_ctx = extra_context.get("claude_buy_shares_prompt")
            if buy_shares_ctx:
                cash = buy_shares_ctx.get("cash_available", 0)
                tkr = buy_shares_ctx.get("underlying", underlying or "the underlying")
                target = buy_shares_ctx.get("target_shares", 0)
                spot = get_underlying_price(market_event, tkr) if market_event else 0.0
                # How many shares cash can actually cover
                max_affordable = int(cash / spot / 100) * 100 if spot > 0 and cash > 0 else 0
                buy_qty = target if target > 0 else max(100, max_affordable // 2)
                user_prompt += (
                    f"\n\n== NO SHARES HELD — RESTORE ORIGINAL POSITION REQUIRED ==\n"
                    f"You currently hold 0 shares of {tkr}. "
                    f"The original target position is {target:,} shares.\n"
                    f"ACTION REQUIRED: Buy {buy_qty:,} shares of {tkr} to restore the covered call base.\n"
                    f"Available cash: ${cash:,.0f}\n"
                    f"Underlying price: ~${spot:,.2f}\n"
                    f"Max affordable: {max_affordable:,} shares\n"
                    f"You MUST output a stock BUY order for {tkr} (not an option contract). "
                    f"Buy as close to {buy_qty:,} shares as cash allows.\n"
                )

        try:
            raw = llm.call_raw(system_prompt, user_prompt)
            self._last_pipeline_calls.append({
                "phase": "executor", "system_prompt": system_prompt,
                "user_prompt": user_prompt, "response": raw,
            })
            return parse_json_array_response(raw)
        except Exception as exc:
            logger.warning("Executor LLM call failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------

    def _build_compressed_chain(
        self, market_event: MarketEvent | None, underlying: str | None = None,
        current_date: str = "",
    ) -> str:
        """Build chain context using AVSR+QOCR for the analyzer."""
        if market_event is None:
            return "(no chain data available)"

        chain_dicts, spot_map = self._parse_chain_dicts(market_event, underlying, current_date)
        if not chain_dicts:
            return "(no option contracts loaded)"

        n_calls = sum(1 for c in chain_dicts if c.get("type") == "C")
        n_puts = sum(1 for c in chain_dicts if c.get("type") == "P")
        logger.info("Chain context: %d contracts (%d calls, %d puts) for %s",
                     len(chain_dicts), n_calls, n_puts, underlying or "all")

        from trading.prompts.chain_context import build_chain_context
        spot = spot_map.get(underlying, 0.0) if underlying else next(iter(spot_map.values()), 0.0)
        recent = [b.close for b in market_event.bar_history.get(underlying or "", [])] if underlying else []
        qocr_cfg = getattr(self, "_QOCR_CONFIG", None)
        result = build_chain_context(chain_dicts, spot, recent, underlying or "",
                                     qocr_config=qocr_cfg,
                                     avsr_config=getattr(self, "_avsr_config", None))
        if result:
            return result

        # Fallback: raw table
        from trading.prompts.context import build_chain_table
        return build_chain_table(market_event, underlying)

    def _build_executor_chain(
        self, market_event: MarketEvent | None, underlying: str | None = None,
        current_date: str = "",
    ) -> str:
        """Build detailed QOCR chain for the executor (per-contract lists)."""
        if market_event is None:
            return "(no chain data available)"

        # Reuse the same OPRA parsing as _build_compressed_chain
        chain_dicts, spot_map = self._parse_chain_dicts(market_event, underlying, current_date)
        if not chain_dicts:
            return "(no option contracts loaded)"

        from trading.prompts.chain_context import build_executor_chain_context
        spot = spot_map.get(underlying, 0.0) if underlying else next(iter(spot_map.values()), 0.0)
        price_range = getattr(self, "_EXECUTOR_PRICE_RANGE", 0.0)
        otm_range   = getattr(self, "_EXECUTOR_OTM_RANGE", 0.0)
        itm_range   = getattr(self, "_EXECUTOR_ITM_RANGE", 0.0)
        dense_range = getattr(self, "_EXECUTOR_DENSE_RANGE", 0.0)
        sparse_gap  = getattr(self, "_EXECUTOR_SPARSE_GAP", 0)
        max_per_dte = getattr(self, "_EXECUTOR_MAX_PER_DTE", 0)
        vol_filter  = getattr(self, "_EXECUTOR_VOL_FILTER", False)
        return build_executor_chain_context(
            chain_dicts, spot, underlying or "",
            price_range=price_range,
            otm_range=otm_range,
            itm_range=itm_range,
            dense_range=dense_range,
            sparse_gap=sparse_gap,
            max_per_dte=max_per_dte,
            vol_filter=vol_filter,
        )

    def _parse_chain_dicts(
        self, market_event: MarketEvent, underlying: str | None, current_date: str,
    ) -> tuple[list[dict], dict[str, float]]:
        """Parse market bars into chain dicts with strike/DTE. Shared helper."""
        from datetime import date as _date
        from trading.core.greeks_math import parse_opra_contract

        chain_dicts: list[dict] = []
        spot_map: dict[str, float] = {}

        try:
            ref_date = _date.fromisoformat(current_date[:10]) if current_date else None
        except ValueError:
            ref_date = None

        for bar in market_event.bars:
            if bar.instrument_type == "STOCK":
                spot_map[bar.symbol] = bar.close
            elif bar.instrument_type == "OPTION" and bar.tradeable:
                if underlying and not bar.symbol.startswith(f"O:{underlying}"):
                    continue
                parsed = parse_opra_contract(bar.symbol)
                strike = parsed[3] if parsed else None
                right = parsed[2] if parsed else None
                dte = None
                if parsed and ref_date:
                    try:
                        expiry_date = _date.fromisoformat(parsed[1])
                        dte = (expiry_date - ref_date).days
                    except ValueError:
                        pass
                chain_dicts.append({
                    "ticker": bar.symbol,
                    "close": bar.close,
                    "delta": bar.delta,
                    "iv": bar.iv,
                    "vega": bar.vega,
                    "gamma": bar.gamma,
                    "theta": bar.theta,
                    "volume": bar.volume,
                    "strike": strike,
                    "dte": dte,
                    "expiry": parsed[1] if parsed else None,
                    "right": right,
                    "type": right,
                    "stale_minutes": bar.stale_minutes,
                    "window_start": bar.window_start,
                })

        return chain_dicts, spot_map

    def _build_portfolio_summary(self, portfolio: PortfolioSnapshot | None) -> str:
        if portfolio is None:
            return "No portfolio data."

        from trading.prompts.context import _infer_spread_groups

        lines = [
            f"Cash: ${portfolio.cash:,.2f}  Equity: ${portfolio.equity:,.2f}  "
            f"Daily P&L: ${portfolio.daily_pnl:,.2f} ({portfolio.daily_pnl_pct:.2%})",
        ]
        if portfolio.positions:
            spread_groups = _infer_spread_groups(portfolio.positions)
            for p in portfolio.positions:
                delta_str = f"delta={p.delta:.2f}" if p.delta is not None else ""
                iv_str    = f"IV={p.iv:.1%}" if p.iv is not None else ""
                stale_tag = f"[STALE {p.stale_minutes}min]" if p.stale else "[LIVE]"
                dte_str   = f"DTE={p.dte}" if p.contract.startswith("O:") else ""
                # Explicit close direction for options only — stock positions are held, not closed
                close_dir = ("to-close:SELL" if p.side == "LONG" else "to-close:BUY") if p.contract.startswith("O:") else "HOLD"
                sg_tag    = f"spread_group={spread_groups[p.contract]}" if p.contract in spread_groups else ""
                lines.append(
                    f"  {p.contract} {p.side} qty={p.qty} entry={p.avg_entry:.2f} "
                    f"mark={p.mark_price:.2f} pnl={p.unrealized_pnl:+.2f} "
                    f"{delta_str} {iv_str} {dte_str} [{close_dir}] {sg_tag} {stale_tag}".strip()
                )
            # Spread pair summary
            paired: dict[str, list[str]] = {}
            for contract, label in spread_groups.items():
                paired.setdefault(label, []).append(contract)
            if paired:
                lines.append("  -- Spread pairs (close both legs together) --")
                for label, contracts in sorted(paired.items()):
                    legs = [
                        f"{c} ({next(p.side for p in portfolio.positions if p.contract == c)})"
                        for c in contracts
                    ]
                    lines.append(f"  Group {label}: {' | '.join(legs)}")
        else:
            lines.append("  No open positions.")
        return "\n".join(lines)

    def _load_news_context(
        self,
        state: TradingState,
        underlying: str | None = None,
    ) -> tuple[str, str]:
        """Load news and earnings context from NPP if enabled in config.

        Returns (news_text, earnings_text).
        news_text includes news + economic events + earnings notices.
        earnings_text is empty (earnings notices are merged into news_text).

        Data-leak prevention: all NPP queries use current_ts as the upper time
        boundary so no future data can appear in the context.
        """
        dp = state.config.data_policy
        if not getattr(dp, "news_enabled", False):
            return "(news disabled)", ""

        try:
            from trading.tools.npp_context import build_overlay_news_context

            npp_url = getattr(dp, "npp_base_url", "") or DEFAULT_ESP_URL
            sym = underlying or (
                state.config.experiment.universe[0]
                if state.config.experiment.universe else "SPY"
            )
            text = build_overlay_news_context(
                npp_base_url=npp_url,
                current_ts=state.current_ts,
                underlying=sym,
                lookback_days=getattr(dp, "news_lookback_days", 7),
            )
            return text, ""
        except Exception as exc:
            logger.warning("NPP news context failed: %s", exc)
            return "(news load error)", ""

    def _build_tradable_list(self, market_event: MarketEvent | None) -> str:
        if market_event is None:
            return "(no tradable contracts)"
        lines = []
        for bar in market_event.bars:
            if bar.instrument_type == "OPTION" and bar.tradeable:
                delta_str = f"delta={bar.delta:.2f}" if bar.delta is not None else "delta=?"
                iv_str = f"IV={bar.iv:.1%}" if bar.iv is not None else "IV=?"
                lines.append(f"{bar.symbol} price=${bar.close:.2f} {delta_str} {iv_str} vol={bar.volume}")
        return "\n".join(lines) if lines else "(no tradable contracts)"

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_analysis(raw: str) -> dict:
        data = parse_json_object_response(raw)
        if not data:
            return {"intent": "neutral", "confidence": 0.0, "reasoning": "parse failure", "target_instruments": []}
        intent = data.get("intent", "neutral")
        if intent not in ("bullish", "bearish", "neutral", "exit_all"):
            intent = "neutral"
        confidence = data.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        return {
            "intent": intent,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "reasoning": data.get("reasoning", ""),
            "target_instruments": data.get("target_instruments", []),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_option_type(contract: str) -> str | None:
        """Extract 'C' or 'P' from OPRA contract string."""
        from trading.core.greeks_math import parse_opra_contract
        parsed = parse_opra_contract(contract)
        return parsed[2] if parsed else None

    def _filter_disallowed_actions(
        self, trade_plan: list[dict], strategy_name: str,
    ) -> list[dict]:
        """Hard-validate executor output against strategy rules.

        overlay_hedge:
          - BUY PUT (open new hedge)
          - SELL PUT (close existing hedge / roll)
        overlay_profit:
          - SELL CALL (open new covered call)
          - BUY CALL (close existing short call / roll)
        """
        if strategy_name == "overlay_hedge":
            filtered = []
            for order in trade_plan:
                action = order.get("action", "").lower()
                opt_type = self._get_option_type(order.get("contract", ""))
                # Allow buy put (new hedge) + sell put (close/roll existing)
                if opt_type == "P" and action in ("buy", "sell"):
                    filtered.append(order)
                else:
                    logger.warning(
                        "[%s] Rejected: %s %s (hedge allows buy/sell put only)",
                        strategy_name, action, order.get("contract", ""),
                    )
            return filtered

        if strategy_name == "overlay_profit":
            filtered = []
            for order in trade_plan:
                action = order.get("action", "").lower()
                contract = order.get("contract", "")
                opt_type = self._get_option_type(contract)
                # Allow sell/buy call (options) + buy stock (for rebuy after assignment)
                is_stock_buy = (action == "buy" and not contract.startswith("O:"))
                if (opt_type == "C" and action in ("buy", "sell")) or is_stock_buy:
                    filtered.append(order)
                else:
                    logger.warning(
                        "[%s] Rejected: %s %s (profit allows calls + stock rebuy only)",
                        strategy_name, action, contract,
                    )
            return filtered

        return trade_plan  # no filter for other strategies

    def _enforce_capacity(
        self, trade_plan: list[dict], extra_context: dict, strategy_name: str,
    ) -> list[dict]:
        """Cap sell call qty to available covered call capacity.

        Only sell-call legs consume capacity. Buy-call legs (upper leg of a
        call-credit spread) pass through freely. When a sell call in a spread
        group is capped, the corresponding buy call leg is also capped to match.
        """
        call_cap = extra_context.get("covered_call_capacity", 0)
        call_used = 0

        # First pass: apply capacity to sell calls; record final qty per spread_group
        spread_group_qty: dict[str, int] = {}
        for order in trade_plan:
            action = order.get("action", "").lower()
            if action != "sell":
                continue
            qty = int(order.get("qty", 1))
            remaining = call_cap - call_used
            if remaining <= 0:
                logger.warning("[%s] Blocked sell call — no coverage left", strategy_name)
                order["_blocked"] = True
                continue
            if qty > remaining:
                logger.warning("[%s] Capped sell call qty %d → %d (coverage)", strategy_name, qty, remaining)
                order["qty"] = remaining
            call_used += order["qty"]
            sg = order.get("spread_group")
            if sg:
                spread_group_qty[sg] = order["qty"]

        # Second pass: sync buy call legs in same spread group to capped sell qty
        result = []
        for order in trade_plan:
            if order.get("_blocked"):
                continue
            sg = order.get("spread_group")
            action = order.get("action", "").lower()
            if sg and action == "buy" and sg in spread_group_qty:
                order["qty"] = spread_group_qty[sg]
            result.append(order)
        return result

    @staticmethod
    def _flatten_all(
        portfolio: PortfolioSnapshot | None, reasoning: str, strategy_name: str,
    ) -> list[TradeIntent]:
        if portfolio is None:
            return []
        _overlay_strategies = {"overlay_profit", "overlay_hedge"}
        intents = []
        for pos in portfolio.positions:
            if pos.qty == 0:
                continue
            # Overlay strategies: NEVER sell the underlying stock on exit_all.
            # The stock is a permanent holding; only flatten option positions.
            if strategy_name in _overlay_strategies and not pos.contract.startswith("O:"):
                logger.info(
                    "[%s] exit_all: skipping stock position %s qty=%d (overlay holds stock permanently)",
                    strategy_name, pos.contract, pos.qty,
                )
                continue
            intents.append(TradeIntent(
                symbol=_extract_underlying(pos.contract),
                contract=pos.contract,
                side="SELL" if pos.qty > 0 else "BUY",
                qty=abs(pos.qty),
                order_type="MARKET",
                rationale=f"exit_all: {reasoning}",
                strategy_name=strategy_name,
            ))
        return intents

    @staticmethod
    def _plan_to_intents(
        trade_plan: list[dict], strategy_name: str, underlying: str | None = None,
    ) -> list[TradeIntent]:
        intents = []
        for order in trade_plan:
            contract = order.get("contract", "")
            symbol = underlying or _extract_underlying(contract)
            intents.append(TradeIntent(
                symbol=symbol,
                contract=contract,
                side=order.get("action", "buy").upper(),
                qty=int(order.get("qty", 1)),
                order_type=order.get("order_type", "MARKET"),
                limit_price=order.get("limit_price"),
                spread_group=order.get("spread_group"),
                rationale=order.get("rationale", ""),
                strategy_name=strategy_name,
            ))
        return intents


def _extract_underlying(contract: str) -> str:
    if contract.startswith("O:"):
        return "".join(ch for ch in contract[2:] if ch.isalpha())
    return contract
