import hashlib
import json
import re
from datetime import datetime, timezone

import pytest

from trading.prompts.loader import PromptLoader
from trading.prompts.provenance import RenderedPrompt, capture_prompt
from trading.ingest.artifact import write_decision_artifact


def old_render(template, **variables):
    marker = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)(:[^{}]+)?\}(?!\})")
    def replace(match):
        value = variables.get(match[1], "")
        try:
            return format(value, match[2][1:]) if match[2] else str(value)
        except (ValueError, TypeError):
            return str(value)
    return marker.sub(replace, template).replace("{{", "{").replace("}}", "}")


@pytest.mark.parametrize("template", ["💹 Cash ${cash:,.2f}; {name}. {name}", '{{"orders": []}}\n{chain}', "{{name}} {name}", "{missing} {cash:invalid}", "{name}{chain}", "{cash:,.2f}"])
@pytest.mark.parametrize("name", ["QQQ", "日本語💹", "{{braces}}", "{", "}\n\"<script>" ])
def test_rendered_prompt_keeps_exact_existing_model_input(template, name):
    values = dict(cash=1234.56, name=name, chain="| 合約 | Ask |\n| QQQ | 2.10 |")
    actual = PromptLoader.render(template, **values)
    assert actual == old_render(template, **values)
    if isinstance(actual, RenderedPrompt):
        for span in actual.spans():
            assert span["variable"] in {"cash", "name", "chain", "missing"}


def test_unicode_spans_and_appended_context_survive_concatenation():
    prompt = PromptLoader.render("💹 {cash:,.2f}\n{chain}", cash=1234.5, chain="line one\nline two")
    prompt = "prefix\n" + prompt
    prompt += "\nAdditional instructions"
    body = capture_prompt({"calls": [{"phase": "analyzer", "system": "Static instruction", "user": prompt}]})
    call = body["calls"][0]
    assert call["_provenance"]["offset_encoding"] == "unicode-codepoints"
    assert call["_provenance"]["system"] == []
    spans = call["_provenance"]["user"]
    assert [call["user"][span["start"]:span["end"]] for span in spans] == ["1,234.50", "line one\nline two"]
    assert "Additional instructions" in call["user"]
    assert json.loads(json.dumps(body)) == body


def test_artifact_persists_exact_provenance_and_hash(tmp_path):
    prompt = {"system": "Static", "user": PromptLoader.render("Cash {cash:,.2f}", cash=1000)}
    artifact = write_decision_artifact(tmp_path, decision_id="D1", seq=1, trading_day="2025-01-06",
        decision_ts=datetime(2025, 1, 6, 15, tzinfo=timezone.utc), model_id="test", provider="mock",
        prompt=prompt, response={"raw": "[]"}, context={}, trades_executed=[], market_snapshot={})
    record = json.loads((tmp_path / "decisions" / "D1" / "record.json").read_text())
    assert record["prompt"]["user"] == "Cash 1,000.00"
    assert record["prompt"]["_provenance"]["user"][0]["variable"] == "cash"
    expected = hashlib.sha256(json.dumps(record["prompt"], sort_keys=True, default=str).encode()).hexdigest()
    assert artifact["prompt_sha256"] == expected
    assert capture_prompt({"system": "old", "user": "record"}) == {"system": "old", "user": "record"}
