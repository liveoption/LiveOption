"""Shared LLM response parsers for all strategies.

All strategies return JSON arrays from the LLM. These parsers extract,
validate, and return the parsed actions.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

REQUIRED_ACTION_FIELDS = {"action", "contract", "qty"}
VALID_ACTIONS = {"buy", "sell"}
VALID_ORDER_TYPES = {"MARKET", "LIMIT"}


def parse_json_array_response(raw: str) -> list[dict]:
    """Extract and validate a JSON array of trade actions from LLM output.

    Strips markdown code fences, finds JSON array via regex,
    validates required fields per action.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
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
        if item.get("action") == "hold":
            continue
        err = _validate_action(item)
        if err:
            logger.warning("dropping invalid LLM action: %s -- %s", err, item)
        else:
            valid.append(item)
    return valid


def parse_json_object_response(raw: str) -> dict:
    """Extract a single JSON object from LLM output.

    Used by overlay and hybrid strategies that return a single decision.
    Returns empty dict on parse failure.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON object from LLM response")
        return {}


def _validate_action(action: dict) -> str | None:
    """Validate a single action dict. Returns error string or None."""
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
