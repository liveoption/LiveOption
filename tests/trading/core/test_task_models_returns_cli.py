import io
from unittest.mock import Mock

import pytest

from trading.cli import _build_parser, cmd_task


@pytest.mark.parametrize("arguments,method,suffix", [
    (["models", "--refresh", "--group-id", "gateway"], "POST", "/api/model-settings/gateway/models/refresh"),
    (["returns", "--run-id", "run-1"], "GET", "/api/tasks/run-1/returns"),
])
def test_cli_shares_group_discovery_and_return_endpoints(monkeypatch, capsys, arguments, method, suffix):
    monkeypatch.setenv("LIVEOPTION_ACCESS_TOKEN", "test-access-token")
    request = Mock(return_value=io.BytesIO(b'{"ok":true}'))
    monkeypatch.setattr("urllib.request.urlopen", request)
    args = _build_parser().parse_args(["task", *arguments])
    cmd_task(args)
    sent = request.call_args.args[0]
    assert sent.method == method and sent.full_url.endswith(suffix)
    assert sent.get_header("Authorization") == "Bearer test-access-token"
    assert '"ok": true' in capsys.readouterr().out
