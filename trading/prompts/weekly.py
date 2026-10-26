"""Weekly prompt templates for the WeeklyLLMStrategy.

Migrated from free_trading/prompt_builder.py with no functional changes.
Provides system/user prompt builders and LLM response parsing.
"""

import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime

logger = logging.getLogger(__name__)

REQUIRED_ACTION_FIELDS = {"action", "contract", "qty"}
VALID_ACTIONS = {"buy", "sell"}
VALID_ORDER_TYPES = {"MARKET", "LIMIT"}
VALID_INSTRUMENT_TYPES = {"option"}

SYSTEM_TEMPLATE = """You are an autonomous options trading agent managing a $100,000 portfolio.
You trade OPTIONS ONLY — no stock positions allowed.

Tradable universe (ONLY these underlyings): {universe_str}

## Strategy Guidance

Available strategies (in order of preference):
1. **Vertical spreads** (bull call spread, bear put spread): defined risk, lower capital.
   Use spread_group to submit both legs atomically.
2. **Single-leg directional**: buy calls for bullish, buy puts for bearish.
   Prefer ATM or slightly OTM (delta 0.30-0.50) for capital efficiency.
3. **Premium collection**: sell covered calls (requires LONG stock position in the underlying).
   Naked short options (uncovered puts or calls without a defined-risk spread) are NOT permitted.
   Short options require margin — be aware of your buying power.
4. **Straddles / strangles**: for high-conviction volatility plays around earnings.

Key principles:
- Deep-ITM calls (delta~1.0) waste capital with no leverage advantage. AVOID them.
- Far OTM options (delta<0.10) usually expire worthless. Use sparingly.
- ATM or near-the-money options (delta 0.30-0.50) provide the best risk/reward.
- Prefer 30-45 DTE for time value; avoid <7 DTE unless closing.
- SELL premium only via covered calls or defined-risk spreads (spread_group). Naked short options are rejected by the risk gate.
- Use spread_group to link legs of multi-leg strategies for atomic execution.

## Position Management Rules

- ALWAYS review existing positions before opening new ones.
- Close or roll positions with <=5 DTE to avoid expiration losses.
- Take profits when unrealized P&L exceeds +50% of premium paid.
- Cut losses when unrealized P&L drops below -50% of premium paid.
- Don't over-concentrate: limit to 3-5 open positions at a time.

## Order Format

All trades must be options. Use OPRA format: O:<UNDERLYING><YY><MM><DD><C|P><STRIKE_8DIGITS>
  Example: O:AAPL250620C00200000 = AAPL call expiring 2025-06-20 at $200 strike
Short positions require margin; margin is enforced by the broker.

Your response MUST be a valid JSON array. Each element is one order:
[
  {{
    "action": "buy" | "sell",
    "instrument_type": "option",
    "contract": "<OPRA contract>",
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

USER_TEMPLATE = """Date: {date}

== Account State ==
Cash: ${cash:,.2f}
Equity: ${equity:,.2f}

## Current Positions
{positions_table}

== Available Option Chain Sample (calls and puts, DTE 7-60) ==
{chain_str}

== Recent News (last 7 days, universe-relevant) ==
{news_str}

== Upcoming Earnings (next 14 days) ==
{earnings_str}

Based on the above, provide your trading decision as a JSON array.
"""


def parse_opra_expiry(contract: str) -> date | None:
    """Extract expiry date from an OPRA contract name.

    Format: O:<UNDERLYING><YYMMDD><C|P><STRIKE_8DIGITS>
    The date portion is the 6 digits immediately before the C/P character.
    """
    m = re.search(r"O:\w+?(\d{6})[CP]\d{8}$", contract)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%y%m%d").date()
    except ValueError:
        return None


def _build_positions_table(positions: list[dict], trade_date: str) -> str:
    """Build a markdown table of current positions with DTE column."""
    if not positions:
        return "No open positions."

    try:
        ref_date = date.fromisoformat(trade_date)
    except (ValueError, TypeError):
        ref_date = date.today()

    header = "| Contract | Side | Qty | Avg Entry | Mark Price | Unrealized P&L | DTE |"
    separator = "|----------|------|-----|-----------|------------|----------------|-----|"
    rows = [header, separator]

    for p in positions:
        contract = p.get("instrument_id", p.get("contract", "?"))
        qty = p.get("qty", 0)
        side = "LONG" if qty > 0 else "SHORT"
        abs_qty = abs(qty)
        avg_cost = p.get("avg_cost", 0)
        mark = p.get("mark_price", p.get("market_value", 0))
        unrealized = p.get("unrealized_pnl", 0)

        # Compute DTE from OPRA contract name
        expiry = parse_opra_expiry(contract)
        if expiry is not None:
            dte = max((expiry - ref_date).days, 0)
            dte_str = str(dte)
        else:
            dte_str = "N/A"

        rows.append(
            f"| {contract} | {side} | {abs_qty} | "
            f"${avg_cost:,.2f} | ${mark:,.2f} | "
            f"${unrealized:,.2f} | {dte_str} |"
        )
    return "\n".join(rows)


def build_system_prompt(universe: list[str]) -> str:
    return SYSTEM_TEMPLATE.format(universe_str=", ".join(universe))


def build_user_prompt(
    date: str,
    account: dict,
    chain_summary: list[dict],
    news_snippets: list[dict],
    earnings_upcoming: list[dict],
    compressed_chain_text: str | None = None,
) -> str:
    positions = account.get("positions", [])
    positions_table = _build_positions_table(positions, date)
    if compressed_chain_text:
        chain_str = compressed_chain_text
    else:
        chain_str = (
            "\n".join(
                f"  {c.get('underlying','?')} {c.get('right','?')} strike={c.get('strike','?')} "
                f"exp={c.get('expiry','?')} DTE={c.get('dte','?')} "
                f"IV={c.get('iv', 0):.1%} delta={c.get('delta', '?')} contract={c.get('contract','?')}"
                for c in chain_summary[:20]
            )
            or "  (no chain data)"
        )
    news_str = (
        "\n".join(
            f"  [{a.get('published','')[:10]}] {a.get('title','')}"
            for a in news_snippets[:15]
        )
        or "  (no news)"
    )
    earnings_str = (
        "\n".join(
            f"  {e.get('ticker','?')}: {e.get('date','?')} {e.get('time','')} "
            f"est_eps={e.get('estimated_eps','?')}"
            for e in earnings_upcoming[:10]
        )
        or "  (none upcoming)"
    )
    return USER_TEMPLATE.format(
        date=date,
        cash=account.get("cash", account.get("cash_available", 0)),
        equity=account.get("equity", 0),
        positions_table=positions_table,
        chain_str=chain_str,
        news_str=news_str,
        earnings_str=earnings_str,
    )


def validate_action(action: dict) -> str | None:
    """Validate a single LLM action dict. Returns error string or None if valid."""
    missing = REQUIRED_ACTION_FIELDS - set(action.keys())
    if missing:
        return f"missing required fields: {missing}"
    if action["action"] not in VALID_ACTIONS:
        return f"invalid action '{action['action']}', must be one of {VALID_ACTIONS}"
    if not isinstance(action["qty"], (int, float)) or action["qty"] <= 0:
        return f"qty must be a positive number, got {action['qty']}"
    instrument_type = action.get("instrument_type", "option")
    if instrument_type not in VALID_INSTRUMENT_TYPES:
        return f"invalid instrument_type '{instrument_type}', must be one of {VALID_INSTRUMENT_TYPES}"
    order_type = action.get("order_type", "MARKET")
    if order_type not in VALID_ORDER_TYPES:
        return f"invalid order_type '{order_type}', must be one of {VALID_ORDER_TYPES}"
    if order_type == "LIMIT" and action.get("limit_price") is None:
        return "LIMIT order requires limit_price"
    return None


def validate_spread_groups(actions: list[dict]) -> list[dict]:
    """Validate spread_group constraints and return filtered list.

    Rules:
    - spread_group is optional; if absent, the order is single-leg.
    - All orders in the same group must have the same qty.
    - Each group should have exactly 2 legs.
    - Both legs must be options (instrument_type == "option" or absent, defaulting to option).
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    singles: list[dict] = []

    for action in actions:
        sg = action.get("spread_group")
        if sg:
            groups[sg].append(action)
        else:
            singles.append(action)

    valid = list(singles)
    for group_key, legs in groups.items():
        # Must have exactly 2 legs
        if len(legs) != 2:
            logger.warning(
                f"spread_group '{group_key}' has {len(legs)} legs (expected 2), "
                f"dropping group"
            )
            continue

        # Both legs must be options
        types = [leg.get("instrument_type", "option") for leg in legs]
        if any(t != "option" for t in types):
            logger.warning(
                f"spread_group '{group_key}' contains non-option legs, dropping group"
            )
            continue

        # All legs must have same qty
        qtys = {leg["qty"] for leg in legs}
        if len(qtys) != 1:
            logger.warning(
                f"spread_group '{group_key}' has mismatched qty {qtys}, dropping group"
            )
            continue

        valid.extend(legs)

    return valid


def parse_llm_response(raw: str) -> list[dict]:
    """Extract and validate JSON array from LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("LLM response is not valid JSON")
        return []
    valid = []
    for item in items:
        err = validate_action(item)
        if err:
            logger.warning(f"dropping invalid LLM action: {err} — {item}")
        else:
            valid.append(item)
    return validate_spread_groups(valid)
