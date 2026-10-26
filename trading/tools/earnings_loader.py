"""Earnings data loader for the Earnings Bet scenario.

Loads earnings data from the ESP (Events & News, formerly NPP) HTTP API,
served by the QFinZero unified hub at ``{hub}/svc/esp``.
Unavailable ESP data is reported through the loader's error counter.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from trading.core.endpoints import DEFAULT_ESP_URL, qfz_headers

logger = logging.getLogger(__name__)

NPP_DEFAULT_URL = DEFAULT_ESP_URL  # legacy name — NPP is now ESP

# BMO tickers report before market open; all others report after market close.
# Used as fallback when time_utc is not available from NPP.
BMO_TICKERS = {"JPM", "TSM", "UNH", "PG", "BRK.B"}

# BRK.B in the Benzinga DB maps to BRKB for UPQ option queries.
TICKER_TO_UPQ = {"BRK.B": "BRKB"}
UPQ_TO_TICKER = {v: k for k, v in TICKER_TO_UPQ.items()}

# Map NPP importance strings to integers (for backward compat)
_IMPORTANCE_MAP = {"high": 5, "medium": 3, "low": 1}


def _map_ticker_to_upq(ticker: str) -> str:
    """Map a Benzinga DB ticker to its UPQ equivalent."""
    return TICKER_TO_UPQ.get(ticker, ticker)


def _map_upq_to_ticker(upq_symbol: str) -> str:
    """Map a UPQ symbol back to its Benzinga DB ticker."""
    return UPQ_TO_TICKER.get(upq_symbol, upq_symbol)


def _is_bmo_from_utc(time_utc: str, ticker: str) -> bool:
    """Determine if an earnings event is before market open.

    Parses time_utc and checks if the ET time is before noon.
    Falls back to BMO_TICKERS heuristic if parsing fails.
    """
    if time_utc:
        try:
            dt = datetime.fromisoformat(time_utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            # UTC < 16:00 → before noon ET even in summer (EDT = UTC-4)
            return dt.hour < 16
        except (ValueError, AttributeError):
            pass
    return ticker in BMO_TICKERS


def _extract_date_time(time_utc: str) -> tuple[str, str]:
    """Extract YYYY-MM-DD date and HH:MM:SS time (ET) from a UTC ISO string.

    Returns ("", "") on failure.
    """
    if not time_utc:
        return "", ""
    try:
        dt = datetime.fromisoformat(time_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Approximate ET offset: use UTC-5 (EST) as a conservative approximation.
        # The exact offset doesn't matter for our BMO/AMC detection.
        et_dt = dt.astimezone(timezone(timedelta(hours=-5)))
        return et_dt.strftime("%Y-%m-%d"), et_dt.strftime("%H:%M:%S")
    except (ValueError, AttributeError):
        return "", ""


class EarningsLoader:
    """Load earnings data from the NPP (News Pushing Pipeline) API.

    Parameters
    ----------
    npp_base_url : str
        Base URL for the ESP service (default: QFinZero hub /svc/esp).
    """

    def __init__(self, npp_base_url: str = NPP_DEFAULT_URL):
        self.npp_base_url = npp_base_url.rstrip("/")
        self._cache: dict[str, list[dict]] = {}
        self.errors: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_earnings_calendar(
        self,
        tickers: list[str],
        start: str,
        end: str,
    ) -> list[dict]:
        """Return earnings events sorted by date.

        Each event dict contains::

            {
                "ticker": str,                    # DB ticker (e.g. "BRK.B")
                "upq_ticker": str,                # UPQ ticker (e.g. "BRKB")
                "date": str,                      # YYYY-MM-DD (ET)
                "time": str,                      # HH:MM:SS (ET) or empty
                "time_utc": str,                  # original UTC ISO string
                "fiscal_period": str,
                "fiscal_year": str | int,
                "estimated_eps": float | None,
                "actual_eps": float | None,
                "previous_eps": float | None,
                "eps_surprise_percent": float | None,
                "actual_revenue": float | None,
                "estimated_revenue": float | None,
                "revenue_surprise_percent": float | None,
                "is_bmo": bool,
                "importance": int | None,         # 1=low, 3=medium, 5=high
                "snippet": str,                   # short summary from NPP
                "status": str,                    # "scheduled" | "occurred" | "updated"
            }
        """
        # Normalise tickers (UPQ → DB ticker for lookup, then back)
        db_tickers = [_map_upq_to_ticker(t) for t in tickers]
        cache_key = f"{','.join(sorted(db_tickers))}|{start}|{end}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        raw = self._query_npp(db_tickers, start, end)
        events = self._normalize_events(raw)
        events.sort(key=lambda e: e["date"])
        self._cache[cache_key] = events
        return events

    def get_event_context(self, ticker: str, earnings_date: str) -> dict:
        """Get context for a single earnings event including historical reactions."""
        db_ticker = _map_upq_to_ticker(ticker)
        events = self._query_npp([db_ticker], earnings_date, earnings_date)
        event = self._normalize_events(events)[0] if events else {}
        historical = self.get_historical_reactions(ticker, earnings_date, n=4)
        return {"event": event, "historical_reactions": historical}

    def get_historical_reactions(
        self,
        ticker: str,
        before_date: str,
        n: int = 4,
    ) -> list[dict]:
        """Get N previous earnings reactions for the ticker (most recent first)."""
        db_ticker = _map_upq_to_ticker(ticker)
        end = (date.fromisoformat(before_date) - timedelta(days=1)).isoformat()
        start = (date.fromisoformat(before_date) - timedelta(days=400)).isoformat()
        raw = self._query_npp([db_ticker], start, end)
        events = self._normalize_events(raw)
        events.sort(key=lambda e: e["date"], reverse=True)
        return events[:n]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query_npp(
        self, tickers: list[str], start: str, end: str
    ) -> list[dict[str, Any]]:
        """Query /esp/calendar/earnings with pagination, return raw ESP events."""
        import urllib.request
        import json as _json

        url = f"{self.npp_base_url}/esp/calendar/earnings"
        all_events: list[dict] = []
        cursor: str | None = None

        while True:
            body: dict[str, Any] = {
                "tickers": tickers,
                "start_date": start,
                "end_date": end,
                "limit": 200,
            }
            if cursor:
                body["cursor"] = cursor

            data = _json.dumps(body).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", **qfz_headers()},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = _json.loads(resp.read())
            except Exception as e:
                self.errors += 1
                logger.warning("ESP earnings query failed: %s", e)
                return []

            all_events.extend(result.get("events", []))
            cursor = result.get("next_cursor")
            if not cursor:
                break

        return all_events

    def _normalize_events(self, raw: list[dict[str, Any]]) -> list[dict]:
        """Convert NPP event dicts to the standardised earnings event format."""
        events = []
        for ev in raw:
            payload = ev.get("payload") or {}
            time_utc = ev.get("time_utc", "")
            et_date, et_time = _extract_date_time(time_utc)

            # Derive ticker from tickers list
            tickers_list = ev.get("tickers") or []
            ticker = tickers_list[0] if tickers_list else ""
            # Normalise special tickers (BRKB → BRK.B lookup)
            ticker = _map_upq_to_ticker(ticker)

            imp_raw = ev.get("importance")
            importance = (
                _IMPORTANCE_MAP.get(imp_raw, 3)
                if isinstance(imp_raw, str)
                else (int(imp_raw) if imp_raw is not None else None)
            )

            events.append({
                "ticker": ticker,
                "upq_ticker": _map_ticker_to_upq(ticker),
                "date": et_date,
                "time": et_time,
                "time_utc": time_utc,
                "fiscal_period": payload.get("fiscal_period", ""),
                "fiscal_year": payload.get("fiscal_year", ""),
                "estimated_eps": _safe_float(payload.get("estimated_eps")),
                "actual_eps": _safe_float(payload.get("actual_eps")),
                "previous_eps": _safe_float(payload.get("previous_eps")),
                "eps_surprise_percent": _safe_float(payload.get("eps_surprise_percent")),
                "actual_revenue": _safe_float(payload.get("actual_revenue")),
                "estimated_revenue": _safe_float(payload.get("estimated_revenue")),
                "revenue_surprise_percent": _safe_float(payload.get("revenue_surprise_percent")),
                "is_bmo": _is_bmo_from_utc(time_utc, ticker),
                "importance": importance,
                "snippet": ev.get("snippet", ""),
                "status": ev.get("status", ""),
                "event_id": ev.get("event_id", ""),
            })
        return events


def fetch_daily_bars(
    upq_base_url: str,
    ticker: str,
    start: str,
    end: str,
) -> list[dict]:
    """Fetch daily OHLCV bars from UPQ for a ticker over a date range.

    Returns a list of dicts: {date, open, high, low, close, volume}.
    Returns [] on failure.
    """
    import urllib.request
    import urllib.parse
    import json as _json

    upq_base_url = upq_base_url.rstrip("/")
    params = urllib.parse.urlencode({
        "tickers": ticker,
        "start": start,
        "end": end,
        "fields": "ticker,date,open,high,low,close,volume",
    })
    url = f"{upq_base_url}/stock/daily?{params}"
    try:
        req = urllib.request.Request(url, headers=qfz_headers())
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = _json.loads(resp.read())
        return sorted(rows, key=lambda r: r.get("date", ""))
    except Exception as e:
        logger.warning("UPQ daily bars failed for %s %s→%s: %s", ticker, start, end, e)
        return []


def fetch_npp_news(
    npp_base_url: str,
    ticker: str,
    start_date: str,
    end_date: str,
    limit: int = 8,
) -> list[dict]:
    """Fetch recent news for a ticker from ESP in a date window.

    Converts YYYY-MM-DD dates to UTC ISO timestamps for the query.
    Returns list of dicts: {title, published, publisher, snippet, keywords}.
    """
    import urllib.request
    import json as _json

    npp_base_url = npp_base_url.rstrip("/")
    # Use T00:00:00Z / T23:59:59Z to cover the full day range
    start_utc = f"{start_date}T00:00:00Z"
    end_utc = f"{end_date}T23:59:59Z"

    body = _json.dumps({
        "tickers": [ticker],
        "start_utc": start_utc,
        "end_utc": end_utc,
        "limit": limit,
    }).encode()
    req = urllib.request.Request(
        f"{npp_base_url}/esp/news/search",
        data=body,
        headers={"Content-Type": "application/json", **qfz_headers()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
        news = []
        for ev in result.get("events", []):
            payload = ev.get("payload") or {}
            news.append({
                "title": ev.get("title", ""),
                "published": ev.get("time_utc", "")[:10],
                "publisher": payload.get("publisher", ""),
                "snippet": ev.get("snippet", ""),
                "keywords": payload.get("keywords") or [],
            })
        return news
    except Exception as e:
        logger.warning("ESP news search failed for %s: %s", ticker, e)
        return []


def enrich_historical_with_price_moves(
    upq_base_url: str,
    ticker: str,
    historical_events: list[dict],
) -> list[dict]:
    """Add price move data to historical earnings events.

    For each event, fetches daily bars for T-5 to T+3 around the earnings date
    and computes:
      - pre_move_pct:  (T-1 close - T-5 close) / T-5 close  (run-up/sell-off)
      - gap_pct:       (T open - T-1 close) / T-1 close       (open gap on earnings day)
      - post_move_pct: (T+2 close - T-1 close) / T-1 close    (full post-earnings move)

    Returns the same list with these fields added in-place.
    """
    enriched = []
    for ev in historical_events:
        ev = dict(ev)  # copy
        earnings_date = ev.get("date", "")
        if not earnings_date:
            enriched.append(ev)
            continue

        ed = date.fromisoformat(earnings_date)
        # Wide window: T-7 to T+4
        fetch_start = (ed - timedelta(days=10)).isoformat()
        fetch_end = (ed + timedelta(days=5)).isoformat()
        bars = fetch_daily_bars(upq_base_url, ticker, fetch_start, fetch_end)
        bars_by_date = {b["date"]: b for b in bars}

        # Find the trading days adjacent to earnings date
        def bar_before(d: date, n: int) -> dict | None:
            """n-th trading day before d."""
            cur = d - timedelta(days=1)
            found = 0
            while (d - cur).days <= 14:
                if cur.isoformat() in bars_by_date:
                    found += 1
                    if found == n:
                        return bars_by_date[cur.isoformat()]
                cur -= timedelta(days=1)
            return None

        def bar_after(d: date, n: int) -> dict | None:
            """n-th trading day after d."""
            cur = d + timedelta(days=1)
            found = 0
            while (cur - d).days <= 10:
                if cur.isoformat() in bars_by_date:
                    found += 1
                    if found == n:
                        return bars_by_date[cur.isoformat()]
                cur += timedelta(days=1)
            return None

        t_minus_1 = bar_before(ed, 1)
        t_minus_5 = bar_before(ed, 5)
        t_bar = bars_by_date.get(earnings_date) or bar_after(ed, 0)
        t_plus_2 = bar_after(ed, 2)

        def pct(a, b) -> float | None:
            if a is None or b is None or b == 0:
                return None
            return round((a - b) / b * 100, 2)

        ev["price_t_minus_1"] = t_minus_1["close"] if t_minus_1 else None
        ev["price_earnings_day"] = t_bar["close"] if t_bar else None
        ev["price_t_plus_2"] = t_plus_2["close"] if t_plus_2 else None
        ev["pre_move_pct"] = pct(
            t_minus_1["close"] if t_minus_1 else None,
            t_minus_5["close"] if t_minus_5 else None,
        )
        ev["gap_pct"] = pct(
            t_bar["open"] if t_bar else None,
            t_minus_1["close"] if t_minus_1 else None,
        )
        ev["post_move_pct"] = pct(
            t_plus_2["close"] if t_plus_2 else None,
            t_minus_1["close"] if t_minus_1 else None,
        )
        enriched.append(ev)
    return enriched


def fetch_price_context(
    upq_base_url: str,
    ticker: str,
    as_of_date: str,
    lookback_days: int = 45,
) -> dict:
    """Fetch recent daily K-bars with MA5, MA15, MA30 for LLM context.

    Returns a dict with:
        bars: list of {date, open, high, low, close, volume, ma5, ma15, ma30}
        summary_text: formatted text ready for LLM prompt injection
    """
    from datetime import timedelta
    ed = date.fromisoformat(as_of_date)
    # Fetch extra days to have enough history for MA30
    fetch_start = (ed - timedelta(days=lookback_days + 45)).isoformat()
    # Use as_of_date - 1 day to avoid data leaking (don't include today's close)
    fetch_end = (ed - timedelta(days=1)).isoformat()
    bars = fetch_daily_bars(upq_base_url, ticker, fetch_start, fetch_end)
    if not bars:
        return {"bars": [], "summary_text": "  (no price data available)"}

    closes = [b["close"] for b in bars]
    # Compute MAs
    for i, b in enumerate(bars):
        b["ma5"] = _ma(closes, i, 5)
        b["ma15"] = _ma(closes, i, 15)
        b["ma30"] = _ma(closes, i, 30)

    # Only return the last ~22 trading days (1 month) for prompt
    recent = bars[-22:]

    # Build text summary
    lines = ["| Date | Open | High | Low | Close | Volume | MA5 | MA15 | MA30 |",
             "|------|------|------|-----|-------|--------|-----|------|------|"]
    for b in recent:
        ma5_s = f"{b['ma5']:.2f}" if b["ma5"] is not None else "—"
        ma15_s = f"{b['ma15']:.2f}" if b["ma15"] is not None else "—"
        ma30_s = f"{b['ma30']:.2f}" if b["ma30"] is not None else "—"
        lines.append(
            f"| {b['date']} | {b['open']:.2f} | {b['high']:.2f} | {b['low']:.2f} "
            f"| {b['close']:.2f} | {b.get('volume', 0):,.0f} "
            f"| {ma5_s} | {ma15_s} | {ma30_s} |"
        )

    # Key stats
    if len(recent) >= 2:
        last = recent[-1]
        prev = recent[-2]
        chg_1d = (last["close"] - prev["close"]) / prev["close"] * 100
        chg_5d = None
        if len(recent) >= 6:
            chg_5d = (last["close"] - recent[-6]["close"]) / recent[-6]["close"] * 100
        chg_20d = None
        if len(recent) >= 21:
            chg_20d = (last["close"] - recent[-21]["close"]) / recent[-21]["close"] * 100

        stats = [f"\nLatest close: ${last['close']:.2f} ({last['date']})"]
        stats.append(f"1-day change: {chg_1d:+.2f}%")
        if chg_5d is not None:
            stats.append(f"5-day change: {chg_5d:+.2f}%")
        if chg_20d is not None:
            stats.append(f"20-day change: {chg_20d:+.2f}%")
        if last["ma5"] and last["ma15"] and last["ma30"]:
            trend = []
            if last["close"] > last["ma5"]:
                trend.append("above MA5")
            else:
                trend.append("below MA5")
            if last["close"] > last["ma30"]:
                trend.append("above MA30")
            else:
                trend.append("below MA30")
            stats.append(f"Trend: {', '.join(trend)}")
        lines.extend(stats)

    return {"bars": recent, "summary_text": "\n".join(lines)}


def build_daily_price_summary(
    upq_base_url: str,
    ticker: str,
    as_of_date: str,
    today_open: float | None = None,
) -> str:
    """Build a compact daily price summary for LLM prompts.

    Produces the same compact format as the intraday strategy's daily context:
      - MA5 / MA15 / MA30 on closes
      - Trend vs short-term MAs
      - 20-bar high/low range + span %
      - Recent 3 closes
      - Optional today's open-vs-prev-close gap (when today_open is provided)
      - Raw OHLC for last 10 trading days (day -1 = most recent available)

    as_of_date is the current simulation date (YYYY-MM-DD); yesterday's close is
    used as the end boundary to avoid data leaking.
    """
    from datetime import timedelta

    ed = date.fromisoformat(as_of_date[:10])
    yesterday = (ed - timedelta(days=1)).isoformat()
    fetch_start = (ed - timedelta(days=80)).isoformat()  # buffer for MA30 + weekends

    bars = fetch_daily_bars(upq_base_url, ticker, fetch_start, yesterday)
    if not bars:
        return f"{ticker} daily: (no data available)"

    closes = [b["close"] for b in bars]
    highs  = [b["high"]  for b in bars]
    lows   = [b["low"]   for b in bars]
    n = len(closes)

    def _tail_ma(period: int) -> float | None:
        if n < period:
            return None
        return round(sum(closes[-period:]) / period, 2)

    ma5  = _tail_ma(5)
    ma15 = _tail_ma(15)
    ma30 = _tail_ma(30)
    prev_close = closes[-1]

    ref = today_open if today_open is not None else prev_close
    if ma5 is not None and ref < ma5:
        trend = "bearish (price below short-term MAs)"
    elif ma5 is not None and ref > ma5:
        trend = "bullish (price above short-term MAs)"
    else:
        trend = "neutral"

    range_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    range_low  = min(lows[-20:])  if len(lows)  >= 20 else min(lows)
    r_pct = (range_high - range_low) / range_low * 100 if range_low > 0 else 0

    recent_closes = [f"${c:.2f}" for c in closes[-3:]]

    ma_str = "  ".join(
        f"MA{p}=${v:.2f}" if v is not None else f"MA{p}=N/A"
        for p, v in [(5, ma5), (15, ma15), (30, ma30)]
    )

    block = [
        f"{ticker} Daily Summary (as of {yesterday}):",
        f"  {ma_str}",
        f"  Trend: {trend}",
        f"  20-bar range: ${range_low:.2f} - ${range_high:.2f} ({r_pct:.1f}%)",
        f"  Recent closes: [{', '.join(recent_closes)}]",
    ]

    if today_open is not None and prev_close > 0:
        gap_pct = (today_open - prev_close) / prev_close * 100
        direction = "gap-up" if gap_pct > 0.1 else ("gap-down" if gap_pct < -0.1 else "flat open")
        block.append(
            f"  Gap today: open=${today_open:.2f} vs prev_close=${prev_close:.2f} "
            f"({gap_pct:+.2f}%, {direction})"
        )

    last10 = bars[-10:]
    n_days = len(last10)
    block.append(f"  Daily OHLC (last {n_days} trading days, day -1=yesterday):")
    for i, b in enumerate(reversed(last10)):
        block.append(
            f"    day {-(i + 1):3d}: {b['date']}  "
            f"O={b['open']:.2f}  H={b['high']:.2f}  L={b['low']:.2f}  C={b['close']:.2f}"
        )

    return "\n".join(block)


def _ma(values: list[float], idx: int, period: int) -> float | None:
    """Compute simple moving average at index idx with given period."""
    if idx < period - 1:
        return None
    window = values[idx - period + 1: idx + 1]
    return sum(window) / len(window)


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
