import json
from unittest.mock import AsyncMock

import pytest

from liveoption_web.runs.logs import log_page, redact
from liveoption_web.runs.report import build_report, report_html, report_markdown

HEADERS = {"X-Internal-Secret": "test-secret"}


def sample():
    config = {"experiment": {"start": "2025-01-06", "end": "2025-01-07", "universe": ["SPY"], "initial_cash": 1000, "frequency": "1m"},
              "strategy": {"params": {"decision_time_et": "10:30", "prompt_templates": {"system": "<script>alert(1)</script>"}}},
              "execution": {"pmb_base_url": "http://private", "fee_model": {"option_fee_per_contract": 1.25}}}
    row = {"id": "test-report", "status": "completed", "title": "<img src=x onerror=alert(1)>",
           "launch_spec_json": json.dumps({"spec": {"decision_time_et": "10:30", "context": "Be cautious"}, "effective_config": config})}
    days = [{"trading_day": "2025-01-06", "portfolio_value": 5010, "pnl_delta": 10, "cum_pnl": 10},
            {"trading_day": "2025-01-07", "portfolio_value": 4950, "pnl_delta": -60, "cum_pnl": -50}]
    decisions = [{"seq": 1, "trading_day": "2025-01-06", "has_trade": True}, {"seq": 2, "trading_day": "2025-01-06", "has_trade": False}]
    return row, config, days, decisions


def test_report_has_real_settings_curves_units_and_escaped_html():
    row, config, days, decisions = sample()
    report = build_report(row, config, days, decisions, [{"price": 2}], {"paper_portfolio": {"sharpe": None}})
    assert report["results"]["summary"]["initial_equity"] == 5000  # Includes initial stock holdings, not cash only.
    assert report["results"]["summary"]["fill_events"] == 1
    assert report["results"]["summary"]["decisions"] == 2
    assert report["results"]["summary"]["trade_proposals"] == 1
    assert report["results"]["series"][-1]["drawdown_pct"] == pytest.approx(4950 / 5010 - 1)
    assert report["settings"]["execution"]["fee_model"]["option_fee_per_contract"] == 1.25
    assert "private" not in json.dumps(report)
    markdown = report_markdown(report)
    assert all(f"## {section}" in markdown for section in ["Background", "Settings", "Results"])
    assert "|---|---:" in markdown and "|\n\n|" not in markdown
    rendered = report_html(report)
    assert "<script>" not in rendered and "<img src=x" not in rendered
    assert "&lt;script&gt;" in rendered and "<svg" in rendered


def test_legacy_and_empty_data_do_not_fabricate_settings_or_zero_results():
    report = build_report({"id": "legacy", "status": "failed"}, None, [], [], [], {})
    assert report["results"]["summary"]["total_pnl"] is None
    assert report["results"]["series"] == []
    assert report["results"]["provisional"] is True
    assert report["settings"]["configuration_source"] == "saved_spec_only"
    assert any("not retained" in w for w in report["warnings"])


def test_log_paging_redacts_known_and_generic_secrets(tmp_path):
    path = tmp_path / "stdout.log"
    secret = "known-value-12345"
    path.write_text(("hello\n" * 1000) + f"token={secret}\nAuthorization: Bearer abc123\nNameError: templates undefined\n", encoding="utf-8")
    page = log_page(path, None, 1024, [secret])
    assert secret not in page["text"] and "abc123" not in page["text"]
    assert "NameError" in page["text"] and page["next_before"] > 0
    older = log_page(path, page["next_before"], 1024, [secret])
    assert older["end"] == page["start"] and older["start"] < older["end"]
    assert "sk-1234567890abcdef" not in redact("api_key=sk-1234567890abcdef")


@pytest.mark.asyncio
async def test_report_download_auth_and_actual_snapshot(client, monkeypatch):
    from liveoption_web.api import tasks
    row, config, days, decisions = sample()
    monkeypatch.setattr(tasks, "task_analysis", AsyncMock(return_value={"run": row, "metrics": None, "paper_portfolio": None}))
    monkeypatch.setattr(tasks.service, "get_timeline", AsyncMock(return_value=decisions))
    monkeypatch.setattr("liveoption_web.eval._common.load_day_completed", lambda *_: days)
    monkeypatch.setattr("liveoption_web.eval._common.load_fills", lambda *_: [])
    assert (await client.get("/v1/tasks/test-report/report")).status_code == 403
    for format in ["json", "markdown", "html"]:
        response = await client.get(f"/v1/tasks/test-report/report?format={format}", headers=HEADERS)
        assert response.status_code == 200, response.text
        if format != "json":
            assert "attachment" in response.headers["content-disposition"]
            assert "Settings" in response.text
        else:
            assert response.json()["settings"]["decision_time_et"] == "10:30"


@pytest.mark.asyncio
async def test_log_endpoint_bounds_paths_and_downloads_redacted_content(client, monkeypatch, tmp_path):
    from liveoption_web.api import tasks
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "super-secret-value")
    settings = client._transport.app.state.settings
    run_dir = settings.runs_dir / "logtest"
    run_dir.mkdir(parents=True)
    (run_dir / "stdout.log").write_text("Authorization: Bearer super-secret-value\nNameError: templates undefined\n")
    row = {"id": "logtest", "status": "failed", "storage_dir": str(run_dir), "exit_code": 1}
    monkeypatch.setattr(tasks.service, "get_run", AsyncMock(return_value=row))
    response = await client.get("/v1/tasks/logtest/logs", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["error_message"] == "NameError: templates undefined"
    assert "super-secret-value" not in response.text
    assert (await client.get("/v1/tasks/logtest/logs?before=-1", headers=HEADERS)).status_code == 422
    response = await client.get("/v1/tasks/logtest/logs?download=true", headers=HEADERS)
    assert "super-secret-value" not in response.text and "NameError" in response.text
    assert "attachment" in response.headers["content-disposition"]
    (tmp_path / "stdout.log").write_text("outside-root")
    monkeypatch.setattr(tasks.service, "get_run", AsyncMock(return_value={**row, "storage_dir": str(tmp_path)}))
    response = await client.get("/v1/tasks/logtest/logs", headers=HEADERS)
    assert response.json()["available"] is False and "outside-root" not in response.text
