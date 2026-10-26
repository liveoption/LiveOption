"""Browser and CLI use the same versioned task, status and analysis contract."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from liveoption_web.eval._common import paper_portfolio_stats


HEADERS = {"X-Internal-Secret": "test-secret", "X-Actor-User": "test-user"}


@pytest.fixture
def sandbox_config(tmp_path, monkeypatch):
    from liveoption_web.api import launch, tasks
    root = tmp_path / "checkout"
    target = root / "configs" / "scenarios"
    target.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "configs" / "scenarios"
    for name in ("intraday_spy_2025_q1", "overlay_spy_qqq_2025_q1", "earnings_2025"):
        shutil.copyfile(source / f"{name}.yaml", target / f"{name}.yaml")
    for module in (launch, tasks):
        monkeypatch.setattr(module, "REPO_ROOT", root)
        monkeypatch.setattr(module, "WEB_RUNS_DIR", root / ".web-runs")
    return root


@pytest.mark.asyncio
async def test_presets_are_copyable_and_server_owned_fields_omitted(client, sandbox_config):
    response = await client.get("/v1/tasks/presets", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["family"] for item in items} == {"overlay", "earnings", "intraday"}
    assert all(set(item["execution"]) == {"option_fee_per_contract", "stock_fee_per_share", "slippage_bps", "option_spread_pct", "order_type_policy"} for item in items)
    assert all("pmb_base_url" not in item["execution"] for item in items)
    assert all("ssh_host" not in item["strategy_params"] for item in items)
    assert all(item["prompt_templates"] and item["avsr"] for item in items)
    from liveoption_web.runs.prompt_catalog import PLACEHOLDER
    for item in items:
        for stage, template in item["prompt_templates"].items():
            documented = item["prompt_variables"][stage]
            assert {entry["name"] for entry in documented} == set(PLACEHOLDER.findall(template))
            assert all(entry["description"] and entry["source"] and entry["example"] for entry in documented)
    assert all(item["data_policy"]["news_enabled"] is True for item in items)
    overlay = next(item for item in items if item["family"] == "overlay")
    assert overlay["overlay_modes"] == ["hedge", "covered_call", "hybrid"]
    assert overlay["overlay_mode"] == "covered_call"
    earnings = next(item for item in items if item["family"] == "earnings")
    assert "pre_system" in earnings["prompt_templates"] and "earnings_pre_system" not in earnings["prompt_templates"]
    custom = response.json()["custom_types"]
    assert {item["cadence"] for item in custom} == {"intraday", "daily"}
    assert all(item["experiment"]["initial_positions"] == [] for item in custom)
    assert all(item["experiment"]["initial_cash"] == 100000 for item in custom)


@pytest.mark.asyncio
async def test_task_launch_uses_materialized_config_and_shared_analysis(client, sandbox_config, monkeypatch):
    from trading.prompts.intraday import INTRADAY_SYSTEM_TEMPLATE, INTRADAY_USER_TEMPLATE
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-gateway-token")
    spawn = AsyncMock(return_value=123)
    monkeypatch.setattr("liveoption_web.api.launch.spawn_batch", spawn)
    response = await client.post("/v1/tasks", headers=HEADERS, json={
        "protocol_version": "liveoption.run.v1",
        "preset_id": "intraday_spy_2025_q1", "model_id": "gemini-3-flash",
        "context": "Only liquid calls; hold when signals disagree.",
        "experiment": {"start": "2025-01-06", "end": "2025-01-06", "initial_cash": 25000, "universe": ["SPY"]},
        "strategy_params": {"decision_interval_minutes": 5, "dte_min": 0, "dte_max": 1, "max_positions": 2},
        "risk": {"max_orders_per_day": 4},
        "data_policy": {"news_enabled": True},
        "prompt_addenda": {"system": "Use conservative sizing."},
        "prompt_templates": {"system": INTRADAY_SYSTEM_TEMPLATE.replace("You are an intraday", "You are a cautious intraday"),
                             "user": INTRADAY_USER_TEMPLATE.replace("provide your trading decisions", "provide careful trading decisions")},
        "execution": {"option_fee_per_contract": 1.25, "stock_fee_per_share": 0.001,
                      "slippage_bps": 4, "option_spread_pct": 0.08, "order_type_policy": "limit"},
        "avsr": {"moneyness_anchors": [0.90, 1.0, 1.10],
                 "expiry_anchors": [5, 21, 63], "distance_lambda": 0.02},
    })
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    config = yaml.safe_load((sandbox_config / ".web-runs" / f"{run_id}.yaml").read_text())
    assert config["experiment"]["run_id"] == run_id
    assert config["experiment"]["mode"] == "replay"
    assert config["experiment"]["initial_cash"] == 25000
    assert config["strategy"]["params"]["llm_provider"] == "anthropic"
    assert config["strategy"]["params"]["prompt_override"].startswith("Only liquid")
    assert config["strategy"]["params"]["prompt_addenda"]["system"] == "Use conservative sizing."
    assert "cautious intraday" in config["strategy"]["params"]["prompt_templates"]["system"]
    assert config["execution"]["fee_model"]["option_fee_per_contract"] == 1.25
    assert config["execution"]["fee_model"]["slippage_bps"] == 4
    assert config["execution"]["order_type_policy"] == "limit"
    assert config["avsr"]["expiry_anchors"] == [5, 21, 63]
    assert config["risk"]["max_orders_per_day"] == 4
    assert spawn.await_args.kwargs["env"]["ANTHROPIC_AUTH_TOKEN"] == "test-gateway-token"
    assert "test-gateway-token" not in json.dumps(config)
    status = await client.get(f"/v1/tasks/{run_id}", headers=HEADERS)
    analysis = await client.get(f"/v1/tasks/{run_id}/analysis", headers=HEADERS)
    assert status.status_code == analysis.status_code == 200
    assert analysis.json()["pending"] is True
    assert analysis.json()["run"]["id"] == run_id


@pytest.mark.asyncio
async def test_task_rejects_cross_family_or_unverified_data(client, sandbox_config, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr("liveoption_web.api.launch.spawn_batch", spawn)
    base = {"preset_id": "intraday_spy_2025_q1", "model_id": "gemini-3-flash"}
    for change in ({"strategy_params": {"stock_qty": 200}}, {"experiment": {"universe": ["BAD"]}},
                   {"context": "sk-12345678901234567890"}, {"experiment": {"universe": ["QQQ"]}},
                   {"prompt_addenda": {"executor_user": "not an intraday stage"}},
                   {"prompt_templates": {"user": "No market data"}},
                   {"prompt_templates": {"executor_user": "wrong stage"}},
                   {"execution": {"option_fee_per_contract": -1}},
                   {"execution": {"order_type_policy": "stop"}},
                   {"execution": {"pmb_base_url": "http://example.invalid"}},
                   {"data_policy": {"news_lookback_days": 31}},
                   {"avsr": {"moneyness_anchors": [1, 0.95, 1.05]}}):
        response = await client.post("/v1/tasks", headers=HEADERS, json={**base, **change})
        assert response.status_code == 422
    spawn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,name", [("hedge", "overlay_hedge"), ("covered_call", "overlay_profit"), ("hybrid", "overlay_hybrid"), (None, "overlay_profit")])
async def test_overlay_submode_selects_real_engine_and_is_saved(client, sandbox_config, monkeypatch, mode, name):
    from trading.core.registry import get_strategy
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    spawn = AsyncMock(return_value=123)
    monkeypatch.setattr("liveoption_web.api.launch.spawn_batch", spawn)
    body = {"preset_id": "overlay_spy_qqq_2025_q1", "model_id": "gemini-3-flash",
            "strategy_params": {"stock_qty": 500}}
    if mode is not None:
        body["overlay_mode"] = mode
    response = await client.post("/v1/tasks", headers=HEADERS, json=body)
    assert response.status_code == 202, response.text
    config = yaml.safe_load((sandbox_config / ".web-runs" / f"{response.json()['run_id']}.yaml").read_text())
    assert config["strategy"]["name"] == name
    assert all(p["qty"] == 500 for p in config["experiment"]["initial_positions"])
    assert get_strategy(name, config["strategy"]["params"]).REQUIRED_FREQUENCIES == frozenset({"1d"})
    from liveoption_web.runs.report import build_report
    row = (await client.get(f"/v1/tasks/{response.json()['run_id']}", headers=HEADERS)).json()
    report = build_report(row, config, [], [], [], {})
    assert report["settings"]["overlay_mode"] == (mode or "covered_call")
    assert report["settings"]["strategy"]["name"] == name


@pytest.mark.parametrize("body", [
    {"preset_id": "intraday_spy_2025_q1", "overlay_mode": "hedge"},
    {"mode": "custom", "cadence": "daily", "overlay_mode": "hybrid"},
    {"preset_id": "overlay_spy_qqq_2025_q1", "overlay_mode": "unknown"},
])
def test_overlay_submode_rejects_invalid_or_cross_family_inputs(body):
    from liveoption_web.runs.protocol import RunSpecV1
    with pytest.raises(ValueError):
        RunSpecV1(model_id="test", **body)


@pytest.mark.asyncio
@pytest.mark.parametrize("cadence,frequency,strategy", [("daily", "1d", "custom_daily_llm"), ("intraday", "1m", "intraday_llm")])
async def test_custom_task_launches_without_preset_and_keeps_own_config(client, sandbox_config, monkeypatch, cadence, frequency, strategy):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    spawn = AsyncMock(return_value=123)
    monkeypatch.setattr("liveoption_web.api.launch.spawn_batch", spawn)
    response = await client.post("/v1/tasks", headers=HEADERS, json={
        "mode": "custom", "cadence": cadence, "model_id": "gemini-3-flash",
        "experiment": {"start": "2025-01-06", "end": "2025-01-10", "universe": ["QQQ"]},
        "prompt_templates": {"system": "Use my own options strategy"},
        "strategy_params": {"dte_min": 10, "dte_max": 30},
    })
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    config = yaml.safe_load((sandbox_config / ".web-runs" / f"{run_id}.yaml").read_text())
    assert config["experiment"]["frequency"] == frequency
    assert config["experiment"]["universe"] == ["QQQ"]
    assert config["experiment"]["initial_positions"] == []
    assert config["experiment"]["initial_cash"] == 100000
    assert config["strategy"]["name"] == strategy
    assert "stock_qty" not in config["strategy"]["params"]
    assert config["strategy"]["params"]["prompt_templates"]["system"] == "Use my own options strategy"
    assert config["data_policy"]["chain_filter_dte_min"] == 10
    assert config["data_policy"]["chain_filter_dte_max"] == 30
    assert config["risk"]["flatten_before_close"] == (cadence == "intraday")
    status = (await client.get(f"/v1/tasks/{run_id}", headers=HEADERS)).json()
    assert status["scenario"] == f"custom_{cadence}"


def test_custom_cadence_cannot_silently_import_a_preset():
    from pydantic import ValidationError
    from liveoption_web.runs.protocol import RunSpecV1
    with pytest.raises(ValidationError, match="independent"):
        RunSpecV1(mode="custom", cadence="daily", preset_id="overlay_spy_qqq_2025_q1", model_id="m")
    with pytest.raises(ValidationError, match="unsupported strategy"):
        RunSpecV1(mode="custom", cadence="daily", model_id="m", strategy_params={"decision_interval_minutes": 15})


def test_custom_mode_clears_preset_context(sandbox_config):
    from liveoption_web.api.launch import _peek_scenario_yaml
    from liveoption_web.runs.catalog import SCENARIOS
    from liveoption_web.runs.protocol import RunSpecV1, materialize
    from types import SimpleNamespace

    source = _peek_scenario_yaml(SCENARIOS["intraday_spy_2025_q1"].config_path)
    source["strategy"]["params"]["prompt_override"] = "paper-only context"
    spec = RunSpecV1(preset_id="intraday_spy_2025_q1", mode="custom", model_id="m", context="")
    config = materialize(spec, source, SimpleNamespace(api_model="m"), SimpleNamespace(provider="anthropic"), "custom-1")
    assert config.strategy.params["prompt_override"] == ""


@pytest.mark.asyncio
async def test_model_keys_are_redacted_and_blank_edit_retains_secret(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-secret-key")
    first = await client.get("/v1/model-settings", headers=HEADERS)
    assert first.status_code == 200
    assert "test-secret-key" not in first.text
    payload = first.json()
    assert payload["groups"][0]["has_api_key"] is True
    for group in payload["groups"]:
        group.pop("has_api_key")
        group["api_key"] = ""
    changed = await client.put("/v1/model-settings", headers=HEADERS, json=payload)
    assert changed.status_code == 200, changed.text
    assert changed.json()["groups"][0]["has_api_key"] is True
    assert "test-secret-key" not in changed.text
    invalid = await client.put("/v1/model-settings", headers=HEADERS, json={
        "groups": [{"id": "bad group id", "label": "Bad", "provider": "anthropic",
                    "api_key": "sensitive-new-key"}], "models": [],
    })
    assert invalid.status_code == 422
    assert "sensitive-new-key" not in invalid.text


def test_paper_portfolio_stats_refuse_sparse_or_invalid_data():
    assert paper_portfolio_stats([])["annualized_return"] is None
    one = paper_portfolio_stats([{"portfolio_value": 101, "pnl_delta": 1}])
    assert one["max_drawdown_pct"] == 0
    assert one["annualized_return"] is None
    two = paper_portfolio_stats([
        {"portfolio_value": 101, "pnl_delta": 1},
        {"portfolio_value": 90, "pnl_delta": -11},
    ])
    assert two["max_drawdown_pct"] > 0.10
    assert two["annualized_return"] < 0
