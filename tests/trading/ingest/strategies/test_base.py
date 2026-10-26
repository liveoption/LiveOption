"""Tests for BaseStrategy web-layer additions: events emitter + abstract checkpoint."""
from __future__ import annotations

import pytest

from trading.agents.strategy.base import BaseStrategy


def test_strategy_base_has_events_none_by_default():
    class Dummy(BaseStrategy):
        def on_step(self, market_event, portfolio, state):
            # implement other abstracts as no-op
            return []

        def save_checkpoint(self):
            return {}

        def load_checkpoint(self, state):
            pass

    d = Dummy()
    assert d.events is None
    d.emit("x", {})  # must not raise


def test_save_and_load_checkpoint_abstract():
    class Broken(BaseStrategy):
        def on_step(self, market_event, portfolio, state):
            return []

    with pytest.raises(TypeError):
        Broken()  # save_checkpoint/load_checkpoint not implemented
