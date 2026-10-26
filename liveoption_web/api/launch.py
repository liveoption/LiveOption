"""``POST /v1/runs`` — scenario-catalog launch endpoint (Plan B, Task 7).

The handler validates + authorizes a launch request, composes the
``trading.cli run`` argv with override flags, writes an optional prompt
override file under ``.web-runs/``, inserts a ``kind='batch'`` row with
the merged spec snapshot in ``launch_spec_json``, and delegates to
:func:`liveoption_web.runs.supervisor.spawn_batch` for the subprocess.

"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from fastapi import Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from liveoption_web.api.deps import Actor
from liveoption_web.api.problem import problem_response
from liveoption_web.db.connection import open_db
from liveoption_web.runs.catalog import (
    LAUNCH_ALLOWLIST,
    LLM_MODEL_CHOICES,
    SCENARIOS,
    Scenario,
)
from liveoption_web.runs.families import pick_override_model
from liveoption_web.runs.supervisor import RunCapExceeded, spawn_batch


# Two path constants — tests may monkeypatch WEB_RUNS_DIR independently
# of REPO_ROOT (the prompt-file scratch directory and the scenario-config
# anchor are semantically distinct, even when they usually resolve under
# the same tree in production).
REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_RUNS_DIR = REPO_ROOT / ".web-runs"


_SECRET_LEAK_RE = re.compile(r"(?i)\b(sk|pk)[-_][A-Za-z0-9]{12,}")


class LaunchRunOverrides(BaseModel):
    """Optional overrides applied on top of the scenario's base YAML."""

    start: date | None = None
    end: date | None = None
    bankroll: float | None = None
    llm_model: str | None = None
    prompt_override: str | None = Field(default=None, max_length=16_384)
    tickers: list[str] | None = None
    quarter: Literal["2025Q1", "2025Q2", "2025Q3", "2025Q4"] | None = None

    @field_validator("bankroll")
    @classmethod
    def _bankroll_range(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not (v > 0 and v < 10_000_000):
            raise ValueError("bankroll must be > 0 and < 10_000_000")
        return v

    @field_validator("llm_model")
    @classmethod
    def _llm_model_allowed(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in LLM_MODEL_CHOICES:
            raise ValueError(
                f"llm_model {v!r} not in allowed set {sorted(LLM_MODEL_CHOICES)}"
            )
        return v

    @field_validator("prompt_override")
    @classmethod
    def _prompt_no_secret(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if _SECRET_LEAK_RE.search(v):
            raise ValueError(
                "prompt_override looks like it contains an API secret"
            )
        return v


class LaunchRunRequest(BaseModel):
    """Body of ``POST /v1/runs``.

    Phase C: ``overrides`` is parsed as a raw dict here so the handler can
    re-validate it against the scenario's family-specific Pydantic model
    (``extra='forbid'``). The family model rejects unknown fields with 422
    before any side effects.
    """

    scenario_id: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    title: str | None = Field(default=None, max_length=200)


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid4().hex[:6]}"


def _check_authenticated(actor: Actor) -> str:
    """Return the caller's normalized email (or empty) after checking auth.

    Clerk is currently invite-only — registration is gated at the identity
    provider, so any Clerk-verified session is already an authorized user.
    We require a non-empty ``X-Actor-User`` (Clerk userId) to prove the
    SSR proxy resolved a Clerk session. Email is NOT required: Clerk
    doesn't put it in the session token by default, and the fallback
    lookup path may or may not be configured. ``created_by`` on the row
    falls back to the userId when email is absent — the value is still
    a stable Clerk-issued identifier, just less human-readable.

    ``LAUNCH_ALLOWLIST`` remains as dormant second-gate infrastructure
    in case Clerk registration ever opens to the public.
    """

    if not actor.user_id or not actor.user_id.strip():
        raise problem_response(
            403,
            "launch-not-authorized",
            "Launch not authorized",
            detail="no authenticated Clerk session on request",
        )
    return (actor.email or "").strip().lower()


def _check_overrides_fit_scenario(
    scenario: Scenario, overrides: BaseModel
) -> None:
    """Raise 400 if any override key is not listed in ``scenario.overridable``."""

    supplied = set(overrides.model_dump(exclude_none=True).keys())
    disallowed = supplied - set(scenario.overridable)
    if disallowed:
        raise problem_response(
            400,
            "override-not-allowed",
            "Override not allowed for scenario",
            detail=(
                f"scenario {scenario.id!r} does not allow overriding "
                f"{sorted(disallowed)}"
            ),
        )


_QUARTER_BOUNDS: dict[str, tuple[date, date]] = {
    "2025Q1": (date(2025, 1, 1), date(2025, 3, 31)),
    "2025Q2": (date(2025, 4, 1), date(2025, 6, 30)),
    "2025Q3": (date(2025, 7, 1), date(2025, 9, 30)),
    "2025Q4": (date(2025, 10, 1), date(2025, 12, 31)),
}


def _quarter_to_dates(quarter: str) -> tuple[date, date]:
    """Map a quarter literal (e.g. '2025Q3') to its (start, end) dates."""
    return _QUARTER_BOUNDS[quarter]


def _check_frequency_compatibility(strategy_name: str, yaml_frequency: str) -> None:
    """Raise problem_response 400 if strategy's REQUIRED_FREQUENCIES
    doesn't include yaml_frequency.

    No-op when the strategy isn't in STRATEGY_REGISTRY or its
    REQUIRED_FREQUENCIES is None — keeps the guard additive and
    avoids coupling the launcher to strategy import order.
    """
    import trading.agents.strategy  # noqa: F401  trigger registry registration
    from trading.core.registry import STRATEGY_REGISTRY

    cls = STRATEGY_REGISTRY.get(strategy_name)
    if cls is None:
        return
    required = getattr(cls, "REQUIRED_FREQUENCIES", None)
    if required is None:
        return
    if yaml_frequency not in required:
        raise problem_response(
            400,
            "scenario-strategy-frequency-mismatch",
            "Scenario frequency incompatible with strategy",
            detail=(
                f"strategy={strategy_name!r} requires frequency in "
                f"{sorted(required)}, scenario YAML declares "
                f"{yaml_frequency!r}"
            ),
        )


def _compose_argv(
    scenario: Scenario,
    overrides: BaseModel,
    run_id: str,
    prompt_file: Path | None,
    job_server_url: str,
) -> list[str]:
    """Build the argv for ``trading.cli run`` with override flags.

    Date precedence: explicit ``start``/``end`` > ``quarter`` mapping >
    scenario YAML defaults. ``quarter`` is preserved in
    ``launch_spec_json`` for audit but never forwarded to the CLI
    (it's a UI concept; the CLI only knows dates).
    """

    start = getattr(overrides, "start", None)
    end = getattr(overrides, "end", None)
    bankroll = getattr(overrides, "bankroll", None)
    llm_model = getattr(overrides, "llm_model", None)
    tickers = getattr(overrides, "tickers", None)
    quarter = getattr(overrides, "quarter", None)

    if quarter is not None and start is None and end is None:
        start, end = _quarter_to_dates(quarter)

    argv: list[str] = [
        sys.executable,
        "-m",
        "trading.cli",
        "run",
        "--config",
        scenario.config_path,
    ]
    if start is not None:
        argv.extend(["--start", start.isoformat()])
    if end is not None:
        argv.extend(["--end", end.isoformat()])
    if bankroll is not None:
        argv.extend(["--bankroll", str(float(bankroll))])
    if llm_model is not None:
        argv.extend(["--llm-model", llm_model])
    interval = getattr(overrides, "decision_interval_minutes", None)
    positions = getattr(overrides, "max_concurrent_positions", None)
    dte_range = getattr(overrides, "dte_range", None)
    if interval is not None:
        argv.extend(["--decision-interval-minutes", str(interval)])
    if positions is not None:
        argv.extend(["--max-concurrent-positions", str(positions)])
    if dte_range is not None:
        argv.extend(["--dte-range", f"{dte_range[0]},{dte_range[1]}"])
    if tickers:
        argv.extend(["--tickers", ",".join(tickers)])
    if prompt_file is not None:
        argv.extend(["--prompt-override", f"@{prompt_file}"])
    argv.extend(["--web-ingest", job_server_url])
    argv.extend(["--run-id", run_id])
    return argv


class ScenarioConfigCorruptError(RuntimeError):
    """Raised when a catalog-listed scenario YAML is missing or malformed."""


def _peek_scenario_yaml(config_path: str) -> dict[str, Any]:
    """Parse the scenario YAML for NOT NULL column seeding.

    The subprocess re-parses the same file authoritatively; this peek
    only exists to populate ``runs.model_id`` / ``provider`` / dates /
    ``storage_dir`` / ``universe_json`` before the row is inserted.

    Fails loud on any parse error rather than returning defaults —
    corrupted YAML indicates a deploy-level defect (catalog points at a
    file that no longer parses) that must not be masked as "run started
    with garbage metadata, failed at config load later". Tests cover
    this via a catalog integrity assertion; in production a corrupted
    YAML should page the operator, not silently accept launches.
    """

    path = REPO_ROOT / config_path
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError as exc:
        raise ScenarioConfigCorruptError(
            f"scenario config {config_path!r} not found at {path!s}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ScenarioConfigCorruptError(
            f"scenario config {config_path!r} failed YAML parse: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ScenarioConfigCorruptError(
            f"scenario config {config_path!r} top-level is not a mapping"
        )
    return data


def _seed_row_fields(
    scenario: Scenario,
    overrides: BaseModel,
    run_id: str,
    runs_dir: Path,
) -> dict[str, Any]:
    """Derive NOT NULL fields for the ``runs`` INSERT from the scenario YAML."""

    cfg = _peek_scenario_yaml(scenario.config_path)
    experiment = cfg.get("experiment", {}) if isinstance(cfg, dict) else {}
    strategy = cfg.get("strategy", {}) if isinstance(cfg, dict) else {}
    params = strategy.get("params", {}) if isinstance(strategy, dict) else {}

    universe = experiment.get("universe") or []
    o_start = getattr(overrides, "start", None)
    o_end = getattr(overrides, "end", None)
    o_llm_model = getattr(overrides, "llm_model", None)
    start = o_start.isoformat() if o_start else str(
        experiment.get("start") or "1970-01-01"
    )
    end = o_end.isoformat() if o_end else str(
        experiment.get("end") or "1970-01-01"
    )
    provider = str(params.get("llm_provider") or "unknown")
    model_id = o_llm_model or str(
        params.get("llm_model") or "unknown"
    )
    if o_llm_model:
        from trading.core.overrides import _provider_for_model
        provider = _provider_for_model(o_llm_model) or provider
    storage_dir = str(Path(runs_dir) / run_id)
    return {
        "universe_json": json.dumps(universe),
        "date_start": start,
        "date_end": end,
        "provider": provider,
        "model_id": model_id,
        "storage_dir": storage_dir,
    }


async def handle_launch_run(
    request: Request, body: LaunchRunRequest, actor: Actor,
    *, scenario_override: Scenario | None = None,
    environment_override: dict[str, str] | None = None,
    spec_snapshot_override: dict[str, Any] | None = None,
    run_id_override: str | None = None,
) -> dict:
    """Compose, persist, and spawn a web-launched batch run."""

    settings = request.app.state.settings

    normalized_email = _check_authenticated(actor)
    # `created_by` stores something human-readable when email is
    # available; else falls back to the Clerk userId so the row still
    # attributes to a stable identity. Never NULL.
    created_by = normalized_email or actor.user_id or "unknown"

    scenario = scenario_override or SCENARIOS.get(body.scenario_id)
    if scenario is None:
        raise problem_response(
            400,
            "unknown-scenario",
            "Unknown scenario",
            detail=f"scenario_id={body.scenario_id!r} not in catalog",
        )

    # Phase C: pick the family-specific override model (extra='forbid')
    # and validate the raw dict body. Unknown / cross-family fields →
    # 422 problem+json before any side effects.
    override_model = pick_override_model(scenario.family)
    try:
        parsed_overrides = override_model.model_validate(body.overrides)
    except ValidationError as exc:
        raise problem_response(
            422,
            "override-validation-failed",
            "Override validation failed",
            detail=str(exc.errors()),
        )

    # Defense-in-depth: secret-leak check on prompt_override if present.
    prompt_override_str = getattr(parsed_overrides, "prompt_override", None)
    if prompt_override_str is not None and _SECRET_LEAK_RE.search(
        prompt_override_str
    ):
        raise problem_response(
            422,
            "override-validation-failed",
            "Override validation failed",
            detail="prompt_override looks like it contains an API secret",
        )

    _check_overrides_fit_scenario(scenario, parsed_overrides)

    # Peek the YAML to validate frequency × strategy compatibility before
    # we spawn a subprocess that would silently never trade.
    try:
        peeked_cfg = _peek_scenario_yaml(scenario.config_path)
    except ScenarioConfigCorruptError as exc:
        raise problem_response(
            500,
            "scenario-config-corrupt",
            "Scenario config is corrupt",
            detail=str(exc),
        )
    _check_frequency_compatibility(
        peeked_cfg.get("strategy", {}).get("name", ""),
        peeked_cfg.get("experiment", {}).get("frequency", ""),
    )

    o_start = getattr(parsed_overrides, "start", None)
    o_end = getattr(parsed_overrides, "end", None)
    if o_start is not None and o_end is not None and o_start > o_end:
        raise problem_response(
            400,
            "invalid-date-range",
            "Invalid date range",
            detail="start must be <= end",
        )

    run_id = run_id_override or _new_run_id()

    prompt_file: Path | None = None
    if prompt_override_str is not None:
        WEB_RUNS_DIR.mkdir(parents=True, exist_ok=True)
        prompt_file = WEB_RUNS_DIR / f"{run_id}.prompt"
        prompt_file.write_text(prompt_override_str, encoding="utf-8")

    argv = _compose_argv(
        scenario,
        parsed_overrides,
        run_id,
        prompt_file,
        settings.ingest_url,
    )

    overrides_dump = parsed_overrides.model_dump(mode="json", exclude_none=True)
    # Decimal → float for stable JSON serialization (preserves bankroll
    # as a number, not a string, matching the pre-Phase-C contract).
    if "bankroll" in overrides_dump:
        try:
            overrides_dump["bankroll"] = float(overrides_dump["bankroll"])
        except (TypeError, ValueError):
            pass
    spec_snapshot = {
        "scenario_id": scenario.id,
        "scenario_display_name": scenario.display_name,
        "config_path": scenario.config_path,
        "overrides": overrides_dump,
    }
    if spec_snapshot_override is not None:
        spec_snapshot = spec_snapshot_override
    launch_spec_json = json.dumps(spec_snapshot, sort_keys=True)

    try:
        seed = _seed_row_fields(scenario, parsed_overrides, run_id, settings.runs_dir)
    except ScenarioConfigCorruptError as exc:
        # Catalog points at a YAML that no longer parses. Fail loud so
        # operators see the deploy defect instead of letting the launch
        # 202 and only surface as a subprocess config-load failure later.
        raise problem_response(
            500,
            "scenario-config-corrupt",
            "Scenario config is corrupt",
            detail=str(exc),
        )
    now = _utc_now_z()

    async with open_db(settings.db_path) as db:
        await db.execute(
            """INSERT INTO runs(
                id, kind, scenario, config_path, model_id, provider,
                universe_json, date_start, date_end, status,
                status_updated_at, created_at, created_by, storage_dir,
                fork_prompt_override, fork_model_override, launch_spec_json,
                family, title
            ) VALUES (
                ?, 'batch', ?, ?, ?, ?,
                ?, ?, ?, 'pending',
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )""",
            (
                run_id,
                scenario.id,
                scenario.config_path,
                seed["model_id"],
                seed["provider"],
                seed["universe_json"],
                seed["date_start"],
                seed["date_end"],
                now,
                now,
                created_by,
                seed["storage_dir"],
                prompt_override_str,
                getattr(parsed_overrides, "llm_model", None),
                launch_spec_json,
                scenario.family.value if scenario.family else None,
                (body.title.strip() if body.title and body.title.strip() else None),
            ),
        )
        await db.commit()

    def _cleanup_prompt_file() -> None:
        if prompt_file is not None:
            try:
                prompt_file.unlink()
            except OSError:
                pass

    try:
        await spawn_batch(
            db_path=settings.db_path,
            runs_dir=settings.runs_dir,
            run_id=run_id,
            argv=argv,
            env={**os.environ, **(environment_override or {})},
            actor_email=actor.email,
            max_concurrent_runs=settings.max_concurrent_runs,
        )
    except RunCapExceeded as exc:
        # Supervisor already flipped the row to 'cancelled'; remove the
        # prompt file so orphans don't accumulate under .web-runs/.
        _cleanup_prompt_file()
        raise problem_response(
            429,
            "concurrency-cap-exceeded",
            "Concurrency cap exceeded",
            detail=str(exc),
        )
    except Exception:
        # Any other spawn-time failure (missing python binary, EACCES on
        # runs_dir, etc.): supervisor has already flipped the row to
        # 'failed'. Remove the orphaned prompt file before propagating so
        # .web-runs/ doesn't leak on hot-path failures.
        _cleanup_prompt_file()
        raise

    return {"run_id": run_id}
