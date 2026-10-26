"""WeeklyLLMStrategy — migrated from free_trading/agent_loop.py.

Calls an LLM once per rebalance day (weekly or daily) to generate option
trade intents. On non-rebalance days, on_step() returns an empty list.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from trading.agents.strategy.base import BaseStrategy
from trading.core.endpoints import DEFAULT_UPQ_URL
from trading.core.registry import register_strategy
from trading.core.schemas import TradeIntent

if TYPE_CHECKING:
    from trading.core.config import RunConfig
    from trading.core.schemas import MarketEvent, PortfolioSnapshot
    from trading.core.state import TradingState

logger = logging.getLogger(__name__)

_CHECKPOINT_SCHEMA_VERSION = 1


def get_rebalance_dates(
    start_date: str,
    end_date: str,
    frequency: str = "weekly",
    weekday: int = 0,
) -> list[str]:
    """Return rebalance dates in [start, end].

    Args:
        frequency: "weekly" returns only the chosen *weekday*; "daily" returns
                   every weekday (Mon-Fri).
        weekday:   Only used when frequency="weekly" (0=Monday).
    """
    start = date.fromisoformat(start_date[:10])
    end = date.fromisoformat(end_date[:10])
    result, d = [], start
    while d <= end:
        if frequency == "daily":
            if d.weekday() < 5:  # Mon-Fri
                result.append(d.isoformat())
        else:
            if d.weekday() == weekday:
                result.append(d.isoformat())
        d += timedelta(days=1)
    return result


@register_strategy("weekly_llm")
class WeeklyLLMStrategy(BaseStrategy):
    """Existing free_trading logic: call LLM once per rebalance day.

    Steps between rebalance days produce no intents.
    """

    REQUIRED_FREQUENCIES = frozenset({"1d"})

    def __init__(
        self,
        rebalance_frequency: str = "weekly",
        rebalance_weekday: int = 0,
        llm_provider: str = "deepseek",
        llm_model: str = "deepseek-chat",
        dte_min: int = 7,
        dte_max: int = 60,
        news_lookback_days: int = 7,
        upq_base_url: str = DEFAULT_UPQ_URL,
        context_host: str = "local",
        **kwargs,
    ):
        self.rebalance_frequency = rebalance_frequency
        self.rebalance_weekday = rebalance_weekday
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.dte_min = dte_min
        self.dte_max = dte_max
        self.news_lookback_days = news_lookback_days
        self.upq_base_url = upq_base_url
        self.context_host = context_host
        self._extra_kwargs = kwargs

        # Initialized in on_session_start
        self._rebalance_dates: set[str] = set()
        self._last_decision_date: str | None = None
        self._llm = None
        self._context_loader = None
        self._universe: list[str] = []
        self._system_prompt: str = ""
        self._decision_seq: int = 0

    def on_session_start(self, config: RunConfig) -> None:
        """Compute rebalance dates and initialize LLM client."""
        from trading.prompts.weekly import build_system_prompt
        from trading.tools.context_loader import ContextLoader
        from trading.tools.llm_client import LLMClient

        self._rebalance_dates = set(
            get_rebalance_dates(
                start_date=config.experiment.start,
                end_date=config.experiment.end,
                frequency=self.rebalance_frequency,
                weekday=self.rebalance_weekday,
            )
        )
        self._llm = LLMClient(provider=self.llm_provider, model=self.llm_model)
        self._context_loader = ContextLoader(ssh_host=self.context_host)
        self._universe = config.experiment.universe
        self._system_prompt = build_system_prompt(self._universe)

    def on_step(
        self,
        market_event: MarketEvent,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> list[TradeIntent]:
        """Return trade intents on rebalance days, empty list otherwise."""
        from trading.core.time_contract import trading_day_from_ts
        from trading.ingest.ulid import new_ulid
        current_date = trading_day_from_ts(state.current_ts)

        if current_date not in self._rebalance_dates:
            self.emit("decision.skipped", {
                "trading_day": current_date,
                "reason": "not_rebalance_day",
            })
            return []

        if current_date == self._last_decision_date:
            self.emit("decision.skipped", {
                "trading_day": current_date,
                "reason": "already_decided_today",
            })
            return []  # already decided today

        self._last_decision_date = current_date
        decision_id = new_ulid()
        seq = self._decision_seq
        self._decision_seq = seq + 1

        self.emit("decision.started", {
            "decision_id": decision_id,
            "trading_day": current_date,
            "seq": seq,
        })

        try:
            intents, prompt_dict, response_dict, context_dict, market_dict = (
                self._call_llm(current_date, portfolio, state)
            )
        except Exception as e:
            logger.error(f"LLM call failed on {current_date}: {e}")
            self.emit("decision.completed", {
                "decision_id": decision_id,
                "seq": seq,
                "has_trade": False,
                "n_trades": 0,
                "error": str(e),
            })
            return []

        self.write_artifact(
            decision_id=decision_id, seq=seq, ts=state.current_ts,
            model_id=getattr(self._llm, "model", self.llm_model),
            provider=getattr(self._llm, "provider", self.llm_provider),
            prompt=prompt_dict, response=response_dict,
            context=context_dict,
            trades=[i.model_dump() if hasattr(i, "model_dump") else dict(i.__dict__) for i in intents],
            market_snapshot=market_dict,
        )
        self.emit("decision.completed", {
            "decision_id": decision_id,
            "seq": seq,
            "has_trade": bool(intents),
            "n_trades": len(intents),
        })
        return intents

    def _call_llm(
        self,
        trade_date: str,
        portfolio: PortfolioSnapshot,
        state: TradingState,
    ) -> tuple[list[TradeIntent], dict, dict, dict, dict]:
        """Query chains, load context, call LLM, parse, convert to TradeIntents.

        Returns (intents, prompt_dict, response_dict, context_dict, market_snapshot).
        """
        from trading.prompts.chain_context import build_multi_ticker_chain_context
        from trading.prompts.weekly import build_user_prompt
        from trading.tools.upq_tools import query_option_chain

        # Query option chain sample (calls + puts, first 4 tickers)
        chain_summary: list[dict] = []
        for ticker in self._universe[:4]:
            for right in ("C", "P"):
                try:
                    contracts = query_option_chain(
                        upq_base_url=self.upq_base_url,
                        underlying=ticker,
                        trade_date=trade_date,
                        right=right,
                        dte_min=self.dte_min,
                        dte_max=self.dte_max,
                    )
                    chain_summary.extend(contracts[:3])
                except Exception as e:
                    logger.warning(f"chain query failed {ticker} {right}: {e}")

        # Group chains by underlying for compressed context
        spot_map = self._get_spot_prices(state)
        chains_by_ticker: dict[str, list[dict]] = {}
        for c in chain_summary:
            sym = c.get("underlying", "")
            if not sym:
                ticker_str = c.get("ticker", c.get("contract", ""))
                if ticker_str.startswith("O:"):
                    prefix = []
                    for ch in ticker_str[2:]:
                        if ch.isalpha():
                            prefix.append(ch)
                        else:
                            break
                    sym = "".join(prefix)
            chains_by_ticker.setdefault(sym, []).append(c)

        recent_map = {
            sym: self._get_recent_prices(sym, state) for sym in chains_by_ticker
        }

        # Build compressed chain context (AVSR + QOCR)
        compressed_chain_text = build_multi_ticker_chain_context(
            chains_by_ticker=chains_by_ticker,
            spot_map=spot_map,
            recent_map=recent_map,
        )

        # Load news and earnings context
        news = self._context_loader.load_news_window(
            end_date=trade_date,
            lookback_days=self.news_lookback_days,
            tickers=self._universe,
        )
        earnings = self._context_loader.load_upcoming_earnings(
            from_date=trade_date,
            to_date=(date.fromisoformat(trade_date) + timedelta(days=14)).isoformat(),
            tickers=self._universe,
        )

        # Build account dict from portfolio snapshot
        account = self._portfolio_to_account(portfolio)

        user_prompt = build_user_prompt(
            date=trade_date,
            account=account,
            chain_summary=chain_summary,
            news_snippets=news[:15],
            earnings_upcoming=earnings[:10],
            compressed_chain_text=compressed_chain_text,
        )

        actions = self._llm.decide(
            system_prompt=self._system_prompt,
            user_prompt=user_prompt,
        )

        intents = self._actions_to_intents(actions)
        prompt_dict = {"system": self._system_prompt, "user": user_prompt}
        response_dict = {"actions": actions}
        context_dict = {
            "account": account,
            "universe": list(self._universe),
            "trade_date": trade_date,
            "news_count": len(news),
            "earnings_count": len(earnings),
        }
        market_dict = {
            "chain_summary": chain_summary,
            "spot_map": spot_map,
            "compressed_chain_text": compressed_chain_text,
        }
        return intents, prompt_dict, response_dict, context_dict, market_dict

    def _portfolio_to_account(self, portfolio: PortfolioSnapshot) -> dict:
        """Convert PortfolioSnapshot to the dict format expected by prompt builder."""
        positions = []
        for pos in portfolio.positions:
            positions.append({
                "instrument_id": pos.contract,
                "qty": pos.qty,
                "avg_cost": pos.avg_entry,
                "mark_price": pos.mark_price,
                "unrealized_pnl": pos.unrealized_pnl,
            })
        return {
            "cash_available": portfolio.cash,
            "equity": portfolio.equity,
            "positions": positions,
        }

    @staticmethod
    def _actions_to_intents(actions: list[dict]) -> list[TradeIntent]:
        """Convert LLM response action dicts to TradeIntent objects."""
        intents = []
        for a in actions:
            side = "BUY" if a["action"] == "buy" else "SELL"
            contract = a["contract"]
            # Extract underlying symbol from OPRA contract (e.g. "O:SPY250106C..." -> "SPY")
            symbol = ""
            if contract.startswith("O:"):
                # Strip "O:" prefix, then take alphabetic prefix as symbol
                stripped = contract[2:]
                sym_chars = []
                for ch in stripped:
                    if ch.isalpha():
                        sym_chars.append(ch)
                    else:
                        break
                symbol = "".join(sym_chars)

            intents.append(
                TradeIntent(
                    symbol=symbol,
                    contract=contract,
                    side=side,
                    qty=int(a["qty"]),
                    order_type=a.get("order_type", "MARKET"),
                    limit_price=a.get("limit_price"),
                    spread_group=a.get("spread_group"),
                    rationale=a.get("rationale", ""),
                    strategy_name="weekly_llm",
                )
            )
        return intents

    @staticmethod
    def _get_spot_prices(state: TradingState) -> dict[str, float]:
        """Extract spot prices for underlying tickers from market event bars."""
        spot: dict[str, float] = {}
        if state.market_event is None:
            return spot
        for bar in state.market_event.bars:
            if bar.instrument_type == "STOCK":
                spot[bar.symbol] = bar.close
        return spot

    @staticmethod
    def _get_recent_prices(symbol: str, state: TradingState) -> list[float]:
        """Get recent close prices for a symbol from bar history."""
        if state.market_event is None:
            return []
        history = state.market_event.bar_history.get(symbol, [])
        return [bar.close for bar in history]

    def save_checkpoint(self) -> dict:
        """Serialize cross-step state for resume.

        Persistent fields:
          - _rebalance_dates: set of YYYY-MM-DD strings (serialized as sorted list)
          - _last_decision_date: YYYY-MM-DD str or None

        Transient (not persisted): _llm client, _context_loader, _universe,
        _system_prompt — all re-derivable in on_session_start.
        """
        return {
            "_version": _CHECKPOINT_SCHEMA_VERSION,
            "rebalance_dates": sorted(self._rebalance_dates),
            "last_decision_date": self._last_decision_date,
        }

    def load_checkpoint(self, state: dict) -> None:
        version = state.get("_version")
        if version != _CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"WeeklyLLMStrategy checkpoint schema version mismatch: "
                f"got {version}, expected {_CHECKPOINT_SCHEMA_VERSION}"
            )
        self._rebalance_dates = set(state.get("rebalance_dates", []))
        self._last_decision_date = state.get("last_decision_date")
