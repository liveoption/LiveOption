"""Tests for strategy plugin registry."""

import pytest

from trading.core.registry import STRATEGY_REGISTRY, get_strategy, register_strategy


def test_register_and_get():
    @register_strategy("test_strat")
    class TestStrategy:
        def __init__(self, param_a=1):
            self.param_a = param_a

    strat = get_strategy("test_strat", {"param_a": 42})
    assert strat.param_a == 42
    # Cleanup
    STRATEGY_REGISTRY.pop("test_strat", None)


def test_get_unknown_strategy():
    with pytest.raises(KeyError):
        get_strategy("nonexistent", {})


def test_register_overwrites():
    """Registering the same name twice overwrites the first."""

    @register_strategy("overwrite_test")
    class First:
        val = 1

    @register_strategy("overwrite_test")
    class Second:
        val = 2

    strat = get_strategy("overwrite_test", {})
    assert strat.val == 2
    STRATEGY_REGISTRY.pop("overwrite_test", None)


def test_register_preserves_class():
    """The decorator returns the original class unchanged."""

    @register_strategy("preserve_test")
    class MyStrategy:
        pass

    assert MyStrategy is STRATEGY_REGISTRY["preserve_test"]
    STRATEGY_REGISTRY.pop("preserve_test", None)


def test_get_strategy_with_no_params():
    @register_strategy("no_params_test")
    class SimpleStrategy:
        def __init__(self):
            self.initialized = True

    strat = get_strategy("no_params_test", {})
    assert strat.initialized is True
    STRATEGY_REGISTRY.pop("no_params_test", None)
