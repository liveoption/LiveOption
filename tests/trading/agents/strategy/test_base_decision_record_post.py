"""Verify BaseStrategy.write_artifact triggers post_decision_record."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from trading.agents.strategy.base import BaseStrategy


class _Strat(BaseStrategy):
    """Minimal concrete subclass for testing the write_artifact hook."""

    def on_step(self, market_event, portfolio, state):
        return []

    def save_checkpoint(self):
        return {}

    def load_checkpoint(self, state):
        pass


def test_write_artifact_posts_decision_record(tmp_path: Path):
    s = _Strat()
    s._runner_ref = SimpleNamespace(run_dir=tmp_path)
    s.events = MagicMock()
    s.events.emit = MagicMock()
    s.events.post_decision_record = MagicMock()

    s.write_artifact(
        decision_id="dec_1",
        seq=42,
        ts=datetime(2025, 1, 2, 14, 30, 0, tzinfo=timezone.utc),
        model_id="deepseek-chat",
        provider="deepseek",
        prompt={"system": "hi"},
        response={"intents_count": 0},
        context={},
        trades=[],
        market_snapshot={"underlying": "SPY"},
    )

    s.events.post_decision_record.assert_called_once()
    payload = s.events.post_decision_record.call_args[0][0]
    assert payload["id"] == "dec_1"
    assert payload["seq"] == 42
    assert payload["model_id"] == "deepseek-chat"
    assert payload["has_trade"] is False
    # storage_dir is the absolute path under tmp_path
    assert payload["storage_dir"].endswith("/decisions/dec_1")
    # prompt_sha256 + response_sha256 are populated
    assert len(payload["prompt_sha256"]) == 64
    assert len(payload["response_sha256"]) == 64
    # decision_ts has +HH:MM offset (NOT Z)
    assert payload["decision_ts"].endswith("+00:00")
