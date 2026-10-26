"""ESP-based news and event context builder for LLM prompts.

ESP (Events & News, formerly NPP) is served by the QFinZero unified hub at
``{hub}/svc/esp`` with routes under ``/esp/*``. Request/response schemas are
unchanged from NPP — only the path prefix moved from ``/npp`` to ``/esp``.

All queries pass the simulation's current_ts as now_utc (or as explicit end_utc
for news/search) to prevent data leaking.

Key timing rule:
  US economic data is typically released at 08:30 ET (13:30 UTC).
  If current_ts is before 13:30 UTC that day, that data will NOT appear.
  If current_ts is after 13:30 UTC, the data will appear.
  This is the correct behaviour for a replay simulation.

Usage:
    from trading.tools.npp_context import (
        build_intraday_news_context,
        build_overlay_news_context,
        fetch_earnings_pre_news,
    )

    text = build_intraday_news_context(
        npp_base_url="http://127.0.0.1:19777/svc/esp",
        current_ts="2025-01-06T14:30:00+00:00",
        universe_tickers=["SPY"],
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from trading.core.endpoints import qfz_headers

logger = logging.getLogger(__name__)

# Fixed market tickers always included for intraday context
INTRADAY_MARKET_TICKERS = ["NVDA", "QQQ", "SPY", "META", "AAPL", "MSFT", "GOOG"]

# FANG+ tickers monitored for earnings
FANG_PLUS_TICKERS = ["META", "AAPL", "NFLX", "GOOG", "GOOGL", "AMZN", "MSFT", "NVDA", "TSLA"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_utc_iso(ts) -> str:
    """Normalise a timestamp (datetime or ISO string) to UTC ISO: YYYY-MM-DDTHH:MM:SSZ."""
    try:
        if isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return str(ts)


def _date_str(ts) -> str:
    """Extract YYYY-MM-DD from any timestamp value (datetime or string)."""
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    s = str(ts)
    return s[:10] if len(s) >= 10 else s


def _days_offset(date_str: str, n: int) -> str:
    """Return date string N days before (n<0) or after (n>0) the given date."""
    from datetime import date as _date
    d = _date.fromisoformat(date_str[:10])
    return (d + timedelta(days=n)).isoformat()


def _post_json(url: str, body: dict, timeout: int = 10) -> dict | None:
    """POST JSON to ESP API; return parsed response or None on failure."""
    import json as _json
    import urllib.request

    try:
        data = _json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **qfz_headers()},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read())
    except Exception as exc:
        logger.debug("ESP POST %s failed: %s", url, exc)
        return None


def _fetch_news(
    npp_base_url: str,
    tickers: list[str],
    start_utc: str,
    end_utc: str,
    limit: int = 12,
) -> list[dict]:
    """Fetch news articles from /esp/news/search.

    end_utc acts as the leak-prevention boundary — no articles after this time.
    """
    result = _post_json(
        f"{npp_base_url.rstrip('/')}/esp/news/search",
        {
            "tickers": tickers,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "limit": limit,
        },
    )
    return (result or {}).get("events", [])


def _fetch_econ_events(
    npp_base_url: str,
    start_date: str,
    end_date: str,
    now_utc: str,
    min_importance: str = "medium",
    limit: int = 30,
) -> list[dict]:
    """Fetch released economic events (up to now_utc — leak-safe)."""
    result = _post_json(
        f"{npp_base_url.rstrip('/')}/esp/calendar/econ",
        {
            "start_date": start_date,
            "end_date": end_date,
            "min_importance": min_importance,
            "now_utc": now_utc,
            "limit": limit,
        },
    )
    return (result or {}).get("events", [])


def _fetch_econ_upcoming(
    npp_base_url: str,
    start_date: str,
    end_date: str,
    min_importance: str = "medium",
    limit: int = 15,
) -> list[dict]:
    """Fetch upcoming (not yet released) economic events.

    No now_utc filter — these are publicly scheduled announcements; their
    existence is not a data leak.  Values and exact times are NOT shown.
    """
    result = _post_json(
        f"{npp_base_url.rstrip('/')}/esp/calendar/econ",
        {
            "start_date": start_date,
            "end_date": end_date,
            "min_importance": min_importance,
            "limit": limit,
        },
    )
    return (result or {}).get("events", [])


def _fetch_earnings_events(
    npp_base_url: str,
    tickers: list[str],
    start_date: str,
    end_date: str,
    now_utc: str,
    limit: int = 10,
) -> list[dict]:
    """Fetch earnings calendar events (already occurred or upcoming) up to now_utc."""
    result = _post_json(
        f"{npp_base_url.rstrip('/')}/esp/calendar/earnings",
        {
            "tickers": tickers,
            "start_date": start_date,
            "end_date": end_date,
            "now_utc": now_utc,
            "limit": limit,
        },
    )
    return (result or {}).get("events", [])


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_event(ev: dict) -> str:
    """Format a single ESP event as a compact line."""
    time_str = ev.get("time_utc", "")[:16].replace("T", " ")
    title = ev.get("title", "").strip()
    snippet = ev.get("snippet", "").strip()
    tickers = ev.get("tickers") or []
    ticker_str = f"[{','.join(tickers[:3])}] " if tickers else ""
    importance = ev.get("importance", "")
    imp_str = f"({importance}) " if importance else ""

    line = f"  • {time_str} {imp_str}{ticker_str}{title}"
    if snippet and snippet != title:
        # Truncate long snippets
        snippet = snippet[:120] + ("…" if len(snippet) > 120 else "")
        line += f"\n    {snippet}"
    return line


def _fmt_news_section(events: list[dict], header: str) -> str:
    if not events:
        return f"{header}:\n  (none)"
    lines = [f"{header}:"]
    for ev in events[:20]:
        lines.append(_fmt_event(ev))
    return "\n".join(lines)


def _parse_utc_dt(time_utc: str) -> datetime | None:
    """Parse a time_utc string into a UTC-aware datetime, or None on failure."""
    try:
        dt = datetime.fromisoformat(time_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _fmt_econ_section(
    past_events: list[dict],
    upcoming_events: list[dict],
    now_dt: datetime,
) -> str:
    """Format economic events as two separate blocks.

    Past block  — high-importance events already released before current_ts.
                  Shows importance, title, days-ago, actual and previous values.
                  (Safe to show — all already public.)
    Upcoming block — scheduled events not yet released; shows ONLY event name
                  and relative timing (days left).  No exact date, no forecast
                  value, no actual value — prevents data leaking.
                  Events from today that were released before current_ts are
                  shown in the past block, not here.
    """
    parts: list[str] = []

    # ── Past: last ~1 month, high importance ──────────────────────────────────
    if past_events:
        lines = ["Economic Events (past, high importance):"]
        for ev in past_events:
            title = ev.get("title", "").strip()
            importance = ev.get("importance", "")
            payload = ev.get("payload") or {}
            imp_str = f"({importance}) " if importance else ""
            ev_dt = _parse_utc_dt(ev.get("time_utc", ""))
            if ev_dt:
                delta_days = (now_dt - ev_dt).days
                timing = f"{delta_days}d ago" if delta_days > 0 else "released today"
            else:
                timing = "recent"
            line = f"  • {imp_str}{title} ({timing})"
            # Always show actual value — already released, no data leak
            actual = payload.get("actual")
            previous = payload.get("previous")
            if actual is not None:
                line += f" | actual={actual}"
            if previous is not None:
                line += f" prev={previous}"
            lines.append(line)
        parts.append("\n".join(lines))

    # ── Upcoming: no values leaked; today events already released go to past ──
    # Filter: only events strictly after now_dt (today-but-already-released
    # were captured by _fetch_econ_events with now_utc, so exclude duplicates)
    future_only = [
        ev for ev in upcoming_events
        if (_parse_utc_dt(ev.get("time_utc", "")) or datetime.min.replace(tzinfo=timezone.utc)) > now_dt
    ]
    if future_only:
        lines = ["Upcoming Economic Events (name & days-to-release only):"]
        for ev in future_only:
            title = ev.get("title", "").strip()
            importance = ev.get("importance", "")
            imp_str = f"({importance}) " if importance else ""
            ev_dt = _parse_utc_dt(ev.get("time_utc", ""))
            if ev_dt:
                delta_days = (ev_dt - now_dt).days
                if delta_days == 0:
                    timing = "later today"
                elif delta_days == 1:
                    timing = "in 1 day"
                else:
                    timing = f"in {delta_days} days"
            else:
                timing = "upcoming"
            lines.append(f"  • {imp_str}{title} — {timing}")
        parts.append("\n".join(lines))

    if not parts:
        return "Economic Events:\n  (none)"
    return "\n\n".join(parts)


def _fmt_earnings_section(events: list[dict], notice_only: bool = False) -> str:
    """Format earnings events.

    notice_only=True: Show only date/status as an FYI warning.
    notice_only=False: Show EPS estimates and surprises.
    """
    if not events:
        return ""
    lines = ["Earnings Events:"]
    for ev in events[:6]:
        time_str = ev.get("time_utc", "")[:10]
        tickers = ev.get("tickers") or []
        title = ev.get("title", "").strip()
        status = ev.get("status", "")
        importance = ev.get("importance", "")
        payload = ev.get("payload") or {}
        imp_str = f"({importance}) " if importance else ""

        if notice_only:
            lines.append(
                f"  • {time_str} {imp_str}{title} [{status}]"
                f"\n    ⚠ NOTICE: Earnings event nearby — do NOT speculate on outcome direction."
            )
        else:
            detail = ""
            est_eps = payload.get("estimated_eps")
            act_eps = payload.get("actual_eps")
            surp_pct = payload.get("eps_surprise_percent")
            if est_eps is not None:
                detail += f" | est EPS ${est_eps:.2f}"
            if act_eps is not None:
                detail += f" | actual ${act_eps:.2f}"
                if surp_pct is not None:
                    detail += f" ({surp_pct:+.1f}%)"
            lines.append(f"  • {time_str} {imp_str}{title} [{status}]{detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_intraday_news_context(
    npp_base_url: str,
    current_ts: str,
    universe_tickers: list[str] | None = None,
    lookback_days: int = 3,
) -> str:
    """Build news/event context for 0DTE intraday strategy.

    Includes:
    1. Recent news for NVDA, QQQ, SPY, META, AAPL, MSFT, GOOG + strategy universe
       (last 3 days up to current_ts — no future data)
    2. Economic events released before current_ts today
       (e.g. 08:30 ET CPI release only appears if current_ts is after 08:30 ET)
    3. FANG+ earnings in a ±7 day window around today
    """
    now_utc = _to_utc_iso(current_ts)
    now_dt = _parse_utc_dt(now_utc) or datetime.now(timezone.utc)
    today = _date_str(current_ts)
    three_days_ago = _days_offset(today, -max(1, min(lookback_days, 30)))
    one_month_ago = _days_offset(today, -30)
    seven_days_ahead = _days_offset(today, 7)
    one_month_ahead = _days_offset(today, 30)
    seven_days_back = _days_offset(today, -7)

    # All tickers: fixed market movers + strategy universe (deduped, order preserved)
    all_tickers: list[str] = list(dict.fromkeys(INTRADAY_MARKET_TICKERS + (universe_tickers or [])))

    # 1. News (last 3 days, ends at current_ts — 20 items)
    news_events = _fetch_news(
        npp_base_url,
        tickers=all_tickers,
        start_utc=f"{three_days_ago}T00:00:00Z",
        end_utc=now_utc,
        limit=20,
    )

    # 2a. Past economic events: last 1 month, HIGH importance only
    past_econ = _fetch_econ_events(
        npp_base_url,
        start_date=one_month_ago,
        end_date=today,
        now_utc=now_utc,
        min_importance="high",
    )

    # 2b. Upcoming economic events: next 1 month (no values shown)
    upcoming_econ = _fetch_econ_upcoming(
        npp_base_url,
        start_date=today,
        end_date=one_month_ahead,
        min_importance="medium",
    )

    # 3. FANG+ earnings ±7 days
    fang_tickers = list(dict.fromkeys(FANG_PLUS_TICKERS + (universe_tickers or [])))
    earnings_events = _fetch_earnings_events(
        npp_base_url,
        tickers=fang_tickers,
        start_date=seven_days_back,
        end_date=seven_days_ahead,
        now_utc=now_utc,
    )

    sections = [
        _fmt_news_section(news_events, f"Market News (last {max(1, min(lookback_days, 30))} days)"),
        _fmt_econ_section(past_econ, upcoming_econ, now_dt),
    ]
    if earnings_events:
        sections.append(_fmt_earnings_section(earnings_events, notice_only=False))

    return "\n\n".join(sections)


def build_overlay_news_context(
    npp_base_url: str,
    current_ts: str,
    underlying: str,
    lookback_days: int = 7,
) -> str:
    """Build news/event context for overlay strategies (profit/hedge).

    Includes:
    1. News for SPY + QQQ + the specific underlying ticker (last lookback_days days)
    2. Recent economic events (last lookback_days days up to current_ts)
    3. Earnings notice for the underlying company only (not for ETFs like QQQ/SPY)
       — shown as a warning only, NOT as a directional signal

    Uses current_ts as the leak-prevention boundary for all queries.
    """
    now_utc = _to_utc_iso(current_ts)
    now_dt = _parse_utc_dt(now_utc) or datetime.now(timezone.utc)
    today = _date_str(current_ts)
    start_date = _days_offset(today, -lookback_days)
    one_month_ago = _days_offset(today, -30)
    one_month_ahead = _days_offset(today, 30)

    # News tickers: always SPY + QQQ + the underlying (deduped)
    news_tickers = list(dict.fromkeys(["SPY", "QQQ", underlying]))

    # 1. News (last lookback_days days, ends at current_ts — 20 items)
    news_events = _fetch_news(
        npp_base_url,
        tickers=news_tickers,
        start_utc=f"{start_date}T00:00:00Z",
        end_utc=now_utc,
        limit=20,
    )

    # 2a. Past economic events: last 1 month, HIGH importance only
    past_econ = _fetch_econ_events(
        npp_base_url,
        start_date=one_month_ago,
        end_date=today,
        now_utc=now_utc,
        min_importance="high",
    )

    # 2b. Upcoming economic events: next 1 month (no values shown)
    upcoming_econ = _fetch_econ_upcoming(
        npp_base_url,
        start_date=today,
        end_date=one_month_ahead,
        min_importance="medium",
    )

    # 3. Earnings notice for the specific underlying — but NOT for ETFs
    IS_ETF = underlying.upper() in {"QQQ", "SPY", "IWM", "DIA", "XLF", "XLE", "XLK"}
    earnings_events = []
    if not IS_ETF:
        earnings_events = _fetch_earnings_events(
            npp_base_url,
            tickers=[underlying],
            start_date=_days_offset(today, -7),
            end_date=one_month_ahead,
            now_utc=now_utc,
        )

    ticker_label = ", ".join(news_tickers)
    sections = [
        _fmt_news_section(news_events, f"News ({ticker_label}, last {lookback_days}d)"),
        _fmt_econ_section(past_econ, upcoming_econ, now_dt),
    ]
    if earnings_events:
        sections.append(_fmt_earnings_section(earnings_events, notice_only=True))

    return "\n\n".join(sections)


def fetch_earnings_pre_news(
    npp_base_url: str,
    current_ts: str,
    ticker: str,
    start_date: str,
    limit: int = 8,
) -> list[dict]:
    """Fetch news for earnings pre-phase context (T-2 to current_date).

    Returns normalised dicts: {title, published, publisher, snippet, keywords}.
    Uses current_ts as the end boundary to prevent data leaking within the day.
    """
    now_utc = _to_utc_iso(current_ts)
    events = _fetch_news(
        npp_base_url,
        tickers=[ticker],
        start_utc=f"{start_date}T00:00:00Z",
        end_utc=now_utc,
        limit=limit,
    )
    result = []
    for ev in events:
        payload = ev.get("payload") or {}
        result.append({
            "title": ev.get("title", ""),
            "published": ev.get("time_utc", "")[:10],
            "publisher": payload.get("publisher", ""),
            "snippet": ev.get("snippet", ""),
            "keywords": payload.get("keywords") or [],
        })
    return result
