"""Tests for ExecutionAgent — fill extraction and order submission."""
from unittest.mock import MagicMock

from trading.agents.execution import ExecutionAgent, strip_instrument_prefix
from trading.core.schemas import FillEvent, RiskDecision, TradeIntent


def _make_intent(
    contract="O:SPY250106C00595000",
    side="BUY",
    qty=5,
    order_type="MARKET",
    spread_group=None,
    limit_price=None,
):
    return TradeIntent(
        symbol="SPY",
        contract=contract,
        side=side,
        qty=qty,
        order_type=order_type,
        spread_group=spread_group,
        limit_price=limit_price,
    )


def _make_decision(
    action="ALLOW",
    intent=None,
    adjusted_qty=None,
):
    if intent is None:
        intent = _make_intent()
    return RiskDecision(
        original_intent=intent,
        action=action,
        adjusted_qty=adjusted_qty,
        reason="test",
    )


def _make_step_result_with_fills():
    """Return a mock PMB step result containing ORDER_EVENT + TRADE_EVENT."""
    return {
        "ok": True,
        "clock": {"current_ts": "2025-01-06T09:32:00"},
        "events": [
            {
                "type": "ORDER_EVENT",
                "ts": "2025-01-06T09:32:00",
                "payload": {
                    "order_id": "ord_123",
                    "client_order_id": "buy_1",
                    "status": "FILLED",
                    "filled_qty": 5,
                    "avg_fill_price": 2.30,
                },
            },
            {
                "type": "TRADE_EVENT",
                "ts": "2025-01-06T09:32:00",
                "payload": {
                    "trade_id": "trd_abc",
                    "order_id": "ord_123",
                    "instrument_id": "OPTION:O:SPY250106C00595000",
                    "side": "BUY",
                    "qty": 5,
                    "price": 2.30,
                    "fees": 3.25,
                },
            },
        ],
    }


def _make_mock_pmb():
    mock = MagicMock()
    mock.place_order.return_value = {"order_id": "ord_new_1"}
    return mock


def test_market_policy_overrides_strategy_limit_order():
    pmb = _make_mock_pmb()
    agent = ExecutionAgent(pmb, order_type_policy="market")
    orders = agent.submit_orders([_make_decision(intent=_make_intent(order_type="LIMIT", limit_price=2.5))], MagicMock())
    assert orders[0].order_type == "MARKET"
    assert orders[0].limit_price is None
    assert pmb.place_order.call_args.args[0]["order_type"] == "MARKET"
    assert "limit_price" not in pmb.place_order.call_args.args[0]


def test_limit_policy_skips_missing_price_and_entire_spread():
    pmb = _make_mock_pmb()
    agent = ExecutionAgent(pmb, order_type_policy="limit")
    state = MagicMock(errors=[])
    decisions = [
        _make_decision(intent=_make_intent(contract="O:A", spread_group="A", limit_price=2.5)),
        _make_decision(intent=_make_intent(contract="O:B", spread_group="A")),
    ]
    assert agent.submit_orders(decisions, state) == []
    assert state.errors and "spread" in state.errors[0]
    pmb.place_order.assert_not_called()


def test_limit_policy_submits_with_strategy_price():
    pmb = _make_mock_pmb()
    agent = ExecutionAgent(pmb, order_type_policy="limit")
    orders = agent.submit_orders([_make_decision(intent=_make_intent(limit_price=2.5))], MagicMock())
    assert orders[0].order_type == "LIMIT"
    assert pmb.place_order.call_args.args[0]["limit_price"] == 2.5


class TestExtractFills:
    def test_extract_fills_from_step_result(self):
        """Parses ORDER_EVENT/TRADE_EVENT from PMB events into FillEvent list."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        step_result = _make_step_result_with_fills()

        fills = agent.extract_fills(step_result)

        assert len(fills) == 1
        fill = fills[0]
        assert isinstance(fill, FillEvent)
        assert fill.trade_id == "trd_abc"
        assert fill.order_id == "ord_123"
        assert fill.side == "BUY"
        assert fill.qty == 5
        assert fill.price == 2.30
        assert fill.fees == 3.25
        assert fill.fill_ts == "2025-01-06T09:32:00"

    def test_extract_fills_strips_instrument_prefix(self):
        """OPTION:O:SPY... is stripped to O:SPY..."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        step_result = _make_step_result_with_fills()

        fills = agent.extract_fills(step_result)

        assert fills[0].contract == "O:SPY250106C00595000"

    def test_extract_fills_strips_stock_prefix(self):
        """STOCK:AAPL is stripped to AAPL."""
        assert strip_instrument_prefix("STOCK:AAPL") == "AAPL"

    def test_extract_fills_no_events(self):
        """Empty events list returns no fills."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)

        fills = agent.extract_fills({"events": []})
        assert fills == []

    def test_extract_fills_skips_non_filled_orders(self):
        """ORDER_EVENT with status != FILLED is not matched."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        step_result = {
            "events": [
                {
                    "type": "ORDER_EVENT",
                    "ts": "2025-01-06T09:32:00",
                    "payload": {
                        "order_id": "ord_999",
                        "status": "PENDING",
                    },
                },
            ]
        }
        fills = agent.extract_fills(step_result)
        assert fills == []


class TestSubmitOrders:
    def test_submit_single_order(self):
        """ALLOW decision -> place_order called with correct args."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        state = MagicMock()

        intent = _make_intent(contract="O:SPY250106C00595000", side="BUY", qty=5)
        decision = _make_decision(action="ALLOW", intent=intent)

        orders = agent.submit_orders([decision], state)

        assert len(orders) == 1
        assert orders[0].contract == "O:SPY250106C00595000"
        assert orders[0].side == "BUY"
        assert orders[0].qty == 5
        assert orders[0].order_type == "MARKET"
        assert orders[0].spread_id is None

        # Verify PMB called correctly
        pmb.place_order.assert_called_once()
        call_args = pmb.place_order.call_args
        action_dict = call_args[0][0]
        assert action_dict["action"] == "buy"
        assert action_dict["contract"] == "O:SPY250106C00595000"
        assert action_dict["qty"] == 5
        assert call_args[1]["spread_id"] is None

    def test_submit_spread_orders(self):
        """Two decisions with same spread_group -> shared spread_id."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        state = MagicMock()

        long_leg = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=5, spread_group="A"
        )
        short_leg = _make_intent(
            contract="O:SPY250106C00600000", side="SELL", qty=5, spread_group="A"
        )
        decisions = [
            _make_decision(action="ALLOW", intent=long_leg),
            _make_decision(action="ALLOW", intent=short_leg),
        ]

        orders = agent.submit_orders(decisions, state)

        assert len(orders) == 2
        # Both orders share the same spread_id
        assert orders[0].spread_id is not None
        assert orders[0].spread_id == orders[1].spread_id
        assert orders[0].spread_id.startswith("spread_")

        # Verify PMB called twice with same spread_id
        assert pmb.place_order.call_count == 2
        spread_ids = [
            call[1]["spread_id"] for call in pmb.place_order.call_args_list
        ]
        assert spread_ids[0] == spread_ids[1]
        assert spread_ids[0] is not None

    def test_skip_rejected_decisions(self):
        """REJECT decisions are not submitted."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        state = MagicMock()

        intent = _make_intent()
        decision = _make_decision(action="REJECT", intent=intent)

        orders = agent.submit_orders([decision], state)

        assert len(orders) == 0
        pmb.place_order.assert_not_called()

    def test_adjust_uses_adjusted_qty(self):
        """ADJUST decision uses adjusted_qty instead of original qty."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        state = MagicMock()

        intent = _make_intent(qty=10)
        decision = _make_decision(action="ADJUST", intent=intent, adjusted_qty=3)

        orders = agent.submit_orders([decision], state)

        assert len(orders) == 1
        assert orders[0].qty == 3  # adjusted, not original 10

        call_args = pmb.place_order.call_args
        action_dict = call_args[0][0]
        assert action_dict["qty"] == 3

    def test_mixed_allow_reject_adjust(self):
        """Mixed decisions: only ALLOW and ADJUST are submitted."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        state = MagicMock()

        decisions = [
            _make_decision(action="ALLOW", intent=_make_intent(contract="O:A")),
            _make_decision(action="REJECT", intent=_make_intent(contract="O:B")),
            _make_decision(
                action="ADJUST",
                intent=_make_intent(contract="O:C", qty=10),
                adjusted_qty=5,
            ),
        ]

        orders = agent.submit_orders(decisions, state)

        assert len(orders) == 2
        assert orders[0].contract == "O:A"
        assert orders[1].contract == "O:C"
        assert orders[1].qty == 5
        assert pmb.place_order.call_count == 2

    def test_spread_rejected_if_any_leg_rejected(self):
        """One leg ALLOW, one REJECT in same spread_group -> neither submitted."""
        pmb = _make_mock_pmb()
        agent = ExecutionAgent(pmb)
        state = MagicMock()

        long_leg = _make_intent(
            contract="O:SPY250106C00595000", side="BUY", qty=5, spread_group="S1"
        )
        short_leg = _make_intent(
            contract="O:SPY250106C00600000", side="SELL", qty=5, spread_group="S1"
        )
        decisions = [
            _make_decision(action="ALLOW", intent=long_leg),
            _make_decision(action="REJECT", intent=short_leg),
        ]

        orders = agent.submit_orders(decisions, state)

        # Neither leg should be submitted — entire spread skipped
        assert len(orders) == 0
        pmb.place_order.assert_not_called()
