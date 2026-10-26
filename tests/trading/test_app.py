from __future__ import annotations

import argparse
import json
import os
import runpy
from unittest.mock import Mock

import pytest

from trading import app


def test_web_api_package_entry_calls_server(monkeypatch):
    from liveoption_web import main as web_main

    start = Mock()
    monkeypatch.setattr(web_main, "main", start)
    runpy.run_module("liveoption_web", run_name="__main__")
    start.assert_called_once_with()


def test_setup_is_private_repeatable_and_does_not_touch_provider_env(tmp_path, capsys):
    provider = tmp_path / ".env"
    provider.write_text("OPENAI_API_KEY=private-provider-value\n")
    target = app.setup(tmp_path)
    original = target.read_text()
    assert target.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".liveoption").stat().st_mode & 0o777 == 0o700
    assert "LIVEOPTION_ACCESS_TOKEN=" in original
    app.setup(tmp_path)
    assert target.read_text() == original
    assert provider.read_text() == "OPENAI_API_KEY=private-provider-value\n"
    for line in original.splitlines():
        if "=" in line:
            assert line.split("=", 1)[1] not in capsys.readouterr().out


def test_environment_precedence_and_isolated_defaults(tmp_path, monkeypatch):
    for name in ("LIVEOPTION_ACCESS_TOKEN", "LIVEOPTION_INTERNAL_SECRET", "LIVEOPTION_DB_PATH", "QFZ_HUB_URL"):
        monkeypatch.delenv(name, raising=False)
    app.setup(tmp_path)
    local = app.environment(tmp_path)
    assert local["QFZ_HUB_URL"] == "http://127.0.0.1:19777"
    assert local["LIVEOPTION_DB_PATH"].startswith(str(tmp_path / ".liveoption"))
    (tmp_path / ".env").write_text("QFZ_HUB_URL=http://localhost:20000\nLIVEOPTION_ACCESS_TOKEN=\n")
    assert app.environment(tmp_path)["LIVEOPTION_ACCESS_TOKEN"] == local["LIVEOPTION_ACCESS_TOKEN"]
    assert app.environment(tmp_path)["QFZ_HUB_URL"] == "http://localhost:20000"
    monkeypatch.setenv("QFZ_HUB_URL", "http://localhost:30000")
    assert app.environment(tmp_path)["QFZ_HUB_URL"] == "http://localhost:30000"


def test_doctor_uses_real_service_paths_and_reports_failure(monkeypatch, capsys):
    urls = []
    def check(url, **kwargs):
        urls.append(url)
        return not url.endswith("/v1/health")
    monkeypatch.setattr(app, "health", check)
    assert app.doctor({"QFZ_HUB_URL": "http://hub:19777/", "QFZ_UPQ_URL": "http://market"}) is False
    assert urls == ["http://hub:19777/health", "http://market/health", "http://hub:19777/svc/pmb/v1/health"]
    assert "FAIL  PMB" in capsys.readouterr().out


@pytest.mark.parametrize("version,valid", [("18.20.0", False), ("20.18.0", False), ("20.19.0", True), ("21.7.0", False), ("22.11.0", False), ("22.12.0", True), ("24.0.0", True)])
def test_node_version_guard(monkeypatch, version, valid):
    monkeypatch.setattr(app.shutil, "which", lambda _: "/node")
    monkeypatch.setattr(app.subprocess, "check_output", lambda *args, **kwargs: version)
    if valid:
        assert app.node_command() == "/node"
    else:
        with pytest.raises(RuntimeError, match="Node"):
            app.node_command()


def test_prepare_build_cannot_inherit_test_auth_bypasses(tmp_path, monkeypatch):
    for name in ("LIVEOPTION_ACCESS_TOKEN", "LIVEOPTION_INTERNAL_SECRET"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "frontend/node_modules").mkdir(parents=True)
    for flag in ("E2E_TEST_AUTH", "E2E_PLAYWRIGHT", "VITE_E2E_TEST_AUTH"):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setattr(app, "node_command", lambda: "/node")
    monkeypatch.setattr(app, "pnpm_command", lambda: ["pnpm"])
    executed = []
    monkeypatch.setattr(app.subprocess, "run", lambda *args, **kwargs: executed.append(kwargs["env"]))
    args = argparse.Namespace(skip_check=True, build=True, prepare_only=True)
    app.web(args, tmp_path)
    assert len(executed) == 1
    assert not any(key.startswith("E2E_") or key == "VITE_E2E_TEST_AUTH" for key in executed[0])


def test_stop_children_never_terminates_already_exited_process():
    stopped = Mock(); stopped.poll.return_value = 0
    running = Mock(); running.poll.return_value = None
    app.stop_children([stopped, running])
    stopped.terminate.assert_not_called()
    running.terminate.assert_called_once()
