"""Lint: configs/scenarios/<id>.yaml must mirror configs/strategies/<family>.yaml
on a fixed allow-list of structural keys.

Drift on any of these keys must be annotated `# web-override: <≥10 char reason>`
on the same line as the key in the scenario YAML, otherwise this test fails.

This catches the class of bug where a web scenario gets cloned from the wrong
template (e.g. earnings cloned from overlay) and silently disagrees with the
preset's strategy defaults.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


# (web_yaml, legacy_yaml) pairs and their must-match key paths.
# Keys use dotted paths into the YAML tree.
PAIRS = [
    {
        "web": "configs/scenarios/earnings_2025.yaml",
        "legacy": "configs/strategies/earnings_bet.yaml",
        "must_match": [
            "experiment.frequency",
            "experiment.universe",
            "strategy.name",
            "strategy.params.dte_min",
            "strategy.params.dte_max",
            "strategy.params.max_position_cost",
            "strategy.params.max_contracts",
            "strategy.params.post_decision_interval_minutes",
            "strategy.params.portfolio_snapshot_interval",
            "risk.allow_naked_short_options",
            "risk.max_daily_loss_pct",
            "risk.max_exposure_pct",
            "risk.circuit_breaker_loss_pct",
            "risk.max_orders_per_day",
            "data_policy.chain_filter_dte_min",
            "data_policy.chain_filter_dte_max",
            "data_policy.chain_filter_delta_min",
            "data_policy.chain_filter_delta_max",
        ],
    },
    {
        "web": "configs/scenarios/intraday_spy_2025_q1.yaml",
        "legacy": "configs/strategies/intraday_spy_options.yaml",
        "must_match": [
            "experiment.frequency",
            "strategy.name",
            "strategy.params.decision_interval_minutes",
            "strategy.params.dte_min",
            "strategy.params.dte_max",
            "data_policy.chain_filter_dte_min",
            "data_policy.chain_filter_dte_max",
        ],
    },
    {
        "web": "configs/scenarios/overlay_spy_qqq_2025_q1.yaml",
        "legacy": "configs/strategies/overlay_profit_qqq.yaml",
        "must_match": [
            "experiment.frequency",
            "strategy.name",
            "strategy.params.dte_min",
            "strategy.params.dte_max",
            "risk.allow_naked_short_options",
            "data_policy.chain_filter_dte_min",
            "data_policy.chain_filter_dte_max",
        ],
    },
]


_OVERRIDE_LINE_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*)\s*:.*#\s*web-override\s*:\s*(.{10,})$"
)


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


_MISSING = object()


def _walk(data: dict, dotted_path: str):
    cur = data
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _annotated_overrides(yaml_path: Path) -> set[str]:
    """Return set of leaf-key names that have a `# web-override: ...` annotation."""
    annotated: set[str] = set()
    with yaml_path.open() as f:
        for line in f:
            m = _OVERRIDE_LINE_RE.match(line)
            if m:
                annotated.add(m.group(1))
    return annotated


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: Path(p["web"]).stem)
def test_scenario_yaml_matches_legacy_baseline(pair):
    web_path = REPO_ROOT / pair["web"]
    legacy_path = REPO_ROOT / pair["legacy"]
    if not web_path.is_file():
        pytest.skip(f"web yaml {web_path} not present")
    if not legacy_path.is_file():
        pytest.skip(f"legacy yaml {legacy_path} not present")

    web = _load_yaml(web_path)
    legacy = _load_yaml(legacy_path)
    annotated = _annotated_overrides(web_path)

    failures: list[str] = []
    for key_path in pair["must_match"]:
        leaf = key_path.rsplit(".", 1)[-1]
        if leaf in annotated:
            continue  # explicitly annotated as a web-override
        web_val = _walk(web, key_path)
        legacy_val = _walk(legacy, key_path)
        if web_val is _MISSING and legacy_val is _MISSING:
            continue  # both absent — fine
        if web_val is _MISSING:
            failures.append(
                f"{key_path}: missing from web yaml; legacy has {legacy_val!r}"
            )
            continue
        if legacy_val is _MISSING:
            # legacy doesn't define this — web is allowed to define it
            continue
        if isinstance(web_val, list) and isinstance(legacy_val, list):
            if set(web_val) != set(legacy_val):
                failures.append(
                    f"{key_path}: web={sorted(web_val)} legacy={sorted(legacy_val)}"
                )
            continue
        if web_val != legacy_val:
            failures.append(
                f"{key_path}: web={web_val!r} legacy={legacy_val!r}"
            )

    if failures:
        joined = "\n  ".join(failures)
        pytest.fail(
            f"\nScenario {web_path.name} drifts from {legacy_path.name}:\n  {joined}\n\n"
            f"Either re-align the values or add `# web-override: <reason>` "
            f"on the same line as the key in {web_path.name}."
        )
