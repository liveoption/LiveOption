"""Prompts for the legacy multi-agent strategy.

Three distinct prompt templates form the decision pipeline:

1. **Analysis Agent** — market assessment + high-level intent
2. **Trader Agent**   — converts intent into concrete option orders
3. **Reflection Agent** — validates orders before submission
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 1: Analysis Agent
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = """\
You are a market analysis agent. Assess the current market state and provide \
a high-level trading intention.

Respond with a single JSON object (no extra text):
{"intent": "bullish"|"bearish"|"neutral"|"exit_all", "confidence": 0.0-1.0, \
"reasoning": "...", "target_instruments": ["O:SPY..."]}
"""

ANALYSIS_USER_TEMPLATE = """\
Current state:

== Option Chain Summary ==
{chain_summary}

== Portfolio ==
{portfolio_summary}

== Time Context ==
{time_context}

Based on the above, provide your market assessment as a JSON object.
"""


def build_analysis_prompt(
    chain_summary: str,
    portfolio_summary: str,
    time_context: str,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the Analysis Agent."""
    user = ANALYSIS_USER_TEMPLATE.format(
        chain_summary=chain_summary,
        portfolio_summary=portfolio_summary,
        time_context=time_context,
    )
    return ANALYSIS_SYSTEM_PROMPT, user


def parse_analysis_response(raw: str) -> dict:
    """Parse the Analysis Agent's JSON response.

    Returns a dict with keys: intent, confidence, reasoning, target_instruments.
    On failure, returns a neutral intent.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Try to find a JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        logger.warning("Analysis response contains no JSON object")
        return _neutral_analysis()

    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("Analysis response is not valid JSON")
        return _neutral_analysis()

    if not isinstance(result, dict):
        return _neutral_analysis()

    # Validate required fields
    intent = result.get("intent", "neutral")
    if intent not in ("bullish", "bearish", "neutral", "exit_all"):
        logger.warning("Invalid intent '%s', defaulting to neutral", intent)
        intent = "neutral"

    confidence = result.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))

    return {
        "intent": intent,
        "confidence": confidence,
        "reasoning": result.get("reasoning", ""),
        "target_instruments": result.get("target_instruments", []),
    }


def _neutral_analysis() -> dict:
    return {
        "intent": "neutral",
        "confidence": 0.0,
        "reasoning": "parse failure",
        "target_instruments": [],
    }


# ---------------------------------------------------------------------------
# Phase 2: Trader Agent
# ---------------------------------------------------------------------------

TRADER_SYSTEM_PROMPT = """\
You are a trade execution planner. Given an analysis, construct specific \
option orders.

Respond with a JSON array of orders (no extra text):
[{"action": "buy"|"sell", "contract": "O:...", "qty": 1, \
"order_type": "MARKET", "rationale": "..."}]

Return [] if no trades are warranted.
"""

TRADER_USER_TEMPLATE = """\
Analysis: {analysis_json}

Available contracts:
{tradable_contracts}

Portfolio positions:
{positions}

Risk budget: max ${max_cost} per trade, max {max_contracts} contracts

Construct your order plan as a JSON array.
"""


def build_trader_prompt(
    analysis: dict,
    tradable_contracts: str,
    positions: str,
    max_cost: float = 5000.0,
    max_contracts: int = 5,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the Trader Agent."""
    user = TRADER_USER_TEMPLATE.format(
        analysis_json=json.dumps(analysis),
        tradable_contracts=tradable_contracts,
        positions=positions,
        max_cost=max_cost,
        max_contracts=max_contracts,
    )
    return TRADER_SYSTEM_PROMPT, user


def parse_trader_response(raw: str) -> list[dict]:
    """Parse the Trader Agent's JSON array response.

    Returns a list of validated order dicts.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return []

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("Trader response is not valid JSON")
        return []

    if not isinstance(items, list):
        return []

    valid: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Minimal validation
        action = item.get("action")
        contract = item.get("contract", "")
        qty = item.get("qty", 0)
        if action not in ("buy", "sell"):
            continue
        if not contract:
            continue
        if not isinstance(qty, (int, float)) or qty <= 0:
            continue
        valid.append(item)

    return valid


# ---------------------------------------------------------------------------
# Phase 3: Reflection Agent
# ---------------------------------------------------------------------------

REFLECTION_SYSTEM_PROMPT = """\
You are an order validation agent. Review proposed orders for errors and \
risk violations.

Respond with a single JSON object (no extra text):
{"valid_orders": [...], "rejected": [...], "notes": "..."}

Keep valid_orders in the same format as the input orders.
"""

REFLECTION_USER_TEMPLATE = """\
Proposed orders:
{orders_json}

Current portfolio positions:
{positions}

Available cash: ${cash}

Check for:
- Orders on non-existent or illiquid contracts
- Position sizing exceeding risk limits
- Contradictory orders (buying and selling same contract)
- Orders that don't align with the stated intent

Provide your validation as a JSON object.
"""


def build_reflection_prompt(
    orders: list[dict],
    positions: str,
    cash: float,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the Reflection Agent."""
    user = REFLECTION_USER_TEMPLATE.format(
        orders_json=json.dumps(orders, indent=2),
        positions=positions,
        cash=cash,
    )
    return REFLECTION_SYSTEM_PROMPT, user


def parse_reflection_response(raw: str) -> dict:
    """Parse the Reflection Agent's JSON response.

    Returns a dict with keys: valid_orders, rejected, notes.
    On failure, returns all orders as valid (pass-through).
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        logger.warning("Reflection response contains no JSON object")
        return {"valid_orders": [], "rejected": [], "notes": "parse failure"}

    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("Reflection response is not valid JSON")
        return {"valid_orders": [], "rejected": [], "notes": "parse failure"}

    if not isinstance(result, dict):
        return {"valid_orders": [], "rejected": [], "notes": "parse failure"}

    return {
        "valid_orders": result.get("valid_orders", []),
        "rejected": result.get("rejected", []),
        "notes": result.get("notes", ""),
    }
