"""CaseRunner — iterate over cases from a cases YAML, running each independently."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from trading.core.config import (
    CasesConfig,
    CaseEntry,
    ModelEntry,
    ModelsConfig,
    ServicesConfig,
    RunConfig,
    load_cases_config,
    load_config,
    resolve_model_entry,
)
from trading.output.manager import OutputManager
from trading.core.endpoints import qfz_headers
from trading.runner import TradingRunner

logger = logging.getLogger(__name__)

# Map case mode → overlay strategy name
_OVERLAY_MODE_TO_STRATEGY = {
    "hedge": "overlay_hedge",
    "profit": "overlay_profit",
}


def _trading_days_offset(d: date, offset: int) -> str:
    """Approximate trading day offset (skips weekends)."""
    current = d
    step = 1 if offset > 0 else -1
    remaining = abs(offset)
    while remaining > 0:
        current += timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    return current.isoformat()


def query_earnings_cases_from_npp(
    strategy_config_path: str,
    services_config: ServicesConfig,
) -> CasesConfig:
    """Query ESP (formerly NPP) for earnings events and build one CaseEntry per event.

    Reads universe + date range from the strategy config. For each event
    found, computes the exact T-2/T+2 trading-day window as the episode
    start/end so the runner only covers the relevant period.

    Returns a CasesConfig ready to pass directly to CaseRunner.
    """
    import urllib.request
    import json as _json

    config = load_config(strategy_config_path)
    tickers = config.experiment.universe
    start_date = config.experiment.start[:10]
    end_date = config.experiment.end[:10]
    npp_url = services_config.npp_base_url.rstrip("/")

    logger.info(
        "Querying ESP for earnings events: %d tickers, %s → %s",
        len(tickers), start_date, end_date,
    )

    # Query ESP with pagination
    all_events: list[dict] = []
    cursor: str | None = None
    while True:
        body: dict = {
            "tickers": tickers,
            "start_date": start_date,
            "end_date": end_date,
            "limit": 200,
        }
        if cursor:
            body["cursor"] = cursor
        req = urllib.request.Request(
            f"{npp_url}/esp/calendar/earnings",
            data=_json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **qfz_headers()},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
        all_events.extend(result.get("events", []))
        cursor = result.get("next_cursor")
        if not cursor:
            break

    logger.info("ESP returned %d earnings events", len(all_events))

    # Build one CaseEntry per event
    cases: list[CaseEntry] = []
    for ev in all_events:
        payload = ev.get("payload") or {}
        tickers_list = ev.get("tickers") or []
        ticker = tickers_list[0] if tickers_list else ""
        if not ticker:
            continue

        # Derive earnings date (ET) from time_utc
        time_utc = ev.get("time_utc", "")
        earnings_date = _earnings_date_et(time_utc) or ev.get("time_utc", "")[:10]
        if not earnings_date:
            continue

        # T-2 / T+2 trading-day window
        ed = date.fromisoformat(earnings_date)
        ep_start = _trading_days_offset(ed, -2)
        ep_end = _trading_days_offset(ed, +2)

        fiscal_period = ""
        fp = payload.get("fiscal_period", "")
        fy = payload.get("fiscal_year", "")
        if fp and fy:
            fiscal_period = f"{fp}_FY{fy}"
        elif fp:
            fiscal_period = str(fp)

        cases.append(CaseEntry(
            ticker=ticker,
            earnings_date=earnings_date,
            fiscal_period=fiscal_period or earnings_date,
            start=ep_start,
            end=ep_end,
        ))

    # Sort by date so they run chronologically
    cases.sort(key=lambda c: (c.earnings_date or "", c.ticker or ""))

    logger.info(
        "Built %d cases from NPP events (%s → %s)",
        len(cases), start_date, end_date,
    )
    return CasesConfig(cases=cases)


def _earnings_date_et(time_utc: str) -> str:
    """Convert NPP time_utc to YYYY-MM-DD in ET. Returns '' on failure."""
    if not time_utc:
        return ""
    try:
        from datetime import datetime, timezone, timedelta as _td
        dt = datetime.fromisoformat(time_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        et_dt = dt.astimezone(timezone(_td(hours=-5)))  # EST approximation
        return et_dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ""


class CaseRunner:
    """Run a batch of experiment cases.

    Cases can be loaded from a YAML file (cases_path) or provided directly
    as a pre-built CasesConfig (cases). When neither is given, raises ValueError.
    """

    def __init__(
        self,
        strategy_config_path: str,
        models_config: ModelsConfig,
        services_config: ServicesConfig,
        cases_path: str | None = None,
        cases: CasesConfig | None = None,
        model_override: str | None = None,
        output_dir: str = "./output",
        news_enabled: bool = False,
    ) -> None:
        if cases_path is None and cases is None:
            raise ValueError("Either cases_path or cases must be provided")
        self._strategy_path = strategy_config_path
        self._models = models_config
        self._services = services_config
        self._cases = cases if cases is not None else load_cases_config(cases_path)
        self._model_override = model_override
        self._output_dir = output_dir
        self._news_enabled = news_enabled

    def run_all(self) -> list[dict]:
        """Run each case independently. Returns list of per-case summaries."""
        summaries = []
        total = len(self._cases.cases)

        for i, case in enumerate(self._cases.cases, 1):
            logger.info("Running case %d/%d: %s", i, total, self._case_label(case))
            try:
                result = self._run_single_case(case)
                result["status"] = "success"
            except Exception as e:
                logger.error("Case %d failed: %s", i, e, exc_info=True)
                result = {"case": case.model_dump(), "status": "error", "error": str(e)}
            summaries.append(result)

        self._write_batch_summary(summaries)
        return summaries

    def _run_single_case(self, case: CaseEntry) -> dict:
        config = load_config(self._strategy_path)
        model_entry = resolve_model_entry(config, self._models, self._model_override)
        model_key = self._model_override or getattr(config, "model", None) or self._models.default_model()

        # -- News context flag (off by default, enabled via --news CLI flag) --
        if self._news_enabled:
            config.data_policy.news_enabled = True
            if not config.data_policy.npp_base_url:
                config.data_policy.npp_base_url = self._services.npp_base_url

        # -- Apply case overrides to config --

        # Ticker: update universe AND initial_positions
        if case.ticker:
            config.experiment.universe = [case.ticker]
            # Intraday strategies don't hold stock positions — skip bootstrap.
            if "intraday" not in config.strategy.name:
                # Rebuild initial_positions for the new ticker, preserving qty
                stock_qty = case.stock_qty
                if stock_qty is None:
                    # Try to get from existing positions (if base config had a different ticker)
                    existing_qty = next(
                        (p.get("qty", 0) for p in config.experiment.initial_positions),
                        config.strategy.params.get("stock_qty", 10000),
                    )
                    stock_qty = existing_qty
                config.experiment.initial_positions = [
                    {"symbol": case.ticker, "qty": stock_qty}
                ]
                # Keep stock_qty in strategy params in sync
                config.strategy.params["stock_qty"] = stock_qty

        if case.start:
            config.experiment.start = case.start
        if case.end:
            config.experiment.end = case.end

        # Auto-compute initial_cash for overlay: stock_qty * price * (1 + buffer)
        # Always compute per-ticker so batch runs with different tickers get correct cash.
        if "overlay" in config.strategy.name and config.experiment.universe:
            try:
                from datetime import timedelta
                from trading.tools.contract_discovery import query_stock_price
                ticker = config.experiment.universe[0]
                sq = config.strategy.params.get("stock_qty", 10000)
                buffer_pct = config.strategy.params.get("cash_buffer_pct", 0.20)
                # Try start date and next 5 trading days (start may be a holiday)
                start_price = None
                try_date = date.fromisoformat(config.experiment.start)
                for _ in range(7):
                    start_price = query_stock_price(
                        self._services.upq_base_url, ticker, try_date.isoformat(),
                    )
                    if start_price and start_price > 0:
                        break
                    try_date += timedelta(days=1)
                if start_price and start_price > 0:
                    auto_cash = sq * start_price * (1 + buffer_pct)
                    config.experiment.initial_cash = auto_cash
                    logger.info(
                        "Auto cash for %s: %d * $%.2f * %.0f%% = $%.0f",
                        ticker, sq, start_price, (1 + buffer_pct) * 100, auto_cash,
                    )
                else:
                    logger.warning("Auto cash: no price found for %s near %s, using config default",
                                   ticker, config.experiment.start)
            except Exception as exc:
                logger.warning("Auto cash computation failed: %s", exc)

        # For earnings: narrow date range to T-2/T+2 around earnings_date
        if case.earnings_date and not case.start:
            ed = date.fromisoformat(case.earnings_date)
            config.experiment.start = _trading_days_offset(ed, -2)
            config.experiment.end = _trading_days_offset(ed, +2)

        # Mode → strategy name override for overlay strategies
        if case.mode and case.mode in _OVERLAY_MODE_TO_STRATEGY:
            config.strategy.name = _OVERLAY_MODE_TO_STRATEGY[case.mode]

        # Date-based intraday cases: set start/end to single-day session
        if case.date and "intraday" in config.strategy.name:
            config.experiment.start = f"{case.date}T09:30:00"
            config.experiment.end = f"{case.date}T16:00:00"
            config.experiment.frequency = "1m"

        # Apply risk overrides from cases YAML (e.g. circuit_breaker_loss_pct)
        if self._cases.risk_overrides:
            for key, val in self._cases.risk_overrides.items():
                if hasattr(config.risk, key):
                    setattr(config.risk, key, val)
                    logger.info("Risk override: %s = %s", key, val)
                else:
                    logger.warning("Unknown risk override key ignored: %s", key)

        # Apply LLM model settings into strategy params (picked up by on_session_start)
        _apply_model_to_strategy_params(config, model_entry)

        # Apply service URLs
        config.execution.pmb_base_url = self._services.pmb_base_url
        config.execution.upq_base_url = self._services.upq_base_url

        # Inject NPP URL into earnings strategy params
        if "earnings" in config.strategy.name:
            config.strategy.params.setdefault("npp_base_url", self._services.npp_base_url)

        config.experiment.run_id = self._case_label(case)

        # Create output manager
        strategy_name = config.strategy.name
        if "earnings" in strategy_name:
            output = OutputManager.for_earnings(
                self._output_dir, model_key,
                case.ticker or "unknown",
                case.fiscal_period or "single",
            )
        elif "overlay" in strategy_name:
            output = OutputManager.for_overlay(
                self._output_dir, model_key,
                case.ticker or "unknown",
                case.mode or "unknown",
            )
        elif "intraday" in strategy_name:
            output = OutputManager.for_intraday(
                self._output_dir, model_key,
                date=case.date or "unknown",
                ticker=case.ticker or "",
            )
        else:
            output = OutputManager.for_generic(
                self._output_dir, model_key, config.experiment.run_id
            )

        # Point evaluation output_dir at the run-specific directory
        config.evaluation.output_dir = str(output.run_dir)

        runner = TradingRunner(config=config)
        result = runner.run()
        result["case"] = case.model_dump()
        return result

    @staticmethod
    def _case_label(case: CaseEntry) -> str:
        parts = []
        if case.ticker:
            parts.append(case.ticker)
        if case.fiscal_period:
            parts.append(case.fiscal_period)
        if case.earnings_date:
            parts.append(case.earnings_date)
        if case.mode:
            parts.append(case.mode)
        if case.date:
            parts.append(case.date)
        return "_".join(parts) or "case"

    def _write_batch_summary(self, summaries: list[dict]) -> None:
        config = load_config(self._strategy_path)
        model_key = self._model_override or self._models.default_model()
        strategy_name = config.strategy.name

        summary_dir = Path(self._output_dir) / model_key / strategy_name
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / "batch_summary.json"

        with open(summary_path, "w") as f:
            json.dump(summaries, f, indent=2, default=str)
        logger.info("Batch summary written to %s", summary_path)


def _apply_model_to_strategy_params(config: RunConfig, model_entry: ModelEntry) -> None:
    """Inject LLM connection params into strategy.params so on_session_start can pick them up.

    Always overwrites llm_* keys — the model selected via --model CLI flag or models.yaml
    must take precedence over any hardcoded values in the strategy config file.
    """
    p = config.strategy.params
    p["llm_provider"]         = model_entry.provider
    p["llm_model"]            = model_entry.model
    p["llm_api_key"]          = model_entry.resolved_api_key()
    p["llm_base_url"]         = model_entry.resolved_base_url()
    p["llm_reasoning_effort"] = model_entry.reasoning_effort
