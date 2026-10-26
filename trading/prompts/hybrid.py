"""Prompt templates and response parsers for HybridIntradayStrategy.

Provides two focused prompts:
- Direction prompt: decide market direction and contract selection
- Stop prompt: decide whether to stop trading for the day

Both prompts are intentionally SHORT to minimize LLM latency and token cost.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Direction prompt
# ---------------------------------------------------------------------------

DIRECTION_SYSTEM = (
    "You are an intraday option direction agent. "
    "Your ONLY job: decide market direction for the next 15-30 minutes."
)

DIRECTION_USER_TEMPLATE = """Current: SPY ${price:.2f}, 15min change {change:+.2f}%
Available:
{options_block}

Respond with ONE JSON object:
{{"direction": "bullish"|"bearish"|"neutral", "contract": "<OPRA with O: prefix>", "qty": 1-5, "rationale": "..."}}
Return {{"direction": "neutral"}} to skip."""


def build_direction_prompt(
    spy_price: float,
    change_pct: float,
    available_options: list[dict],
) -> tuple[str, str]:
    """Build system + user prompts for the direction decision.

    Parameters
    ----------
    spy_price : float
        Current SPY price.
    change_pct : float
        SPY 15-minute percentage change.
    available_options : list[dict]
        Top options with keys: contract, price, type (call/put).

    Returns
    -------
    tuple[str, str]
        (system_prompt, user_prompt)
    """
    lines = []
    for opt in available_options[:4]:
        contract = opt.get("contract", "?")
        price = opt.get("price", 0)
        opt_type = opt.get("type", "?")
        lines.append(f"  {contract} ({opt_type}) ${price:.2f}")
    options_block = "\n".join(lines) if lines else "  (no options available)"

    user_prompt = DIRECTION_USER_TEMPLATE.format(
        price=spy_price,
        change=change_pct,
        options_block=options_block,
    )
    return DIRECTION_SYSTEM, user_prompt


# ---------------------------------------------------------------------------
# Stop prompt
# ---------------------------------------------------------------------------

STOP_SYSTEM = (
    "You are a session exit advisor. "
    "Decide whether we should stop trading for today."
)

STOP_USER_TEMPLATE = """Realized P&L: ${realized:+.2f}, {n_trades} trades, {n_wins} wins
Time remaining: {minutes}min
Target: ${target:.0f}, Limit: -${limit:.0f}

Respond: {{"stop": true|false, "rationale": "..."}}"""


def build_stop_prompt(
    realized: float,
    n_trades: int,
    n_wins: int,
    minutes_left: int,
    target: float,
    limit: float,
) -> tuple[str, str]:
    """Build system + user prompts for the stop-trading decision.

    Returns
    -------
    tuple[str, str]
        (system_prompt, user_prompt)
    """
    user_prompt = STOP_USER_TEMPLATE.format(
        realized=realized,
        n_trades=n_trades,
        n_wins=n_wins,
        minutes=minutes_left,
        target=target,
        limit=limit,
    )
    return STOP_SYSTEM, user_prompt


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def parse_direction_response(raw: str) -> dict:
    """Parse LLM direction response into a dict.

    Expected JSON: {"direction": "bullish"|"bearish"|"neutral",
                    "contract": "<OPRA>", "qty": int, "rationale": "..."}

    Returns a dict with at least ``"direction"`` key. On parse failure
    returns ``{"direction": "neutral"}``.
    """
    try:
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.warning("No JSON object found in direction response")
            return {"direction": "neutral"}

        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            return {"direction": "neutral"}

        direction = data.get("direction", "neutral")
        if direction not in ("bullish", "bearish", "neutral"):
            logger.warning("Invalid direction '%s', defaulting to neutral", direction)
            return {"direction": "neutral"}

        return data

    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Failed to parse direction response: %s", exc)
        return {"direction": "neutral"}


def parse_stop_response(raw: str) -> dict:
    """Parse LLM stop-trading response into a dict.

    Expected JSON: {"stop": true|false, "rationale": "..."}

    Returns a dict with at least ``"stop"`` key. On parse failure
    returns ``{"stop": false, "rationale": "parse_error"}``.
    """
    try:
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.warning("No JSON object found in stop response")
            return {"stop": False, "rationale": "parse_error"}

        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            return {"stop": False, "rationale": "parse_error"}

        # Ensure "stop" is a bool
        stop_val = data.get("stop", False)
        if not isinstance(stop_val, bool):
            stop_val = str(stop_val).lower() in ("true", "1", "yes")
        data["stop"] = stop_val

        return data

    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Failed to parse stop response: %s", exc)
        return {"stop": False, "rationale": "parse_error"}
