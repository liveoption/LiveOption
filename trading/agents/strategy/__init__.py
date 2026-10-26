"""Strategy agent package.

Importing this package triggers @register_strategy decorators.
"""

from trading.agents.strategy.base import BaseStrategy

__all__ = ["BaseStrategy"]

# Import concrete strategies to trigger registration:
from trading.agents.strategy import weekly_llm  # noqa: F401
from trading.agents.strategy import intraday_llm  # noqa: F401
from trading.agents.strategy import overlay  # noqa: F401  (registers overlay_profit + overlay_hedge)
from trading.agents.strategy import intraday_0dte  # noqa: F401  (registers intraday_0dte)
from trading.agents.strategy import earnings_bet  # noqa: F401
from trading.agents.strategy import rule_baselines  # noqa: F401  (registers buy_and_hold + intraday/overlay rule baselines)

# Optional strategies (may not exist in all installs):
try:
    from trading.agents.strategy import hybrid_intraday  # noqa: F401
except ImportError:
    pass
try:
    from trading.agents.strategy import paper_aligned  # noqa: F401
except ImportError:
    pass
