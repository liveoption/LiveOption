"""Strategy plugin registry.

Strategies register themselves via the @register_strategy decorator.
The runner looks up strategies by name via get_strategy().
"""

from __future__ import annotations

from typing import Any

# Global registry mapping strategy name -> strategy class.
STRATEGY_REGISTRY: dict[str, type] = {}


def register_strategy(name: str):
    """Decorator to register a strategy class under the given name.

    Usage::

        @register_strategy("my_strategy")
        class MyStrategy(BaseStrategy):
            ...
    """

    def wrapper(cls):
        STRATEGY_REGISTRY[name] = cls
        return cls

    return wrapper


def get_strategy(name: str, params: dict[str, Any]):
    """Instantiate a registered strategy by name with the given params.

    Args:
        name: The registry key (e.g. "weekly_llm", "intraday_llm").
        params: Keyword arguments passed to the strategy constructor.

    Returns:
        An instance of the registered strategy class.

    Raises:
        KeyError: If no strategy is registered under the given name.
    """
    if name not in STRATEGY_REGISTRY:
        raise KeyError(
            f"Unknown strategy '{name}'. "
            f"Available: {list(STRATEGY_REGISTRY.keys())}"
        )
    cls = STRATEGY_REGISTRY[name]
    return cls(**params)
