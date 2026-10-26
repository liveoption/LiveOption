"""LLM call audit records are written in the format consumed by analyses."""

import json

from trading.tools.llm_client import LLMClient


def test_mock_call_creates_separate_json_records(tmp_path):
    client = LLMClient(provider="mock", model="mock", log_dir=tmp_path / "llm_calls")
    client.call_raw("You are an analyst.", "First prompt")
    client.call_raw("You are an analyst.", "Second prompt")

    paths = sorted((tmp_path / "llm_calls").glob("*.json"))
    assert len(paths) == 2
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert {record["user_prompt"] for record in records} == {"First prompt", "Second prompt"}
    assert all(record["provider"] == "mock" for record in records)
    assert all("response" in record and "tokens" in record for record in records)
    assert all(record["call_id"] and path.with_suffix(".txt").exists()
               for record, path in zip(records, paths))
