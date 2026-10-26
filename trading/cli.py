"""CLI entry point for the trading pipeline.

Usage:
    python -m trading.cli run --config path.yaml [--dry-run]
    python -m trading.cli overlay --config path.yaml --cases path.yaml \\
        --models configs/models.yaml --services configs/services.yaml \\
        [--model deepseek-v3] [--output ./output]
    python -m trading.cli eval --logdir path --scales 1m,5m,15m,1d
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


class _PromptOverrideError(ValueError):
    """Raised when --prompt-override @PATH can't be resolved safely."""


_PROMPT_OVERRIDE_MAX_BYTES = 16 * 1024  # 16 KB


def _resolve_prompt_override(value: str, root: Path) -> str:
    """Return the prompt text: `@PATH` reads a file; otherwise pass through.

    Raises:
        _PromptOverrideError: if the `@PATH` target is outside ``root``, does
            not exist, is not a regular file, exceeds the 16 KB size cap, or
            is not valid UTF-8.
    """
    if not value.startswith("@"):
        return value

    path = Path(value[1:]).expanduser().resolve()
    root_resolved = root.resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError:
        raise _PromptOverrideError(
            f"prompt-override file {path} is outside {root_resolved}"
        )

    if not path.exists():
        raise _PromptOverrideError(
            f"prompt-override file {path} does not exist"
        )
    if not path.is_file():
        raise _PromptOverrideError(
            f"prompt-override path {path} is not a regular file"
        )

    size = path.stat().st_size
    if size > _PROMPT_OVERRIDE_MAX_BYTES:
        raise _PromptOverrideError(
            f"prompt-override file {path} too large ({size} > {_PROMPT_OVERRIDE_MAX_BYTES})"
        )

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise _PromptOverrideError(
            f"prompt-override file {path} is not valid UTF-8: {e}"
        ) from e


def cmd_run(args: argparse.Namespace) -> None:
    """Execute a single trading run from a YAML config file."""
    from trading.core.config import load_config
    from trading.core.overrides import apply_overrides
    from trading.output.manager import OutputManager
    from trading.runner import TradingRunner

    config = load_config(args.config)

    # Resolve --prompt-override @file BEFORE apply_overrides. Surface resolver
    # errors as a clean CLI exit (code 2) rather than a traceback.
    if getattr(args, "prompt_override", None):
        repo_root = Path(__file__).resolve().parents[1]
        try:
            args.prompt_override = _resolve_prompt_override(
                args.prompt_override, repo_root
            )
        except _PromptOverrideError as e:
            print(f"trading-cli: error: {e}", file=sys.stderr)
            sys.exit(2)

    # --model is the old flag; new canonical is --llm-model. If both set,
    # --llm-model wins. If only --model set, forward it as llm_model.
    if not getattr(args, "llm_model", None) and getattr(args, "model", None):
        args.llm_model = args.model

    config = apply_overrides(config, args)

    if args.dry_run:
        print(f"[DRY RUN] Config loaded: {config.experiment.run_id}")
        print(f"  Strategy : {config.strategy.name}")
        print(f"  Universe : {config.experiment.universe}")
        print(f"  Frequency: {config.experiment.frequency}")
        print(f"  Period   : {config.experiment.start} -> {config.experiment.end}")
        return

    # Auto-create structured output dir based on strategy type
    output_root = getattr(args, "output", "./output")
    strategy = config.strategy.name
    ticker = config.experiment.universe[0] if config.experiment.universe else "unknown"
    model = args.model or config.strategy.params.get("llm_model") or strategy

    if "intraday" in strategy:
        date = config.experiment.start[:10]
        output = OutputManager.for_intraday(output_root, model, date, ticker)
    elif "overlay" in strategy:
        mode = "hedge" if "hedge" in strategy else "profit"
        output = OutputManager.for_overlay(output_root, model, ticker, mode)
    elif "earnings" in strategy:
        output = OutputManager.for_earnings(output_root, model, ticker, config.experiment.run_id)
    else:
        output = OutputManager.for_generic(output_root, model, config.experiment.run_id)

    config.evaluation.output_dir = str(output.run_dir)
    print(f"[run] output → {output.run_dir}")

    runner = TradingRunner(
        config,
        web_ingest=getattr(args, "web_ingest", None),
        run_id=getattr(args, "run_id", None),
    )
    result = runner.run()
    print(f"Run complete: {result}")


def cmd_overlay(args: argparse.Namespace) -> None:
    """Run a batch of overlay experiment cases."""
    from trading.case_runner import CaseRunner
    from trading.core.config import load_models_config, load_services_config

    models = load_models_config(args.models)
    services = load_services_config(args.services)

    runner = CaseRunner(
        strategy_config_path=args.config,
        models_config=models,
        services_config=services,
        cases_path=args.cases,
        model_override=args.model or None,
        output_dir=args.output,
        news_enabled=getattr(args, "news", False),
    )
    summaries = runner.run_all()

    success = sum(1 for s in summaries if s.get("status") == "success")
    failed = len(summaries) - success
    print(f"\nBatch complete: {success} succeeded, {failed} failed.")


def cmd_intraday(args: argparse.Namespace) -> None:
    """Run a batch of intraday 0DTE experiment cases."""
    from trading.case_runner import CaseRunner
    from trading.core.config import load_models_config, load_services_config

    models = load_models_config(args.models)
    services = load_services_config(args.services)

    runner = CaseRunner(
        strategy_config_path=args.config,
        models_config=models,
        services_config=services,
        cases_path=args.cases,
        model_override=args.model or None,
        output_dir=args.output,
        news_enabled=getattr(args, "news", False),
    )
    summaries = runner.run_all()

    success = sum(1 for s in summaries if s.get("status") == "success")
    failed = len(summaries) - success
    print(f"\nBatch complete: {success} succeeded, {failed} failed.")


def cmd_earnings(args: argparse.Namespace) -> None:
    """Run a batch of earnings-bet experiment cases.

    Cases can be supplied via --cases (YAML file) or auto-discovered from NPP
    using the strategy config's universe + date range (default when --cases is
    omitted).
    """
    from trading.case_runner import CaseRunner, query_earnings_cases_from_npp
    from trading.core.config import load_models_config, load_services_config

    models = load_models_config(args.models)
    services = load_services_config(args.services)

    news = getattr(args, "news", False)
    if args.cases:
        runner = CaseRunner(
            strategy_config_path=args.config,
            models_config=models,
            services_config=services,
            cases_path=args.cases,
            model_override=args.model or None,
            output_dir=args.output,
            news_enabled=news,
        )
    else:
        print("[earnings] No --cases file provided — querying NPP for events...")
        cases = query_earnings_cases_from_npp(args.config, services)
        print(f"[earnings] {len(cases.cases)} cases discovered from NPP")
        for c in cases.cases:
            print(f"  {c.ticker:6s}  {c.earnings_date}  ({c.fiscal_period})  {c.start} → {c.end}")
        print()
        runner = CaseRunner(
            strategy_config_path=args.config,
            models_config=models,
            services_config=services,
            cases=cases,
            model_override=args.model or None,
            output_dir=args.output,
            news_enabled=news,
        )

    summaries = runner.run_all()

    success = sum(1 for s in summaries if s.get("status") == "success")
    failed = len(summaries) - success
    print(f"\nBatch complete: {success} succeeded, {failed} failed.")


def _build_generic_summary(runner) -> "GenericPortfolioSummary":
    """Project the live (or reconstructed) runner into a generic portfolio
    summary.

    Today the only caller is the disk-reconstruction capture path, which has
    no live ``Runner`` — ``runner`` is always ``None`` and this returns a
    zero-valued summary. The helper is shaped to absorb a live runner once
    that capture path lands; until then, ``generic`` in the on-disk envelope
    is intentionally minimal.
    """
    from trading.checkpoint.captured import GenericPortfolioSummary

    pf = None
    if runner is not None:
        # Prefer a live state if we have one; fall back to a captured snapshot.
        state = getattr(runner, "state", None)
        if state is not None:
            pf = getattr(state, "portfolio", None)
        if pf is None:
            pf = getattr(runner, "_last_portfolio_snapshot", None)
    if pf is None:
        return GenericPortfolioSummary(
            cash=0.0, positions=[], realized_pnl=0.0, unrealized_pnl=0.0,
            gross_exposure=0.0, net_delta=0.0,
        )
    positions: list[dict] = []
    net_delta = 0.0
    gross = 0.0
    for p in (getattr(pf, "positions", []) or []):
        contract = getattr(p, "contract", None)
        qty = float(getattr(p, "qty", 0) or 0)
        mark = float(getattr(p, "mark_price", 0.0) or 0.0)
        delta = getattr(p, "delta", None)
        # market_value is contract-multiplier-aware in PMB; for the summary
        # we project a per-contract notional approximation: qty * mark.
        # Equities: qty * mark; options: qty * mark * 100 (multiplier).
        is_option = isinstance(contract, str) and contract.startswith("O:")
        mult = 100.0 if is_option else 1.0
        mv = qty * mark * mult
        if delta is not None:
            net_delta += float(delta) * qty
        gross += abs(mv)
        positions.append({
            "contract": contract,
            "qty": int(qty),
            "mark_price": mark,
            "market_value": mv,
            "delta": float(delta) if delta is not None else None,
        })
    return GenericPortfolioSummary(
        cash=float(getattr(pf, "cash", 0.0) or 0.0),
        positions=positions,
        realized_pnl=float(getattr(pf, "realized_pnl", 0.0) or 0.0),
        unrealized_pnl=float(getattr(pf, "unrealized_pnl", 0.0) or 0.0),
        gross_exposure=gross,
        net_delta=net_delta,
    )


def _build_family_view(runner) -> dict | None:
    """Ask the active strategy to project its family view.

    Today the only caller is the disk-reconstruction capture path, which has
    no live ``Runner.strategy`` — ``runner`` is always ``None`` and this
    returns ``None``. The helper is shaped to absorb a live runner once that
    capture path lands; until then, ``family_view`` in the on-disk envelope
    is None.
    """
    if runner is None:
        return None
    strat = getattr(runner, "strategy", None)
    if strat is None or not hasattr(strat, "project_family_view"):
        return None
    state = getattr(runner, "state", None)
    portfolio = getattr(state, "portfolio", None) if state is not None else None
    try:
        return strat.project_family_view(portfolio, state)
    except Exception:  # pragma: no cover — defensive
        logging.getLogger(__name__).warning(
            "project_family_view failed during capture-checkpoint; emitting None",
            exc_info=True,
        )
        return None


def cmd_capture_checkpoint(args: argparse.Namespace) -> None:
    """Reconstruct a lossy RunnerCheckpoint from an exited run's portfolio.jsonl,
    then wrap it in a CapturedCheckpoint envelope.

    Phase 1: only the exited-run reconstruction path is supported. Live-run
    checkpoint capture via IPC is Plan B scope. Because this path reconstructs
    from disk and never builds a Runner instance, we pass ``runner=None`` to
    the helpers — yielding a zero-valued generic summary and ``None`` family
    view, which is the legitimate degenerate output for the reconstruction
    path.
    """
    import json
    import os
    from dataclasses import asdict
    from datetime import datetime, timezone
    from pathlib import Path

    from trading.checkpoint.captured import CapturedCheckpoint
    from trading.ingest.runner_checkpoint import RunnerCheckpoint

    # run_dir convention per spec §3.2: ~/LiveOption/runs/<run_id>/
    run_dir = Path.home() / "LiveOption" / "runs" / args.run_id
    pf = run_dir / "portfolio.jsonl"
    if not pf.exists():
        raise SystemExit(
            f"portfolio.jsonl not found for run {args.run_id} at {pf}"
        )
    lines = pf.read_text().splitlines()
    if not lines:
        raise SystemExit(f"portfolio.jsonl is empty for run {args.run_id}")
    last = json.loads(lines[-1])
    ckpt = RunnerCheckpoint(
        version=1,
        trading_state={
            "step_id": last.get("step_id", 0),
            "current_ts": last.get("event_ts", ""),
        },
        accumulators={
            "last_portfolio": last.get("portfolio", {}),
            "reconstructed": True,
        },
        pmb_session_id="",
        pmb_account_id="",
        strategy_checkpoint={"_reconstructed": True, "_version": 1},
        llm_client_state={},
        run_config={},
    )
    # Disk reconstruction path: no live runner instance is available, so the
    # generic summary will be zero-valued and family_view will be None.
    # Coerce runner_state through json.dumps(default=str) so non-JSON
    # primitives (datetime, Decimal, numpy scalars) survive — restores the
    # safety net the prior RunnerCheckpoint.save() had.
    runner_state_dict = json.loads(json.dumps(asdict(ckpt), default=str))
    captured = CapturedCheckpoint(
        schema_version="1",
        run_id=args.run_id,
        decision_id=args.decision_id,
        captured_at=datetime.now(timezone.utc),
        generic=_build_generic_summary(None),
        family_view=_build_family_view(None),
        runner_state=runner_state_dict,
    )
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp file then os.replace so a crash mid-write can't leave
    # a partially written checkpoint on disk.
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(captured.model_dump_json(indent=2))
    os.replace(tmp_path, output_path)
    print(
        f"[capture-checkpoint] wrote {args.output_path} "
        f"(reconstructed=True from {pf.name})"
    )


def cmd_resume_from_checkpoint(args: argparse.Namespace) -> None:
    """Resume a run from a saved RunnerCheckpoint (diverge mode only)."""
    from pathlib import Path

    from trading.ingest.resume import run_from_checkpoint

    if args.checkpoint_path:
        ckpt_path = Path(args.checkpoint_path)
    else:
        ckpt_path = (
            Path.home()
            / "LiveOption"
            / "runs"
            / "checkpoints"
            / args.checkpoint_id
        )
    run_dir = Path.home() / "LiveOption" / "runs" / args.run_id
    result = run_from_checkpoint(
        checkpoint_path=ckpt_path,
        mode=args.mode,
        prompt_override=args.prompt_override,
        model_override=args.model_override,
        web_ingest=args.web_ingest,
        run_id=args.run_id,
        run_dir=run_dir,
    )
    print(f"[resume] done: {result}")


def cmd_eval(args: argparse.Namespace) -> None:
    """Evaluate a completed run from its JSONL log directory."""
    scales = [s.strip() for s in args.scales.split(",")]
    print(f"[EVAL] logdir={args.logdir}, scales={scales}")
    print("Evaluation not yet implemented.")


def cmd_task(args: argparse.Namespace) -> None:
    """Use the same versioned task endpoints as the browser."""
    import yaml
    from pydantic import ValidationError

    try:
        _cmd_task(args)
    except ValidationError as exc:
        details = "; ".join(item["msg"] for item in exc.errors(include_input=False))
        raise SystemExit(f"Invalid task: {details}") from None
    except (json.JSONDecodeError, yaml.YAMLError):
        raise SystemExit("Invalid task file: expected valid YAML or JSON") from None
    except FileExistsError:
        raise SystemExit("Report output already exists; choose a new file") from None
    except OSError:
        raise SystemExit("Cannot read the task/token file or write the report; check paths and permissions") from None


def _cmd_task(args: argparse.Namespace) -> None:
    token = os.getenv("LIVEOPTION_ACCESS_TOKEN", "").strip()
    if args.token_file:
        token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("Set LIVEOPTION_ACCESS_TOKEN or --token-file")
    from trading.task_client import TaskClient, TaskError
    client = TaskClient(args.server, token)
    run_id = urllib.parse.quote(args.run_id, safe="") if args.run_id else ""
    paths = {
        "submit": ("POST", "/api/tasks"),
        "presets": ("GET", "/api/tasks/presets"),
        "models": ("GET", "/api/model-settings"),
        "list": ("GET", "/api/runs"),
        "logs": ("GET", f"/api/tasks/{run_id}/logs" if run_id else ""),
        "returns": ("GET", f"/api/tasks/{run_id}/returns" if run_id else ""),
        "status": ("GET", f"/api/tasks/{run_id}" if run_id else ""),
        "analysis": ("GET", f"/api/tasks/{run_id}/analysis" if run_id else ""),
        "report": ("GET", f"/api/tasks/{run_id}/report?format={getattr(args, 'format', 'json')}" if run_id else ""),
    }
    if args.action == "models" and getattr(args, "refresh", False):
        if not getattr(args, "group_id", None):
            raise SystemExit("--group-id is required for models --refresh")
        paths["models"] = ("POST", f"/api/model-settings/{urllib.parse.quote(args.group_id, safe='')}/models/refresh")
    method, path = paths[args.action]
    if not path:
        raise SystemExit("--run-id is required for status, analysis, report, logs and returns")
    body = None
    if args.action == "submit":
        if not args.spec:
            raise SystemExit("--spec is required for submit")
        source = Path(args.spec).read_text(encoding="utf-8")
        value = json.loads(source) if args.spec.endswith(".json") else __import__("yaml").safe_load(source)
        from liveoption_web.runs.protocol import RunSpecV1
        body = RunSpecV1.model_validate(value).model_dump(mode="json")
    try:
        content = client.request(path, method, body, text=True)
    except TaskError as exc:
        raise SystemExit(str(exc)) from exc
    if args.action == "report":
        if getattr(args, "output", None):
            # Exclusive creation prevents accidental replacement of a previous report.
            with Path(args.output).open("x", encoding="utf-8") as handle:
                handle.write(content)
        else:
            print(content)
    else:
        print(json.dumps(json.loads(content), indent=2, ensure_ascii=False))


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser (factored out so tests can reuse it)."""
    parser = argparse.ArgumentParser(
        prog="trading",
        description="Multi-agent trading pipeline CLI",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable DEBUG logging"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- run --
    run_parser = subparsers.add_parser("run", help="Execute a single trading run")
    run_parser.add_argument("--config", required=True, help="Path to YAML config file")
    run_parser.add_argument("--output", default="./output",
                            help="Output root directory (default: ./output)")
    run_parser.add_argument("--model", default=None,
                            help="(deprecated) Model label for output path; use --llm-model instead")
    run_parser.add_argument("--dry-run", action="store_true",
                            help="Print config summary without executing")
    run_parser.add_argument("--web-ingest", default=None,
                            help="FastAPI ingest URL; if set, events POST'd in addition to JSONL")
    run_parser.add_argument("--run-id", default=None,
                            help="External run ID (ULID). If omitted, runner generates one.")
    # -- run overrides (see trading/core/overrides.py) --
    run_parser.add_argument("--start", type=date.fromisoformat, default=None,
                            help="Override experiment.start (YYYY-MM-DD)")
    run_parser.add_argument("--end", type=date.fromisoformat, default=None,
                            help="Override experiment.end (YYYY-MM-DD)")
    run_parser.add_argument("--bankroll", type=float, default=None,
                            help="Override experiment.initial_cash")
    run_parser.add_argument("--strategy", default=None,
                            help="Override strategy.name (registry key)")
    run_parser.add_argument("--llm-model", default=None,
                            help="Override strategy.params.llm_model (LLM strategies only)")
    run_parser.add_argument("--decision-interval-minutes", type=int, default=None)
    run_parser.add_argument("--max-concurrent-positions", type=int, default=None)
    run_parser.add_argument("--dte-range", type=lambda s: tuple(int(v) for v in s.split(",")), default=None)
    run_parser.add_argument("--prompt-override", default=None,
                            help="Override strategy.params.prompt_override. "
                                 "Use @PATH to read from a file (16 KB cap, path must be "
                                 "under repo root).")
    run_parser.add_argument(
        "--tickers",
        type=lambda s: [t.strip().upper() for t in s.split(",") if t.strip()],
        default=None,
        help="Comma-separated ticker subset; reduces experiment.universe to this list.",
    )

    # -- overlay --
    ov_parser = subparsers.add_parser("overlay", help="Run batch overlay experiments")
    ov_parser.add_argument("--config", required=True,
                           help="Base strategy YAML (overlay_hedge_qqq.yaml etc.)")
    ov_parser.add_argument("--cases", required=True,
                           help="Cases YAML (configs/cases/overlay_cases.yaml)")
    ov_parser.add_argument("--models",
                           default="configs/models.yaml",
                           help="Models YAML (default: configs/models.yaml)")
    ov_parser.add_argument("--services",
                           default="configs/services.yaml",
                           help="Services YAML (default: configs/services.yaml)")
    ov_parser.add_argument("--model", default=None,
                           help="Override model name (e.g. deepseek-v3)")
    ov_parser.add_argument("--output", default="./output",
                           help="Output root directory (default: ./output)")
    ov_parser.add_argument("--news", action="store_true",
                           help="Enable NPP news/event context in LLM prompts (default: off)")

    # -- intraday --
    id_parser = subparsers.add_parser("intraday", help="Run batch intraday 0DTE experiments")
    id_parser.add_argument("--config", required=True,
                           help="Base strategy YAML (intraday_0dte.yaml)")
    id_parser.add_argument("--cases", required=True,
                           help="Cases YAML (configs/cases/intraday_0dte_cases.yaml)")
    id_parser.add_argument("--models",
                           default="configs/models.yaml",
                           help="Models YAML (default: configs/models.yaml)")
    id_parser.add_argument("--services",
                           default="configs/services.yaml",
                           help="Services YAML (default: configs/services.yaml)")
    id_parser.add_argument("--model", default=None,
                           help="Override model name (e.g. deepseek-v3)")
    id_parser.add_argument("--output", default="./output",
                           help="Output root directory (default: ./output)")
    id_parser.add_argument("--news", action="store_true",
                           help="Enable NPP news/event context in LLM prompts (default: off)")

    # -- earnings --
    eb_parser = subparsers.add_parser("earnings", help="Run batch earnings-bet experiments")
    eb_parser.add_argument("--config", required=True,
                           help="Base strategy YAML (configs/strategies/earnings_bet.yaml)")
    eb_parser.add_argument("--cases", default=None,
                           help="Cases YAML (optional — auto-discovered from NPP when omitted)")
    eb_parser.add_argument("--models",
                           default="configs/models.yaml",
                           help="Models YAML (default: configs/models.yaml)")
    eb_parser.add_argument("--services",
                           default="configs/services.yaml",
                           help="Services YAML (default: configs/services.yaml)")
    eb_parser.add_argument("--model", default=None,
                           help="Override model name (e.g. deepseek-v3)")
    eb_parser.add_argument("--output", default="./output",
                           help="Output root directory (default: ./output)")
    eb_parser.add_argument("--news", action="store_true",
                           help="Enable NPP news/event context in LLM prompts (default: off)")

    # -- capture-checkpoint --
    cc_parser = subparsers.add_parser(
        "capture-checkpoint",
        help="Reconstruct a RunnerCheckpoint from a completed run's portfolio.jsonl (lossy)",
    )
    cc_parser.add_argument("--run-id", required=True,
                           help="Run ID whose ~/LiveOption/runs/<run_id> dir holds portfolio.jsonl")
    cc_parser.add_argument("--decision-id", required=True,
                           help="Anchor decision ID for labeling; not used by reconstruction")
    cc_parser.add_argument("--output-path", required=True,
                           help="Path to write the reconstructed checkpoint JSON")

    # -- resume-from-checkpoint --
    rp = subparsers.add_parser(
        "resume-from-checkpoint",
        help="Resume a run from a saved checkpoint (diverge mode only)",
    )
    rp.add_argument("--checkpoint-id", required=True,
                    help="Checkpoint ID (used to infer path under ~/LiveOption/runs/checkpoints)")
    rp.add_argument("--checkpoint-path", default=None,
                    help="Explicit checkpoint directory (overrides --checkpoint-id derivation)")
    rp.add_argument("--mode", choices=["diverge"], default="diverge",
                    help="Fork mode (Phase 1: diverge only)")
    rp.add_argument("--prompt-override", default=None,
                    help="Override strategy prompt template")
    rp.add_argument("--model-override", default=None,
                    help="Override LLM model name")
    rp.add_argument("--web-ingest", default=None,
                    help="FastAPI ingest URL; if set, events POST'd in addition to JSONL")
    rp.add_argument("--run-id", required=True,
                    help="External run ID (ULID) for the forked run")

    # -- eval --
    eval_parser = subparsers.add_parser("eval", help="Evaluate a completed run")
    eval_parser.add_argument("--logdir", required=True,
                             help="Path to run output directory")
    eval_parser.add_argument("--scales", default="1m,5m,15m,1d",
                             help="Comma-separated evaluation scales")

    task_parser = subparsers.add_parser("task", help="Web/CLI shared task protocol")
    task_parser.add_argument("action", choices=["submit", "status", "analysis", "report", "returns", "presets", "models", "list", "logs"])
    task_parser.add_argument("--server", default=os.getenv("LIVEOPTION_WEB_URL", "http://127.0.0.1:3001"))
    task_parser.add_argument("--token-file", default=None, help="Path to web access token; or LIVEOPTION_ACCESS_TOKEN")
    task_parser.add_argument("--spec", default=None, help="Versioned task YAML/JSON for submit")
    task_parser.add_argument("--run-id", default=None, help="Run ID for status/analysis")
    task_parser.add_argument("--format", choices=["json", "markdown", "html"], default="json", help="Report format")
    task_parser.add_argument("--output", default=None, help="Save report to a new file (never overwrite)")
    task_parser.add_argument("--group-id", default=None, help="API group for models --refresh")
    task_parser.add_argument("--refresh", action="store_true", help="Refresh the selected API group's /models catalog")

    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the appropriate command."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.command == "run":
        cmd_run(args)
    elif args.command == "overlay":
        cmd_overlay(args)
    elif args.command == "intraday":
        cmd_intraday(args)
    elif args.command == "earnings":
        cmd_earnings(args)
    elif args.command == "capture-checkpoint":
        cmd_capture_checkpoint(args)
    elif args.command == "resume-from-checkpoint":
        cmd_resume_from_checkpoint(args)
    elif args.command == "eval":
        cmd_eval(args)
    elif args.command == "task":
        cmd_task(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
