# trading/tools/context_loader.py
"""
Context loader for news and earnings data.

Provides news and earnings context for strategy decisions.
"""
import json
import logging
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

import requests as _requests

logger = logging.getLogger(__name__)

NEWS_DIR = os.environ.get("LIVEOPTION_NEWS_DIR", ".liveoption/data/news")
EARNINGS_DB = os.environ.get("LIVEOPTION_EARNINGS_DB", ".liveoption/data/earnings.sqlite3")


class ContextLoader:
    """
    Loads optional file-based news and earnings context for a date window.

    ssh_host="your-data-host": fetch from an explicitly configured SSH host.
    ssh_host="local": access data files and APIs directly on the local machine.
    ssh_host=None: returns empty data (for unit tests / dry-run).
    """

    def __init__(self, ssh_host: str | None = "local"):
        self.ssh_host = ssh_host
        self.news_errors: int = 0
        self.earnings_errors: int = 0

    def load_news_window(
        self, end_date: str, lookback_days: int, tickers: list[str]
    ) -> list[dict]:
        """Return filtered news articles from [end_date - lookback_days, end_date]."""
        end = date.fromisoformat(end_date)
        articles = []
        for i in range(lookback_days, -1, -1):
            d = (end - timedelta(days=i)).isoformat()
            articles.extend(self._fetch_news_file(d))
        return self._filter_news(articles, tickers)

    def load_upcoming_earnings(
        self, from_date: str, to_date: str, tickers: list[str]
    ) -> list[dict]:
        """Return earnings events between from_date and to_date for universe tickers."""
        if self.ssh_host is None:
            return []
        if self.ssh_host == "local":
            return self._load_earnings_local(from_date, to_date, tickers)
        return self._load_earnings_ssh(from_date, to_date, tickers)

    def _load_earnings_local(
        self, from_date: str, to_date: str, tickers: list[str]
    ) -> list[dict]:
        """Load earnings directly from the local SQLite database."""
        try:
            import sqlite3

            tickers_sql = ",".join(f"'{t}'" for t in tickers)
            query = (
                "SELECT ticker, date, time, actual_eps, estimated_eps, "
                "eps_surprise_percent "
                f"FROM earnings WHERE ticker IN ({tickers_sql}) "
                f"AND date >= '{from_date}' AND date <= '{to_date}' ORDER BY date"
            )
            conn = sqlite3.connect(EARNINGS_DB)
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(query).fetchall()]
            conn.close()
            return rows
        except Exception as e:
            self.earnings_errors += 1
            logger.warning(f"Local earnings query failed for {from_date}\u2192{to_date}: {e}")
            return []

    def _load_earnings_ssh(
        self, from_date: str, to_date: str, tickers: list[str]
    ) -> list[dict]:
        """Load earnings via SSH + sqlite3 CLI."""
        tickers_sql = ",".join(f"'{t}'" for t in tickers)
        query = (
            f"SELECT ticker,date,time,actual_eps,estimated_eps,eps_surprise_percent "
            f"FROM earnings WHERE ticker IN ({tickers_sql}) "
            f"AND date >= '{from_date}' AND date <= '{to_date}' ORDER BY date"
        )
        cmd = ["ssh", self.ssh_host, f'sqlite3 {EARNINGS_DB} -json "{query}"']
        try:
            out = subprocess.check_output(cmd, timeout=15).decode()
            return json.loads(out) if out.strip() else []
        except Exception as e:
            self.earnings_errors += 1
            logger.warning(f"earnings query failed for {from_date}\u2192{to_date}: {e}")
            return []

    def build_context_stub(self, date: str, tickers: list[str]) -> dict:
        """Return empty-but-valid context dict (for tests without SSH)."""
        return {"news": [], "earnings": [], "date": date, "tickers": tickers}

    def _fetch_news_file(self, date_str: str) -> list[dict]:
        if self.ssh_host is None:
            return []
        remote_path = f"{NEWS_DIR}/{date_str}.json"
        local = Path(remote_path)
        if self.ssh_host == "local" or local.exists():
            if local.exists():
                with open(local) as f:
                    return json.load(f).get("results", [])
            if self.ssh_host == "local":
                logger.warning(
                    f"Local news file not found: {remote_path} — "
                    "returning empty context. Configure LIVEOPTION_NEWS_DIR "
                    "or an explicit context_host to read your data."
                )
            return []
        try:
            out = subprocess.check_output(
                ["ssh", self.ssh_host, f"cat {remote_path}"], timeout=15
            ).decode()
            return json.loads(out).get("results", [])
        except Exception as e:
            self.news_errors += 1
            logger.warning(f"news fetch failed for {date_str}: {e}")
            return []

    def _filter_news(self, articles: list[dict], tickers: list[str]) -> list[dict]:
        """Keep articles tagged with any universe ticker, or untagged (macro)."""
        ticker_set = set(tickers)
        return [
            a for a in articles
            if not set(a.get("tickers", [])) or set(a.get("tickers", [])) & ticker_set
        ]
