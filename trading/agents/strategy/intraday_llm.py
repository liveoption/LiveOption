"""IntradayLLMStrategy — intraday option trading with interval-based LLM decisions.

Buffers minute-level market events and calls the LLM every N minutes to
produce TradeIntents.  Registered as ``"intraday_llm"`` in the strategy
registry.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.core.registry import register_strategy
from trading.core.time_contract import et_time_from_ts, trading_day_from_ts
from trading.prompts.intraday import (
    build_intraday_system_prompt,
    build_intraday_user_prompt,
    parse_intraday_response,
)

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import BarData, MarketEvent, PortfolioSnapshot, TradeIntent
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)


@register_strategy("intraday_llm")
class IntradayLLMStrategy(BaseStrategy):
    """Intraday option trading: LLM decides every N minutes.

    Accumulates minute bars into decision windows, then aggregates and
    calls the LLM at each decision point.

    Args:
        decision_interval_minutes: How many steps between LLM calls.
        llm_provider: LLM provider name (e.g. "deepseek", "claude", "mock").
        llm_model: Model identifier for the LLM provider.
        dte_min: Minimum DTE for chain filtering.
        dte_max: Maximum DTE for chain filtering.
        max_positions: Maximum number of open positions.
        chain_sample_tickers: Override tickers for chain sampling; if None,
            uses the experiment universe.
        flatten_before_close: Whether positions must be closed before EOD.
        flatten_minutes_before_close: Minutes before close to start flatten.
        **kwargs: Additional keyword arguments (ignored).
    """

    REQUIRED_FREQUENCIES = frozenset({"1m"})
    STRATEGY_NAME = "intraday_llm"

    def __init__(
        self,
        decision_interval_minutes: int = 15,
        llm_provider: str = "deepseek",
        llm_model: str = "deepseek-chat",
        dte_min: int = 0,
        dte_max: int = 7,
        max_positions: int = 5,
        prompt_override: str | None = None,
        prompt_addenda: dict[str, str] | None = None,
        prompt_templates: dict[str, str] | None = None,
        chain_sample_tickers: list[str] | None = None,
        flatten_before_close: bool = True,
        flatten_minutes_before_close: int = 5,
        **kwargs,
    ):
        self._interval = decision_interval_minutes
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._dte_min = dte_min
        self._dte_max = dte_max
        self._max_positions = max_positions
        self._custom_context = prompt_override.strip() if prompt_override else ""
        self._prompt_addenda = prompt_addenda or {}
        self._prompt_templates = prompt_templates or {}
        self._chain_sample_tickers = chain_sample_tickers
        self._flatten_before_close = flatten_before_close
        self._flatten_minutes_before_close = flatten_minutes_before_close

        # Mutable per-session state
        self._llm = None  # initialized in on_session_start
        self._bar_buffer: list[MarketEvent] = []
        self._steps_since_decision: int = 0
        self._universe: list[str] = []
        # Monotonic per-run decision counter, drives decision_records.seq.
        # decision_id is a ULID so the web layer can locate the artifact dir.
        self._decision_seq: int = 0

    # -- Lifecycle hooks ---------------------------------------------------

    def on_session_start(self, config: RunConfig) -> None:
        """Initialise the LLM client from config."""
        from trading.tools.llm_client import LLMClient

        self._llm = LLMClient(
            provider=self._llm_provider,
            model=self._llm_model,
        )
        self._universe = (
            self._chain_sample_tickers
            or list(config.experiment.universe)
        )
        self._data_policy = config.data_policy
        self._flatten_before_close = config.risk.flatten_before_close
        self._flatten_minutes_before_close = config.risk.flatten_minutes_before_close

    def on_day_open(self, date: str, state: TradingState) -> None:
        """Reset bar buffer and decision counter at each day's open."""
        self._bar_buffer = []
        self._steps_since_decision = 0

    # -- Core decision logic -----------------------------------------------

    def on_step(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Buffer bars and decide at the configured interval.

        Returns an empty list on non-decision steps or if the LLM call fails.
        """
        self._bar_buffer.append(market_event)
        self._steps_since_decision += 1

        if self._steps_since_decision < self._interval:
            return []

        # Decision point
        self._steps_since_decision = 0
        window_bars = self._bar_buffer[-self._interval :]
        intents = self._call_llm(window_bars, portfolio, state)
        return intents

    # -- Internal helpers --------------------------------------------------

    def _aggregate_window(self, bars: list[BarData]) -> dict:
        """Aggregate a list of 1-minute BarData into an OHLCV summary.

        Returns a dict with keys: open, high, low, close, volume.
        The first bar's open, max of highs, min of lows, last bar's close,
        and sum of volumes.

        Args:
            bars: List of BarData objects for the same instrument.

        Returns:
            Dict with aggregated OHLCV values.
        """
        if not bars:
            return {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}

        return {
            "open": bars[0].open,
            "high": max(b.high for b in bars),
            "low": min(b.low for b in bars),
            "close": bars[-1].close,
            "volume": sum(b.volume for b in bars),
        }

    def _build_price_windows(
        self,
        window_events: list[MarketEvent],
    ) -> list[dict]:
        """Convert buffered MarketEvents into price window summaries.

        Groups bars by ticker, aggregates into an OHLCV window, and
        computes change percentages.
        """
        # Collect bars per ticker across the window
        ticker_bars: dict[str, list[BarData]] = {}
        for event in window_events:
            for bar in event.bars:
                if bar.instrument_type == "STOCK":
                    ticker_bars.setdefault(bar.symbol, []).append(bar)

        # Build a single window summary
        window: dict = {
            "window_idx": 1,
            "time": window_events[-1].event_ts[11:16] if window_events else "?",
        }

        for ticker in self._universe:
            bars = ticker_bars.get(ticker, [])
            agg = self._aggregate_window(bars)
            window[f"{ticker}_close"] = agg["close"]
            window[f"{ticker}_volume"] = agg["volume"]
            if agg["open"] and agg["open"] != 0:
                change_pct = ((agg["close"] - agg["open"]) / agg["open"]) * 100
            else:
                change_pct = 0.0
            window[f"{ticker}_change_pct"] = round(change_pct, 2)

        return [window]

    def _build_chain_sample(
        self,
        market_event: MarketEvent,
        current_date: str = "",
    ) -> list[dict]:
        """Extract available option chain info from the latest market event."""
        from trading.agents.strategy._opra_utils import parse_opra_dte, parse_opra_strike

        chain = []
        for bar in market_event.bars:
            if bar.instrument_type == "OPTION":
                dte = parse_opra_dte(bar.symbol, current_date)
                chain.append({
                    "contract": bar.symbol,
                    "ticker": bar.symbol,  # ChainCompressor expects 'ticker'
                    "dte": dte,
                    "strike": parse_opra_strike(bar.symbol),
                    "delta": bar.delta or 0,
                    "iv": bar.iv or 0,
                    "vega": bar.vega or 0,
                    "close": bar.close,
                    "ask": bar.close,  # Approximate with close price
                    "volume": bar.volume,
                    "stale": bar.stale,
                    "stale_minutes": bar.stale_minutes,
                })
        return chain

    def _portfolio_to_dict(self, portfolio: PortfolioSnapshot) -> dict:
        """Convert PortfolioSnapshot to a dict for prompt building."""
        positions = []
        for p in portfolio.positions:
            positions.append({
                "contract": p.contract,
                "side": p.side,
                "qty": p.qty,
                "avg_entry": p.avg_entry,
                "mark_price": p.mark_price,
                "unrealized_pnl": p.unrealized_pnl,
                "dte": p.dte,
                "stale": p.stale,
                "stale_minutes": p.stale_minutes,
            })
        return {
            "cash": portfolio.cash,
            "equity": portfolio.equity,
            "daily_pnl": portfolio.daily_pnl,
            "daily_pnl_pct": portfolio.daily_pnl_pct,
            "positions": positions,
        }

    @staticmethod
    def _compress_chain(
        chain_sample: list[dict],
        market_event: MarketEvent | None,
        state: TradingState,
    ) -> str | None:
        """Run ChainCompressor + QOCREncoder on the chain sample, grouped by underlying."""
        if not chain_sample:
            return None
        from trading.prompts.chain_context import build_multi_ticker_chain_context

        # Get spot prices from stock bars
        spot_map: dict[str, float] = {}
        recent_map: dict[str, list[float]] = {}
        if market_event is not None:
            for bar in market_event.bars:
                if bar.instrument_type == "STOCK":
                    spot_map[bar.symbol] = bar.close
        if state.market_event is not None:
            for sym, history in state.market_event.bar_history.items():
                recent_map[sym] = [b.close for b in history]

        # Group chain by underlying
        chains_by_ticker: dict[str, list[dict]] = {}
        for c in chain_sample:
            contract = c.get("contract", c.get("ticker", ""))
            prefix = []
            raw = contract[2:] if contract.startswith("O:") else contract
            for ch in raw:
                if ch.isalpha():
                    prefix.append(ch)
                else:
                    break
            sym = "".join(prefix)
            chains_by_ticker.setdefault(sym, []).append(c)

        return build_multi_ticker_chain_context(
            chains_by_ticker=chains_by_ticker,
            spot_map=spot_map,
            recent_map=recent_map,
            avsr_config=getattr(state.config, "avsr", None),
        )

    @staticmethod
    def _compute_minutes_to_close(current_ts: str) -> int:
        """Estimate minutes remaining until market close (16:00 ET).

        Properly converts the timestamp to ET before extracting HH:MM,
        since PMB timestamps are UTC.  Returns 0 if past close.
        """
        from trading.agents.strategy._tz_utils import minutes_to_close_et
        return minutes_to_close_et(current_ts)

    def _call_llm(
        self,
        window_events: list[MarketEvent],
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Build prompts, call LLM, parse response, return TradeIntents.

        Retries once on failure with a 2-second sleep. Returns [] on
        persistent failure.

        Each decision (success OR fail) emits ``decision.started`` and
        ``decision.completed`` events and, on success, writes a decision
        artifact + registers a ``decision_records`` row so the web
        Timeline / Inspector can render the run.
        """
        from trading.core.schemas import TradeIntent
        from trading.ingest.ulid import new_ulid
        from trading.core.time_contract import trading_day_from_ts

        if self._llm is None:
            logger.error("LLM client not initialized; returning []")
            return []

        decision_id = new_ulid()
        seq = self._decision_seq
        self._decision_seq = seq + 1
        trading_day = trading_day_from_ts(state.current_ts) if state.current_ts else ""
        self.emit("decision.started", {
            "decision_id": decision_id,
            "trading_day": trading_day,
            "seq": seq,
            "strategy": self.STRATEGY_NAME,
        })

        # Compute flatten time
        close_minutes = 16 * 60
        flatten_offset = self._flatten_minutes_before_close
        flatten_hour = (close_minutes - flatten_offset) // 60
        flatten_min = (close_minutes - flatten_offset) % 60
        flatten_time = f"{flatten_hour:02d}:{flatten_min:02d}"

        system_prompt = build_intraday_system_prompt(
            universe=self._universe,
            flatten_before_close=self._flatten_before_close,
            flatten_time=flatten_time,
            max_positions=self._max_positions,
            interval=self._interval,
            dte_min=self._dte_min,
            dte_max=self._dte_max,
            template=getattr(self, "_prompt_templates", {}).get("system"),
        )
        prompt_addenda = getattr(self, "_prompt_addenda", {})
        if prompt_addenda.get("system"):
            system_prompt += "\n\nAdditional system instructions:\n" + prompt_addenda["system"]

        price_windows = self._build_price_windows(window_events)
        latest_event = window_events[-1] if window_events else None
        from trading.core.time_contract import trading_day_from_ts
        current_date = trading_day_from_ts(state.current_ts) if state.current_ts else ""
        chain_sample = (
            self._build_chain_sample(latest_event, current_date)
            if latest_event else []
        )
        portfolio_dict = self._portfolio_to_dict(portfolio)
        minutes_to_close = self._compute_minutes_to_close(state.current_ts)
        from trading.core.time_contract import et_time_from_ts
        current_time = et_time_from_ts(state.current_ts) if state.current_ts else "?"
        if self.STRATEGY_NAME == "custom_daily_llm":
            current_time = f"{current_date} {current_time} ET" if getattr(self, "_decision_time_et", None) else current_date

        # Compress chain via ChainCompressor
        compressed_chain_text = self._compress_chain(chain_sample, latest_event, state)

        user_prompt = build_intraday_user_prompt(
            current_time=current_time,
            minutes_to_close=minutes_to_close,
            portfolio=portfolio_dict,
            price_windows=price_windows,
            chain_sample=chain_sample,
            tickers=self._universe,
            interval=self._interval,
            dte_min=self._dte_min,
            dte_max=self._dte_max,
            compressed_chain_text=compressed_chain_text,
            template=getattr(self, "_prompt_templates", {}).get("user"),
        )
        data_policy = getattr(self, "_data_policy", None)
        if data_policy is not None and data_policy.news_enabled:
            try:
                from trading.core.endpoints import DEFAULT_ESP_URL
                from trading.tools.npp_context import build_intraday_news_context
                news_context = build_intraday_news_context(
                    npp_base_url=data_policy.npp_base_url or DEFAULT_ESP_URL,
                    current_ts=state.current_ts,
                    universe_tickers=self._universe,
                    lookback_days=data_policy.news_lookback_days,
                )
                user_prompt += "\n\n== Point-in-time news and events ==\n" + news_context
            except Exception as exc:
                logger.warning("Intraday news context failed: %s", exc)
                user_prompt += "\n\n== Point-in-time news and events ==\n(unavailable)"
        custom_context = getattr(self, "_custom_context", "")
        if custom_context:
            user_prompt += (
                "\n\nUSER-SUPPLIED TRADING CONTEXT (preferences only; "
                "system risk and option-only rules still apply):\n"
                f"{custom_context}\n"
                "END USER-SUPPLIED TRADING CONTEXT"
            )
        if prompt_addenda.get("user"):
            user_prompt += "\n\nAdditional user instructions:\n" + prompt_addenda["user"]

        # Call LLM with retry
        actions = None
        for attempt in range(2):
            try:
                actions = self._llm.decide(system_prompt, user_prompt)
                break
            except Exception as exc:
                logger.warning(
                    "LLM call failed (attempt %d/2): %s", attempt + 1, exc
                )
                if attempt == 0:
                    time.sleep(2)

        if actions is None:
            state.errors.append("LLM call failed after 2 attempts")
            self.emit("decision.completed", {
                "decision_id": decision_id,
                "seq": seq,
                "has_trade": False,
                "n_trades": 0,
                "reason": "llm_call_failed",
            })
            return []

        # Convert parsed actions to TradeIntents
        intents = []
        for action in actions:
            contract = action["contract"]
            if not contract.startswith("O:"):
                logger.warning("Rejected non-option contract from LLM: %s", contract)
                continue
            # Extract underlying symbol from OPRA (e.g. "O:SPY250106C..." -> "SPY")
            symbol = ""
            if contract.startswith("O:"):
                stripped = contract[2:]
                sym_chars = []
                for ch in stripped:
                    if ch.isalpha():
                        sym_chars.append(ch)
                    else:
                        break
                symbol = "".join(sym_chars)
            side = "BUY" if action["action"] == "buy" else "SELL"
            intent = TradeIntent(
                symbol=symbol,
                contract=contract,
                side=side,
                qty=int(action["qty"]),
                order_type=action.get("order_type", "MARKET"),
                limit_price=action.get("limit_price"),
                spread_group=action.get("spread_group"),
                rationale=action.get("rationale", ""),
                strategy_name=self.STRATEGY_NAME,
            )
            intents.append(intent)

        # Persist artifact + register decision_records row so web Timeline
        # can render this decision. Non-fatal on errors (write_artifact
        # already swallows internally).
        self.write_artifact(
            decision_id=decision_id,
            seq=seq,
            ts=state.current_ts,
            model_id=getattr(self._llm, "model", self._llm_model),
            provider=getattr(self._llm, "provider", self._llm_provider),
            prompt={"system": system_prompt, "user": user_prompt},
            response={"raw": getattr(self._llm, "_last_raw_response", None), "actions": actions},
            context={
                "current_time": current_time,
                "minutes_to_close": minutes_to_close,
            },
            trades=[
                i.model_dump() if hasattr(i, "model_dump") else dict(i.__dict__)
                for i in intents
            ],
            market_snapshot={
                "n_price_windows": len(price_windows),
                "n_chain_contracts": len(chain_sample),
            },
        )
        self.emit("decision.completed", {
            "decision_id": decision_id,
            "seq": seq,
            "has_trade": bool(intents),
            "n_trades": len(intents),
        })
        return intents

    def save_checkpoint(self) -> dict:
        """Capture LLM-driven intraday state.

        Internal state is intentionally minimal: a rolling bar buffer and a
        step counter. LLM client and universe are reconstructed from config
        on load_checkpoint via on_session_start.
        """
        return {
            "schema_version": "1",
            "bar_buffer": list(getattr(self, "_bar_buffer", []) or []),
            "steps_since_decision": int(getattr(self, "_steps_since_decision", 0) or 0),
        }

    def load_checkpoint(self, state: dict) -> None:
        version = state.get("schema_version")
        if version != "1":
            raise ValueError(
                f"intraday_llm checkpoint schema_version {version!r} not supported (expected '1')"
            )
        self._bar_buffer = list(state.get("bar_buffer", []) or [])
        self._steps_since_decision = int(state.get("steps_since_decision", 0) or 0)

    def project_family_view(self, portfolio, state) -> dict:
        positions = list(getattr(portfolio, "positions", []) or [])
        minutes_to_close = self._compute_minutes_to_close(getattr(state, "current_ts", ""))
        # same_day_open_count: PositionInfo has no open-time field; cannot
        # derive "opened today" from current state. Emit 0 (do not invent).
        same_day_open_count = 0
        gamma_exposure = 0.0
        for p in positions:
            g = getattr(p, "gamma", None)
            q = getattr(p, "qty", 0)
            if g is not None and q is not None:
                gamma_exposure += float(g) * float(q)
        intraday_realized = float(getattr(portfolio, "realized_pnl", 0.0) or 0.0)
        return {
            "kind": "intraday",
            "minutes_to_close": int(minutes_to_close or 0),
            "same_day_open_count": same_day_open_count,
            "gamma_exposure": gamma_exposure,
            "intraday_realized_today": intraday_realized,
        }


@register_strategy("custom_daily_llm")
class CustomDailyLLMStrategy(IntradayLLMStrategy):
    """One options decision per daily bar, with the same audit and order protocol."""

    REQUIRED_FREQUENCIES = frozenset({"1d", "1m"})
    STRATEGY_NAME = "custom_daily_llm"

    def __init__(self, decision_time_et: str | None = None, **kwargs):
        from trading.prompts.custom import custom_prompts
        kwargs.pop("decision_interval_minutes", None)
        kwargs.setdefault("flatten_before_close", False)
        kwargs["prompt_templates"] = {**custom_prompts("daily"), **(kwargs.get("prompt_templates") or {})}
        super().__init__(decision_interval_minutes=1, **kwargs)
        if decision_time_et is not None:
            import re
            if not re.fullmatch(r"(?:09:[3-5][0-9]|1[0-5]:[0-5][0-9])", decision_time_et):
                raise ValueError("decision_time_et must be 09:30–15:59 ET")
        self._decision_time_et = decision_time_et
        self._last_decision_day: str | None = None

    def on_session_start(self, config):
        if self._decision_time_et and config.experiment.frequency != "1m":
            raise ValueError("Scheduled daily decisions require minute replay")
        super().on_session_start(config)

    def on_step(self, market_event, portfolio, state):
        if self._decision_time_et is None:
            return super().on_step(market_event, portfolio, state)
        self._bar_buffer.append(market_event)
        if state.current_ts is None:
            return []
        day = trading_day_from_ts(state.current_ts)
        if et_time_from_ts(state.current_ts) != self._decision_time_et or day == self._last_decision_day:
            return []
        # Exact minute only: do not silently move a missed decision to a later bar.
        self._last_decision_day = day
        return self._call_llm(self._bar_buffer, portfolio, state)

    def save_checkpoint(self):
        return {**super().save_checkpoint(), "last_decision_day": self._last_decision_day}

    def load_checkpoint(self, state):
        super().load_checkpoint(state)
        self._last_decision_day = state.get("last_decision_day")

    def _build_price_windows(self, window_events):
        if not window_events:
            return []
        if self._decision_time_et:
            return super()._build_price_windows(window_events)
        event = window_events[-1]
        history = event.bar_history
        by_date: dict[str, dict] = {}
        for symbol in self._universe:
            for bar in history.get(symbol, []):
                day = bar.window_start[:10]
                row = by_date.setdefault(day, {"time": day})
                row.update({f"{symbol}_close": bar.close, f"{symbol}_volume": bar.volume,
                            f"{symbol}_change_pct": round((bar.close / bar.open - 1) * 100, 2) if bar.open else 0})
        if not by_date:
            return super()._build_price_windows(window_events)
        return [{**row, "window_idx": index + 1} for index, (_, row) in enumerate(sorted(by_date.items()))]

    def project_family_view(self, portfolio, state):
        return None
