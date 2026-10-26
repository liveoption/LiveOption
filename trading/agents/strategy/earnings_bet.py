"""EarningsBetStrategy — event-driven option betting around earnings.

Places option trades based on predictions of earnings results. Manages a
T-2 to T+2 window per earnings event, with pre-earnings entry, post-earnings
management, and auto-close at T+2.

Key design:
  - Pre-T: agent places bets at daily close (T-2 entry, T-1 adjust)
  - Post-T: intraday management every 30min with stop-profit/stop-loss/continue
  - Agent predictions saved to individual files in output folder
  - Portfolio snapshots logged at 5-minute intervals (not every bar)
  - Data-leak prevention: pre-T uses yesterday's close; post-T uses prev bar close
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.core.endpoints import DEFAULT_ESP_URL, DEFAULT_UPQ_URL
from trading.core.registry import register_strategy
from trading.core.schemas import TradeIntent

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import MarketEvent, PortfolioSnapshot, PositionInfo
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)

_CHECKPOINT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Event window tracking
# ---------------------------------------------------------------------------

@dataclass
class EventWindow:
    """Tracks the state of a single earnings event window (T-2 to T+2)."""

    ticker: str  # DB ticker (e.g. "BRK.B")
    upq_ticker: str  # UPQ ticker (e.g. "BRKB")
    earnings_date: str  # YYYY-MM-DD
    is_bmo: bool
    event_data: dict  # full event dict from EarningsLoader
    t_minus_2: str = ""  # YYYY-MM-DD
    t_minus_1: str = ""
    t_plus_1: str = ""
    t_plus_2: str = ""

    # State tracking
    phase: str = "pending"  # "pending" | "pre" | "post" | "closed"
    entry_decided: bool = False
    entry_date: str | None = None
    post_decisions: list[str] = field(default_factory=list)
    positions_opened: bool = False

    # Agent predictions (saved to file)
    prediction: dict | None = None
    post_assessment: dict | None = None

    # Price references for gap computation
    t_minus_1_close: float | None = None  # last close before earnings

    def contains_date(self, d: str) -> bool:
        return self.t_minus_2 <= d <= self.t_plus_2

    def get_phase_for_date(self, d: str) -> str:
        """Determine the trading phase for a given date.

        BMO (Before Market Open) — release before market opens on earnings_date:
          pre: t_minus_2, t_minus_1
          post: earnings_date (= t_plus_1), t_plus_2

        AMC (After Market Close) — release after market closes on earnings_date:
          pre: t_minus_2, t_minus_1
          skip: earnings_date (results not yet known during market hours)
          post: t_plus_1, t_plus_2
        """
        if d < self.t_minus_2 or d > self.t_plus_2:
            return "outside"
        if d <= self.t_minus_1:
            return "pre"
        if self.is_bmo:
            # BMO: results known before market opens on earnings_date → post
            return "post"
        else:
            # AMC: results released after market closes on earnings_date
            if d == self.earnings_date:
                return "skip"   # hold existing bets, wait for after-hours release
            return "post"       # t_plus_1, t_plus_2 — reaction days

    def days_to_earnings(self, d: str) -> int:
        """Return number of trading days from d to earnings_date (exclusive of d, inclusive of earnings_date)."""
        start = date.fromisoformat(d)
        end = date.fromisoformat(self.earnings_date)
        count = 0
        cur = start
        while cur < end:
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                count += 1
        return count

    def post_day_number(self, d: str) -> int:
        """Return 1 or 2 for post-earnings days (t_plus_1 = day 1, t_plus_2 = day 2)."""
        if d <= self.t_plus_1:
            return 1
        return 2


def _compute_trading_days(center_date: str, offset: int) -> str:
    d = date.fromisoformat(center_date)
    step = 1 if offset > 0 else -1
    remaining = abs(offset)
    while remaining > 0:
        d += timedelta(days=step)
        if d.weekday() < 5:
            remaining -= 1
    return d.isoformat()


def build_event_windows(events: list[dict]) -> list[EventWindow]:
    """Build event windows with adaptive T+1/T+2 based on BMO vs AMC.

    T is the earnings release date (calendar date, not a trading day offset).

    BMO (release before market open on earnings_date):
      Pre trading days:  T-2, T-1
      Post trading days: T (= t_plus_1, first reaction), T+1 (= t_plus_2)
      Total: 4 trading days

    AMC (release after market close on earnings_date):
      Pre trading days:  T-2, T-1
      Skip:              T (results unknown during market hours)
      Post trading days: T+1 (= t_plus_1, first reaction), T+2 (= t_plus_2)
      Total: 4 active trading days
    """
    windows = []
    for ev in events:
        earnings_date = ev["date"]
        is_bmo = ev.get("is_bmo", False)

        t_minus_2 = _compute_trading_days(earnings_date, -2)
        t_minus_1 = _compute_trading_days(earnings_date, -1)

        if is_bmo:
            # Reaction starts on earnings_date itself (market opens after results)
            t_plus_1 = earnings_date
            t_plus_2 = _compute_trading_days(earnings_date, 1)
        else:
            # Reaction starts next trading day (results released after close)
            t_plus_1 = _compute_trading_days(earnings_date, 1)
            t_plus_2 = _compute_trading_days(earnings_date, 2)

        w = EventWindow(
            ticker=ev["ticker"],
            upq_ticker=ev.get("upq_ticker", ev["ticker"]),
            earnings_date=earnings_date,
            is_bmo=is_bmo,
            event_data=ev,
            t_minus_2=t_minus_2,
            t_minus_1=t_minus_1,
            t_plus_1=t_plus_1,
            t_plus_2=t_plus_2,
        )
        windows.append(w)
    return windows


# ---------------------------------------------------------------------------
# Intraday timing utilities
# ---------------------------------------------------------------------------


def _minutes_elapsed(ts_a, ts_b, threshold: int) -> bool:
    """Return True if at least `threshold` minutes have elapsed between ts_a and ts_b.

    Accepts tz-aware ``datetime`` or ISO ``str`` inputs.
    """
    try:
        from datetime import datetime, timezone

        def _parse(s):
            if isinstance(s, datetime):
                return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        delta = abs((_parse(ts_b) - _parse(ts_a)).total_seconds())
        return delta >= threshold * 60
    except Exception:
        return True


def _ts_to_et_hhmm(ts) -> str:
    """Accepts tz-aware ``datetime`` or ISO ``str``; returns HH:MM in ET."""
    try:
        from datetime import datetime, timezone
        from trading.agents.strategy._tz_utils import ET
        if isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%H:%M")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@register_strategy("earnings_bet")
class EarningsBetStrategy(BaseStrategy):
    """Event-driven option betting around earnings announcements.

    Phase structure
    ---------------
    Pre-earnings  (T-2, T-1): daily cadence — agent bets at close of day.
                               T-2: initial entry. T-1: adjust based on latest price.
    Post-earnings (T, T+1):   intraday cadence — every 30 min.
                               Agent can stop-profit, stop-loss, continue, or adjust.
    Force-close   (T+2 EOD):  all positions closed mechanically; no LLM call.
    """

    REQUIRED_FREQUENCIES = frozenset({"1m"})

    from trading.agents.qocr import (
        qocr_config_earnings, qocr_config_earnings_post, get_executor_chain_config,
    )
    _QOCR_CONFIG = qocr_config_earnings()
    _exec_cfg = get_executor_chain_config("earnings")
    _EXECUTOR_PRICE_RANGE = _exec_cfg["price_range"]
    _EXECUTOR_OTM_RANGE   = _exec_cfg["otm_range"]
    _EXECUTOR_ITM_RANGE   = _exec_cfg["itm_range"]
    _EXECUTOR_DENSE_RANGE = _exec_cfg["dense_range"]
    _EXECUTOR_SPARSE_GAP  = _exec_cfg["sparse_gap"]
    _EXECUTOR_MAX_PER_DTE = _exec_cfg["max_per_dte"]

    # Post-earnings phase uses intraday-like config (flat chain, tighter range)
    _POST_QOCR_CONFIG = qocr_config_earnings_post()
    _post_exec_cfg = get_executor_chain_config("earnings_post")
    _POST_EXECUTOR_PRICE_RANGE = _post_exec_cfg["price_range"]
    _POST_EXECUTOR_OTM_RANGE   = _post_exec_cfg["otm_range"]
    _POST_EXECUTOR_ITM_RANGE   = _post_exec_cfg["itm_range"]
    _POST_EXECUTOR_DENSE_RANGE = _post_exec_cfg["dense_range"]
    _POST_EXECUTOR_SPARSE_GAP  = _post_exec_cfg["sparse_gap"]
    _POST_EXECUTOR_MAX_PER_DTE = _post_exec_cfg["max_per_dte"]
    _POST_EXECUTOR_VOL_FILTER  = _post_exec_cfg["vol_filter"]

    def __init__(
        self,
        llm_provider: str = "deepseek",
        llm_model: str = "deepseek-chat",
        llm_api_key: str = "",
        llm_base_url: str = "",
        llm_reasoning_effort: str | None = None,
        max_position_cost: float = 10000,
        max_contracts: int = 20,
        auto_close_at_t2: bool = True,
        dte_min: int = 7,
        dte_max: int = 14,
        upq_base_url: str = "",
        npp_base_url: str = "",
        stop_loss_pct: float = 0.50,
        post_decision_interval_minutes: int = 30,
        portfolio_snapshot_interval: int = 5,
        prompt_override: str = "",
        prompt_addenda: dict[str, str] | None = None,
        prompt_templates: dict[str, str] | None = None,
        **kwargs,
    ):
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url
        self.llm_reasoning_effort = llm_reasoning_effort
        self._custom_context = prompt_override.strip()
        self._prompt_addenda = prompt_addenda or {}
        self._prompt_templates = prompt_templates or {}
        self.max_position_cost = max_position_cost
        self.max_contracts = max_contracts
        self.auto_close_at_t2 = auto_close_at_t2
        self.dte_min = dte_min
        self.dte_max = dte_max
        self.upq_base_url = upq_base_url or DEFAULT_UPQ_URL
        self.npp_base_url = npp_base_url or DEFAULT_ESP_URL
        self.stop_loss_pct = stop_loss_pct
        self.post_decision_interval_minutes = post_decision_interval_minutes
        self.portfolio_snapshot_interval = portfolio_snapshot_interval
        self._extra_kwargs = kwargs

        # Initialized in on_session_start
        self._event_windows: list[EventWindow] = []
        self._llm = None
        self._earnings_loader = None
        self._universe: list[str] = []
        self._output_dir: Path | None = None
        self._predictions_dir: Path | None = None
        # Per-window: last intraday post-decision timestamp
        self._last_post_ts: dict[str, str] = {}
        # Per-window: stop signal received
        self._stopped: set[str] = set()
        # Snapshot interval tracking
        self._last_snapshot_ts: str = ""
        # Pre-T: track which days we've already acted on
        self._pre_acted_dates: dict[str, set[str]] = {}  # win_key -> set of dates
        self._decision_seq: int = 0

    def on_session_start(self, config: RunConfig) -> None:
        from trading.tools.earnings_loader import EarningsLoader
        from trading.tools.llm_client import LLMClient

        self._universe = config.experiment.universe
        self._news_enabled = config.data_policy.news_enabled
        self._news_lookback_days = config.data_policy.news_lookback_days
        self._output_dir = Path(config.evaluation.output_dir)
        self._predictions_dir = self._output_dir / "predictions"
        self._predictions_dir.mkdir(parents=True, exist_ok=True)

        llm_log_dir = self._output_dir / "llm_calls"
        self._llm = LLMClient(
            provider=self.llm_provider,
            model=self.llm_model,
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
            log_dir=llm_log_dir,
            reasoning_effort=self.llm_reasoning_effort,
        )
        self._earnings_loader = EarningsLoader(npp_base_url=self.npp_base_url)

        events = self._earnings_loader.get_earnings_calendar(
            tickers=self._universe,
            start=config.experiment.start[:10],
            end=config.experiment.end[:10],
        )
        self._event_windows = build_event_windows(events)
        logger.info(
            "EarningsBet: loaded %d earnings events for %d tickers",
            len(self._event_windows), len(self._universe),
        )

    def should_log_snapshot(self, current_ts: str) -> bool:
        """Return True if enough time has passed for a portfolio snapshot (5-min interval)."""
        if not self._last_snapshot_ts:
            self._last_snapshot_ts = current_ts
            return True
        if _minutes_elapsed(self._last_snapshot_ts, current_ts, self.portfolio_snapshot_interval):
            self._last_snapshot_ts = current_ts
            return True
        return False

    def on_step(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        from trading.agents.strategy._tz_utils import minutes_to_close_et

        from trading.core.time_contract import trading_day_from_ts
        current_date = trading_day_from_ts(state.current_ts)
        mins_to_close = minutes_to_close_et(state.current_ts)

        # EOD flatten: at 15:55 ET, close post-earnings positions
        if mins_to_close <= 5:
            return self._eod_flatten(portfolio, current_date)

        if mins_to_close == 0:
            return []

        all_intents: list[TradeIntent] = []

        orphan_intents = self._close_orphan_stock_positions(portfolio)
        all_intents.extend(orphan_intents)

        for window in self._event_windows:
            if window.phase == "closed":
                continue
            if not window.contains_date(current_date):
                continue

            win_key = f"{window.ticker}|{window.earnings_date}"
            if win_key in self._stopped:
                # Agent sent stop signal — force close remaining
                intents = self._force_close(window, current_date, portfolio)
                all_intents.extend(intents)
                continue

            try:
                intents = self._handle_event_step(window, current_date, portfolio, state)
                all_intents.extend(intents)
            except Exception as e:
                logger.error(
                    "EarningsBet: error handling %s on %s: %s",
                    window.ticker, current_date, e,
                )

        already_closing = {i.contract for i in all_intents}
        all_intents.extend(self._check_stop_loss(portfolio, already_closing))
        return all_intents

    def _eod_flatten(
        self, portfolio: PortfolioSnapshot, current_date: str
    ) -> list[TradeIntent]:
        """At 15:55 ET: close post-earnings positions; leave pre-earnings open overnight."""
        post_windows = {
            w.upq_ticker
            for w in self._event_windows
            if w.phase == "post" and w.contains_date(current_date)
        }
        if not post_windows:
            return []

        intents = []
        for pos in portfolio.positions:
            if not pos.contract.startswith("O:"):
                continue
            underlying = self._extract_underlying(pos.contract)
            if underlying not in post_windows:
                continue
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            intents.append(TradeIntent(
                symbol=underlying,
                contract=pos.contract,
                side=close_side,
                qty=abs(pos.qty),
                order_type="MARKET",
                rationale="EOD close — no after-hours trading for post-earnings positions",
                strategy_name="earnings_bet",
            ))

        if intents:
            logger.info(
                "EOD flatten: closing %d post-earnings positions at 15:55 ET on %s",
                len(intents), current_date,
            )
        return intents

    def _handle_event_step(
        self,
        window: EventWindow,
        current_date: str,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        phase = window.get_phase_for_date(current_date)

        # T+2: force close at any point during the day
        if current_date >= window.t_plus_2 and self.auto_close_at_t2:
            return self._force_close(window, current_date, portfolio)

        if phase == "pre":
            return self._handle_pre_earnings(window, current_date, portfolio, state)
        elif phase == "post":
            return self._handle_post_earnings(window, current_date, portfolio, state)
        elif phase == "skip":
            # AMC earnings day: results unknown during market hours.
            # Hold existing positions; no strategy call.
            window.phase = "pre"
            return []
        return []

    # ------------------------------------------------------------------
    # Pre-earnings (T-2, T-1) — daily cadence, at close of day
    # ------------------------------------------------------------------

    def _handle_pre_earnings(
        self,
        window: EventWindow,
        current_date: str,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Pre-earnings: analyzer-executor pipeline near close of day.

        Phase 1 (Analyzer): predict earnings outcome, recommend strategy with qty.
        Phase 2 (Executor): translate fuzzy intent into specific OPRA orders.
        """
        from trading.agents.strategy._tz_utils import minutes_to_close_et

        mins_to_close = minutes_to_close_et(state.current_ts)

        # Gate: only act near close of day (between 15:30 and 15:55 ET)
        win_key = f"{window.ticker}|{window.earnings_date}"
        acted_dates = self._pre_acted_dates.setdefault(win_key, set())
        if current_date in acted_dates:
            return []
        if mins_to_close > 30:  # before 15:30 ET
            return []

        acted_dates.add(current_date)
        window.phase = "pre"

        # Determine phase label
        is_t_minus_2 = (current_date == window.t_minus_2)
        phase_label = "T-2 ENTRY" if is_t_minus_2 else "T-1 ADJUST"

        from trading.ingest.ulid import new_ulid
        decision_id = new_ulid()
        self._last_decision_calls = []
        seq = self._decision_seq
        self._decision_seq = seq + 1
        self.emit("decision.started", {
            "decision_id": decision_id, "trading_day": current_date, "seq": seq,
            "strategy": "earnings_bet", "ticker": window.upq_ticker,
            "phase_label": phase_label,
        })

        # --- Build context (data-leak safe: bars up to yesterday's close) ---
        historical = self._fetch_enriched_historical(window)
        price_context_text = self._fetch_price_context(window.upq_ticker, current_date)
        news = self._fetch_news(window.upq_ticker, window.t_minus_2, state.current_ts)
        chain_summary = self._query_chain(window.upq_ticker, current_date)
        compressed_chain_text = self._compress_chain(chain_summary, window.upq_ticker, state)
        self._record_close_price(window, state)

        from trading.prompts.earnings import build_pre_earnings_system, build_pre_earnings_user

        system_prompt = build_pre_earnings_system(
            ticker=window.upq_ticker,
            max_position_cost=self.max_position_cost,
            max_contracts=self.max_contracts,
            template=self._prompt_templates.get("pre_system"),
        )
        account = self._portfolio_to_account(portfolio)
        user_prompt = build_pre_earnings_user(
            date=current_date,
            ticker=window.upq_ticker,
            earnings_date=window.earnings_date,
            is_bmo=window.is_bmo,
            days_to_earnings=window.days_to_earnings(current_date),
            account=account,
            historical_reactions=historical,
            estimated_eps=window.event_data.get("estimated_eps"),
            estimated_revenue=window.event_data.get("estimated_revenue"),
            chain_summary=chain_summary,
            compressed_chain_text=compressed_chain_text,
            news=news,
            price_context_text=price_context_text,
            phase_label=phase_label,
            t_minus_2=window.t_minus_2,
            t_minus_1=window.t_minus_1,
            t_plus_1=window.t_plus_1,
            t_plus_2=window.t_plus_2,
            template=self._prompt_templates.get("pre_user"),
        )

        logger.info(
            "PRE-EARNINGS %s for %s (date=%s, earnings=%s, chain=%d, news=%d)",
            phase_label, window.upq_ticker, current_date, window.earnings_date,
            len(chain_summary), len(news),
        )

        # --- Phase 1: Analyzer ---
        prediction, target_instruments = self._call_pre_analyzer(system_prompt, user_prompt)
        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "pre_analyzer",
            "n_targets": len(target_instruments or []),
        })

        if prediction:
            window.prediction = prediction
            self._save_prediction(window, current_date, phase_label, prediction)

        window.entry_decided = True
        window.entry_date = current_date

        # Mandatory trade: retry if analyzer returned no targets
        if not target_instruments:
            logger.warning(
                "PRE-EARNINGS: %s analyzer returned no targets on %s — retrying",
                window.upq_ticker, phase_label,
            )
            retry_prompt = user_prompt + (
                "\n\nYou MUST include at least one target_instrument. "
                "This is a betting scenario — uncertainty = buy a spread or straddle."
            )
            _, target_instruments = self._call_pre_analyzer(system_prompt, retry_prompt)
            if not target_instruments:
                logger.warning("PRE-EARNINGS: %s still no targets after retry — skipping", window.upq_ticker)
                self._write_earnings_artifact(
                    decision_id=decision_id, seq=seq, state=state,
                    prompt_system=system_prompt, prompt_user=user_prompt,
                    analysis={"prediction": prediction, "reason": "no_targets_after_retry"},
                    actions=[], intents=[], chain_summary=chain_summary, ticker=window.upq_ticker,
                )
                self.emit("decision.completed", {
                    "decision_id": decision_id, "seq": seq,
                    "has_trade": False, "n_trades": 0,
                    "reason": "no_targets_after_retry",
                })
                return []

        # --- Phase 2: Executor ---
        analysis = {
            "prediction": prediction,
            "target_instruments": target_instruments,
        }
        actions = self._call_earnings_executor(
            analysis, chain_summary, compressed_chain_text,
            account, window.upq_ticker,
        )
        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "pre_executor",
            "n_actions": len(actions or []),
        })

        if not actions:
            logger.warning("PRE-EARNINGS: executor returned no orders for %s", window.upq_ticker)
            self._write_earnings_artifact(
                decision_id=decision_id, seq=seq, state=state,
                prompt_system=system_prompt, prompt_user=user_prompt,
                analysis=analysis, actions=[], intents=[],
                chain_summary=chain_summary, ticker=window.upq_ticker,
            )
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": False, "n_trades": 0,
                "reason": "executor_no_orders",
            })
            return []

        logger.info("PRE-EARNINGS: %s → %d actions", window.upq_ticker, len(actions))
        window.positions_opened = True
        # Pre-T: force MARKET orders so they fill at next bar.
        for a in actions:
            a["order_type"] = "MARKET"
            a.pop("limit_price", None)
        intents = self._actions_to_intents(actions, window.upq_ticker, portfolio)
        self._write_earnings_artifact(
            decision_id=decision_id, seq=seq, state=state,
            prompt_system=system_prompt, prompt_user=user_prompt,
            analysis=analysis, actions=actions, intents=intents,
            chain_summary=chain_summary, ticker=window.upq_ticker,
        )
        self.emit("decision.completed", {
            "decision_id": decision_id, "seq": seq,
            "has_trade": bool(intents),
            "n_trades": len(intents), "phase_label": phase_label,
        })
        return intents

    # ------------------------------------------------------------------
    # Post-earnings (T, T+1) — intraday cadence, every N minutes
    # ------------------------------------------------------------------

    def _handle_post_earnings(
        self,
        window: EventWindow,
        current_date: str,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Post-earnings: analyzer-executor pipeline for position management.

        Phase 1 (Analyzer): assess earnings outcome, decide exit/hold/adjust/new_trade.
        Phase 2 (Executor): translate intent into specific OPRA orders.
        """
        window.phase = "post"

        if not window.positions_opened:
            window.phase = "closed"
            return []

        ticker_positions = self._get_ticker_positions(portfolio, window.upq_ticker)
        if not ticker_positions:
            window.phase = "closed"
            return []

        # Intraday interval gate
        win_key = f"{window.ticker}|{window.earnings_date}"
        last_ts = self._last_post_ts.get(win_key, "")
        current_ts = state.current_ts
        if last_ts and not _minutes_elapsed(last_ts, current_ts, self.post_decision_interval_minutes):
            return []
        self._last_post_ts[win_key] = current_ts

        from trading.ingest.ulid import new_ulid
        decision_id = new_ulid()
        self._last_decision_calls = []
        seq = self._decision_seq
        self._decision_seq = seq + 1
        self.emit("decision.started", {
            "decision_id": decision_id, "trading_day": current_date, "seq": seq,
            "strategy": "earnings_bet", "ticker": window.upq_ticker,
            "phase_label": "POST",
        })

        time_et = _ts_to_et_hhmm(current_ts)
        gap_info = self._build_gap_info(window, state)
        prediction_recap = self._format_prediction_recap(window)
        news = self._fetch_news(window.upq_ticker, window.earnings_date, state.current_ts)

        # Post-earnings: include 0-14 DTE
        chain_summary = self._query_chain(window.upq_ticker, current_date, dte_min=0)
        compressed_chain_text = self._compress_chain(
            chain_summary, window.upq_ticker, state, qocr_config=self._POST_QOCR_CONFIG,
        )

        post_day = window.post_day_number(current_date)

        from trading.prompts.earnings import build_post_earnings_system, build_post_earnings_user

        system_prompt = build_post_earnings_system(
            ticker=window.upq_ticker,
            post_day=post_day,
            time_et=time_et,
            template=self._prompt_templates.get("post_system"),
        )
        account = self._portfolio_to_account(portfolio)
        user_prompt = build_post_earnings_user(
            date=current_date,
            ticker=window.upq_ticker,
            earnings_date=window.earnings_date,
            actual_eps=window.event_data.get("actual_eps"),
            estimated_eps=window.event_data.get("estimated_eps"),
            eps_surprise_percent=window.event_data.get("eps_surprise_percent"),
            actual_revenue=window.event_data.get("actual_revenue"),
            estimated_revenue=window.event_data.get("estimated_revenue"),
            revenue_surprise_percent=window.event_data.get("revenue_surprise_percent"),
            account=account,
            chain_summary=chain_summary,
            post_day=post_day,
            time_et=time_et,
            compressed_chain_text=compressed_chain_text,
            news=news,
            gap_info=gap_info,
            prediction_recap=prediction_recap,
            template=self._prompt_templates.get("post_user"),
        )

        logger.info(
            "POST-EARNINGS call for %s (date=%s, time=%s ET, day=%d, pos=%d)",
            window.upq_ticker, current_date, time_et, post_day, len(ticker_positions),
        )

        # --- Phase 1: Analyzer ---
        assessment, target_instruments, stop_signal = self._call_post_analyzer(
            system_prompt, user_prompt,
        )
        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "post_analyzer",
            "decision": assessment.get("decision") if assessment else None,
            "stop": bool(stop_signal),
        })
        from datetime import datetime as _dt_cls
        from trading.core.time_contract import to_iso_z as _to_iso_z
        window.post_decisions.append(
            _to_iso_z(current_ts) if isinstance(current_ts, _dt_cls) else current_ts
        )

        if assessment:
            window.post_assessment = assessment
            self._save_assessment(window, current_date, time_et, assessment)

        # Handle stop signal
        if stop_signal:
            logger.info("STOP signal received for %s — closing all positions", window.upq_ticker)
            self._stopped.add(win_key)
            intents = self._force_close(window, current_date, portfolio)
            self._write_earnings_artifact(
                decision_id=decision_id, seq=seq, state=state,
                prompt_system=system_prompt, prompt_user=user_prompt,
                analysis={"assessment": assessment, "stop_signal": True}, actions=[],
                intents=intents, chain_summary=chain_summary, ticker=window.upq_ticker,
            )
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": bool(intents),
                "n_trades": len(intents), "reason": "stop_signal",
            })
            return intents

        # If decision is "hold" with no targets, skip executor
        decision = assessment.get("decision", "hold") if assessment else "hold"
        if not target_instruments and decision == "hold":
            self._write_earnings_artifact(
                decision_id=decision_id, seq=seq, state=state,
                prompt_system=system_prompt, prompt_user=user_prompt,
                analysis={"assessment": assessment, "reason": "hold_no_targets"},
                actions=[], intents=[], chain_summary=chain_summary, ticker=window.upq_ticker,
            )
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": False, "n_trades": 0,
                "reason": "hold_no_targets",
            })
            return []

        # --- Phase 2: Executor ---
        analysis = {
            "assessment": assessment,
            "target_instruments": target_instruments,
        }
        actions = self._call_earnings_executor(
            analysis, chain_summary, compressed_chain_text,
            account, window.upq_ticker,
        )
        self.emit("phase.completed", {
            "decision_id": decision_id, "phase": "post_executor",
            "n_actions": len(actions or []),
        })

        if not actions:
            self._write_earnings_artifact(
                decision_id=decision_id, seq=seq, state=state,
                prompt_system=system_prompt, prompt_user=user_prompt,
                analysis=analysis, actions=[], intents=[],
                chain_summary=chain_summary, ticker=window.upq_ticker,
            )
            self.emit("decision.completed", {
                "decision_id": decision_id, "seq": seq,
                "has_trade": False, "n_trades": 0,
                "reason": "executor_no_orders",
            })
            return []

        # Post-earnings: allow both buy and sell (agent decides)
        intents = self._actions_to_intents(actions, window.upq_ticker, portfolio)
        self._write_earnings_artifact(
            decision_id=decision_id, seq=seq, state=state,
            prompt_system=system_prompt, prompt_user=user_prompt,
            analysis=analysis, actions=actions, intents=intents,
            chain_summary=chain_summary, ticker=window.upq_ticker,
        )
        self.emit("decision.completed", {
            "decision_id": decision_id, "seq": seq,
            "has_trade": bool(intents),
            "n_trades": len(intents), "phase_label": "POST",
        })
        return intents

    def _write_earnings_artifact(
        self, *, decision_id, seq, state, prompt_system, prompt_user,
        analysis, actions, intents, chain_summary, ticker,
    ) -> None:
        """Serialize prompts/responses and call base.write_artifact."""
        calls = getattr(self, "_last_decision_calls", [])
        self.write_artifact(
            decision_id=decision_id, seq=seq, ts=state.current_ts,
            model_id=getattr(self._llm, "model", self.llm_model),
            provider=getattr(self._llm, "provider", self.llm_provider),
            prompt={"calls": [
                {"phase": call["phase"], "system": call["system_prompt"],
                 "user": call["user_prompt"]}
                for call in calls
            ], "base_system": prompt_system, "base_user": prompt_user},
            response={"calls": [
                {"phase": call["phase"], "raw": call["response"]}
                for call in calls
            ], "analysis": analysis, "actions": actions},
            context={"ticker": ticker},
            trades=[
                i.model_dump() if hasattr(i, "model_dump") else dict(i.__dict__)
                for i in (intents or [])
            ],
            market_snapshot={"chain_summary_count": len(chain_summary or [])},
        )

    def _force_close(
        self,
        window: EventWindow,
        current_date: str,
        portfolio: PortfolioSnapshot,
    ) -> list[TradeIntent]:
        if window.phase == "closed":
            return []

        ticker_positions = self._get_ticker_positions(portfolio, window.upq_ticker)
        window.phase = "closed"

        if not ticker_positions:
            return []

        logger.info(
            "EarningsBet: force-closing %d positions for %s at %s",
            len(ticker_positions), window.upq_ticker, current_date,
        )

        intents = []
        for pos in ticker_positions:
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            intents.append(
                TradeIntent(
                    symbol=window.upq_ticker,
                    contract=pos.contract,
                    side=close_side,
                    qty=abs(pos.qty),
                    order_type="MARKET",
                    rationale=f"T+2 auto-close for {window.ticker} earnings",
                    strategy_name="earnings_bet",
                )
            )
        return intents

    # ------------------------------------------------------------------
    # Orphan stock & stop-loss
    # ------------------------------------------------------------------

    def _close_orphan_stock_positions(
        self, portfolio: PortfolioSnapshot
    ) -> list[TradeIntent]:
        universe_tickers = set(self._universe) | {
            w.upq_ticker for w in self._event_windows
        }
        intents: list[TradeIntent] = []
        for pos in portfolio.positions:
            if pos.contract.startswith("O:"):
                continue
            if pos.contract not in universe_tickers:
                continue
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            qty = abs(pos.qty)
            if qty == 0:
                continue
            logger.info(
                "Closing orphan stock position: %s qty=%d",
                pos.contract, pos.qty,
            )
            intents.append(
                TradeIntent(
                    symbol=pos.contract,
                    contract=pos.contract,
                    side=close_side,
                    qty=qty,
                    order_type="MARKET",
                    rationale="Close orphan stock from option exercise/assignment",
                    strategy_name="earnings_bet",
                )
            )
        return intents

    def _check_stop_loss(
        self, portfolio: PortfolioSnapshot, already_closing: set[str]
    ) -> list[TradeIntent]:
        universe_tickers = set(self._universe) | {
            w.upq_ticker for w in self._event_windows
        }
        intents: list[TradeIntent] = []
        for pos in portfolio.positions:
            if not pos.contract.startswith("O:"):
                continue
            if pos.contract in already_closing:
                continue
            underlying = self._extract_underlying(pos.contract)
            if underlying not in universe_tickers:
                continue
            entry_cost = pos.avg_entry * abs(pos.qty) * 100
            if entry_cost == 0:
                continue
            loss_threshold = -(entry_cost * self.stop_loss_pct)
            if pos.unrealized_pnl < loss_threshold:
                close_side = "SELL" if pos.side == "LONG" else "BUY"
                logger.warning(
                    "Stop-loss triggered for %s: pnl=$%.2f, threshold=$%.2f",
                    pos.contract, pos.unrealized_pnl, loss_threshold,
                )
                intents.append(
                    TradeIntent(
                        symbol=underlying,
                        contract=pos.contract,
                        side=close_side,
                        qty=abs(pos.qty),
                        order_type="MARKET",
                        rationale=f"Stop-loss: lost {self.stop_loss_pct:.0%} of entry cost",
                        strategy_name="earnings_bet",
                    )
                )
        return intents

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------

    def _fetch_enriched_historical(self, window: EventWindow) -> list[dict]:
        """Fetch 4+ historical earnings with price move enrichment."""
        if not self._earnings_loader:
            return []
        raw_hist = self._earnings_loader.get_historical_reactions(
            window.upq_ticker, window.earnings_date, n=4
        )
        try:
            from trading.tools.earnings_loader import enrich_historical_with_price_moves
            return enrich_historical_with_price_moves(
                self.upq_base_url, window.upq_ticker, raw_hist
            )
        except Exception as e:
            logger.warning("Price enrichment failed: %s", e)
            return raw_hist

    def _fetch_price_context(self, ticker: str, as_of_date: str) -> str:
        """Fetch daily price summary with MAs, trend, range, and last 10 OHLC."""
        try:
            from trading.tools.earnings_loader import build_daily_price_summary
            return build_daily_price_summary(self.upq_base_url, ticker, as_of_date)
        except Exception as e:
            logger.warning("Price context fetch failed for %s: %s", ticker, e)
            return "  (price data unavailable)"

    def _fetch_news(self, ticker: str, start_date: str, current_ts) -> list[dict]:
        """Fetch news for ticker from start_date up to current_ts.

        Uses current_ts (not end-of-day) as the upper boundary to prevent
        data leaking within the trading day.  Accepts datetime or ISO string.
        """
        if not getattr(self, "_news_enabled", True):
            return []
        try:
            from trading.tools.npp_context import fetch_earnings_pre_news
            from datetime import datetime as _dt
            from trading.core.time_contract import to_iso_z
            ts_str = to_iso_z(current_ts) if isinstance(current_ts, _dt) else current_ts
            lookback_start = (date.fromisoformat(ts_str[:10]) - timedelta(days=getattr(self, "_news_lookback_days", 7))).isoformat()
            bounded_start = max(start_date, lookback_start)
            return fetch_earnings_pre_news(
                self.npp_base_url, ts_str, ticker, bounded_start, limit=8,
            )
        except Exception as e:
            logger.warning("News fetch failed for %s: %s", ticker, e)
            return []

    def _record_close_price(self, window: EventWindow, state: TradingState) -> None:
        """Record the underlying close price for later gap computation."""
        if state.market_event is None:
            return
        for bar in state.market_event.bars:
            if bar.symbol == window.upq_ticker and bar.instrument_type == "STOCK":
                window.t_minus_1_close = bar.close
                break

    def _build_gap_info(self, window: EventWindow, state: TradingState) -> str:
        """Build gap info string comparing current price to T-1 close."""
        if window.t_minus_1_close is None:
            return "  (T-1 close not available for gap computation)"

        current_price = None
        if state.market_event:
            for bar in state.market_event.bars:
                if bar.symbol == window.upq_ticker and bar.instrument_type == "STOCK":
                    # Use open price of current bar to avoid data leaking
                    current_price = bar.open
                    break

        if current_price is None:
            return f"  T-1 close: ${window.t_minus_1_close:.2f} (current price unavailable)"

        gap_pct = (current_price - window.t_minus_1_close) / window.t_minus_1_close * 100
        direction = "UP" if gap_pct > 0 else "DOWN" if gap_pct < 0 else "FLAT"
        return (
            f"  T-1 close: ${window.t_minus_1_close:.2f}\n"
            f"  Current price (open): ${current_price:.2f}\n"
            f"  Gap: {gap_pct:+.2f}% ({direction})"
        )

    def _format_prediction_recap(self, window: EventWindow) -> str:
        """Format the pre-earnings prediction for post-earnings context."""
        if not window.prediction:
            return "  (no pre-earnings prediction was recorded)"
        p = window.prediction
        lines = [
            f"  Direction: {p.get('direction', 'N/A')}",
            f"  Expected move: {p.get('expected_move_pct', 'N/A')}%",
            f"  Confidence: {p.get('confidence', 'N/A')}",
            f"  Thesis: {p.get('thesis', 'N/A')}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Prediction/assessment persistence
    # ------------------------------------------------------------------

    def _save_prediction(
        self, window: EventWindow, trade_date: str, phase: str, prediction: dict
    ) -> None:
        """Save agent prediction to individual file in predictions/ folder."""
        if self._predictions_dir is None:
            return
        filename = f"{window.upq_ticker}_{window.earnings_date}_{phase.replace(' ', '_')}.json"
        filepath = self._predictions_dir / filename
        data = {
            "ticker": window.upq_ticker,
            "earnings_date": window.earnings_date,
            "trade_date": trade_date,
            "phase": phase,
            "prediction": prediction,
        }
        try:
            filepath.write_text(json.dumps(data, indent=2))
            logger.info("Saved prediction to %s", filepath)
        except Exception as e:
            logger.warning("Failed to save prediction: %s", e)

    def _save_assessment(
        self, window: EventWindow, trade_date: str, time_et: str, assessment: dict
    ) -> None:
        """Save post-earnings assessment to individual file."""
        if self._predictions_dir is None:
            return
        filename = f"{window.upq_ticker}_{window.earnings_date}_post_{trade_date}_{time_et.replace(':', '')}.json"
        filepath = self._predictions_dir / filename
        data = {
            "ticker": window.upq_ticker,
            "earnings_date": window.earnings_date,
            "trade_date": trade_date,
            "time_et": time_et,
            "assessment": assessment,
        }
        try:
            filepath.write_text(json.dumps(data, indent=2))
            logger.info("Saved assessment to %s", filepath)
        except Exception as e:
            logger.warning("Failed to save assessment: %s", e)

    # ------------------------------------------------------------------
    # Analyzer-Executor LLM calls
    # ------------------------------------------------------------------

    def _call_pre_analyzer(
        self, system_prompt: str, user_prompt: str,
    ) -> tuple[dict | None, list[str]]:
        """Phase 1: call LLM analyzer for pre-earnings.

        Returns (prediction_dict, target_instruments_list).
        """
        from trading.prompts.earnings import parse_pre_earnings_analysis

        if self._custom_context:
            user_prompt += f"\n\nUser-defined trading context:\n{self._custom_context}"
        if self._prompt_addenda.get("pre_system"):
            system_prompt += "\n\nAdditional system instructions:\n" + self._prompt_addenda["pre_system"]
        if self._prompt_addenda.get("pre_user"):
            user_prompt += "\n\nAdditional user instructions:\n" + self._prompt_addenda["pre_user"]
        raw = self._llm.call_raw(system_prompt, user_prompt)
        self._last_decision_calls.append({
            "phase": "pre_analyzer", "system_prompt": system_prompt,
            "user_prompt": user_prompt, "response": raw,
        })
        prediction, targets = parse_pre_earnings_analysis(raw)
        logger.info(
            "PRE-EARNINGS analyzer: prediction=%s, %d targets. Raw (300): %s",
            prediction.get("direction") if prediction else "none",
            len(targets),
            raw[:300].replace("\n", " "),
        )
        return prediction, targets

    def _call_post_analyzer(
        self, system_prompt: str, user_prompt: str,
    ) -> tuple[dict | None, list[str], bool]:
        """Phase 1: call LLM analyzer for post-earnings.

        Returns (assessment_dict, target_instruments_list, stop_signal).
        """
        from trading.prompts.earnings import parse_post_earnings_analysis

        if self._custom_context:
            user_prompt += f"\n\nUser-defined trading context:\n{self._custom_context}"
        if self._prompt_addenda.get("post_system"):
            system_prompt += "\n\nAdditional system instructions:\n" + self._prompt_addenda["post_system"]
        if self._prompt_addenda.get("post_user"):
            user_prompt += "\n\nAdditional user instructions:\n" + self._prompt_addenda["post_user"]
        raw = self._llm.call_raw(system_prompt, user_prompt)
        self._last_decision_calls.append({
            "phase": "post_analyzer", "system_prompt": system_prompt,
            "user_prompt": user_prompt, "response": raw,
        })
        assessment, targets, stop = parse_post_earnings_analysis(raw)
        logger.info(
            "POST-EARNINGS analyzer: decision=%s, %d targets, stop=%s. Raw (300): %s",
            assessment.get("decision") if assessment else "none",
            len(targets), stop,
            raw[:300].replace("\n", " "),
        )
        return assessment, targets, stop

    def _call_earnings_executor(
        self,
        analysis: dict,
        chain_summary: list[dict],
        compressed_chain_text: str | None,
        account: dict,
        ticker: str,
    ) -> list[dict]:
        """Phase 2: call LLM executor to translate analyzer intent into orders.

        Uses the standard executor.txt prompt with earnings-specific constraints.
        """
        import json as _json
        from trading.prompts.loader import get_loader
        from trading.prompts.earnings import _build_positions_table, _format_chain

        loader = get_loader()

        # Build executor system prompt with earnings constraints
        base_system = loader.render(
            self._prompt_templates.get("executor_system") or loader.load_system("executor"),
            MAX_COST=f"{self.max_position_cost:,.0f}",
            MAX_CONTRACTS=str(self.max_contracts),
        )
        earnings_constraints = (
            "\n## Strategy Constraints (earnings_bet)\n"
            "- NO naked selling — never sell options without a matching spread leg.\n"
            "- Spread legs MUST have same qty, same expiry, same spread_group letter.\n"
            "- Use ONLY options with 7-14 DTE from the chain data.\n"
            "- Use LIMIT orders at approximately the chain's last close price.\n"
            "- OPRA format: O:<UNDERLYING><YY><MM><DD><C|P><STRIKE_8DIGITS>\n"
        )
        system_prompt = base_system + earnings_constraints

        # Build executor user prompt
        positions_table = _build_positions_table(account.get("positions", []), "")
        chain_str = compressed_chain_text or _format_chain(chain_summary)

        user_prompt = loader.render(
            self._prompt_templates.get("executor_user") or loader.load_user("executor"),
            ANALYSIS_JSON=_json.dumps(analysis, indent=2),
            TRADABLE_CONTRACTS=chain_str,
            POSITIONS_SUMMARY=positions_table,
        )

        from trading.prompts.parsers import parse_json_array_response
        if self._custom_context:
            user_prompt += f"\n\nUser-defined trading context:\n{self._custom_context}"
        if self._prompt_addenda.get("executor_system"):
            system_prompt += "\n\nAdditional system instructions:\n" + self._prompt_addenda["executor_system"]
        if self._prompt_addenda.get("executor_user"):
            user_prompt += "\n\nAdditional user instructions:\n" + self._prompt_addenda["executor_user"]
        raw = self._llm.call_raw(system_prompt, user_prompt)
        self._last_decision_calls.append({
            "phase": "executor", "system_prompt": system_prompt,
            "user_prompt": user_prompt, "response": raw,
        })
        orders = parse_json_array_response(raw)
        logger.info(
            "EARNINGS executor: %d orders. Raw (300): %s",
            len(orders), raw[:300].replace("\n", " "),
        )
        return orders

    # ------------------------------------------------------------------
    # Chain & compression
    # ------------------------------------------------------------------

    def _compress_chain(
        self,
        chain_summary: list[dict],
        ticker: str,
        state: TradingState,
        qocr_config=None,
    ) -> str | None:
        if not chain_summary:
            return None
        from trading.prompts.chain_context import build_chain_context

        spot = 0.0
        recent: list[float] = []
        if state.market_event is not None:
            for bar in state.market_event.bars:
                if bar.symbol == ticker and bar.instrument_type == "STOCK":
                    spot = bar.close
                    break
            history = state.market_event.bar_history.get(ticker, [])
            recent = [b.close for b in history]

        text = build_chain_context(
            raw_chain=chain_summary,
            spot_price=spot,
            recent_prices=recent,
            underlying=ticker,
            qocr_config=qocr_config if qocr_config is not None else self._QOCR_CONFIG,
            avsr_config=getattr(state.config, "avsr", None),
        )
        return text if text else None

    def _query_chain(
        self, ticker: str, trade_date: str, dte_min: int | None = None,
    ) -> list[dict]:
        from trading.tools.upq_tools import query_option_chain

        effective_dte_min = dte_min if dte_min is not None else self.dte_min
        chain: list[dict] = []
        for right in ("C", "P"):
            try:
                contracts = query_option_chain(
                    upq_base_url=self.upq_base_url,
                    underlying=ticker,
                    trade_date=trade_date,
                    right=right,
                    dte_min=effective_dte_min,
                    dte_max=self.dte_max,
                )
                contracts.sort(key=lambda c: c.get("volume", 0), reverse=True)
                chain.extend(contracts[:10])
            except Exception as e:
                logger.warning("chain query failed %s %s: %s", ticker, right, e)
        return chain

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_ticker_positions(
        self, portfolio: PortfolioSnapshot, upq_ticker: str
    ) -> list:
        return [
            pos for pos in portfolio.positions
            if self._extract_underlying(pos.contract) == upq_ticker
        ]

    @staticmethod
    def _extract_underlying(contract: str) -> str:
        if not contract.startswith("O:"):
            return contract
        stripped = contract[2:]
        sym = []
        for ch in stripped:
            if ch.isalpha():
                sym.append(ch)
            else:
                break
        return "".join(sym)

    def _portfolio_to_account(self, portfolio: PortfolioSnapshot) -> dict:
        positions = []
        for pos in portfolio.positions:
            positions.append({
                "instrument_id": pos.contract,
                "qty": pos.qty,
                "side": pos.side,
                "avg_cost": pos.avg_entry,
                "mark_price": pos.mark_price,
                "unrealized_pnl": pos.unrealized_pnl,
            })
        return {
            "cash_available": portfolio.cash,
            "equity": portfolio.equity,
            "positions": positions,
        }

    def _actions_to_intents(
        self,
        actions: list[dict],
        ticker: str,
        portfolio: PortfolioSnapshot,
    ) -> list[TradeIntent]:
        pos_by_contract: dict[str, PositionInfo] = {
            pos.contract: pos for pos in portfolio.positions
        }

        intents: list[TradeIntent] = []
        for a in actions:
            side = "BUY" if a["action"] == "buy" else "SELL"
            contract = a["contract"]
            qty = int(a["qty"])

            if side == "SELL":
                existing = pos_by_contract.get(contract)
                if existing is None:
                    if a.get("spread_group"):
                        logger.info(
                            "SELL-to-open for spread leg %s (group=%s)",
                            contract, a["spread_group"],
                        )
                    else:
                        logger.warning("SELL skipped — no position for %s", contract)
                        continue
                else:
                    max_qty = abs(existing.qty)
                    if qty > max_qty:
                        logger.warning("SELL qty capped: %s %d→%d", contract, qty, max_qty)
                        qty = max_qty
                    if qty == 0:
                        continue

            symbol = ticker
            if contract.startswith("O:"):
                stripped = contract[2:]
                sym_chars = []
                for ch in stripped:
                    if ch.isalpha():
                        sym_chars.append(ch)
                    else:
                        break
                symbol = "".join(sym_chars) or ticker

            intent = TradeIntent(
                symbol=symbol,
                contract=contract,
                side=side,
                qty=qty,
                order_type=a.get("order_type", "MARKET"),
                limit_price=a.get("limit_price"),
                spread_group=a.get("spread_group"),
                rationale=a.get("rationale", ""),
                strategy_name="earnings_bet",
            )

            if side == "BUY":
                price_est = intent.limit_price
                if price_est is None:
                    existing = pos_by_contract.get(contract)
                    price_est = existing.mark_price if existing else None
                if price_est is not None:
                    estimated_cost = qty * price_est * 100
                    if estimated_cost > self.max_position_cost:
                        logger.warning(
                            "BUY dropped — cost $%.0f > max $%.0f for %s",
                            estimated_cost, self.max_position_cost, contract,
                        )
                        continue

            intents.append(intent)
        return intents

    def save_checkpoint(self) -> dict:
        """Serialize cross-step state for resume.

        Persistent fields:
          - _event_windows: list of EventWindow dataclasses (via asdict).
          - _last_post_ts: dict[win_key -> ISO-Z ts str].
          - _stopped: set[win_key] (serialized as list).
          - _pre_acted_dates: dict[win_key -> set[date str]] (sets → lists).
          - _last_snapshot_ts: str (for 5-min snapshot cadence).

        Transient (not persisted): _llm, _earnings_loader, _output_dir,
        _predictions_dir, _universe — re-initialized in on_session_start.
        """
        from dataclasses import asdict

        return {
            "_version": _CHECKPOINT_SCHEMA_VERSION,
            "event_windows": [asdict(w) for w in self._event_windows],
            "last_post_ts": dict(self._last_post_ts),
            "stopped": sorted(self._stopped),
            "pre_acted_dates": {
                k: sorted(v) for k, v in self._pre_acted_dates.items()
            },
            "last_snapshot_ts": self._last_snapshot_ts,
        }

    def load_checkpoint(self, state: dict) -> None:
        version = state.get("_version")
        if version != _CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"EarningsBetStrategy checkpoint schema version mismatch: "
                f"got {version}, expected {_CHECKPOINT_SCHEMA_VERSION}"
            )
        self._event_windows = [
            EventWindow(**w) for w in state.get("event_windows", [])
        ]
        self._last_post_ts = dict(state.get("last_post_ts", {}))
        self._stopped = set(state.get("stopped", []))
        self._pre_acted_dates = {
            k: set(v) for k, v in state.get("pre_acted_dates", {}).items()
        }
        self._last_snapshot_ts = state.get("last_snapshot_ts", "") or ""

    def project_family_view(self, portfolio, state) -> dict:
        windows = list(getattr(self, "_event_windows", []) or [])
        current_day = getattr(state, "trading_day", None) or getattr(state, "current_day", None)
        active: list[dict] = []
        pending: list[dict] = []
        post: list[dict] = []
        for w in windows:
            phase = w.get_phase_for_date(current_day) if current_day else getattr(w, "phase", "pending")
            entry = {
                "ticker": getattr(w, "ticker", None),
                "earnings_date": getattr(w, "earnings_date", None),
                "phase": phase,
            }
            if phase in ("pre", "post"):
                active.append(entry)
            elif phase == "pending":
                pending.append(entry)
            else:  # "closed" (and any other terminal/outside state)
                post.append(entry)
        return {
            "kind": "earnings",
            "active_event_windows": active,
            "pending_events": pending,
            "post_event_realized": post,
        }
