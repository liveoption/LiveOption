"""Overlay-specific prompt templates for LLM-driven overlay strategies.

Two variants:
  - Profit Increase (covered call + cash-secured put)
  - Hedging (protective put / put spread)

Provides:
  - build_overlay_system_prompt(mode, underlying, stock_qty)
  - build_overlay_user_prompt(...)
  - parse_overlay_response(raw)
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PROFIT_SYSTEM_TEMPLATE = """\
You are an options overlay trading agent. Your objective is to enhance \
portfolio income via covered calls AND cash-secured puts on a stock position.

Rules:
- You manage a portfolio of {stock_qty:,} shares of {underlying}
- You may SELL covered calls (short call) to collect premium
- You may SELL cash-secured puts (short put) to collect premium
- Each option contract covers 100 shares
- CRITICAL: Call strikes MUST be ABOVE the current stock price (OTM only).
  Example: if stock is at $520, sell calls with strike >= $530. NEVER sell calls with strike <= current price — they will be exercised and you will lose shares.
- CRITICAL: Put strikes MUST be BELOW the current stock price (OTM only).
  Example: if stock is at $520, sell puts with strike <= $510.
- CRITICAL: Total SELL call contracts must NOT exceed shares / 100.
  If you hold {stock_qty:,} shares, you can sell at most {max_call_contracts} call contracts TOTAL (across all expiries).
  Selling more would create naked calls which have unlimited loss risk.
- Cash-secured put requires: strike * 100 * contracts <= available cash
- Maximum 1 active call + 1 active put position at a time
- Prefer 30-45 DTE for time decay; avoid <7 DTE
- Roll or close positions when <=5 DTE to avoid assignment risk
- Take profits when premium decays by 50%+
- If you already have active short call/put positions, BUY them back before opening new ones

Decision framework:
1. WHEN to sell: consider IV rank, recent price action, upcoming earnings
2. WHICH strike/expiry: balance premium income vs. assignment probability
3. HOLD vs ROLL: if existing position has >50% profit, consider closing early
4. POSITION MANAGEMENT: Always check active positions before opening new ones

Your response MUST be a valid JSON array. Each element is one order:
[
  {{
    "action": "buy" | "sell",
    "contract": "<OPRA contract e.g. O:AAPL250620C00200000>",
    "qty": <positive integer (contracts)>,
    "order_type": "MARKET" | "LIMIT",
    "limit_price": <float, required if LIMIT, omit if MARKET>,
    "rationale": "<brief explanation>"
  }}
]

If no trade is warranted, return an empty array: []
Do not include any text outside the JSON array.
"""

HEDGE_SYSTEM_TEMPLATE = """\
You are a risk management agent. Your objective is to protect a stock \
portfolio from downside risk using put options while controlling hedging cost.

Rules:
- You manage a portfolio of {stock_qty:,} shares of {underlying}
- You may BUY protective puts to hedge downside
- You may BUY a put spread (buy near-OTM put + sell far-OTM put) to reduce cost
- Each put contract covers 100 shares
- Only buy puts with strikes below the current price (OTM)
- Maximum 1 active hedge position at a time
- Prefer 30-60 DTE for protection duration
- Budget: keep hedging cost below ~2% of portfolio equity per month

Decision framework:
1. WHEN to hedge: consider VIX level, recent drawdown, upcoming earnings/events
2. WHICH strike/expiry: balance protection level vs. premium cost
3. HOW MUCH to spend: more protection when risk is elevated

Your response MUST be a valid JSON array. Each element is one order:
[
  {{
    "action": "buy" | "sell",
    "contract": "<OPRA contract e.g. O:AAPL250620P00180000>",
    "qty": <positive integer (contracts)>,
    "order_type": "MARKET" | "LIMIT",
    "limit_price": <float, required if LIMIT, omit if MARKET>,
    "spread_group": "<optional letter A-Z, links legs of a spread>",
    "rationale": "<brief explanation>"
  }}
]

If no trade is warranted, return an empty array: []
Do not include any text outside the JSON array.
"""

OVERLAY_USER_TEMPLATE = """\
Date: {date}

== Account State ==
Cash: ${cash:,.2f}
Equity: ${equity:,.2f}
Stock Position: {stock_qty:,} shares of {underlying} @ ${price:.2f}

== Active Option Positions ==
{positions_str}

== Available Option Chain ==
{chain_str}

== Recent News (last 7 days) ==
{news_str}

== Upcoming Earnings (next 14 days) ==
{earnings_str}

Based on the above, provide your trading decision as a JSON array.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_overlay_system_prompt(
    mode: str,
    underlying: str,
    stock_qty: int,
) -> str:
    """Build the system prompt for an overlay strategy.

    Args:
        mode: ``"profit"`` for covered call + cash-secured put,
              ``"hedge"`` for protective put / put spread.
        underlying: Underlying ticker symbol (e.g. ``"AAPL"``).
        stock_qty: Number of shares held.
    """
    if mode == "profit":
        return PROFIT_SYSTEM_TEMPLATE.format(
            underlying=underlying,
            stock_qty=stock_qty,
            max_call_contracts=max(stock_qty // 100, 0),
        )
    elif mode == "hedge":
        return HEDGE_SYSTEM_TEMPLATE.format(
            underlying=underlying,
            stock_qty=stock_qty,
        )
    else:
        raise ValueError(f"Unknown overlay mode: {mode!r}")


def build_overlay_user_prompt(
    date: str,
    underlying: str,
    price: float,
    cash: float,
    equity: float,
    stock_qty: int,
    active_positions: list[dict],
    chain_summary: list[dict],
    news: list[dict],
    earnings: list[dict],
    compressed_chain_text: str | None = None,
) -> str:
    """Build the user prompt with full market context.

    Args:
        date: Trade date (``YYYY-MM-DD``).
        underlying: Underlying ticker.
        price: Current underlying price.
        cash: Available cash.
        equity: Portfolio equity.
        stock_qty: Number of shares held.
        active_positions: List of active option position dicts.
        chain_summary: Raw chain contract dicts from UPQ.
        news: Recent news article dicts.
        earnings: Upcoming earnings dicts.
        compressed_chain_text: Pre-built compressed chain text (AVSR+QOCR).
    """
    # Positions table
    if active_positions:
        pos_lines = []
        for p in active_positions:
            contract = p.get("contract", p.get("instrument_id", "?"))
            qty = p.get("qty", 0)
            side = "LONG" if qty > 0 else "SHORT"
            mark = p.get("mark_price", 0)
            unrealized = p.get("unrealized_pnl", 0)
            pos_lines.append(
                f"  {contract} {side} qty={abs(qty)} "
                f"mark=${mark:.2f} pnl=${unrealized:+.2f}"
            )
        positions_str = "\n".join(pos_lines)
    else:
        positions_str = "  None"

    # Chain
    if compressed_chain_text:
        chain_str = compressed_chain_text
    else:
        chain_lines = []
        for c in chain_summary[:15]:
            iv_str = ""
            iv_val = c.get("iv")
            if iv_val is not None:
                iv_str = f" IV={iv_val:.2%}"
            chain_lines.append(
                f"  {c.get('ticker', '?')} strike=${c.get('strike', 0):.2f} "
                f"expiry={c.get('expiry', 'N/A')} "
                f"DTE={c.get('dte', '?')} "
                f"premium=${c.get('close', 0):.2f}{iv_str}"
            )
        chain_str = "\n".join(chain_lines) if chain_lines else "  (no chain data)"

    # News
    news_str = (
        "\n".join(
            f"  [{a.get('published', '')[:10]}] {a.get('title', '')}"
            for a in news[:15]
        )
        or "  (no news)"
    )

    # Earnings
    earnings_str = (
        "\n".join(
            f"  {e.get('ticker', '?')}: {e.get('date', '?')} {e.get('time', '')} "
            f"est_eps={e.get('estimated_eps', '?')}"
            for e in earnings[:10]
        )
        or "  (none upcoming)"
    )

    return OVERLAY_USER_TEMPLATE.format(
        date=date,
        underlying=underlying,
        price=price,
        cash=cash,
        equity=equity,
        stock_qty=stock_qty,
        positions_str=positions_str,
        chain_str=chain_str,
        news_str=news_str,
        earnings_str=earnings_str,
    )


def parse_overlay_response(raw: str) -> list[dict]:
    """Parse LLM response into validated action dicts.

    Delegates to ``trading.prompts.weekly.parse_llm_response`` which handles
    JSON extraction, field validation, and spread-group checks.
    """
    from trading.prompts.weekly import parse_llm_response

    return parse_llm_response(raw)
