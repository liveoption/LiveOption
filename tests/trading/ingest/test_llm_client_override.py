"""Tests for LLMClient runtime model_override (Plan A, Task 13).

The override feature lets the web fork/diverge flow swap the effective model
at runtime without reconstructing strategies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trading.core.config import ModelsConfig, load_models_config
from trading.tools.llm_client import LLMClient


REPO_ROOT = Path(__file__).resolve().parents[3]
MODELS_YAML = REPO_ROOT / "configs" / "models.yaml"


def _make_client() -> LLMClient:
    models = load_models_config(MODELS_YAML)
    # Use the mock provider so no API keys / network are needed; inject the
    # real models_config so override validation exercises the real catalog.
    return LLMClient(
        provider="mock",
        model="mock",
        models_config=models,
        default_model_id=models.default_model() or "deepseek-v3",
    )


def test_set_model_override_accepts_known_model():
    client = _make_client()
    models = load_models_config(MODELS_YAML)
    # Pick any real model id from the registry (deterministic: first key).
    target = next(iter(models.models.keys()))
    client.set_model_override(target)
    assert client._model_override == target


def test_set_model_override_rejects_unknown_model():
    client = _make_client()
    with pytest.raises(ValueError, match="unknown model"):
        client.set_model_override("fake-model-xyz")


def test_set_model_override_none_clears():
    client = _make_client()
    target = next(iter(load_models_config(MODELS_YAML).models.keys()))
    client.set_model_override(target)
    assert client._model_override == target
    client.set_model_override(None)
    assert client._model_override is None


def test_set_model_override_without_models_config_raises():
    client = LLMClient(provider="mock", model="mock")
    with pytest.raises(RuntimeError, match="requires LLMClient to be constructed"):
        client.set_model_override("deepseek-v3")


def test_call_honours_override_switching_provider_and_model():
    """With an override pointing at a models.yaml entry, `_apply_override_fields`
    resolves the entry's provider/model, and `_call_api` swaps them onto self
    for the duration of the call. We verify the effective fields via the
    internal helper (which is what _call_api uses)."""
    client = _make_client()
    models = load_models_config(MODELS_YAML)
    # Pick the mock entry if present, else any entry — we only inspect the
    # resolved tuple, we don't actually hit the network.
    target = "mock" if "mock" in models.models else next(iter(models.models.keys()))
    client.set_model_override(target)
    provider, model, _api_key, _base_url, _max_tokens, _reason = (
        client._apply_override_fields()
    )
    entry = models.get_entry(target)
    assert provider == entry.provider
    assert model == entry.model
