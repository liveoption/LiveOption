"""MarketDataAgent — bar extraction, staleness tracking, and forward-fill.

Extracts stock and option bars from PMB step results, tracks per-instrument
staleness for sparse option minute bars, forward-fills missing bars with a
stale flag, and maintains a ring buffer of recent bars per instrument.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from trading.core.config import DataPolicyConfig
from trading.core.schemas import BarData, MarketEvent


@dataclass
class InstrumentState:
    """Per-instrument tracking state for staleness and forward-fill."""

    last_bar: BarData | None = None
    last_bar_ts: str = ""
    stale: bool = False
    stale_minutes: int = 0
    last_known_iv: float | None = None


class MarketDataAgent:
    """Extract bars from PMB step results and track staleness.

    Responsibilities:
    - Parse MARKET_TICK events from PMB step results into BarData objects.
    - Track per-option-contract staleness (missing bars get forward-filled).
    - Maintain a ring buffer of recent bars per instrument for strategy use.

    Args:
        data_policy: Configuration controlling staleness thresholds and buffer sizes.
    """

    def __init__(self, data_policy: DataPolicyConfig | None = None) -> None:
        if data_policy is None:
            data_policy = DataPolicyConfig()
        self._max_stale = data_policy.max_stale_minutes
        self._lookback_bars = data_policy.lookback_bars
        self._instrument_state: dict[str, InstrumentState] = {}
        self._bar_history: dict[str, deque[BarData]] = {}
        self._tracked_contracts: set[str] = set()

    def register_contracts(self, contracts: set[str]) -> None:
        """Start tracking a set of option contracts for staleness.

        Any contract registered here will produce forward-filled bars when
        no real bar arrives in a PMB step.

        Args:
            contracts: Set of OPRA-format contract identifiers (e.g. "O:SPY250106C00595000").
        """
        for contract in contracts:
            if contract not in self._tracked_contracts:
                self._tracked_contracts.add(contract)
                self._instrument_state.setdefault(contract, InstrumentState())
                if contract not in self._bar_history:
                    self._bar_history[contract] = deque(maxlen=self._lookback_bars)

    def unregister_contracts(self, contracts: set[str]) -> None:
        """Stop tracking these contracts (e.g., after expiry or day change)."""
        for contract in contracts:
            self._tracked_contracts.discard(contract)
            self._instrument_state.pop(contract, None)
            self._bar_history.pop(contract, None)

    def process_step(self, step_result: dict) -> MarketEvent:
        """Process a PMB step result and produce a MarketEvent.

        Extracts stock and option bars from MARKET_TICK events, applies
        staleness tracking for tracked option contracts, and maintains
        the per-instrument bar history ring buffer.

        Args:
            step_result: Raw PMB step result dict with "clock" and "events" keys.

        Returns:
            A MarketEvent containing all bars (real + forward-filled) and
            the current bar history snapshot.
        """
        current_ts = step_result["clock"]["current_ts"]
        stock_bars = self._extract_stock_bars(step_result)
        option_bars_by_contract = self._extract_option_bars(step_result)

        enriched_bars: list[BarData] = []

        # Stock bars: always fresh, never stale-tracked
        for bar_data in stock_bars:
            bar = BarData(
                symbol=bar_data["symbol"],
                open=bar_data["open"],
                high=bar_data["high"],
                low=bar_data["low"],
                close=bar_data["close"],
                volume=bar_data["volume"],
                window_start=current_ts,
                instrument_type="STOCK",
                stale=False,
                stale_minutes=0,
                tradeable=True,
            )
            enriched_bars.append(bar)
            self._update_history(bar_data["symbol"], bar)

        # Option bars: check freshness for each tracked contract
        # First, handle contracts that DO have bars in this step
        seen_contracts: set[str] = set()
        for contract, bar_data in option_bars_by_contract.items():
            seen_contracts.add(contract)
            bar = BarData(
                symbol=contract,
                open=bar_data["open"],
                high=bar_data["high"],
                low=bar_data["low"],
                close=bar_data["close"],
                volume=bar_data["volume"],
                window_start=current_ts,
                instrument_type="OPTION",
                stale=False,
                stale_minutes=0,
                tradeable=True,
                iv=bar_data.get("iv"),
                delta=bar_data.get("delta"),
                gamma=bar_data.get("gamma"),
                theta=bar_data.get("theta"),
                vega=bar_data.get("vega"),
            )
            enriched_bars.append(bar)

            # Update instrument state — fresh bar resets staleness
            state = self._instrument_state.get(contract)
            if state is None:
                state = InstrumentState()
                self._instrument_state[contract] = state
            state.last_bar = bar
            state.last_bar_ts = current_ts
            state.stale = False
            state.stale_minutes = 0
            if bar.iv is not None:
                state.last_known_iv = bar.iv

            # Also register for tracking if not already
            self._tracked_contracts.add(contract)
            self._update_history(contract, bar)

        # Second, handle tracked contracts that are MISSING a bar this step
        for contract in self._tracked_contracts:
            if contract in seen_contracts:
                continue
            state = self._instrument_state.get(contract)
            if state is None or state.last_bar is None:
                continue  # never seen a bar for this contract yet

            # Forward-fill with stale flag
            state.stale_minutes += 1
            state.stale = True
            tradeable = state.stale_minutes <= self._max_stale

            forward_filled = BarData(
                symbol=state.last_bar.symbol,
                open=state.last_bar.close,
                high=state.last_bar.close,
                low=state.last_bar.close,
                close=state.last_bar.close,
                volume=0,
                window_start=current_ts,
                instrument_type="OPTION",
                stale=True,
                stale_minutes=state.stale_minutes,
                tradeable=tradeable,
                iv=state.last_bar.iv,
                delta=state.last_bar.delta,
                gamma=state.last_bar.gamma,
                theta=state.last_bar.theta,
                vega=state.last_bar.vega,
            )
            enriched_bars.append(forward_filled)
            self._update_history(contract, forward_filled)

        # Build bar history snapshot (convert deques to lists)
        bar_history_snapshot: dict[str, list[BarData]] = {
            sym: list(buf) for sym, buf in self._bar_history.items()
        }

        return MarketEvent(
            event_ts=current_ts,
            bars=enriched_bars,
            bar_history=bar_history_snapshot,
        )

    def get_history(self, symbol: str, n: int | None = None) -> list[BarData]:
        """Get recent bar history for an instrument.

        Args:
            symbol: Instrument identifier (ticker or OPRA contract).
            n: Number of most recent bars to return. Defaults to all available.

        Returns:
            List of BarData objects, oldest first.
        """
        buf = self._bar_history.get(symbol)
        if buf is None:
            return []
        bars = list(buf)
        if n is not None:
            bars = bars[-n:]
        return bars

    # -- Private helpers --

    def _extract_stock_bars(self, step_result: dict) -> list[dict]:
        """Extract stock bar dicts from MARKET_TICK events."""
        bars = []
        for event in step_result.get("events", []):
            if event.get("type") == "MARKET_TICK":
                payload = event.get("payload", {})
                for stock in payload.get("stocks", []):
                    bars.append(stock)
        return bars

    def _extract_option_bars(self, step_result: dict) -> dict[str, dict]:
        """Extract option bar dicts from MARKET_TICK events, keyed by contract."""
        bars: dict[str, dict] = {}
        for event in step_result.get("events", []):
            if event.get("type") == "MARKET_TICK":
                payload = event.get("payload", {})
                for option in payload.get("options", []):
                    contract = option["contract"]
                    bars[contract] = option
        return bars

    def _update_history(self, symbol: str, bar: BarData) -> None:
        """Append a bar to the ring buffer for the given instrument."""
        if symbol not in self._bar_history:
            self._bar_history[symbol] = deque(maxlen=self._lookback_bars)
        self._bar_history[symbol].append(bar)
