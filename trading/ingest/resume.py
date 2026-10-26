"""run_from_checkpoint entrypoint. Phase 1: diverge only. Spec 6.10, NG9."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from trading.core.config import RunConfig
from trading.runner import TradingRunner
from trading.ingest.runner_checkpoint import RunnerCheckpoint


def run_from_checkpoint(
    *,
    checkpoint_path: Path,
    mode: Literal["diverge"],
    prompt_override: str | None,
    model_override: str | None,
    web_ingest: str | None,
    run_id: str,
    run_dir: Path,
) -> dict:
    """Resume a run from a saved RunnerCheckpoint.

    Phase 1: only ``diverge`` mode is supported (NG9). At least one override
    (prompt or model) is required to enforce the diverge contract.

    Args:
        checkpoint_path: Directory containing ``runner_checkpoint.json``, OR a
            direct path to the checkpoint JSON file.
        mode: ``"diverge"`` — replay is rejected (Phase 2 scope).
        prompt_override: If set, applied to ``runner.strategy.prompt_template``.
        model_override: If set, applied via ``set_model_override`` on the
            strategy's LLM client when supported.
        web_ingest: Optional FastAPI ingest URL for events.
        run_id: External run ID (ULID) for the forked run.
        run_dir: Output directory for the forked run.
    """
    if mode == "replay":
        raise ValueError(
            "replay mode deferred to Phase 2 (see NG9 / verify-replays CLI)"
        )
    if mode != "diverge":
        raise ValueError(f"unknown mode: {mode}")
    if not (prompt_override or model_override):
        raise ValueError(
            "diverge requires at least one of prompt_override or model_override"
        )

    ckpt_path = Path(checkpoint_path)
    if ckpt_path.is_dir():
        ckpt_file = ckpt_path / "runner_checkpoint.json"
    else:
        ckpt_file = ckpt_path
    ckpt = RunnerCheckpoint.load(ckpt_file)

    cfg = _reconstruct_run_config(ckpt.run_config, str(run_dir))

    # Ensure fork output dir exists for EventEmitter
    Path(run_dir).mkdir(parents=True, exist_ok=True)

    # Phase 1: prompt_override wiring is Plan B scope (no strategy currently
    # reads prompt_template / _prompt_override). Reject rather than silently
    # no-op.  model_override is honored when the strategy's LLM client was
    # built with models_config (see LLMClient.set_model_override).
    if prompt_override is not None:
        raise NotImplementedError(
            "prompt_override wiring is deferred to Plan B; "
            "use model_override in Phase 1"
        )

    runner = TradingRunner(cfg, web_ingest=web_ingest, run_id=run_id)
    runner._restore_from_checkpoint(ckpt)

    # Tell the runner's _main_loop this is a fork, so its run.started emit
    # carries kind=fork/fork_mode=diverge + the overridden model_id.  Single
    # bookend source — no duplicate run.started/run.finished on the fork path.
    runner._fork_context = {
        "kind": "fork",
        "fork_mode": "diverge",
        "model_override": model_override,
    }

    if model_override is not None:
        applied = False
        llm = getattr(runner.strategy, "_llm", None) or getattr(
            runner, "llm_client", None
        )
        if llm is not None and hasattr(llm, "set_model_override"):
            try:
                llm.set_model_override(model_override)
                applied = True
            except Exception:
                applied = False
        if not applied:
            # Stash so strategy.on_session_start (if later re-invoked) can
            # read it when constructing its LLM client.
            setattr(runner.strategy, "_model_override", model_override)

    # _main_loop owns the run.started / run.finished bookends and calls
    # events.close() in its finally block.  Exit code comes back via its
    # return dict (run_exit_code is stashed on the runner by _main_loop).
    result = runner._main_loop()
    exit_code = getattr(runner, "_last_exit_code", 0)
    return {"exit_code": exit_code, "result": result}


def _reconstruct_run_config(snapshot: dict, output_dir: str) -> RunConfig:
    """Build a minimal ``RunConfig`` from the checkpoint's saved run_config.

    Phase 1 Plan A scope:
      1. If ``snapshot["_config_path"]`` is present, load that YAML (preferred
         path — Plan B will populate this during promote).
      2. Otherwise, build an inline minimal RunConfig from strategy.name and
         experiment.start/end.  Requires these fields to be present in the
         snapshot; relies on pydantic defaults for sub-configs.
    """
    from trading.core.config import (
        ExperimentConfig,
        RunConfig as _RunConfig,
        StrategyConfig,
        EvaluationConfig,
        load_config,
    )

    cfg_path = snapshot.get("_config_path")
    if cfg_path:
        cfg = load_config(cfg_path)
        cfg.evaluation.output_dir = output_dir
        return cfg

    strategy = snapshot.get("strategy") or {}
    exp = snapshot.get("experiment") or {}
    strategy_name = strategy.get("name")
    start = exp.get("start")
    end = exp.get("end")

    if not (strategy_name and start and end):
        raise ValueError(
            "checkpoint run_config missing required fields: strategy.name + "
            "experiment.start + experiment.end (or _config_path)"
        )

    cfg = _RunConfig(
        experiment=ExperimentConfig(
            run_id=exp.get("run_id") or "resume",
            start=start,
            end=end,
            universe=exp.get("universe") or ["SPY"],
            frequency=exp.get("frequency", "1d"),
        ),
        strategy=StrategyConfig(
            name=strategy_name,
            params=strategy.get("params") or {},
        ),
        evaluation=EvaluationConfig(output_dir=output_dir),
    )
    return cfg
