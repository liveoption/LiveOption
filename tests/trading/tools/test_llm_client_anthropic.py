from __future__ import annotations

import time
from types import SimpleNamespace

from trading.tools.llm_client import LLMClient


def test_anthropic_client_uses_configured_base_url(monkeypatch):
    captured: dict = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
                content=[SimpleNamespace(text="OK")],
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.messages = FakeMessages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    client = LLMClient(
        provider="anthropic",
        model="gemini-3-flash",
        api_key="test-key",
        base_url="http://gateway.example/antigravity",
    )

    response, usage = client._call_anthropic("system", "user")

    assert captured["client"] == {
        "api_key": "test-key",
        "base_url": "http://gateway.example/antigravity",
    }
    assert captured["request"]["model"] == "gemini-3-flash"
    assert response == "OK"
    assert usage == {"input": 3, "output": 2, "total": 5}


def test_gateway_environment_fallbacks(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-from-env")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://anthropic-gateway.example")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key-from-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai-gateway.example/v1")

    gemini = LLMClient(provider="anthropic", model="gemini-3-flash")
    gpt = LLMClient(provider="openai_compatible", model="gpt-5.6-terra")

    assert gemini._api_key == "token-from-env"
    assert gemini._base_url == "http://anthropic-gateway.example"
    assert gpt._api_key == "openai-key-from-env"
    assert gpt._base_url == "http://openai-gateway.example/v1"


def test_shared_rate_limit_spaces_sibling_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEOPTION_LLM_MIN_INTERVAL_SECONDS", "0.02")
    monkeypatch.setenv("LIVEOPTION_LLM_RATE_LIMIT_DIR", str(tmp_path))
    first = LLMClient(provider="mock", model="shared-model")
    second = LLMClient(provider="mock", model="shared-model")

    first._wait_for_request_slot()
    started = time.monotonic()
    second._wait_for_request_slot()

    assert time.monotonic() - started >= 0.015
