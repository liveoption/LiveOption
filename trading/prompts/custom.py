"""Minimal editable examples for cadence-based options tasks."""
from trading.prompts.intraday import INTRADAY_USER_TEMPLATE


def custom_prompts(cadence: str) -> dict[str, str]:
    timing = (
        "Make one decision per trading day when invoked. Use only the supplied point-in-time data. Orders are evaluated on the next replay bar."
        if cadence == "daily" else
        "Make a decision every {interval} minutes. {flatten_clause}"
    )
    system = """You manage an options-only portfolio on {universe}.
""" + timing + """
Use options with {dte_min}–{dte_max} days to expiry. Hold at most {max_positions} positions.
Choose your own strategy from the supplied market data. Return [] when there is no trade.
Return only a JSON array of orders, for example:
[{{"action":"buy","contract":"<OPRA>","qty":1,"order_type":"MARKET","rationale":"<reason>"}}]
Use "sell" to reduce a position. LIMIT orders must include a numeric limit_price.
"""
    user = INTRADAY_USER_TEMPLATE if cadence == "intraday" else """Date: {current_time}
Cash: ${cash:,.2f} | Equity: ${equity:,.2f}
Daily P&L: ${daily_pnl:,.2f} ({daily_pnl_pct:.2f}%)

Current positions:
{positions_table}

Recent prices available at decision time:
{price_windows_table}

Available options (DTE {dte_min}–{dte_max}):
{chain_sample_table}

Return your option orders as a JSON array, or [] to hold.
"""
    return {"system": system, "user": user}
