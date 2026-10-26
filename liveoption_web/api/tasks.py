"""Versioned task protocol and server-side model settings for Web and CLI."""
from __future__ import annotations

import asyncio
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.api.launch import (
    LaunchRunRequest, REPO_ROOT, WEB_RUNS_DIR, _new_run_id,
    _peek_scenario_yaml, handle_launch_run,
)
from liveoption_web.runs import service
from liveoption_web.runs.catalog import SCENARIOS, SUPPORTED_UNDERLYINGS
from liveoption_web.runs.custom_tasks import custom_prompts, custom_source
from liveoption_web.runs.model_settings import (
    ModelSettings, load_model_settings, merge_public_update, save_model_settings,
)
from liveoption_web.runs.prompt_catalog import variables_by_stage
from liveoption_web.runs.protocol import RunSpecV1, materialize
from trading.core.config import AvsrConfig, DataPolicyConfig, ExecutionConfig, RiskConfig
from trading.core.endpoints import service_url, qfz_headers

router = APIRouter(prefix="/tasks", tags=["tasks"])
settings_router = APIRouter(prefix="/model-settings", tags=["model-settings"])


@router.get("/presets")
async def list_presets(actor: Actor = Depends(verify_internal)) -> dict:
    """Preset defaults, suitable for copying into a versioned run spec."""
    items = []
    for scenario in SCENARIOS.values():
        source = _peek_scenario_yaml(scenario.config_path)
        from trading.prompts.intraday import INTRADAY_SYSTEM_TEMPLATE, INTRADAY_USER_TEMPLATE
        from trading.prompts.loader import get_loader
        if scenario.family.value == "intraday":
            prompt_templates = {"system": INTRADAY_SYSTEM_TEMPLATE, "user": INTRADAY_USER_TEMPLATE}
        else:
            names = ("analyzer", "executor") if scenario.family.value == "overlay" else ("earnings_pre", "earnings_post", "executor")
            loader = get_loader()
            prompt_templates = {f"{name.removeprefix('earnings_')}_{role}": loader.load_system(name) if role == "system" else loader.load_user(name)
                                for name in names for role in ("system", "user")}
        execution = ExecutionConfig.model_validate(source.get("execution", {}))
        items.append({
            "id": scenario.id,
            "name": scenario.display_name,
            "description": scenario.description,
            "family": scenario.family.value,
            **({"overlay_mode": "covered_call", "overlay_modes": ["hedge", "covered_call", "hybrid"]}
               if scenario.family.value == "overlay" else {}),
            "experiment": source["experiment"],
            "strategy_params": {k: v for k, v in source["strategy"]["params"].items()
                                if k in {
                                    "decision_interval_minutes", "max_positions", "dte_min", "dte_max",
                                    "stock_qty", "cash_buffer_pct", "otm_pct", "rebalance_weekday",
                                    "max_position_cost", "max_contracts", "auto_close_at_t2",
                                    "post_decision_interval_minutes",
                                }},
            "risk": RiskConfig.model_validate(source.get("risk", {})).model_dump(),
            "data_policy": {k: v for k, v in DataPolicyConfig.model_validate(source.get("data_policy", {})).model_dump().items()
                            if k != "npp_base_url"},
            "avsr": AvsrConfig.model_validate(source.get("avsr", {})).model_dump(),
            "prompt_templates": prompt_templates,
            "prompt_variables": variables_by_stage(prompt_templates),
            "execution": {**execution.fee_model, "order_type_policy": execution.order_type_policy},
            "context": source["strategy"]["params"].get("prompt_override", ""),
        })
    custom_types = []
    for cadence in ("intraday", "daily"):
        source = custom_source(cadence)
        templates = custom_prompts(cadence)
        variables = variables_by_stage(templates)
        if cadence == "daily":
            for entry in variables["user"]:
                if entry["name"] == "current_time":
                    entry.update(description="当前决策对应的交易日期", description_en="Trading date of this decision", example="2025-01-08")
                if entry["name"] == "price_windows_table":
                    entry.update(description="近期日线价格、当日涨跌幅和成交量表", description_en="Recent daily prices, daily changes and volumes")
        execution = ExecutionConfig.model_validate(source["execution"])
        custom_types.append({
            "id": f"custom_{cadence}", "cadence": cadence, "name": cadence.title(),
            "description": "Create your own options task", "family": cadence,
            "experiment": source["experiment"], "strategy_params": source["strategy"]["params"],
            "risk": source["risk"],
            "data_policy": {key: value for key, value in source["data_policy"].items() if key != "npp_base_url"},
            "avsr": source["avsr"], "execution": {**execution.fee_model, "order_type_policy": execution.order_type_policy},
            "prompt_templates": templates, "prompt_variables": variables, "context": "",
        })
    return {"protocol_version": "liveoption.run.v1", "items": items, "custom_types": custom_types,
            "supported_underlyings": sorted(SUPPORTED_UNDERLYINGS)}


@router.post("", status_code=202)
async def create_task(
    body: dict[str, Any], request: Request, actor: Actor = Depends(verify_internal),
) -> dict:
    try:
        body = RunSpecV1.model_validate(body)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors())
        raise HTTPException(422, details) from exc
    settings = request.app.state.settings
    registry = load_model_settings(settings)
    model = next((entry for entry in registry.models if entry.id == body.model_id), None)
    if model is None:
        raise HTTPException(422, "unknown model_id; configure it under API settings")
    if not model.available:
        raise HTTPException(422, "model is no longer listed by its API group; refresh models and select an available model")
    group = next(entry for entry in registry.groups if entry.id == model.group_id)
    if not group.api_key:
        raise HTTPException(422, "selected API group has no configured key")
    scenario = body.scenario
    run_id = _new_run_id()
    try:
        source = custom_source(body.cadence) if body.cadence else _peek_scenario_yaml(scenario.config_path)
        config = materialize(body, source, model, group, run_id)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc
    # The generated config is the exact engine input; no UI-only override path.
    WEB_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = WEB_RUNS_DIR / f"{run_id}.yaml"
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config.model_dump(mode="json"), handle, sort_keys=False)
    relative_path = str(config_path.relative_to(REPO_ROOT))
    effective_scenario = scenario.model_copy(update={"config_path": relative_path})
    child_env: dict[str, str] = {"LIVEOPTION_LLM_BASE_URL": group.base_url}
    if group.provider == "anthropic":
        child_env["ANTHROPIC_BASE_URL"] = group.base_url or "https://api.anthropic.com"
        if group.api_key: child_env["ANTHROPIC_AUTH_TOKEN"] = group.api_key
    elif group.provider in {"openai", "openai_compatible"}:
        child_env["OPENAI_BASE_URL"] = group.base_url or "https://api.openai.com/v1"
        if group.api_key: child_env["OPENAI_API_KEY"] = group.api_key
    else:
        if group.api_key: child_env[{"gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "qwen": "DASHSCOPE_API_KEY"}[group.provider]] = group.api_key
    snapshot = {
        "protocol_version": body.protocol_version,
        "preset_id": body.preset_id,
        "cadence": body.cadence,
        "model_id": body.model_id,
        "model_group_id": group.id,
        "spec": body.model_dump(mode="json"),
        "effective_config": config.model_dump(mode="json"),
        "config_path": relative_path,
    }
    return await handle_launch_run(
        request, LaunchRunRequest(scenario_id=scenario.id, title=body.title), actor,
        scenario_override=effective_scenario,
        environment_override=child_env,
        spec_snapshot_override=snapshot,
        run_id_override=run_id,
    )


@router.get("/{run_id}")
async def task_status(run_id: str, request: Request, actor: Actor = Depends(verify_internal)) -> dict:
    row = await service.get_run(request.app.state.settings.db_path, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    return row


def _normalize_bar(row: dict, resolution: str) -> dict | None:
    try:
        raw_ts = row.get("window_start") if resolution == "1m" else row.get("date")
        if resolution == "1m":
            stamp = int(raw_ts)
            timestamp = datetime.fromtimestamp(stamp / (1_000_000_000 if stamp > 10**16 else 1000), timezone.utc).isoformat().replace("+00:00", "Z")
        else:
            timestamp = date.fromisoformat(str(raw_ts)[:10]).isoformat()
        result = {"time": timestamp, "open": float(row["open"]), "high": float(row["high"]),
                  "low": float(row["low"]), "close": float(row["close"]), "volume": float(row.get("volume") or 0)}
        if result["low"] > result["high"] or any(not math.isfinite(result[k]) for k in ("open", "high", "low", "close", "volume")):
            return None
        return result
    except (TypeError, ValueError, OverflowError, KeyError, OSError):
        return None


@router.get("/{run_id}/chart")
async def task_chart(
    run_id: str, request: Request, symbol: str, day: date | None = None,
    actor: Actor = Depends(verify_internal),
) -> dict:
    """Point-in-time QFinZero OHLCV for a run's verified underlying."""
    row = await service.get_run(request.app.state.settings.db_path, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    universe = row.get("universe") or []
    if symbol not in universe:
        raise HTTPException(422, "symbol must belong to this run's universe")
    start_day, end_day = date.fromisoformat(row["date_start"][:10]), date.fromisoformat(row["date_end"][:10])
    if day is not None and not start_day <= day <= end_day:
        raise HTTPException(422, "day must be within the run's date range")
    if row["scenario"] in {"custom_daily", "custom_intraday"}:
        resolution = row.get("replay_frequency") or ("1d" if row["scenario"] == "custom_daily" else "1m")
    else:
        scenario = SCENARIOS.get(row["scenario"])
        if scenario is None:
            raise HTTPException(422, "chart is unavailable for this scenario")
        source = _peek_scenario_yaml(scenario.config_path)
        resolution = source["experiment"].get("frequency", "1m")
    if resolution == "1m":
        if day is None:
            raise HTTPException(422, "day is required for minute bars")
        # The chart's day selector is an Eastern trading date. UPQ's naive
        # timestamps are UTC, so convert both local midnights separately;
        # this also handles the 23/25-hour daylight-saving transition days.
        eastern = ZoneInfo("America/New_York")
        start = datetime.combine(day, datetime.min.time(), eastern).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), eastern).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        endpoint = "/stock"
        params = {"tickers": symbol, "start": start, "end": end, "limit": 3000}
    else:
        endpoint = "/stock/daily"
        params = {"tickers": symbol, "start": start_day.isoformat(), "end": end_day.isoformat()}

    def fetch() -> list:
        import requests
        headers = qfz_headers()
        response = requests.get(f"{service_url('upq')}{endpoint}", params=params, headers=headers, timeout=20)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, list):
            raise ValueError("QFinZero returned a non-list bar response")
        return result

    try:
        raw_bars = await asyncio.to_thread(fetch)
    except Exception as exc:
        raise HTTPException(503, "QFinZero price data is temporarily unavailable") from exc
    bars = [normalized for item in raw_bars if isinstance(item, dict)
            if (normalized := _normalize_bar(item, resolution)) is not None]
    return {"protocol_version": "liveoption.chart.v1", "run_id": run_id, "symbol": symbol,
            "day": day.isoformat() if day else None, "resolution": resolution,
            "source": "qfinzero.upq", "bars": bars}


@router.get("/{run_id}/analysis")
async def task_analysis(run_id: str, request: Request, actor: Actor = Depends(verify_internal)) -> dict:
    db_path = request.app.state.settings.db_path
    row = await service.get_run(db_path, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    metrics = await service.get_run_metrics(db_path, run_id)
    if metrics is None and row.get("status") not in {"pending", "running"}:
        from liveoption_web import eval as eval_pkg
        import asyncio
        metrics = await asyncio.to_thread(eval_pkg.aggregate_for, run_id, db_path)
    if metrics is not None:
        from liveoption_web import eval as eval_pkg
        import asyncio
        if metrics.get("evaluator_version") != eval_pkg.current_evaluator_version(metrics.get("family")):
            metrics = await asyncio.to_thread(eval_pkg.aggregate_for, run_id, db_path) or metrics
    portfolio = None
    if metrics is not None:
        import sqlite3
        from liveoption_web.eval._common import load_day_completed, paper_portfolio_stats

        def compute_portfolio() -> dict:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                return paper_portfolio_stats(load_day_completed(conn, run_id))

        portfolio = await asyncio.to_thread(compute_portfolio)
    return {
        "protocol_version": "liveoption.analysis.v1",
        "run": row,
        "metrics": metrics,
        "paper_portfolio": portfolio,
        "sections": ["generic", "family_specific", "behavioral"],
        "quality": {
            "generic": "paper_portfolio uses compounded annual return and fractional equity drawdown; null means insufficient observations. Legacy generic win_rate is profitable-day rate and max_drawdown is absolute P&L drawdown.",
            "family_specific": "Some fields are proxies derived from fills, not directly measured strategy returns.",
            "behavioral": "Only intent_consistency and decision counts are estimated; unmeasured fields are null.",
        },
        "pending": metrics is None,
    }


@router.get("/{run_id}/returns")
async def task_returns(run_id: str, request: Request, symbols: str | None = None, account: bool = True,
                       actor: Actor = Depends(verify_internal)) -> dict:
    import sqlite3
    import requests
    from contextlib import closing
    from liveoption_web.eval._common import load_day_completed
    from liveoption_web.runs.returns import compare_returns
    row = await service.get_run(request.app.state.settings.db_path, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    universe = row.get("universe") or []
    selected = list(dict.fromkeys(item.strip().upper() for item in symbols.split(",") if item.strip())) if symbols is not None else universe
    if set(selected) - set(universe) or len(selected) > 24 or not selected and not account:
        raise HTTPException(422, "select run underlyings and/or account")
    def observations():
        with closing(sqlite3.connect(request.app.state.settings.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return load_day_completed(conn, run_id)
    days = await asyncio.to_thread(observations)
    start, end = row["date_start"][:10], row["date_end"][:10]
    observed = [item.get("trading_day") for item in days if isinstance(item.get("trading_day"), str)]
    if row["status"] != "completed":
        end = min(end, max(observed)) if observed else start
    raw = []
    if selected and (observed or row["status"] == "completed"):
        def prices():
            headers = qfz_headers()
            response = requests.get(f"{service_url('upq')}/stock/daily",
                                    params={"tickers": ",".join(selected), "start": start, "end": end}, headers=headers, timeout=20)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError("invalid daily-price response")
            return value
        try:
            raw = await asyncio.to_thread(prices)
        except (requests.RequestException, ValueError):
            raise HTTPException(503, "QFinZero daily prices are temporarily unavailable") from None
    result = compare_returns(run_id, selected, raw, days, start, end, account)
    result["provisional"] = row["status"] != "completed"
    return result


@router.get("/{run_id}/report")
async def task_report(run_id: str, request: Request, format: Literal["json", "markdown", "html"] = "json",
                      actor: Actor = Depends(verify_internal)):
    from pathlib import Path
    import json
    import sqlite3
    from liveoption_web.eval._common import load_day_completed, load_fills
    from liveoption_web.runs.report import build_report, report_html, report_markdown

    analysis = await task_analysis(run_id, request, actor)
    row = analysis["run"]
    try:
        snapshot = json.loads(row.get("launch_spec_json") or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    config = snapshot.get("effective_config")
    # Legacy tasks can have their immutable generated YAML but no config snapshot.
    # Never read today's preset file and label it as an old run's actual settings.
    if not isinstance(config, dict):
        config = None
        candidate = Path(REPO_ROOT / row.get("config_path", "")).resolve()
        if candidate.is_relative_to(WEB_RUNS_DIR.resolve()) and candidate.is_file():
            try:
                value = yaml.safe_load(candidate.read_text(encoding="utf-8"))
                config = value if isinstance(value, dict) else None
            except (OSError, yaml.YAMLError):
                pass
    def observations():
        with __import__("contextlib").closing(sqlite3.connect(request.app.state.settings.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            # A single read transaction keeps completed days and fills coherent.
            conn.execute("BEGIN")
            return load_day_completed(conn, run_id), load_fills(conn, run_id)
    days, fills = await asyncio.to_thread(observations)
    decisions = await service.get_timeline(request.app.state.settings.db_path, run_id)
    report = build_report(row, config, days, decisions, fills, analysis)
    if format == "json":
        return report
    content = report_html(report) if format == "html" else report_markdown(report)
    # Never use a user-controlled title as a response header / filename.
    safe_id = __import__("re").sub(r"[^A-Za-z0-9_-]", "_", run_id)
    return Response(content, media_type="text/html" if format == "html" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{safe_id}-report.{"html" if format == "html" else "md"}"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"})


@router.get("/{run_id}/logs")
async def task_logs(run_id: str, request: Request, before: int | None = Query(None, ge=0),
                    limit: int = Query(32768, ge=1024, le=65536), download: bool = False,
                    actor: Actor = Depends(verify_internal)):
    from liveoption_web.runs.logs import environment_secrets, log_page, log_path, redact
    settings = request.app.state.settings
    row = await service.get_run(settings.db_path, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    path = log_path(row, settings.runs_dir)
    if path is None:
        if download:
            raise HTTPException(404, "worker log is not available")
        return {"available": False, "text": "", "next_before": None, "status": row["status"], "exit_code": row.get("exit_code"), "error_message": row.get("error_message")}
    secrets = environment_secrets() + [settings.internal_secret.get_secret_value()]
    secrets.extend(group.api_key for group in load_model_settings(settings).groups if group.api_key)
    if download:
        def lines():
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    yield redact(line, secrets)
        safe_id = __import__("re").sub(r"[^A-Za-z0-9_-]", "_", run_id)
        return StreamingResponse(lines(), media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{safe_id}-worker.log"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    page = await asyncio.to_thread(log_page, path, before, limit, secrets)
    tail = await asyncio.to_thread(log_page, path, None, 16384, secrets)
    error_lines = [line for line in tail["text"].splitlines() if __import__("re").search(r"(?i)(error|exception|failed|traceback)", line)]
    return {**page, "available": True, "status": row["status"], "exit_code": row.get("exit_code"),
            "error_message": redact(row.get("error_message") or (error_lines[-1] if error_lines else ""), secrets)}


@settings_router.get("")
async def get_model_settings(request: Request, actor: Actor = Depends(verify_internal)) -> dict:
    return load_model_settings(request.app.state.settings).public()


@settings_router.put("")
async def put_model_settings(
    body: dict[str, Any], request: Request, actor: Actor = Depends(verify_internal),
) -> dict:
    if not actor.user_id:
        raise HTTPException(403, "authentication required")
    try:
        value = merge_public_update(request.app.state.settings, ModelSettings.model_validate(body))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "invalid model settings; check group IDs, URLs and model references") from exc
    save_model_settings(request.app.state.settings, value)
    return value.public()


@settings_router.post("/{group_id}/models/refresh")
async def refresh_models(group_id: str, request: Request, actor: Actor = Depends(verify_internal)) -> dict:
    from liveoption_web.runs.model_discovery import DiscoveryError, discover, merge_discovered
    if not actor.user_id:
        raise HTTPException(403, "authentication required")
    settings = request.app.state.settings
    registry = load_model_settings(settings)
    group = next((group for group in registry.groups if group.id == group_id), None)
    if group is None:
        raise HTTPException(404, "API group not found")
    try:
        entries = await asyncio.to_thread(discover, group)
    except DiscoveryError as exc:
        raise HTTPException(502, str(exc)) from None
    # Reload after I/O to preserve edits made during discovery. Never apply a
    # catalog fetched with a different URL / credential to newly edited settings.
    current = load_model_settings(settings)
    current_group = next((item for item in current.groups if item.id == group_id), None)
    if current_group is None or any(getattr(current_group, key) != getattr(group, key) for key in ("provider", "base_url", "api_key")):
        raise HTTPException(409, "API group changed during discovery; retry")
    value = merge_discovered(current, group_id, entries)
    save_model_settings(settings, value)
    return {**value.public(), "discovery": {"group_id": group_id, "count": len(entries), "checked_at": next(g.models_updated_at for g in value.groups if g.id == group_id)}}
