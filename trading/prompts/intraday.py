"""Intraday option trading prompt builder and response parser.

Builds system and user prompts for the IntradayLLMStrategy, and parses
LLM JSON responses into validated action dicts.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

REQUIRED_ACTION_FIELDS = {"action", "contract", "qty"}
VALID_ACTIONS = {"buy", "sell"}
VALID_ORDER_TYPES = {"MARKET", "LIMIT"}

INTRADAY_SYSTEM_TEMPLATE = """You are an intraday options trading agent managing a portfolio.
You trade OPTIONS ONLY on: {universe}.

You make decisions every {interval} minutes during market hours (09:30-16:00 ET).
{flatten_clause}

## Available Actions
- BUY: Open or add to a long option position
- SELL: Close or reduce an option position
- HOLD: Take no action this interval

## Strategy Guidance
- Focus on liquid options within the configured {dte_min}-{dte_max} DTE range
- ATM/near-the-money (delta 0.30-0.50) preferred for intraday moves
- Consider IV and theta decay within the day
- Use spread_group for multi-leg strategies
- Instruments marked [STALE Xmin] have not traded recently - trade with caution

## Risk Rules
- Max {max_positions} open positions
- Close losers at -50% unrealized P&L
- Take profits at +50% unrealized P&L

## Output Format
JSON array:
[
  {{
    "action": "buy" | "sell",
    "instrument_type": "option",
    "contract": "<OPRA>",
    "qty": <int>,
    "order_type": "MARKET" | "LIMIT",
    "limit_price": <optional>,
    "spread_group": "<optional A-Z>",
    "rationale": "<brief>"
  }}
]
Return [] for HOLD.
Do not include any text outside the JSON array.
"""

INTRADAY_USER_TEMPLATE = """Time: {current_time} ET | Minutes to close: {minutes_to_close}

== Account State ==
Cash: ${cash:,.2f}  Equity: ${equity:,.2f}  Daily P&L: ${daily_pnl:,.2f} ({daily_pnl_pct:.2f}%)

== Current Positions ==
{positions_table}

== Recent Price Action ({interval}min windows) ==
{price_windows_table}

== Available Options (filtered: DTE {dte_min}-{dte_max}, delta 0.20-0.60) ==
{chain_sample_table}

Based on the above, provide your trading decisions as a JSON array.
"""


def build_intraday_system_prompt(
    universe: list[str],
    flatten_before_close: bool = True,
    flatten_time: str = "15:55",
    max_positions: int = 5,
    interval: int = 15,
    dte_min: int = 0,
    dte_max: int = 7,
    template: str | None = None,
) -> str:
    """Build the system prompt for the intraday LLM strategy.

    Args:
        universe: List of underlying tickers (e.g. ["SPY", "QQQ"]).
        flatten_before_close: Whether to flatten positions before close.
        flatten_time: Time to flatten by (e.g. "15:55").
        max_positions: Maximum number of open positions allowed.
        interval: Decision interval in minutes.

    Returns:
        Formatted system prompt string.
    """
    if flatten_before_close:
        flatten_clause = (
            f"You MUST close all positions before {flatten_time} ET. "
            "No overnight holdings."
        )
    else:
        flatten_clause = ""

    from trading.prompts.loader import PromptLoader
    return PromptLoader.render(template if template is not None else INTRADAY_SYSTEM_TEMPLATE,
        universe=", ".join(universe),
        interval=interval,
        flatten_clause=flatten_clause,
        max_positions=max_positions,
        dte_min=dte_min,
        dte_max=dte_max,
    )


def _build_positions_table(positions: list[dict]) -> str:
    """Build a markdown table of current positions with staleness tags.

    Each position dict is expected to have:
      contract, side, qty, avg_entry, mark_price, unrealized_pnl, dte,
      stale (bool), stale_minutes (int).
    """
    if not positions:
        return "No open positions."

    header = "| Contract | Side | Qty | Entry | Mark | Unrealized P&L | DTE | Data |"
    separator = "|----------|------|-----|-------|------|----------------|-----|------|"
    rows = [header, separator]

    for p in positions:
        contract = p.get("contract", "?")
        side = p.get("side", "?")
        qty = p.get("qty", 0)
        avg_entry = p.get("avg_entry", 0)
        mark = p.get("mark_price", 0)
        unrealized = p.get("unrealized_pnl", 0)
        dte = p.get("dte", "?")
        stale = p.get("stale", False)
        stale_minutes = p.get("stale_minutes", 0)

        if stale and stale_minutes > 0:
            data_tag = f"[STALE {stale_minutes}min]"
        else:
            data_tag = "[LIVE]"

        pnl_str = f"+${unrealized:,.2f}" if unrealized >= 0 else f"-${abs(unrealized):,.2f}"

        rows.append(
            f"| {contract} | {side} | {qty} | "
            f"${avg_entry:,.2f} | ${mark:,.2f} | "
            f"{pnl_str} | {dte} | {data_tag} |"
        )

    return "\n".join(rows)


def _build_price_windows_table(
    price_windows: list[dict],
    tickers: list[str],
    interval: int = 15,
) -> str:
    """Build a table of aggregated N-minute OHLCV windows.

    Each window dict has: window_idx, time, and per-ticker data:
      {ticker}_close, {ticker}_change_pct, {ticker}_volume.
    """
    if not price_windows:
        return "(no price data)"

    # Build header dynamically based on tickers
    ticker_headers = " | ".join(
        f"{t} Close | {t} Chg% | {t} Vol" for t in tickers
    )
    header = f"Window | Time | {ticker_headers}"
    rows = [header]

    for w in price_windows:
        idx = w.get("window_idx", "?")
        time_str = w.get("time", "?")
        parts = [f"{idx}", f"{time_str}"]
        for t in tickers:
            close_val = w.get(f"{t}_close", "?")
            chg_val = w.get(f"{t}_change_pct", "?")
            vol_val = w.get(f"{t}_volume", "?")
            if isinstance(close_val, (int, float)):
                close_str = f"{close_val:.2f}"
            else:
                close_str = str(close_val)
            if isinstance(chg_val, (int, float)):
                chg_str = f"{chg_val:+.2f}%"
            else:
                chg_str = str(chg_val)
            if isinstance(vol_val, (int, float)):
                vol_str = _format_volume(vol_val)
            else:
                vol_str = str(vol_val)
            parts.extend([close_str, chg_str, vol_str])
        rows.append(" | ".join(parts))

    return "\n".join(rows)


def _format_volume(volume: int | float) -> str:
    """Format volume with K/M suffixes."""
    if volume >= 1_000_000:
        return f"{volume / 1_000_000:.1f}M"
    if volume >= 1_000:
        return f"{volume / 1_000:.0f}K"
    return str(int(volume))


def _build_chain_sample_table(chain_sample: list[dict]) -> str:
    """Build available options list with staleness tags.

    Each chain entry: contract, dte, delta, iv, ask, stale, stale_minutes.
    """
    if not chain_sample:
        return "(no chain data)"

    lines = []
    for c in chain_sample:
        contract = c.get("contract", "?")
        dte = c.get("dte", "?")
        delta = c.get("delta", "?")
        iv = c.get("iv", 0)
        ask = c.get("ask", "?")
        stale = c.get("stale", False)
        stale_minutes = c.get("stale_minutes", 0)

        if stale and stale_minutes > 0:
            data_tag = f"[STALE {stale_minutes}min]"
        else:
            data_tag = "[LIVE]"

        delta_str = f"{delta:.2f}" if isinstance(delta, (int, float)) else str(delta)
        iv_str = f"{iv:.1%}" if isinstance(iv, (int, float)) else str(iv)
        ask_str = f"${ask:.2f}" if isinstance(ask, (int, float)) else str(ask)

        lines.append(
            f"{contract} DTE={dte} delta={delta_str} IV={iv_str} ask={ask_str} {data_tag}"
        )

    return "\n".join(lines)


def build_intraday_user_prompt(
    current_time: str,
    minutes_to_close: int,
    portfolio: dict,
    price_windows: list[dict],
    chain_sample: list[dict],
    tickers: list[str] | None = None,
    interval: int = 15,
    dte_min: int = 0,
    dte_max: int = 7,
    compressed_chain_text: str | None = None,
    template: str | None = None,
) -> str:
    """Build the user prompt for an intraday decision point.

    Args:
        current_time: Current market time (e.g. "10:30").
        minutes_to_close: Minutes until market close.
        portfolio: Dict with cash, equity, daily_pnl, daily_pnl_pct, positions.
        price_windows: List of aggregated price window dicts.
        chain_sample: List of available option chain entries.
        tickers: List of underlying tickers for price table headers.
        interval: Decision interval in minutes.
        dte_min: Minimum DTE filter for display.
        dte_max: Maximum DTE filter for display.
        compressed_chain_text: Pre-formatted chain summary from ChainCompressor.

    Returns:
        Formatted user prompt string.
    """
    tickers = tickers or []
    positions = portfolio.get("positions", [])
    positions_table = _build_positions_table(positions)
    price_windows_table = _build_price_windows_table(
        price_windows, tickers, interval
    )
    if compressed_chain_text:
        chain_sample_table = compressed_chain_text
    else:
        chain_sample_table = _build_chain_sample_table(chain_sample)

    from trading.prompts.loader import PromptLoader
    return PromptLoader.render(template if template is not None else INTRADAY_USER_TEMPLATE,
        current_time=current_time,
        minutes_to_close=minutes_to_close,
        cash=portfolio.get("cash", 0),
        equity=portfolio.get("equity", 0),
        daily_pnl=portfolio.get("daily_pnl", 0),
        daily_pnl_pct=portfolio.get("daily_pnl_pct", 0),
        interval=interval,
        positions_table=positions_table,
        price_windows_table=price_windows_table,
        dte_min=dte_min,
        dte_max=dte_max,
        chain_sample_table=chain_sample_table,
    )


def parse_intraday_response(raw: str) -> list[dict]:
    """Extract and validate JSON array from LLM response.

    Reuses the same logic as free_trading/prompt_builder.py's
    parse_llm_response: strip code fences, find JSON array via regex,
    validate required fields.

    Args:
        raw: Raw LLM response text.

    Returns:
        List of validated action dicts. Empty list on parse failure.
    """
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Find JSON array
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return []

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("LLM response is not valid JSON")
        return []

    if not isinstance(items, list):
        return []

    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        err = _validate_action(item)
        if err:
            logger.warning(f"dropping invalid LLM action: {err} -- {item}")
        else:
            valid.append(item)

    return valid


def _validate_action(action: dict) -> str | None:
    """Validate a single LLM action dict. Returns error string or None."""
    missing = REQUIRED_ACTION_FIELDS - set(action.keys())
    if missing:
        return f"missing required fields: {missing}"
    if action["action"] not in VALID_ACTIONS:
        return f"invalid action '{action['action']}'"
    if not isinstance(action["qty"], (int, float)) or action["qty"] <= 0:
        return f"qty must be positive, got {action['qty']}"
    order_type = action.get("order_type", "MARKET")
    if order_type not in VALID_ORDER_TYPES:
        return f"invalid order_type '{order_type}'"
    if order_type == "LIMIT" and action.get("limit_price") is None:
        return "LIMIT order requires limit_price"
    return None
