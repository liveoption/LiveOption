"""Earnings Bet prompt helpers for the EarningsBetStrategy.

Phases:
  Pre-earnings  (T-2, T-1) — daily cadence at close, place/adjust option bets
  Post-earnings (T, T+1, T+2) — intraday cadence, manage/exit positions
  Force-close   (T+2 EOD) — mechanical close, no LLM needed

Prompts are loaded from text files via PromptLoader:
  system: prompts/system/earnings_pre.txt, earnings_post.txt
  user:   prompts/user/earnings_pre.txt, earnings_post.txt

Data-leak prevention:
  - Pre-T: context uses bars up to T-1 close (never current day's close)
  - Post-T: use open price of current bar or previous bar's close as "current"
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime

from trading.prompts.loader import get_loader

logger = logging.getLogger(__name__)

REQUIRED_ACTION_FIELDS = {"action", "contract", "qty"}
VALID_ACTIONS = {"buy", "sell", "hold", "stop"}
VALID_ORDER_TYPES = {"MARKET", "LIMIT"}


# ---------------------------------------------------------------------------
# Analyzer prompt builders (load from .txt files)
# ---------------------------------------------------------------------------


def build_pre_earnings_system(
    ticker: str, max_position_cost: float, max_contracts: int, template: str | None = None
) -> str:
    loader = get_loader()
    return loader.render(
        template if template is not None else loader.load_system("earnings_pre"),
        TICKER=ticker,
        MAX_POSITION_COST=f"{max_position_cost:,.0f}",
        MAX_CONTRACTS=str(max_contracts),
    )


def build_pre_earnings_user(
    date: str,
    ticker: str,
    earnings_date: str,
    is_bmo: bool,
    days_to_earnings: int,
    account: dict,
    historical_reactions: list[dict],
    estimated_eps: float | None,
    estimated_revenue: float | None,
    chain_summary: list[dict],
    compressed_chain_text: str | None = None,
    news: list[dict] | None = None,
    price_context_text: str = "",
    phase_label: str = "T-2 ENTRY",
    t_minus_2: str = "",
    t_minus_1: str = "",
    t_plus_1: str = "",
    t_plus_2: str = "",
    template: str | None = None,
) -> str:
    loader = get_loader()
    timing = "BMO (Before Market Open)" if is_bmo else "AMC (After Market Close)"
    positions_table = _build_positions_table(account.get("positions", []), date)
    hist_str = _format_historical_reactions(historical_reactions)
    chain_str = compressed_chain_text or _format_chain(chain_summary)
    news_window = f"T-5 to {date}"
    news_section = _format_news(news or [])
    price_ctx = price_context_text or "  (no price data)"

    return loader.render(
        template if template is not None else loader.load_user("earnings_pre"),
        PHASE_LABEL=phase_label,
        DATE=date,
        TICKER=ticker,
        EARNINGS_DATE=earnings_date,
        TIMING=timing,
        DAYS_TO_EARNINGS=str(days_to_earnings),
        T_MINUS_2=t_minus_2 or "?",
        T_MINUS_1=t_minus_1 or "?",
        T_PLUS_1=t_plus_1 or "?",
        T_PLUS_2=t_plus_2 or "?",
        CASH=f"{account.get('cash', account.get('cash_available', 0)):,.2f}",
        EQUITY=f"{account.get('equity', 0):,.2f}",
        POSITIONS_TABLE=positions_table,
        PRICE_CONTEXT=price_ctx,
        N_HISTORICAL=str(len(historical_reactions)),
        HISTORICAL_REACTIONS=hist_str,
        ESTIMATED_EPS=_fmt_val(estimated_eps),
        ESTIMATED_REVENUE=_fmt_revenue(estimated_revenue),
        NEWS_WINDOW=news_window,
        NEWS_SECTION=news_section,
        CHAIN_CONTEXT=chain_str,
    )


def build_post_earnings_system(ticker: str, post_day: int, time_et: str, template: str | None = None) -> str:
    loader = get_loader()
    return loader.render(
        template if template is not None else loader.load_system("earnings_post"),
        TICKER=ticker,
        POST_DAY=str(post_day),
        TIME_ET=time_et or "—",
    )


def build_post_earnings_user(
    date: str,
    ticker: str,
    earnings_date: str,
    actual_eps: float | None,
    estimated_eps: float | None,
    eps_surprise_percent: float | None,
    actual_revenue: float | None,
    estimated_revenue: float | None,
    revenue_surprise_percent: float | None,
    account: dict,
    chain_summary: list[dict],
    post_day: int,
    time_et: str = "",
    compressed_chain_text: str | None = None,
    news: list[dict] | None = None,
    gap_info: str = "",
    prediction_recap: str = "",
    template: str | None = None,
) -> str:
    loader = get_loader()
    positions_table = _build_positions_table(account.get("positions", []), date)
    chain_str = compressed_chain_text or _format_chain(chain_summary)
    news_section = _format_news(news or [])

    eps_surprise = f"{eps_surprise_percent:+.1f}%" if eps_surprise_percent is not None else "N/A"
    rev_surprise = f"{revenue_surprise_percent:+.1f}%" if revenue_surprise_percent is not None else "N/A"

    return loader.render(
        template if template is not None else loader.load_user("earnings_post"),
        DATE=date,
        TIME_ET=time_et or "—",
        TICKER=ticker,
        EARNINGS_DATE=earnings_date,
        ACTUAL_EPS=_fmt_val(actual_eps),
        ESTIMATED_EPS=_fmt_val(estimated_eps),
        EPS_SURPRISE=eps_surprise,
        ACTUAL_REVENUE=_fmt_revenue(actual_revenue),
        ESTIMATED_REVENUE=_fmt_revenue(estimated_revenue),
        REV_SURPRISE=rev_surprise,
        CASH=f"{account.get('cash', account.get('cash_available', 0)):,.2f}",
        EQUITY=f"{account.get('equity', 0):,.2f}",
        POSITIONS_TABLE=positions_table,
        CHAIN_CONTEXT=chain_str,
        POST_DAY=str(post_day),
        NEWS_SECTION=news_section,
        GAP_INFO=gap_info or "  (gap data not available)",
        PREDICTION_RECAP=prediction_recap or "  (no prior prediction recorded)",
    )


# ---------------------------------------------------------------------------
# Response parsing — analyzer phase
# ---------------------------------------------------------------------------


def parse_pre_earnings_analysis(raw: str) -> tuple[dict | None, list[str]]:
    """Parse pre-earnings analyzer response.

    Returns (prediction_dict, target_instruments_list).
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            obj = json.loads(obj_match.group(0))
            if isinstance(obj, dict):
                prediction = obj.get("prediction")
                targets = obj.get("target_instruments", [])
                if isinstance(targets, list):
                    return prediction, targets
        except json.JSONDecodeError:
            pass

    logger.warning("Pre-earnings analyzer response is not valid JSON")
    return None, []


def parse_post_earnings_analysis(raw: str) -> tuple[dict | None, list[str], bool]:
    """Parse post-earnings analyzer response.

    Returns (assessment_dict, target_instruments_list, stop_signal).
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            obj = json.loads(obj_match.group(0))
            if isinstance(obj, dict):
                assessment = obj.get("assessment")
                targets = obj.get("target_instruments", [])
                stop = bool(obj.get("stop", False))
                if isinstance(targets, list):
                    return assessment, targets, stop
        except json.JSONDecodeError:
            pass

    logger.warning("Post-earnings analyzer response is not valid JSON")
    return None, [], False


# ---------------------------------------------------------------------------
# Response parsing — executor phase (reuses common parser)
# ---------------------------------------------------------------------------


def parse_earnings_response(raw: str) -> tuple[dict | None, list[dict]]:
    """Extract prediction + orders from LLM JSON response (legacy single-call format).

    Returns (prediction_dict, orders_list).
    prediction_dict may be None if not present.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            obj = json.loads(obj_match.group(0))
            if isinstance(obj, dict):
                prediction = obj.get("prediction")
                orders = obj.get("orders", [])
                if isinstance(orders, list):
                    valid_orders = _validate_orders(orders)
                    return prediction, valid_orders
        except json.JSONDecodeError:
            pass

    arr_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if arr_match:
        try:
            items = json.loads(arr_match.group(0))
            valid_orders = _validate_orders(items)
            return None, valid_orders
        except json.JSONDecodeError:
            pass

    logger.warning("LLM response is not valid JSON")
    return None, []


def parse_post_earnings_response(raw: str) -> tuple[dict | None, list[dict], bool]:
    """Extract assessment + orders + stop signal from post-earnings LLM response.

    Returns (assessment_dict, orders_list, stop_signal).
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            obj = json.loads(obj_match.group(0))
            if isinstance(obj, dict):
                assessment = obj.get("assessment")
                orders = obj.get("orders", [])
                stop = bool(obj.get("stop", False))
                if isinstance(orders, list):
                    valid_orders = _validate_orders(orders)
                    return assessment, valid_orders, stop
        except json.JSONDecodeError:
            pass

    logger.warning("Post-earnings LLM response is not valid JSON")
    return None, [], False


def _validate_orders(items: list) -> list[dict]:
    """Validate and filter a list of order dicts."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_action(action: dict) -> str | None:
    required = {"action", "contract", "qty"}
    missing = required - set(action.keys())
    if missing:
        return f"missing required fields: {missing}"
    if action["action"] not in ("buy", "sell"):
        return f"invalid action '{action['action']}'"
    if not isinstance(action["qty"], (int, float)) or action["qty"] <= 0:
        return f"qty must be positive, got {action['qty']}"
    order_type = action.get("order_type", "MARKET")
    if order_type not in VALID_ORDER_TYPES:
        return f"invalid order_type '{order_type}'"
    if order_type == "LIMIT" and action.get("limit_price") is None:
        return "LIMIT order requires limit_price"
    return None


def _build_positions_table(positions: list[dict], trade_date: str) -> str:
    if not positions:
        return "  (no open positions)"

    try:
        ref_date = date.fromisoformat(trade_date[:10])
    except (ValueError, TypeError):
        ref_date = date.today()

    header = "| Type | Contract | Side | Qty | Avg Entry | Mark | Unreal P&L | DTE |"
    sep    = "|------|----------|------|-----|-----------|------|------------|-----|"
    rows = [header, sep]

    for p in positions:
        contract = p.get("instrument_id", p.get("contract", "?"))
        qty = p.get("qty", 0)
        side = p.get("side") or ("LONG" if qty > 0 else "SHORT")
        avg_cost = p.get("avg_cost", p.get("avg_entry", 0))
        mark = p.get("mark_price", p.get("market_value", 0))
        unrealized = p.get("unrealized_pnl", 0)
        is_option = isinstance(contract, str) and contract.startswith("O:")
        pos_type = "OPT" if is_option else "STK"
        dte_str = _compute_dte(contract, ref_date) if is_option else "—"
        rows.append(
            f"| {pos_type} | {contract} | {side} | {abs(qty)} | "
            f"${avg_cost:,.2f} | ${mark:,.2f} | ${unrealized:,.2f} | {dte_str} |"
        )
    return "\n".join(rows)


def _compute_dte(contract: str, ref_date: date) -> str:
    m = re.search(r"O:\w+?(\d{6})[CP]\d{8}$", contract)
    if not m:
        return "N/A"
    try:
        expiry = datetime.strptime(m.group(1), "%y%m%d").date()
        return str(max((expiry - ref_date).days, 0))
    except ValueError:
        return "N/A"


def _format_historical_reactions(reactions: list[dict]) -> str:
    """Format historical earnings reactions including price move data."""
    if not reactions:
        return "  (no historical data)"
    lines = []
    for r in reactions:
        period = r.get("fiscal_period", "?")
        year = r.get("fiscal_year", "?")
        actual = r.get("actual_eps")
        est = r.get("estimated_eps")
        surprise = r.get("eps_surprise_percent")

        surprise_str = f"{surprise:+.1%}" if surprise is not None else "N/A"
        actual_str = f"${actual:.2f}" if actual is not None else "N/A"
        est_str = f"${est:.2f}" if est is not None else "N/A"

        # Price move data (added by enrich_historical_with_price_moves)
        pre = r.get("pre_move_pct")
        gap = r.get("gap_pct")
        post = r.get("post_move_pct")
        pre_str  = f"{pre:+.1f}%" if pre is not None else "N/A"
        gap_str  = f"{gap:+.1f}%" if gap is not None else "N/A"
        post_str = f"{post:+.1f}%" if post is not None else "N/A"

        lines.append(
            f"  {period} FY{year}: EPS {actual_str} vs {est_str} (surprise {surprise_str}) | "
            f"pre-run {pre_str} | gap {gap_str} | post T+2 {post_str}"
        )
    return "\n".join(lines)


def _format_news(news: list[dict]) -> str:
    if not news:
        return "  (no news in window)"
    lines = []
    for n in news[:6]:
        pub = n.get("published", "")[:10]
        title = n.get("title", "")
        publisher = n.get("publisher", "")
        snippet = n.get("snippet", "")[:120]
        lines.append(f"  [{pub}] {title} ({publisher})")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def _format_chain(chain_summary: list[dict]) -> str:
    if not chain_summary:
        return "  (no chain data)"
    header = "| Type | Strike | Expiry | DTE | Price | Volume | IV | Delta | Contract |"
    sep    = "|------|--------|--------|-----|-------|--------|----|-------|----------|"
    rows = [header, sep]
    for c in chain_summary[:20]:
        contract = c.get("ticker") or c.get("contract", "?")
        right = c.get("type") or c.get("right", "?")
        iv = c.get("iv")
        iv_str = f"{iv:.1%}" if iv is not None else "N/A"
        delta = c.get("delta")
        delta_str = f"{delta:.2f}" if delta is not None else "N/A"
        raw_price = c.get("close") or c.get("price")
        price_str = f"${raw_price:,.2f}" if isinstance(raw_price, (int, float)) else "N/A"
        raw_vol = c.get("volume")
        vol_str = f"{raw_vol:,}" if isinstance(raw_vol, (int, float)) else "N/A"
        rows.append(
            f"| {right} | {c.get('strike', '?')} | {c.get('expiry', '?')} "
            f"| {c.get('dte', '?')} | {price_str} | {vol_str} "
            f"| {iv_str} | {delta_str} | {contract} |"
        )
    return "\n".join(rows)


def _fmt_val(val: float | None) -> str:
    return f"${val:.2f}" if val is not None else "N/A"


def _fmt_revenue(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1e9:
        return f"${val / 1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.2f}M"
    return f"${val:,.0f}"
