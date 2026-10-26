# trading/agents/execution.py
"""
ExecutionAgent — converts approved RiskDecisions into PMB orders and extracts fills.

Two responsibilities per step, executed in order:
1. Extract fills from the current step's PMB events (fills of orders from previous steps).
2. Submit new orders from the current step's risk decisions.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

import logging

from trading.core.schemas import FillEvent, OrderRequest, RiskDecision

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from trading.core.state import TradingState
    from trading.tools.pmb_client import PMBClient


def strip_instrument_prefix(instrument_id: str) -> str:
    """Strip PMB instrument type prefix (OPTION: or STOCK:) from an instrument ID.

    PMB uses prefixed instrument_id internally:
        OPTION:O:SPY250106C00595000 -> O:SPY250106C00595000
        STOCK:AAPL                  -> AAPL

    The trading layer uses clean IDs without the type prefix.
    """
    if instrument_id.startswith("OPTION:"):
        return instrument_id[len("OPTION:"):]
    if instrument_id.startswith("STOCK:"):
        return instrument_id[len("STOCK:"):]
    return instrument_id


class ExecutionAgent:
    """Converts risk-approved decisions into PMB orders and extracts fills."""

    def __init__(self, pmb_client: PMBClient, order_type_policy: str = "strategy"):
        self._pmb = pmb_client
        self._order_type_policy = order_type_policy

    def extract_fills(self, step_result: dict) -> list[FillEvent]:
        """Parse ORDER_EVENT (status=FILLED) and TRADE_EVENT from PMB step events.

        PMB step results contain an 'events' list. Each event has 'type', 'ts',
        and 'payload'. We correlate ORDER_EVENTs (for order_id, filled_qty) with
        TRADE_EVENTs (for trade_id, instrument_id, side, price, fees) to produce
        FillEvent objects.

        Args:
            step_result: Raw PMB step result dict with 'events' key.

        Returns:
            List of FillEvent for all fills found in this step.
        """
        events = step_result.get("events", [])
        fills: list[FillEvent] = []

        # Index filled orders by order_id
        filled_orders: dict[str, dict] = {}
        for event in events:
            if event.get("type") == "ORDER_EVENT":
                payload = event.get("payload", {})
                if payload.get("status") == "FILLED":
                    filled_orders[payload["order_id"]] = payload

        # Build FillEvents from TRADE_EVENTs that correspond to filled orders
        for event in events:
            if event.get("type") == "TRADE_EVENT":
                payload = event.get("payload", {})
                order_id = payload.get("order_id", "")
                trade_id = payload.get("trade_id", "")
                instrument_id = payload.get("instrument_id", "")
                contract = strip_instrument_prefix(instrument_id)

                fill = FillEvent(
                    trade_id=trade_id,
                    order_id=order_id,
                    contract=contract,
                    side=payload.get("side", ""),
                    qty=payload.get("qty", 0),
                    price=payload.get("price", 0.0),
                    fees=payload.get("fees", 0.0),
                    fill_ts=event.get("ts", ""),
                )
                fills.append(fill)

        # Log PMB option expiry events (exercise / assignment / worthless)
        for event in events:
            if event.get("type") == "OPTION_EXPIRY_EVENT":
                payload = event.get("payload", {})
                contract = payload.get("contract", "")
                is_itm = payload.get("is_itm", False)
                assignment = payload.get("assignment")
                if is_itm and assignment:
                    _logger.info(
                        "EXPIRY: %s expired ITM → %s %d shares at $%.2f",
                        contract, assignment.get("side", "?"),
                        assignment.get("qty", 0), assignment.get("strike", 0),
                    )
                else:
                    _logger.info("EXPIRY: %s expired worthless (OTM)", contract)

        return fills

    def submit_orders(
        self,
        decisions: list[RiskDecision],
        state: TradingState,
    ) -> list[OrderRequest]:
        """Submit approved decisions to PMB as orders.

        Groups decisions by spread_group: decisions with the same non-None
        spread_group share a spread_id. Only ALLOW and ADJUST decisions are
        submitted; REJECT decisions are skipped.

        For ADJUST decisions, the adjusted_qty is used instead of the original qty.

        Args:
            decisions: Risk gate output for this step.
            state: Current trading state (used for context, not currently read).

        Returns:
            List of OrderRequest objects for orders submitted to PMB.
        """
        orders: list[OrderRequest] = []

        # Separate singles (no spread_group) and spreads.
        # For spreads, collect ALL legs (including rejected) so we can check
        # that every leg in a group is approved before submitting any.
        singles: list[RiskDecision] = []
        spread_groups: dict[str, list[RiskDecision]] = defaultdict(list)

        for decision in decisions:
            sg = decision.original_intent.spread_group
            if sg is not None:
                spread_groups[sg].append(decision)
            else:
                if decision.action in ("ALLOW", "ADJUST"):
                    singles.append(decision)

        # Submit single orders
        for decision in singles:
            if self._order_type_policy == "limit" and (decision.original_intent.limit_price is None or decision.original_intent.limit_price <= 0):
                message = f"LIMIT policy skipped {decision.original_intent.contract}: missing positive limit_price"
                state.errors.append(message)
                _logger.warning(message)
                continue
            order = self._submit_order(decision)
            orders.append(order)

        # Submit spread orders — only if ALL legs in the group are approved.
        # If any leg is REJECT, skip the entire spread to avoid naked legs.
        for group_key, legs in spread_groups.items():
            all_approved = all(d.action in ("ALLOW", "ADJUST") for d in legs)
            if not all_approved:
                continue  # skip entire spread if any leg rejected
            if self._order_type_policy == "limit" and any(leg.original_intent.limit_price is None or leg.original_intent.limit_price <= 0 for leg in legs):
                message = f"LIMIT policy skipped spread {group_key}: every leg needs a positive limit_price"
                state.errors.append(message)
                _logger.warning(message)
                continue
            spread_id = f"spread_{uuid.uuid4().hex[:8]}"
            for leg in legs:
                order = self._submit_order(leg, spread_id=spread_id)
                orders.append(order)

        return orders

    def _submit_order(
        self,
        decision: RiskDecision,
        spread_id: str | None = None,
    ) -> OrderRequest:
        """Submit a single order to PMB and return an OrderRequest record."""
        intent = decision.original_intent
        qty = decision.adjusted_qty if decision.action == "ADJUST" else intent.qty
        order_type = intent.order_type
        limit_price = intent.limit_price
        if self._order_type_policy == "market":
            order_type, limit_price = "MARKET", None
        elif self._order_type_policy == "limit":
            if limit_price is None or limit_price <= 0:
                raise ValueError("LIMIT order policy requires a positive limit_price from the strategy")
            order_type = "LIMIT"

        # Detect stock vs option from contract format
        is_stock = not intent.contract.startswith("O:")
        action = {
            "action": intent.side.lower(),
            "contract": intent.contract,
            "qty": qty,
            "order_type": order_type,
            "instrument_type": "STOCK" if is_stock else "OPTION",
        }
        if limit_price is not None:
            action["limit_price"] = limit_price

        result = self._pmb.place_order(action, spread_id=spread_id)

        return OrderRequest(
            order_id=result.get("order_id", ""),
            contract=intent.contract,
            side=intent.side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            spread_id=spread_id,
        )
