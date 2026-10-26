"""OutputManager — structured output directories per task run.

Directory layout:
  {base_dir}/{model_name}/{task_type}/{context_path}/{timestamp}/
  e.g. output/deepseek-v3/earnings/AAPL/Q1_FY2025/20250408_143022/

Each run gets its own timestamped folder so multiple runs don't collide.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _make_timestamp() -> str:
    """Generate a compact timestamp string: YYYYMMDD_HHMMSS."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


class OutputManager:
    """Manages structured output directories per task run."""

    def __init__(self, base_dir: str, model_name: str, task_type: str, context_path: str = "") -> None:
        self._base = Path(base_dir)
        self._model = model_name
        self._task = task_type
        parts = [base_dir, model_name, task_type]
        if context_path:
            parts.append(context_path)
        # Add timestamp subfolder for run isolation
        parts.append(_make_timestamp())
        self.run_dir = Path(*parts)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.llm_calls_dir = self.run_dir / "llm_calls"

    def get_trades_path(self) -> Path:
        return self.run_dir / "trades.jsonl"

    def get_market_path(self) -> Path:
        return self.run_dir / "market.jsonl"

    def get_portfolio_path(self) -> Path:
        return self.run_dir / "portfolio.jsonl"

    def get_llm_calls_dir(self) -> Path:
        self.llm_calls_dir.mkdir(parents=True, exist_ok=True)
        return self.llm_calls_dir

    def get_summary_path(self) -> Path:
        return self.run_dir / "summary.json"

    # -- Factory methods --

    @staticmethod
    def for_intraday(base_dir: str, model: str, date: str, ticker: str = "") -> OutputManager:
        context = f"{ticker}/{date}" if ticker else date
        return OutputManager(base_dir, model, "intraday", context)

    @staticmethod
    def for_earnings(base_dir: str, model: str, ticker: str, period: str) -> OutputManager:
        return OutputManager(base_dir, model, "earnings", f"{ticker}/{period}")

    @staticmethod
    def for_overlay(base_dir: str, model: str, ticker: str, mode: str) -> OutputManager:
        return OutputManager(base_dir, model, "overlay", f"{ticker}/{mode}")

    @staticmethod
    def for_weekly(base_dir: str, model: str, week_label: str = "") -> OutputManager:
        return OutputManager(base_dir, model, "weekly", week_label)

    @staticmethod
    def for_generic(base_dir: str, model: str, run_id: str = "") -> OutputManager:
        return OutputManager(base_dir, model, "runs", run_id)
