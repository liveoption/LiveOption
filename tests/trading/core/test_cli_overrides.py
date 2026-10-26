"""CLI flag parsing + prompt-override @file handling."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading.cli import _build_parser, _resolve_prompt_override, _PromptOverrideError


def _parse_run(argv: list[str]):
    parser = _build_parser()
    return parser.parse_args(["run", "--config", "configs/dummy.yaml", *argv])


def test_parser_accepts_no_overrides():
    args = _parse_run([])
    assert args.start is None
    assert args.end is None
    assert args.bankroll is None
    assert args.strategy is None
    assert args.llm_model is None
    assert args.prompt_override is None


def test_parser_parses_date_overrides():
    args = _parse_run(["--start", "2025-02-01", "--end", "2025-02-28"])
    assert args.start == date(2025, 2, 1)
    assert args.end == date(2025, 2, 28)


def test_parser_rejects_invalid_date():
    with pytest.raises(SystemExit):
        _parse_run(["--start", "not-a-date"])


def test_parser_parses_bankroll_and_strategy():
    args = _parse_run(["--bankroll", "250000", "--strategy", "llm_gemini_v1"])
    assert args.bankroll == 250_000.0
    assert args.strategy == "llm_gemini_v1"


def test_parser_parses_llm_model_and_prompt_inline():
    args = _parse_run(["--llm-model", "gemini-2.0-flash", "--prompt-override", "hi"])
    assert args.llm_model == "gemini-2.0-flash"
    assert args.prompt_override == "hi"


def test_resolve_prompt_override_inline_pass_through():
    assert _resolve_prompt_override("hello world", Path("/tmp")) == "hello world"


def test_resolve_prompt_override_reads_file(tmp_path: Path):
    f = tmp_path / "prompt.txt"
    f.write_text("You are a trader.\n", encoding="utf-8")
    assert _resolve_prompt_override(f"@{f}", tmp_path).startswith("You are a trader.")


def test_resolve_prompt_override_rejects_missing_file(tmp_path: Path):
    with pytest.raises(_PromptOverrideError, match="does not exist"):
        _resolve_prompt_override(f"@{tmp_path}/missing.txt", tmp_path)


def test_resolve_prompt_override_rejects_path_outside_root(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        with pytest.raises(_PromptOverrideError, match="outside"):
            _resolve_prompt_override(f"@{outside}", tmp_path)
    finally:
        outside.unlink(missing_ok=True)


def test_resolve_prompt_override_rejects_oversize(tmp_path: Path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 17_000, encoding="utf-8")  # 16 KB cap
    with pytest.raises(_PromptOverrideError, match="too large"):
        _resolve_prompt_override(f"@{big}", tmp_path)


def test_resolve_prompt_override_rejects_non_utf8(tmp_path: Path):
    """A binary / mis-encoded file must surface as a clean resolver error."""
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe\x00\x00\xff")  # invalid UTF-8
    with pytest.raises(_PromptOverrideError, match="not valid UTF-8"):
        _resolve_prompt_override(f"@{bad}", tmp_path)


def test_resolve_prompt_override_rejects_directory(tmp_path: Path):
    """A directory path inside root must be rejected distinctly from 'missing'."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    with pytest.raises(_PromptOverrideError, match="not a regular file"):
        _resolve_prompt_override(f"@{subdir}", tmp_path)
