"""Intraday0DTEStrategy — 0DTE option trading with multi-timeframe context.

Runs on 1-minute bars, makes decisions every 5 minutes using the
analyzer-executor two-agent pipeline (MultiAgentMixin).  Builds
multi-timeframe price context at 5-min, 15-min, and 1-hour intervals
from the minute bar history.

Only trades options expiring today (DTE=0).  Flattens all positions
before market close.

Registered as ``"intraday_0dte"`` in the strategy registry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.core.endpoints import DEFAULT_ESP_URL, DEFAULT_UPQ_URL
from trading.agents.strategy.multi_agent import MultiAgentMixin
from trading.core.registry import register_strategy
from trading.core.schemas import TradeIntent

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import BarData, MarketEvent, PortfolioSnapshot
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Multi-timeframe OHLCV aggregation
# ---------------------------------------------------------------------------


def _aggregate_bars(bars: list[BarData]) -> dict:
    """Aggregate a list of 1-minute BarData into a single OHLCV dict."""
    if not bars:
        return {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0, "time": "?"}
    return {
        "open": bars[0].open,
        "high": max(b.high for b in bars),
        "low": min(b.low for b in bars),
        "close": bars[-1].close,
        "volume": sum(b.volume for b in bars),
        "time": _bar_time_et(bars[-1].window_start),
    }


def build_multi_timeframe_series(
    bar_history: list[BarData],
    interval: int,
    max_bars: int = 12,
) -> list[dict]:
    """Split 1-minute bar history into non-overlapping N-minute OHLCV bars.

    Args:
        bar_history: List of 1-minute BarData, oldest first.
        interval: Aggregation interval in minutes (5, 15, 60).
        max_bars: Maximum number of aggregated bars to return.

    Returns:
        List of aggregated OHLCV dicts, oldest first.
    """
    if not bar_history:
        return []

    # Work backwards from the end so the most recent complete bar is aligned
    # to the current time.
    n = len(bar_history)
    bars_out: list[dict] = []
    idx = n
    while idx > 0 and len(bars_out) < max_bars:
        start = max(0, idx - interval)
        chunk = bar_history[start:idx]
        bars_out.append(_aggregate_bars(chunk))
        idx = start

    bars_out.reverse()
    return bars_out


def format_timeframe_table(
    ticker: str,
    bars: list[dict],
    label: str,
) -> str:
    """Format aggregated bars into a compact text table."""
    if not bars:
        return f"  {label}: (no data)"

    lines = [f"  {label}:"]
    for b in bars:
        chg = ((b["close"] - b["open"]) / b["open"] * 100) if b["open"] else 0
        lines.append(
            f"    {b['time']} O={b['open']:.2f} H={b['high']:.2f} "
            f"L={b['low']:.2f} C={b['close']:.2f} V={_fmt_vol(b['volume'])} "
            f"({chg:+.2f}%)"
        )
    return "\n".join(lines)


def _bar_time_et(window_start: str) -> str:
    """Convert a bar's window_start (UTC ISO) to HH:MM ET."""
    from trading.agents.strategy._tz_utils import ts_to_et_display
    return ts_to_et_display(window_start)


def _fmt_vol(v: int) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@register_strategy("intraday_0dte")
class Intraday0DTEStrategy(BaseStrategy, MultiAgentMixin):
    """0DTE intraday option trading with multi-timeframe context.

    Decides every 5 minutes via the analyzer-executor pipeline.
    Context includes 5-min, 15-min, and 1-hour price bars.

    Args:
        decision_interval_minutes: Minutes between LLM decisions (default 5).
        llm_provider: LLM provider name.
        llm_model: Model identifier.
        llm_api_key: Optional API key override.
        llm_base_url: Optional base URL override.
        max_positions: Maximum concurrent option positions.
        flatten_before_close: Whether to flatten before market close.
        flatten_minutes_before_close: Minutes before 16:00 ET to start flatten.
        trade_equity_pct: Fraction of equity to risk per trade.
    """

    # QOCR config: 0DTE only, no top-tradable section
    from trading.agents.qocr import qocr_config_0dte, get_executor_chain_config
    _QOCR_CONFIG = qocr_config_0dte()
    _exec_cfg = get_executor_chain_config("intraday_0dte")
    _EXECUTOR_PRICE_RANGE = _exec_cfg["price_range"]
    _EXECUTOR_OTM_RANGE   = _exec_cfg["otm_range"]
    _EXECUTOR_ITM_RANGE   = _exec_cfg["itm_range"]
    _EXECUTOR_DENSE_RANGE = _exec_cfg["dense_range"]
    _EXECUTOR_SPARSE_GAP  = _exec_cfg["sparse_gap"]
    _EXECUTOR_MAX_PER_DTE = _exec_cfg["max_per_dte"]
    _EXECUTOR_VOL_FILTER  = _exec_cfg["vol_filter"]

    # Intent filter: both directions allowed for 0DTE
    _ALLOWED_INTENTS = {
        "intraday_0dte": {"bullish", "bearish"},
    }

    # 0DTE executor constraints
    _STRATEGY_CONSTRAINTS = {
        "intraday_0dte": (
            "\n## Strategy Constraints (STRICT)\n"
            "- This is a 0DTE intraday strategy. ONLY trade options expiring TODAY.\n"
            "- Both calls and puts are allowed.\n"
            "- Allowed actions:\n"
            "  * buy call / buy put — open directional position\n"
            "  * sell call / sell put — close existing position\n"
            "  * buy+sell spread — defined-risk spread (use spread_group)\n"
            "\n## 0DTE Risk Rules\n"
            "- Prefer defined-risk strategies (vertical spreads) over naked options.\n"
            "- Keep position sizes small: 1-10 contracts per trade.\n"
            "- Close losers at -40% unrealized P&L.\n"
            "- Take profits at +60% unrealized P&L.\n"
            "- As minutes_to_close < 30, reduce new entries and focus on closing.\n"
            "- As minutes_to_close < 10, close ALL remaining positions.\n"
            "\n## Contract Selection\n"
            "- ATM/near-ATM (delta 0.30-0.55) for directional plays.\n"
            "- OTM (delta 0.15-0.30) for spread wings.\n"
            "- Prefer contracts with volume > 100 and tight bid-ask (not stale).\n"
        ),
    }

    def __init__(
        self,
        decision_interval_minutes: int = 5,
        llm_provider: str = "deepseek",
        llm_model: str = "deepseek-chat",
        llm_api_key: str = "",
        llm_base_url: str = "",
        llm_reasoning_effort: str | None = None,
        max_positions: int = 5,
        flatten_before_close: bool = True,
        flatten_minutes_before_close: int = 5,
        trade_equity_pct: float = 0.05,
        # Auto-stop: agent-driven intraday exit when satisfied with session P&L
        enable_auto_stop: bool = False,
        auto_stop_check_interval_minutes: int = 30,
        **kwargs,
    ) -> None:
        # Drop unknown kwargs (e.g. stock_qty injected by CaseRunner for other strategies)
        kwargs.pop("stock_qty", None)
        super().__init__(**kwargs)
        self._interval = decision_interval_minutes
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._llm_reasoning_effort = llm_reasoning_effort
        self._max_positions = max_positions
        self._flatten_before_close = flatten_before_close
        self._flatten_minutes_before_close = flatten_minutes_before_close
        self._trade_equity_pct = trade_equity_pct
        self._enable_auto_stop = enable_auto_stop
        self._auto_stop_check_interval = auto_stop_check_interval_minutes

        # Mutable per-session state
        self._llm = None
        self._steps_since_decision: int = 0
        self._universe: list[str] = []
        self._upq_base_url: str = DEFAULT_UPQ_URL
        self._locked_in: bool = False  # True after end-of-day lock-in decision
        # Auto-stop state (reset each day)
        self._auto_stopped: bool = False
        self._equity_history: list[tuple[str, float]] = []  # (ts, equity)
        self._steps_since_auto_stop_check: int = 0

    # -- Lifecycle hooks ---------------------------------------------------

    def on_session_start(self, config: RunConfig) -> None:
        from pathlib import Path
        from trading.tools.llm_client import LLMClient

        self._universe = list(config.experiment.universe)
        self._upq_base_url: str = config.execution.upq_base_url or DEFAULT_UPQ_URL
        llm_log_dir = Path(config.evaluation.output_dir) / "llm_calls"
        self._llm = LLMClient(
            provider=self._llm_provider,
            model=self._llm_model or "deepseek-chat",
            api_key=self._llm_api_key,
            base_url=self._llm_base_url,
            log_dir=llm_log_dir,
            reasoning_effort=self._llm_reasoning_effort,
        )
        logger.info(
            "Intraday0DTE: provider=%s model=%s interval=%dm universe=%s",
            self._llm_provider, self._llm_model, self._interval, self._universe,
        )

    def on_day_open(self, date: str, state: TradingState) -> None:
        self._steps_since_decision = 0
        self._locked_in = False
        self._auto_stopped = False
        self._equity_history = []
        self._steps_since_auto_stop_check = 0

    # -- Core decision logic -----------------------------------------------

    def on_step(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        self._steps_since_decision += 1

        # Compute minutes to close for flatten logic
        minutes_to_close = self._compute_minutes_to_close(state.current_ts)

        # Forced flatten near close
        if self._flatten_before_close and minutes_to_close <= self._flatten_minutes_before_close:
            return self._flatten_options(portfolio, "flatten before close", market_event)

        # End-of-day lock-in: once triggered, stop calling LLM for the rest of session
        if self._locked_in:
            return []

        # Auto-stop: if feature enabled and agent already decided to stop today, go silent
        if self._auto_stopped:
            return []

        # Record equity for the auto-stop trend series
        if portfolio is not None:
            self._equity_history.append((state.current_ts, portfolio.equity))

        # Auto-stop check: runs every N minutes during the active window
        # (after ≥60 min of session, before the last-60-min EOD window)
        if (
            self._enable_auto_stop
            and not self._auto_stopped
            and minutes_to_close > 60          # leave EOD window untouched
            and len(self._equity_history) >= 12  # need at least 1 hour of data
        ):
            self._steps_since_auto_stop_check += 1
            if self._steps_since_auto_stop_check >= self._auto_stop_check_interval:
                self._steps_since_auto_stop_check = 0
                stop = self._check_auto_stop(portfolio, state, minutes_to_close, market_event)
                if stop:
                    self._auto_stopped = True
                    logger.info("[intraday_0dte] Auto-stop triggered — done for today")
                    return self._flatten_options(portfolio, "auto-stop: agent satisfied", market_event)

        # In the last 60 minutes, make one lock-in decision via LLM then go silent
        if minutes_to_close <= 60 and not self._locked_in:
            if self._steps_since_decision < self._interval:
                return []
            self._steps_since_decision = 0
            self._locked_in = True  # regardless of outcome, no more LLM calls after this
            return self._decide_end_of_day(portfolio, state, minutes_to_close, market_event)

        # Only decide at the configured interval
        if self._steps_since_decision < self._interval:
            return []

        self._steps_since_decision = 0

        if self._llm is None:
            return []

        # Build multi-timeframe context and run pipeline
        budget = (portfolio.equity * self._trade_equity_pct) if portfolio.equity > 0 else 5000.0
        max_contracts = min(10, max(1, int(budget / 500)))  # rough estimate

        # Count existing option positions
        existing_positions = sum(
            1 for p in portfolio.positions
            if p.contract.startswith("O:") and p.qty != 0
        )
        remaining_slots = max(0, self._max_positions - existing_positions)

        if remaining_slots <= 0 and minutes_to_close > 30:
            # At capacity, only manage existing (pipeline handles via intent)
            pass

        intents = self._run_0dte_pipeline(
            market_event=market_event,
            portfolio=portfolio,
            state=state,
            max_cost=budget,
            max_contracts=min(max_contracts, max(remaining_slots, 1)),
            minutes_to_close=minutes_to_close,
        )
        return self._enforce_limit_prices(intents, market_event)

    # -- Auto-stop (optional, flag-gated) ------------------------------------

    def _check_auto_stop(
        self,
        portfolio: PortfolioSnapshot,
        state: TradingState,
        minutes_to_close: int,
        market_event: MarketEvent | None,
    ) -> bool:
        """Ask the LLM whether to stop trading for the day based on session P&L.

        Only called when ``enable_auto_stop=True``.  Returns True if the agent
        decides to stop (caller then flattens and sets _auto_stopped).

        The LLM receives:
        - Session P&L ($ and %) over starting equity
        - Rolling equity series (last 12 data points = ~1 hour at 5-min decisions)
        - Minutes remaining
        - Instruction to be conservative: only stop if gain is substantial AND
          the trend is NOT accelerating upward (don't cut a winning streak early)
        """
        if self._llm is None:
            return False

        session_pnl = portfolio.daily_pnl if portfolio else 0.0
        start_equity = portfolio.equity - session_pnl if portfolio else portfolio.equity
        pnl_pct = session_pnl / start_equity if start_equity > 0 else 0.0

        # Build equity trend series (last 12 recorded points ≈ 1 hour of data)
        recent = self._equity_history[-12:]
        equity_series = "  ".join(
            f"{ts.strftime('%H:%M')}=${eq:,.0f}" for ts, eq in recent
        )

        prompt = (
            f"Current time: {state.current_ts.strftime('%H:%M')} UTC | {minutes_to_close} min to close.\n"
            f"Session P&L: ${session_pnl:+,.2f} ({pnl_pct:+.2%} of starting equity ${start_equity:,.0f}).\n"
            f"Equity trend (last ~1hr, 5-min snapshots):\n  {equity_series}\n\n"
            "Question: should we STOP trading for the rest of today and lock in this result?\n\n"
            "Guidelines for this decision (be CONSERVATIVE about stopping early):\n"
            "- Only recommend STOP if the session gain is meaningful AND the equity trend "
            "appears to have peaked or is flattening — not if it is still climbing.\n"
            "- Do NOT recommend STOP just because we are profitable; a rising equity trend "
            "suggests we should keep running the strategy.\n"
            "- Recommend STOP if: gain is solid, trend has flattened or reversed, and "
            "there is meaningful risk of giving back today's profits.\n"
            "- Recommend CONTINUE if: gain is small, trend is still rising, or we are at a loss.\n"
            "- Default to CONTINUE when uncertain.\n\n"
            'Respond with exactly one JSON object: {"decision": "stop" | "continue", "reasoning": "<1-2 sentences>"}'
        )

        try:
            raw = self._llm.call_raw(
                "You are a disciplined intraday trading risk manager deciding whether to stop trading early.",
                prompt,
            )
            import json as _json
            clean = raw.strip().strip("```json").strip("```").strip()
            result = _json.loads(clean)
            decision = result.get("decision", "continue")
            reasoning = result.get("reasoning", "")
        except Exception as exc:
            logger.warning("[intraday_0dte] Auto-stop LLM check failed (%s), continuing", exc)
            return False

        logger.info("[intraday_0dte] Auto-stop check: %s — %s", decision, reasoning)
        return decision == "stop"

    # -- End-of-day lock-in -----------------------------------------------

    def _decide_end_of_day(
        self,
        portfolio: PortfolioSnapshot,
        state: TradingState,
        minutes_to_close: int,
        market_event: MarketEvent | None = None,
    ) -> list[TradeIntent]:
        """One final LLM decision in the last 60 minutes.

        Asks the LLM whether to lock in current P&L (flatten all) or let
        positions run to expiry. After this call the session goes silent —
        no more LLM calls regardless of outcome.
        """
        if self._llm is None:
            return self._flatten_options(portfolio, "end-of-day: no LLM, flatten", market_event)

        open_positions = [p for p in (portfolio.positions or [])
                          if p.contract.startswith("O:") and p.qty != 0]
        if not open_positions:
            logger.info("[intraday_0dte] EOD lock-in: no open positions, done.")
            return []

        session_pnl = portfolio.daily_pnl if portfolio else 0.0
        start_equity = portfolio.equity - session_pnl if portfolio else 0.0
        pnl_pct = session_pnl / start_equity if start_equity > 0 else 0.0

        pos_lines = "\n".join(
            f"  {p.contract}: qty={p.qty} unrealized_pnl={getattr(p, 'unrealized_pnl', 'N/A')}"
            for p in open_positions
        )

        prompt = (
            f"It is now {minutes_to_close} minutes before market close.\n"
            f"Session P&L: ${session_pnl:+,.2f} ({pnl_pct:+.2%} of starting equity).\n"
            f"Open positions ({len(open_positions)}):\n{pos_lines}\n\n"
            "Decision: should we EXIT all positions now to lock in today's result, "
            "or HOLD and let them run to expiry?\n\n"
            "Consider: remaining time value, current P&L, risk of giving back gains or deepening losses.\n"
            "Respond with exactly one JSON object:\n"
            '{"decision": "exit" | "hold", "reasoning": "<1-2 sentences>"}'
        )

        try:
            raw = self._llm.call_raw(
                "You are a disciplined 0DTE options risk manager making the final end-of-day decision.",
                prompt,
            )
            import json as _json
            # Strip markdown fences if present
            clean = raw.strip().strip("```json").strip("```").strip()
            result = _json.loads(clean)
            decision = result.get("decision", "hold")
            reasoning = result.get("reasoning", "")
        except Exception as exc:
            logger.warning("[intraday_0dte] EOD LLM failed (%s), defaulting to hold", exc)
            decision = "hold"
            reasoning = "LLM error"

        logger.info("[intraday_0dte] EOD lock-in decision: %s — %s", decision, reasoning)

        if decision == "exit":
            return self._flatten_options(portfolio, f"EOD lock-in: {reasoning}", market_event)
        return []

    # -- Pipeline ----------------------------------------------------------

    def _run_0dte_pipeline(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
        max_cost: float,
        max_contracts: int,
        minutes_to_close: int,
    ) -> list[TradeIntent]:
        """Run analyzer-executor pipeline with 0DTE multi-timeframe context."""
        from trading.prompts.loader import get_loader
        from trading.prompts.parsers import parse_json_array_response, parse_json_object_response
        from trading.agents.strategy._tz_utils import ts_to_et_display
        import json

        loader = get_loader()

        # --- Phase 1: Analyzer ---
        chain_context = self._build_compressed_chain(
            market_event, self._universe[0] if self._universe else None, state.current_date,
        )
        portfolio_summary = self._build_portfolio_summary(portfolio)
        multi_tf_context = self._build_multi_timeframe_context(market_event)
        daily_price_context = self._build_daily_price_context(market_event, state.current_date)

        # --- News context (data-leak safe: only events up to current_ts) ---
        news_context = "(news disabled)"
        dp = getattr(state.config, "data_policy", None)
        if dp is not None and getattr(dp, "news_enabled", False):
            try:
                from trading.tools.npp_context import build_intraday_news_context
                npp_url = getattr(dp, "npp_base_url", "") or DEFAULT_ESP_URL
                from trading.core.time_contract import to_iso_z as _to_iso_z
                news_context = build_intraday_news_context(
                    npp_base_url=npp_url,
                    current_ts=_to_iso_z(state.current_ts),
                    universe_tickers=self._universe,
                )
            except Exception as _exc:
                logger.debug("Intraday news context failed: %s", _exc)

        system_prompt = loader.load_system("0dte_analyzer")
        user_prompt = loader.render(
            loader.load_user("0dte_analyzer"),
            CURRENT_DATE=state.current_date,
            CURRENT_TIME=ts_to_et_display(state.current_ts),
            MINUTES_TO_CLOSE=str(minutes_to_close),
            PORTFOLIO_SUMMARY=portfolio_summary,
            DAILY_PRICE_CONTEXT=daily_price_context,
            MULTI_TIMEFRAME_CONTEXT=multi_tf_context,
            CHAIN_CONTEXT=chain_context,
            NEWS_CONTEXT=news_context,
        )

        try:
            raw_analysis = self._llm.call_raw(system_prompt, user_prompt)
            analysis = self._parse_analysis(raw_analysis)
        except Exception as exc:
            logger.warning("0DTE analyzer failed: %s", exc)
            return []

        intent = analysis.get("intent", "neutral")

        if intent == "neutral":
            logger.info("[intraday_0dte] Analyzer: neutral — hold")
            return []

        if intent == "exit_all":
            logger.info("[intraday_0dte] Analyzer: exit_all")
            return self._flatten_options(portfolio, analysis.get("reasoning", "exit_all"))

        logger.info(
            "[intraday_0dte] Analyzer: %s (conf=%.2f) — calling executor",
            intent, analysis.get("confidence", 0),
        )

        # --- Phase 2: Executor ---
        tradable = self._build_executor_chain(
            market_event, self._universe[0] if self._universe else None, state.current_date,
        )

        base_prompt = loader.render(
            loader.load_system("0dte_executor"),
            MAX_COST=f"{max_cost:,.0f}",
            MAX_CONTRACTS=str(max_contracts),
            MINUTES_TO_CLOSE=str(minutes_to_close),
            MAX_POSITIONS=str(self._max_positions),
        )
        constraints = self._STRATEGY_CONSTRAINTS.get("intraday_0dte", "")
        executor_system = base_prompt + constraints

        user_prompt = loader.render(
            loader.load_user("0dte_executor"),
            ANALYSIS_JSON=json.dumps(analysis, indent=2),
            TRADABLE_CONTRACTS=tradable,
            POSITIONS_SUMMARY=portfolio_summary,
            MINUTES_TO_CLOSE=str(minutes_to_close),
        )

        try:
            raw_plan = self._llm.call_raw(executor_system, user_prompt)
            trade_plan = parse_json_array_response(raw_plan)
        except Exception as exc:
            logger.warning("0DTE executor failed: %s", exc)
            return []

        if not trade_plan:
            logger.info("[intraday_0dte] Executor returned empty plan")
            return []

        # Convert to TradeIntents
        intents = self._plan_to_intents(trade_plan, "intraday_0dte",
                                        self._universe[0] if self._universe else None)
        for ti in intents:
            logger.info(
                "[intraday_0dte] ORDER: %s %s qty=%d %s — %s",
                ti.side, ti.contract, ti.qty, ti.order_type, ti.rationale,
            )
        return intents

    # -- Daily price context -----------------------------------------------

    def _build_daily_price_context(
        self,
        market_event: MarketEvent,
        current_date: str,
    ) -> str:
        """Fetch daily bars from UPQ and build a compact daily summary.

        Delegates to the shared build_daily_price_summary() utility.
        Passes today's open from intraday bar_history for the gap line.
        """
        from trading.tools.earnings_loader import build_daily_price_summary

        sections: list[str] = []
        for ticker in self._universe:
            # Get today's open from the first intraday bar (no data leak — it's the open)
            today_open: float | None = None
            bar_hist = market_event.bar_history.get(ticker, [])
            if bar_hist:
                today_open = bar_hist[0].open
            try:
                text = build_daily_price_summary(
                    self._upq_base_url, ticker, current_date, today_open=today_open,
                )
            except Exception as exc:
                logger.debug("Daily price summary failed for %s: %s", ticker, exc)
                text = f"{ticker} daily: (unavailable)"
            sections.append(text)

        return "\n\n".join(sections) if sections else "(no daily data)"

    # -- Multi-timeframe context -------------------------------------------

    def _build_multi_timeframe_context(self, market_event: MarketEvent) -> str:
        """Build price series at 5-min, 15-min, and 1-hour from bar history.

        Uses bar_history[:-1] (all bars except the current forming bar) so the
        agent only sees fully completed minute bars and avoids look-ahead on the
        current bar's close.  The current bar's close (spot price) is shown
        separately as the live reference price.
        """
        sections = []

        for ticker in self._universe:
            full_history = market_event.bar_history.get(ticker, [])
            if not full_history:
                continue

            # Current live spot price (from the bar in progress — real-time OK)
            current = full_history[-1].close if full_history else 0
            sections.append(f"{ticker}: ${current:.2f}")

            # Use only completed bars (exclude the still-forming current bar)
            history = full_history[:-1] if len(full_history) > 1 else full_history

            # 5-minute bars (last 12 = 1 hour of 5-min bars)
            bars_5m = build_multi_timeframe_series(history, 5, max_bars=12)
            sections.append(format_timeframe_table(ticker, bars_5m, "5-min bars"))

            # 15-minute bars (last 6 = 1.5 hours of 15-min bars)
            bars_15m = build_multi_timeframe_series(history, 15, max_bars=6)
            sections.append(format_timeframe_table(ticker, bars_15m, "15-min bars"))

            # 1-hour bars (last 4 = 4 hours)
            bars_1h = build_multi_timeframe_series(history, 60, max_bars=4)
            sections.append(format_timeframe_table(ticker, bars_1h, "1-hour bars"))

        return "\n".join(sections) if sections else "(no price data)"

    # -- Flatten helpers ---------------------------------------------------

    def _flatten_options(
        self,
        portfolio: PortfolioSnapshot,
        reason: str,
        market_event: MarketEvent | None = None,
    ) -> list[TradeIntent]:
        """Close all open option positions using LIMIT orders at current bar price."""
        if portfolio is None:
            return []
        intents = []
        for pos in portfolio.positions:
            if not pos.contract.startswith("O:") or pos.qty == 0:
                continue
            side = "SELL" if pos.qty > 0 else "BUY"
            price = self._bar_price(pos.contract, market_event, side)
            intents.append(TradeIntent(
                symbol=self._universe[0] if self._universe else "",
                contract=pos.contract,
                side=side,
                qty=abs(pos.qty),
                order_type="LIMIT" if price is not None else "MARKET",
                limit_price=price,
                rationale=reason,
                strategy_name="intraday_0dte",
            ))
        if intents:
            logger.info("[intraday_0dte] Flattening %d positions: %s", len(intents), reason)
        return intents

    def _bar_price(
        self,
        contract: str,
        market_event: MarketEvent | None,
        side: str,
    ) -> float | None:
        """Return a realistic order price for a contract at the current step.

        Uses bar.open of the current bar — the first known price at the start
        of the current minute, equivalent to the previous bar's close.  This
        avoids look-ahead on the current bar's close, which isn't known until
        the minute ends.

        For buys:  open * 1.01  (willing to pay 1% over to ensure fill)
        For sells: open * 0.99  (willing to accept 1% under to ensure fill)
        Returns None if no bar data is available.
        """
        if market_event is None:
            return None
        for bar in market_event.bars:
            if bar.symbol == contract and bar.open and bar.open > 0:
                slippage = 1.01 if side == "BUY" else 0.99
                price = round(bar.open * slippage, 2)
                return max(price, 0.01)
        return None

    def _enforce_limit_prices(
        self,
        intents: list[TradeIntent],
        market_event: MarketEvent | None,
    ) -> list[TradeIntent]:
        """Convert any MARKET orders to LIMIT using current bar close price.

        If no bar price is available for a contract, leave as MARKET so the
        order still executes rather than silently dropping it.
        """
        for ti in intents:
            if ti.order_type == "MARKET" or ti.limit_price is None:
                price = self._bar_price(ti.contract, market_event, ti.side)
                if price is not None:
                    ti.order_type = "LIMIT"
                    ti.limit_price = price
        return intents

    @staticmethod
    def _compute_minutes_to_close(current_ts: str) -> int:
        from trading.agents.strategy._tz_utils import minutes_to_close_et
        return minutes_to_close_et(current_ts)

    def save_checkpoint(self) -> dict:
        raise NotImplementedError(f"{self.__class__.__name__} does not support checkpoint yet (Phase 1 scope or Phase 2 scenario)")

    def load_checkpoint(self, state: dict) -> None:
        raise NotImplementedError(f"{self.__class__.__name__} does not support checkpoint yet")
