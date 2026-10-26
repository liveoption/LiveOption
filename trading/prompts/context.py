"""Shared context builders for prompt rendering.

Extracts real data from MarketEvent, PortfolioSnapshot, and TradingState
into template variables for all strategies.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading.core.schemas import MarketEvent, PortfolioSnapshot


# ---------------------------------------------------------------------------
# Portfolio context
# ---------------------------------------------------------------------------

def _infer_spread_groups(positions: list) -> dict[str, str]:
    """Infer spread group labels for option position pairs.

    Matches LONG+SHORT legs that share the same underlying, expiry, right (C/P),
    and quantity — typical of a spread opened together. Returns a mapping from
    contract → spread_group label ("A", "B", ...).
    """
    import re

    def parse_opra(contract: str) -> dict | None:
        m = re.match(r"O:([A-Z]+)(\d{6})([CP])(\d{8})", contract)
        if not m:
            return None
        return {
            "underlying": m.group(1),
            "expiry": m.group(2),
            "right": m.group(3),
            "strike": int(m.group(4)),
        }

    opt_positions = [p for p in positions if p.contract.startswith("O:")]
    label_map: dict[str, str] = {}
    used: set[int] = set()
    label_idx = 0

    for i, p1 in enumerate(opt_positions):
        if i in used:
            continue
        info1 = parse_opra(p1.contract)
        if not info1:
            continue
        for j, p2 in enumerate(opt_positions):
            if j <= i or j in used:
                continue
            info2 = parse_opra(p2.contract)
            if not info2:
                continue
            # Match: same underlying/expiry/right, opposite sides, same qty
            if (info1["underlying"] == info2["underlying"]
                    and info1["expiry"] == info2["expiry"]
                    and info1["right"] == info2["right"]
                    and p1.side != p2.side
                    and p1.qty == p2.qty):
                label = chr(ord("A") + label_idx)
                label_map[p1.contract] = label
                label_map[p2.contract] = label
                used.add(i)
                used.add(j)
                label_idx += 1
                break

    return label_map


def build_positions_table(portfolio: PortfolioSnapshot | None) -> str:
    """Build a markdown table of current positions with Greeks.

    Includes:
    - ToClose column: the action needed to close this position (sell for LONG, buy for SHORT)
    - SpreadGrp column: inferred spread group label when two legs are paired
    """
    if portfolio is None or not portfolio.positions:
        return "No open positions."

    spread_groups = _infer_spread_groups(portfolio.positions)

    header = "| Contract | Side | Qty | Entry | Mark | P&L | DTE | Delta | IV | ToClose | SpreadGrp |"
    sep =    "|----------|------|-----|-------|------|-----|-----|-------|----|---------|-----------|"
    rows = [header, sep]

    for p in portfolio.positions:
        pnl_str   = f"${p.unrealized_pnl:+,.2f}"
        delta_str = f"{p.delta:.2f}" if p.delta is not None else "N/A"
        iv_str    = f"{p.iv:.1%}" if p.iv is not None else "N/A"
        # Close direction: LONG → sell to close; SHORT → buy to close
        to_close  = "sell" if p.side == "LONG" else "buy"
        sg_label  = spread_groups.get(p.contract, "—")
        rows.append(
            f"| {p.contract} | {p.side} | {p.qty} | "
            f"${p.avg_entry:.2f} | ${p.mark_price:.2f} | {pnl_str} | "
            f"{p.dte} | {delta_str} | {iv_str} | {to_close} | {sg_label} |"
        )

    # Add spread group summary if any pairs found
    if spread_groups:
        paired = {}
        for contract, label in spread_groups.items():
            paired.setdefault(label, []).append(contract)
        notes = ["", "Spread pairs (close both legs together):"]
        for label, contracts in sorted(paired.items()):
            notes.append(f"  Group {label}: {' + '.join(contracts)}")
        rows.extend(notes)

    return "\n".join(rows)


def get_underlying_price(
    market_event: MarketEvent | None, underlying: str
) -> float:
    """Extract the latest price for an underlying from market bars."""
    if market_event is None:
        return 0.0
    for bar in market_event.bars:
        if bar.instrument_type == "STOCK" and bar.symbol == underlying:
            return bar.close
    return 0.0


def get_active_option_contracts(portfolio: PortfolioSnapshot | None) -> str:
    """List active option positions as a comma-separated string."""
    if portfolio is None or not portfolio.positions:
        return "None"
    options = [
        f"{p.contract} ({p.side} {p.qty})"
        for p in portfolio.positions
        if p.contract.startswith("O:")
    ]
    return ", ".join(options) if options else "None"


def has_option_bars(market_event: MarketEvent | None, underlying: str | None = None) -> bool:
    """Return True if market_event contains any tradeable option bars."""
    if market_event is None:
        return False
    for bar in market_event.bars:
        if bar.instrument_type == "OPTION" and bar.tradeable:
            if underlying is None or bar.symbol.startswith(f"O:{underlying}"):
                return True
    return False


# ---------------------------------------------------------------------------
# Chain context
# ---------------------------------------------------------------------------

def build_chain_table(
    market_event: MarketEvent | None,
    underlying: str | None = None,
    max_rows: int = 20,
) -> str:
    """Build a formatted table of available option contracts from market bars."""
    if market_event is None:
        return "(no chain data available)"

    option_bars = [
        bar for bar in market_event.bars
        if bar.instrument_type == "OPTION"
        and bar.tradeable
        and (underlying is None or bar.symbol.startswith(f"O:{underlying}"))
    ]

    if not option_bars:
        return "(no option contracts loaded)"

    option_bars.sort(key=lambda b: b.volume, reverse=True)
    option_bars = option_bars[:max_rows]

    lines = ["| Contract | Price | Volume | IV | Delta | Gamma | Theta | Data |",
             "|----------|-------|--------|----|-------|-------|-------|------|"]
    for bar in option_bars:
        iv_str = f"{bar.iv:.1%}" if bar.iv is not None else "N/A"
        delta_str = f"{bar.delta:.2f}" if bar.delta is not None else "N/A"
        gamma_str = f"{bar.gamma:.4f}" if bar.gamma is not None else "N/A"
        theta_str = f"{bar.theta:.4f}" if bar.theta is not None else "N/A"
        data_tag = f"[STALE {bar.stale_minutes}min]" if bar.stale else "[LIVE]"
        lines.append(
            f"| {bar.symbol} | ${bar.close:.2f} | {_fmt_volume(bar.volume)} | "
            f"{iv_str} | {delta_str} | {gamma_str} | {theta_str} | {data_tag} |"
        )
    return "\n".join(lines)


def build_chain_summary(
    market_event: MarketEvent | None,
    underlying: str | None = None,
    max_rows: int = 10,
) -> str:
    """Build a compact chain summary (for overlay strategies)."""
    if market_event is None:
        return "(no chain data available)"

    option_bars = [
        bar for bar in market_event.bars
        if bar.instrument_type == "OPTION"
        and bar.tradeable
        and (underlying is None or bar.symbol.startswith(f"O:{underlying}"))
    ]

    if not option_bars:
        return "(no option contracts loaded)"

    option_bars.sort(key=lambda b: b.volume, reverse=True)
    lines = []
    for bar in option_bars[:max_rows]:
        iv_str = f" iv={bar.iv:.2f}" if bar.iv is not None else ""
        delta_str = f" delta={bar.delta:.2f}" if bar.delta is not None else ""
        lines.append(
            f"  {bar.symbol} price=${bar.close:.2f} vol={_fmt_volume(bar.volume)}{iv_str}{delta_str}"
        )
    return "\n".join(lines) if lines else "(no contracts available)"


# ---------------------------------------------------------------------------
# Price windows (intraday)
# ---------------------------------------------------------------------------

def build_price_windows(
    market_event: MarketEvent | None,
    tickers: list[str] | None = None,
    n_bars: int = 5,
) -> str:
    """Build a recent price summary from bar history."""
    if market_event is None:
        return "(no price data)"

    tickers = tickers or []
    lines = []
    for ticker in tickers:
        history = market_event.bar_history.get(ticker, [])
        recent = history[-n_bars:] if history else []
        if not recent:
            continue
        for bar in recent:
            ts = bar.window_start[11:16] if len(bar.window_start) > 16 else bar.window_start
            lines.append(f"  {ticker} {ts} close=${bar.close:.2f} vol={_fmt_volume(bar.volume)}")
    return "\n".join(lines) if lines else "(no price data)"


# ---------------------------------------------------------------------------
# Time context
# ---------------------------------------------------------------------------

def compute_minutes_to_close(current_ts) -> int:
    """Calculate minutes remaining until 16:00 ET.

    Accepts a tz-aware ``datetime`` or an ISO string.
    """
    try:
        from trading.agents.strategy._tz_utils import minutes_to_close_et
        return minutes_to_close_et(current_ts)
    except (ValueError, TypeError):
        return 0


def build_time_context(current_ts, frequency: str = "1d") -> str:
    """Build time context string for analyzer prompt.

    Accepts a tz-aware ``datetime`` (preferred) or an ISO ``str`` for legacy
    callers.  For daily frequency: shows 15:55 ET / 5 min to close (daily bars
    = EOD snapshot).  For intraday: shows actual timestamp and minutes to
    close.
    """
    from trading.core.time_contract import et_time_from_ts, trading_day_from_ts, utc_from_iso

    if isinstance(current_ts, datetime):
        dt = current_ts
    else:
        dt = utc_from_iso(str(current_ts))
    current_date = trading_day_from_ts(dt)
    ts_display = et_time_from_ts(dt)

    if frequency in ("1d", "daily"):
        return (
            f"Date: {current_date} | Frequency: daily rebalance\n"
            f"This is the end-of-day rebalancing decision. You have the full daily bar data.\n"
            f"Orders will execute at next open. Focus on overnight/multi-day thesis, not intraday timing."
        )

    # Intraday
    mtc = compute_minutes_to_close(current_ts)
    return f"Date: {current_date} | Time: {ts_display} ET | Minutes to close: {mtc}"


# ---------------------------------------------------------------------------
# Price trend + TA metrics
# ---------------------------------------------------------------------------

def build_price_trend(
    market_event: MarketEvent | None,
    tickers: list[str] | None = None,
    n_bars: int = 30,
) -> str:
    """Build recent price trend with TA metrics from bar history.

    Computes MA5, MA15, MA30, daily change %, and trend direction
    from the bar_history in market_event.
    """
    if market_event is None:
        return "(no price history)"

    tickers = tickers or []
    if not tickers:
        # Auto-detect stock tickers from bars
        tickers = list({
            bar.symbol for bar in market_event.bars
            if bar.instrument_type == "STOCK"
        })

    if not tickers:
        return "(no stock data)"

    sections = []
    for ticker in tickers:
        history = market_event.bar_history.get(ticker, [])
        if not history:
            continue

        closes = [bar.close for bar in history[-n_bars:]]
        if len(closes) < 2:
            continue

        current = closes[-1]
        prev = closes[-2]
        daily_chg = ((current - prev) / prev * 100) if prev != 0 else 0.0

        # Moving averages
        ma5 = _moving_average(closes, 5)
        ma15 = _moving_average(closes, 15)
        ma30 = _moving_average(closes, 30)

        # Trend detection
        trend = _detect_trend_from_mas(current, ma5, ma15, ma30)

        # Recent range
        recent_20 = closes[-20:] if len(closes) >= 20 else closes
        high_20 = max(recent_20)
        low_20 = min(recent_20)
        range_pct = ((high_20 - low_20) / low_20 * 100) if low_20 != 0 else 0.0

        lines = [
            f"{ticker}: ${current:.2f} ({daily_chg:+.2f}% daily)",
            f"  MA5=${ma5:.2f}  MA15=${ma15:.2f}  MA30=${ma30:.2f}" if ma30 else f"  MA5=${ma5:.2f}  MA15=${ma15:.2f}",
            f"  Trend: {trend}",
            f"  20-bar range: ${low_20:.2f} - ${high_20:.2f} ({range_pct:.1f}%)",
        ]

        # Price series (last 10 bars for context)
        recent_10 = closes[-10:]
        price_str = ", ".join(f"${p:.2f}" for p in recent_10)
        lines.append(f"  Recent closes: [{price_str}]")

        sections.append("\n".join(lines))

    return "\n".join(sections) if sections else "(no price history)"


def _moving_average(prices: list[float], period: int) -> float:
    """Compute simple moving average over the last N prices."""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    return sum(prices[-period:]) / period


def _detect_trend_from_mas(
    price: float, ma5: float, ma15: float, ma30: float,
) -> str:
    """Detect trend from price vs moving averages."""
    if ma30 == 0:
        if price > ma5 > ma15:
            return "bullish (price > MA5 > MA15)"
        elif price < ma5 < ma15:
            return "bearish (price < MA5 < MA15)"
        return "mixed"

    if price > ma5 > ma15 > ma30:
        return "strong bullish (price > MA5 > MA15 > MA30)"
    elif price < ma5 < ma15 < ma30:
        return "strong bearish (price < MA5 < MA15 < MA30)"
    elif price > ma5 and price > ma15:
        return "bullish (price above short-term MAs)"
    elif price < ma5 and price < ma15:
        return "bearish (price below short-term MAs)"
    elif ma5 > ma15:
        return "mildly bullish (MA5 > MA15, price mixed)"
    elif ma5 < ma15:
        return "mildly bearish (MA5 < MA15, price mixed)"
    return "range-bound (MAs converging)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_volume(volume: int) -> str:
    if volume >= 1_000_000:
        return f"{volume / 1_000_000:.1f}M"
    if volume >= 1_000:
        return f"{volume / 1_000:.0f}K"
    return str(volume)
