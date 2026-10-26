from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from trading.task_client import TaskClient, TaskError
from trading.cli import _build_parser, cmd_task


def test_client_routes_and_redacts_request_failures(monkeypatch):
    requests = []
    def open_url(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"items":[]}')
    monkeypatch.setattr("urllib.request.urlopen", open_url)
    client = TaskClient("http://localhost:3001/", "private-test-token")
    assert client.runs() == {"items": []}
    assert requests[-1].full_url == "http://localhost:3001/api/runs"
    assert requests[-1].get_header("Authorization") == "Bearer private-test-token"
    client.run("id/with ?", "analysis")
    assert requests[-1].full_url.endswith("/api/tasks/id%2Fwith%20%3F/analysis")
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("", 401, "unauthorized", {}, io.BytesIO(b'private-test-token'))
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(TaskError) as error:
        client.models()
    assert "private-test-token" not in str(error.value)
    assert "401" in str(error.value)


@pytest.mark.parametrize("action,suffix", [
    ("presets", "/api/tasks/presets"), ("models", "/api/model-settings"),
    ("list", "/api/runs"), ("status", "/api/tasks/run-a"),
    ("logs", "/api/tasks/run-a/logs"), ("analysis", "/api/tasks/run-a/analysis"),
])
def test_cli_reads_shared_endpoints(monkeypatch, capsys, action, suffix):
    monkeypatch.setenv("LIVEOPTION_ACCESS_TOKEN", "test-token")
    request = Mock(return_value='{"ok":true}')
    monkeypatch.setattr(TaskClient, "request", request)
    cmd_task(_build_parser().parse_args(["task", action, "--run-id", "run-a"]))
    assert request.call_args.args[0] == suffix
    assert '"ok": true' in capsys.readouterr().out


def test_cli_submits_versioned_spec(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEOPTION_ACCESS_TOKEN", "test-token")
    path = tmp_path / "task.yaml"
    path.write_text("mode: custom\ncadence: daily\nmodel_id: user-model\n")
    request = Mock(return_value='{"run_id":"new-run"}')
    monkeypatch.setattr(TaskClient, "request", request)
    cmd_task(_build_parser().parse_args(["task", "submit", "--spec", str(path)]))
    route, method, spec = request.call_args.args
    assert (route, method) == ("/api/tasks", "POST")
    assert spec["protocol_version"] == "liveoption.run.v1"
    assert spec["model_id"] == "user-model"


def test_cli_reports_never_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEOPTION_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(TaskClient, "request", Mock(return_value="# Run report\n"))
    target = tmp_path / "report.md"
    target.write_text("existing")
    args = _build_parser().parse_args(["task", "report", "--run-id", "run-a", "--output", str(target)])
    with pytest.raises(SystemExit, match="already exists"):
        cmd_task(args)
    assert target.read_text() == "existing"
    args.output = str(tmp_path / "new.md")
    cmd_task(args)
    assert (tmp_path / "new.md").read_text() == "# Run report\n"


@pytest.mark.parametrize("source", [
    json.dumps({"mode": "custom", "cadence": "daily", "model_id": "m", "context": "sk-" + "a" * 30}),
    'context: ["sk-' + "a" * 30,
])
def test_cli_invalid_task_does_not_echo_inputs(monkeypatch, tmp_path, source):
    monkeypatch.setenv("LIVEOPTION_ACCESS_TOKEN", "test-token")
    path = tmp_path / "invalid.yaml"
    path.write_text(source)
    request = Mock()
    monkeypatch.setattr(TaskClient, "request", request)
    with pytest.raises(SystemExit) as error:
        cmd_task(_build_parser().parse_args(["task", "submit", "--spec", str(path)]))
    assert "a" * 30 not in str(error.value)
    request.assert_not_called()
