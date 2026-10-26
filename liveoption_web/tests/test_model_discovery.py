from unittest.mock import AsyncMock, Mock

import pytest
import requests

from liveoption_web.runs.model_discovery import DiscoveryError, discover, endpoint, merge_discovered
from liveoption_web.runs.model_settings import ModelGroup, ModelSettings, load_model_settings, save_model_settings

HEADERS = {"X-Internal-Secret": "test-secret", "X-Actor-User": "test-user"}


def registry():
    return ModelSettings.model_validate({"groups": [
        {"id": "g1", "label": "Test group", "provider": "openai_compatible", "base_url": "http://example.invalid/v1", "api_key": "secret-credential"},
        {"id": "g2", "label": "Other group", "provider": "anthropic", "api_key": "other-secret"}],
        "models": [{"id": "existing", "label": "My label", "api_model": "model-a", "group_id": "g1"},
                   {"id": "old", "label": "Old model", "api_model": "old-model", "group_id": "g1"},
                   {"id": "other", "label": "Other", "api_model": "model-a", "group_id": "g2"}]})


def reply(value, code=200):
    return Mock(ok=200 <= code < 300, status_code=code, is_redirect=300 <= code < 400, json=lambda: value)


@pytest.mark.parametrize("provider,base,suffix", [("openai", "https://api.example/v1", "/v1/models"),
    ("anthropic", "https://api.example/antigravity", "/antigravity/v1/models"),
    ("anthropic", "https://api.example/v1/", "/v1/models"),
    ("gemini", "https://api.example/v1beta/openai", "/v1beta/openai/models")])
def test_endpoint_preserves_group_prefix(provider, base, suffix):
    assert endpoint(ModelGroup(id="g", label="group", provider=provider, base_url=base)).endswith(suffix)


def test_paginated_discovery_uses_credentials_but_never_follows_upstream_urls(monkeypatch):
    get = Mock(side_effect=[reply({"data": [{"id": "model-a", "display_name": "Model A"}], "has_more": True, "last_id": "model-a"}),
                            reply({"data": [{"id": "model-b"}, {"id": "model-a"}], "has_more": False})])
    monkeypatch.setattr(requests, "get", get)
    group = registry().groups[1]
    found = discover(group)
    assert {item["api_model"] for item in found} == {"model-a", "model-b"}
    assert get.call_args_list[1].kwargs["params"] == {"after_id": "model-a"}
    assert get.call_args.kwargs["headers"]["x-api-key"] == "other-secret"
    assert get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("value", [{"data": "bad"}, {"data": [{}]}, {"data": [{"id": "bad\nmodel"}]}, {"data": [], "has_more": True}])
def test_invalid_discovery_does_not_return_partial_catalog(monkeypatch, value):
    monkeypatch.setattr(requests, "get", lambda *a, **k: reply(value))
    with pytest.raises(DiscoveryError):
        discover(registry().groups[0])


def test_stable_ids_group_isolation_and_removed_models():
    original = registry()
    entries = [{"api_model": "model-a", "label": "Provider A"}, {"api_model": "model-b", "label": "Provider B"}]
    result = merge_discovered(original, "g1", entries)
    assert next(model for model in result.models if model.id == "existing").label == "My label"
    assert next(model for model in result.models if model.id == "old").available is False
    assert next(model for model in result.models if model.id == "other").available is True
    again = merge_discovered(result, "g1", entries)
    assert [m.id for m in again.models] == [m.id for m in result.models]
    assert result.groups[0].models_updated_at and not result.groups[1].models_updated_at
    assert original.groups[0].models_updated_at is None


@pytest.mark.asyncio
async def test_refresh_persists_catalog_keeps_secret_and_stable_id(client, monkeypatch):
    settings = client._transport.app.state.settings
    save_model_settings(settings, registry())
    monkeypatch.setattr("liveoption_web.runs.model_discovery.discover", lambda group: [{"api_model": "model-a", "label": "New name"}, {"api_model": "model-b", "label": "B"}])
    path = "/v1/model-settings/g1/models/refresh"
    assert (await client.post(path)).status_code == 403
    response = await client.post(path, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert "secret-credential" not in response.text and "other-secret" not in response.text
    assert response.json()["discovery"]["count"] == 2
    assert response.json()["groups"][0]["models_updated_at"] == response.json()["discovery"]["checked_at"]
    assert load_model_settings(settings).groups[0].api_key == "secret-credential"
    assert next(m for m in response.json()["models"] if m["id"] == "existing")["available"]
    blocked = await client.post("/v1/tasks", headers=HEADERS, json={"model_id": "old", "preset_id": "intraday_spy_2025_q1"})
    assert blocked.status_code == 422 and "no longer listed" in blocked.text


@pytest.mark.asyncio
async def test_upstream_failure_retains_previous_catalog_and_hides_body(client, monkeypatch):
    settings = client._transport.app.state.settings
    save_model_settings(settings, registry())
    monkeypatch.setattr(requests, "get", lambda *a, **k: reply({"error": "secret-credential"}, 401))
    response = await client.post("/v1/model-settings/g1/models/refresh", headers=HEADERS)
    assert response.status_code == 502 and "models_endpoint_http_401" in response.text
    assert "secret-credential" not in response.text
    assert load_model_settings(settings).models[1].available


@pytest.mark.parametrize("provider", ["gemini", "qwen", "deepseek", "openai_compatible", "anthropic"])
@pytest.mark.asyncio
async def test_run_uses_the_same_group_endpoint_as_discovery(client, monkeypatch, tmp_path, provider):
    from liveoption_web.api import tasks
    from trading.tools.llm_client import LLMClient
    settings = client._transport.app.state.settings
    value = registry()
    value.groups[0].provider = provider
    save_model_settings(settings, value)
    monkeypatch.setattr(tasks, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tasks, "WEB_RUNS_DIR", tmp_path / ".web-runs")
    launch = AsyncMock(return_value={"run_id": "test"})
    monkeypatch.setattr(tasks, "handle_launch_run", launch)
    response = await client.post("/v1/tasks", headers=HEADERS, json={"mode": "custom", "cadence": "intraday", "model_id": "existing"})
    assert response.status_code == 202, response.text
    environment = launch.await_args.kwargs["environment_override"]
    assert environment["LIVEOPTION_LLM_BASE_URL"] == "http://example.invalid/v1"
    monkeypatch.setenv("LIVEOPTION_LLM_BASE_URL", environment["LIVEOPTION_LLM_BASE_URL"])
    assert LLMClient(provider=provider, api_key="test-key")._base_url == "http://example.invalid/v1"
