"""Portable run reports from saved inputs and observed events, without credentials."""
from __future__ import annotations

import html
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def public_value(value: Any) -> Any:
    """Defense in depth for legacy prompt text and nested configuration."""
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()
                if not re.search(r"(?i)(secret|token|api_key|password|base_url|storage_dir|config_path)", key)}
    if isinstance(value, list):
        return [public_value(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?i)\b(?:sk|pk)[-_][A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def build_report(row: dict, config: dict | None, days: list[dict], decisions: list[dict], fills: list[dict], analysis: dict) -> dict:
    try:
        snapshot = json.loads(row.get("launch_spec_json") or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    spec = snapshot.get("spec") or {}
    warnings = [
        "Historical replay, not live brokerage performance or investment advice.",
        "Curves use completed-day events. Intraday fluctuations are not represented by daily drawdown.",
        "Trade proposals are decisions with intent, not executed fills. A fill event can be a partial fill.",
        "Annualized returns extrapolate a short sample. VaR/CVaR require at least 20 valid daily returns.",
    ]
    effective = config or {}
    settings = {
        "configuration_source": "saved_effective_config" if config else "saved_spec_only",
        "experiment": effective.get("experiment") or {key: row.get(source) for key, source in
            [("start", "date_start"), ("end", "date_end"), ("universe", "universe")]},
        "model": {"provider": row.get("provider"), "model_id": row.get("model_id"),
                  "api_model": effective.get("strategy", {}).get("params", {}).get("llm_model")},
        "decision_time_et": spec.get("decision_time_et") or effective.get("strategy", {}).get("params", {}).get("decision_time_et"),
        "overlay_mode": spec.get("overlay_mode") or {
            "overlay_profit": "covered_call", "overlay_hedge": "hedge", "overlay_hybrid": "hybrid",
        }.get(effective.get("strategy", {}).get("name")),
        "strategy": effective.get("strategy") or {"params": spec.get("strategy_params")},
        "risk": effective.get("risk", spec.get("risk")),
        "data_policy": effective.get("data_policy", spec.get("data_policy")),
        "avsr": effective.get("avsr", spec.get("avsr")),
        "execution": effective.get("execution", spec.get("execution")),
        "context": spec.get("context", effective.get("strategy", {}).get("params", {}).get("prompt_override")),
        "prompt_templates": effective.get("strategy", {}).get("params", {}).get("prompt_templates", spec.get("prompt_templates")),
        "prompt_addenda": effective.get("strategy", {}).get("params", {}).get("prompt_addenda", spec.get("prompt_addenda")),
    }
    if not config:
        warnings.append("Effective configuration was not retained for this legacy run. Unrecorded defaults are unknown; current presets are not substituted.")
    counts = Counter(item.get("trading_day") for item in decisions)
    proposals = Counter(item.get("trading_day") for item in decisions if item.get("has_trade"))
    initial = None
    if days:
        equity, delta = finite(days[0].get("portfolio_value")), finite(days[0].get("pnl_delta"))
        if equity is not None and delta is not None:
            initial = equity - delta
    if initial is None:
        initial = finite(settings["experiment"].get("initial_cash"))
    peak = initial if initial is not None and initial > 0 else None
    seen = set()
    series = []
    for day in days:
        date = day.get("trading_day")
        if date in seen:
            warnings.append(f"Duplicate completed-day record: {date}. All observed records are retained.")
        seen.add(date)
        equity = finite(day.get("portfolio_value"))
        if equity is not None:
            peak = max(peak, equity) if peak is not None else equity
        series.append({"trading_day": date, "portfolio_value": equity,
                       "cum_pnl": finite(day.get("cum_pnl")), "daily_pnl": finite(day.get("pnl_delta")),
                       "drawdown_pct": (equity / peak - 1) if equity is not None and peak and peak > 0 else None,
                       "decisions": counts[date], "trade_proposals": proposals[date]})
    # Activity can exist before a day is completed; keep it separate from equity observations.
    activity = [{"trading_day": day, "decisions": counts[day], "trade_proposals": proposals[day]}
                for day in sorted(key for key in counts if key)]
    final_pnl = series[-1]["cum_pnl"] if series else None
    final_equity = series[-1]["portfolio_value"] if series else None
    return public_value({
        "protocol_version": "liveoption.report.v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "background": {"run_id": row["id"], "title": row.get("title"), "scenario": row.get("scenario"),
                       "status": row.get("status"), "context": settings["context"],
                       "purpose": "Evaluate an options agent on point-in-time historical market data.",
                       "services": {"market_data": "QFinZero UPQ", "execution": "QFinZero PMB replay (simulated broker)"},
                       "timezone": "America/New_York (ET, DST-aware)"},
        "settings": settings,
        "results": {"provisional": row.get("status") not in {"completed"},
                    "summary": {"completed_days": len(series), "decisions": len(decisions),
                                "trade_proposals": sum(bool(d.get("has_trade")) for d in decisions),
                                "fill_events": len(fills), "initial_equity": initial,
                                "final_equity": final_equity, "total_pnl": final_pnl},
                    "paper_portfolio": analysis.get("paper_portfolio"),
                    "metrics": analysis.get("metrics"), "series": series, "activity": activity,
                    "decision_records": [{key: d.get(key) for key in ("seq", "trading_day", "decision_ts", "has_trade", "cum_pnl_after")} for d in decisions],
                    "fills": fills},
        "warnings": warnings,
    })


def report_markdown(report: dict) -> str:
    # Four-backtick fences survive the triple-backticks users often put in prompts.
    def block(value):
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        fence = "`" * max(4, max((len(x) + 1 for x in re.findall(r"`+", text)), default=4))
        return f"{fence}json\n{text}\n{fence}"
    lines = ["# LiveOption Run Report", f"Generated: {report['generated_at']}", "## Background", block(report["background"]), "## Settings"]
    for key, value in report["settings"].items():
        lines.extend([f"### {key.replace('_', ' ').title()}", block(value)])
    results = report["results"]
    lines.extend(["## Results", "Provisional / partial results." if results["provisional"] else "Completed run.",
                  "### Summary", block(results["summary"]), "### Portfolio metrics", block(results["paper_portfolio"]),
                  "### Daily results", "| ET trading day | Equity (USD) | Daily P&L (USD) | Cumulative P&L (USD) | Drawdown | Decisions | Trade proposals |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    def number(value, percent=False):
        return "N/A" if value is None else f"{value:.2%}" if percent else f"{value:,.2f}"
    for item in results["series"]:
        lines.append(f"| {item['trading_day']} | {number(item['portfolio_value'])} | {number(item['daily_pnl'])} | {number(item['cum_pnl'])} | {number(item['drawdown_pct'], True)} | {item['decisions']} | {item['trade_proposals']} |")
    for title, key in [("Decision activity", "activity"), ("Decision records", "decision_records"), ("Executed fill events", "fills"), ("Research metrics", "metrics")]:
        lines.extend([f"### {title}", block(results[key])])
    lines.extend(["### Data notes", *[f"- {warning}" for warning in report["warnings"]]])
    return re.sub(r"(?m)(^\|.*\|)\n\n(?=\|)", r"\1\n", "\n\n".join(lines)) + "\n"


def report_html(report: dict) -> str:
    esc = lambda value: html.escape(str(value), quote=True)
    def render(value):
        if isinstance(value, dict):
            return "<dl>" + "".join(f"<dt>{esc(k.replace('_', ' ').title())}</dt><dd>{render(v)}</dd>" for k, v in value.items()) + "</dl>"
        if isinstance(value, list):
            if value and all(isinstance(item, dict) for item in value):
                columns = list(dict.fromkeys(key for item in value for key in item))
                if all(not isinstance(item.get(key), (dict, list)) for item in value for key in columns):
                    def cell(key, item):
                        val = item.get(key)
                        if val is None:
                            return "N/A"
                        if isinstance(val, float):
                            return f"{val:.2%}" if key.endswith("_pct") else f"{val:,.2f}"
                        return esc(val)
                    heading = "".join(f'<th style="padding:8px;text-align:left;border-bottom:1px solid #ccc">{esc(key.replace("_", " ").title())}</th>' for key in columns)
                    rows = "".join("<tr>" + "".join(f'<td style="padding:8px;border-bottom:1px solid #eee">{cell(key, item)}</td>' for key in columns) + "</tr>" for item in value)
                    return f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr>{heading}</tr></thead><tbody>{rows}</tbody></table></div>'
            return "<pre>" + esc(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)) + "</pre>"
        return "<span class='text'>" + esc("Not recorded / insufficient data" if value is None else value) + "</span>"
    def curve(key, title, percent=False):
        points = report["results"]["series"]
        valid = [(i, p[key]) for i, p in enumerate(points) if p[key] is not None]
        if not valid:
            return f"<h3>{title}</h3><p>No completed-day observations.</p>"
        low, high = min(v for _, v in valid), max(v for _, v in valid)
        def xy(i, v):
            return f"{40 + i / max(1, len(points)-1) * 720:.1f},{180 - (v-low) / (high-low or 1) * 140:.1f}"
        segments = []
        current = []
        for i, point in enumerate(points):
            if point[key] is None:
                if current: segments.append(current)
                current = []
            else:
                current.append((i, point[key]))
        if current: segments.append(current)
        paths = "".join(f'<polyline points="{" ".join(xy(i,v) for i,v in segment)}" fill="none" stroke="#255eb5" stroke-width="2"/>' for segment in segments)
        dots = "".join(f'<circle cx="{xy(i,v).split(",")[0]}" cy="{xy(i,v).split(",")[1]}" r="3" fill="#255eb5"><title>{esc(points[i]["trading_day"])}: {v:.2%}</title></circle>' if percent else f'<circle cx="{xy(i,v).split(",")[0]}" cy="{xy(i,v).split(",")[1]}" r="3" fill="#255eb5"><title>{esc(points[i]["trading_day"])}: ${v:,.2f}</title></circle>' for i,v in valid)
        labels = f"{low:.2%} – {high:.2%}" if percent else f"${low:,.2f} – ${high:,.2f}"
        return f'<h3>{title}</h3><p>{labels}</p><svg viewBox="0 0 800 220" role="img" aria-label="{title}"><path d="M40 20V180H760" fill="none" stroke="#ccd3df"/>{paths}{dots}<text x="40" y="210" font-size="12">{esc(points[0]["trading_day"])}</text><text x="760" y="210" font-size="12" text-anchor="end">{esc(points[-1]["trading_day"])}</text></svg>'
    results = report["results"]
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>LiveOption Run Report</title><style>body{font:15px/1.6 system-ui,sans-serif;color:#172334;background:#f6f8fb;margin:0 auto;padding:32px;max-width:1000px}section{background:white;border:1px solid #d6dce6;border-radius:12px;padding:24px;margin:20px 0}h1,h2,h3{line-height:1.3}dt{font-weight:600;margin-top:12px}dd{margin-left:0}pre,.text{white-space:pre-wrap;overflow-wrap:anywhere}pre{background:#f4f6f9;padding:12px;font:12px/1.6 monospace;max-height:480px;overflow:auto}svg{width:100%}@media print{body{background:white;padding:0}pre{max-height:none;overflow:visible}section{break-inside:auto}h2,h3{break-after:avoid}}</style>' + f'<h1>LiveOption Run Report</h1><p>{esc(report["generated_at"])}</p><section><h2>Background</h2>{render(report["background"])}</section><section><h2>Settings</h2>{render(report["settings"])}</section><section><h2>Results</h2><p>{"Provisional / partial results" if results["provisional"] else "Completed run"}</p>{render(results["summary"])}{curve("portfolio_value", "Account equity · USD")}{curve("cum_pnl", "Cumulative P&L · USD")}{curve("drawdown_pct", "Daily-equity drawdown", True)}<h3>Portfolio metrics</h3>{render(results["paper_portfolio"])}<h3>Daily results</h3>{render(results["series"])}<details><summary>Decision activity and records</summary>{render(results["activity"])}{render(results["decision_records"])}</details><details><summary>Executed fill events</summary>{render(results["fills"])}</details><details><summary>Research metrics</summary>{render(results["metrics"])}</details></section><section><h2>Data notes</h2><ul>{"".join("<li>"+esc(w)+"</li>" for w in report["warnings"])}</ul></section></html>'
