"""Merge optional CLI/web override values into a loaded RunConfig.

Single merge point so CLI, Web Job Server, and future CI all agree on
what "override" means.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from trading.core.config import RunConfig

# Strategies that accept llm_model / prompt_override in their params dict.
# Recognized by naming convention: the token "llm" appears somewhere in the
# registry key. Matches both the existing `weekly_llm` / `intraday_llm` keys
# and any future `llm_<provider>_<variant>` style names.
_LLM_TOKEN = "llm"

BANKROLL_MIN = 1_000.0
BANKROLL_MAX = 10_000_000.0


class OverrideError(ValueError):
    """Raised when an override combination is invalid."""


class _ArgsLike(Protocol):
    start: date | None
    end: date | None
    bankroll: float | None
    strategy: str | None
    llm_model: str | None
    prompt_override: str | None
    tickers: list[str] | None
    decision_interval_minutes: int | None
    max_concurrent_positions: int | None
    dte_range: tuple[int, int] | None


# Map legacy model identifiers to their LLMClient provider key.
# Matches _PROVIDER_MAP in trading/tools/llm_client.py.
_MODEL_TO_PROVIDER = {
    "gpt-4.1-mini": "openai",
    "gpt-5.6-terra": "openai_compatible",
    "deepseek-chat": "deepseek",
    "gemini-2.5-flash": "gemini",
    "gemini-3-flash": "anthropic",
    "claude-sonnet-4-5": "anthropic",
    "qwen2.5-max": "qwen",
}


def _provider_for_model(model: str) -> str | None:
    """Return the LLMClient provider key for ``model``, or None if unknown.

    Models not in this set fall back to whatever the YAML's
    llm_provider is. This keeps the override path additive.
    """
    return _MODEL_TO_PROVIDER.get(model)


# Strategies that accept llm_provider / llm_model params even though their
# registry key does not contain the `llm` token. The three preset scenarios
# (overlay_profit, earnings_bet, intraday_llm) are all LLM-driven; the first
# two predate the naming convention.
_LLM_AWARE_STRATEGIES: frozenset[str] = frozenset({
    "overlay_profit",
    "overlay_hedge",
    "overlay_hybrid",
    "earnings_bet",
})


def _is_llm_strategy(name: str) -> bool:
    """True if the strategy accepts llm_provider/llm_model params.

    Either the registry key contains the `llm` token (split on underscores
    so substrings like `"silent_talisman"` never spuriously hit) — or the
    name is in the `_LLM_AWARE_STRATEGIES` allowlist for LLM-driven
    strategies whose name predates the naming convention.
    """
    lower = name.lower()
    if _LLM_TOKEN in lower.split("_"):
        return True
    return lower in _LLM_AWARE_STRATEGIES


def apply_overrides(config: RunConfig, args: _ArgsLike) -> RunConfig:
    """Return a config with any non-None override fields applied.

    The config is mutated in place and returned. `args` is a duck-typed
    namespace (argparse.Namespace, or a Pydantic model with matching
    attributes). Absent overrides are indicated by None.

    Raises:
        OverrideError: on invalid combinations (e.g. llm_model on a
            non-LLM strategy, bankroll out of range, start > end).
    """
    start = getattr(args, "start", None)
    end = getattr(args, "end", None)
    bankroll = getattr(args, "bankroll", None)
    strategy = getattr(args, "strategy", None)
    llm_model = getattr(args, "llm_model", None)
    prompt_override = getattr(args, "prompt_override", None)

    # Dates — stored as "YYYY-MM-DD" strings on ExperimentConfig.
    if start is not None:
        config.experiment.start = start.isoformat()
    if end is not None:
        config.experiment.end = end.isoformat()

    # Validate the effective start/end after mutation, always. A partial
    # override (e.g. only start) is the common web-form case and can silently
    # invert the window against the loaded config's unchanged endpoint. Fail
    # here at the single merge point with a concrete error, rather than let
    # an empty window surface deep in the runner.
    if start is not None or end is not None:
        _validate_date_window(config.experiment.start, config.experiment.end)

    # Bankroll → initial_cash.
    if bankroll is not None:
        if not (BANKROLL_MIN <= bankroll <= BANKROLL_MAX):
            raise OverrideError(
                f"bankroll {bankroll} out of range [{BANKROLL_MIN}, {BANKROLL_MAX}]"
            )
        config.experiment.initial_cash = bankroll

    # Strategy swap BEFORE llm_model check so we validate against the new name.
    if strategy is not None:
        config.strategy.name = strategy

    if llm_model is not None:
        if not _is_llm_strategy(config.strategy.name):
            raise OverrideError(
                f"llm_model supplied but strategy '{config.strategy.name}' "
                "is not an LLM strategy"
            )
        config.strategy.params["llm_model"] = llm_model
        inferred_provider = _provider_for_model(llm_model)
        if inferred_provider is not None:
            config.strategy.params["llm_provider"] = inferred_provider

    if prompt_override is not None:
        if not _is_llm_strategy(config.strategy.name):
            raise OverrideError(
                f"prompt_override supplied but strategy '{config.strategy.name}' "
                "is not an LLM strategy"
            )
        config.strategy.params["prompt_override"] = prompt_override

    interval = getattr(args, "decision_interval_minutes", None)
    positions = getattr(args, "max_concurrent_positions", None)
    dte_range = getattr(args, "dte_range", None)
    if any(v is not None for v in (interval, positions, dte_range)):
        if config.strategy.name != "intraday_llm":
            raise OverrideError("intraday settings require intraday_llm strategy")
        if interval is not None:
            if interval not in (1, 5, 15, 30):
                raise OverrideError("decision_interval_minutes must be 1, 5, 15, or 30")
            config.strategy.params["decision_interval_minutes"] = interval
        if positions is not None:
            if not 1 <= positions <= 50:
                raise OverrideError("max_concurrent_positions must be between 1 and 50")
            config.strategy.params["max_positions"] = positions
        if dte_range is not None:
            if len(dte_range) != 2:
                raise OverrideError("dte_range requires exactly two values")
            lo, hi = dte_range
            if not 0 <= lo <= hi <= 365:
                raise OverrideError("dte_range must satisfy 0 <= min <= max <= 365")
            config.strategy.params["dte_min"] = lo
            config.strategy.params["dte_max"] = hi
            config.data_policy.chain_filter_dte_min = lo
            config.data_policy.chain_filter_dte_max = hi

    tickers = getattr(args, "tickers", None)
    if tickers is not None:
        if len(tickers) == 0:
            raise OverrideError("tickers override is empty (would remove all symbols)")
        scenario_universe = set(config.experiment.universe)
        bad = [t for t in tickers if t not in scenario_universe]
        if bad:
            raise OverrideError(
                f"tickers {bad!r} not in scenario universe "
                f"{sorted(scenario_universe)}"
            )
        # Preserve caller-supplied order; dedupe.
        seen: set[str] = set()
        ordered: list[str] = []
        for t in tickers:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        config.experiment.universe = ordered

    return config


def _validate_date_window(start: str, end: str) -> None:
    # Compare as strings — ISO-8601 sorts chronologically.
    s = start.split("T", 1)[0]
    e = end.split("T", 1)[0]
    if s > e:
        raise OverrideError(f"start {s!r} is after the end {e!r}")
