"""Verify EventEmitter.post_decision_record builds the right URL + payload."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest


def test_post_decision_record_derives_sibling_url(tmp_path: Path):
    from trading.ingest.event_emitter import EventEmitter

    emitter = EventEmitter(
        run_dir=tmp_path,
        run_id="r1",
        ingest_url="http://127.0.0.1:19720/_ingest/events",
    )
    try:
        with patch("trading.ingest.event_emitter.requests.post") as mock_post:
            mock_post.return_value.raise_for_status.return_value = None
            emitter.post_decision_record({"id": "d1", "seq": 1, "trading_day": "2025-01-02"})
            args, kwargs = mock_post.call_args
            assert args[0] == "http://127.0.0.1:19720/_ingest/decision-record"
            assert kwargs["json"] == {
                "run_id": "r1",
                "decision": {"id": "d1", "seq": 1, "trading_day": "2025-01-02"},
            }
    finally:
        emitter.close(timeout=1.0)


def test_post_decision_record_swallows_errors(tmp_path: Path, caplog):
    from trading.ingest.event_emitter import EventEmitter

    emitter = EventEmitter(
        run_dir=tmp_path,
        run_id="r2",
        ingest_url="http://127.0.0.1:19720/_ingest/events",
    )
    try:
        with patch("trading.ingest.event_emitter.requests.post", side_effect=Exception("boom")):
            # Must not raise.
            emitter.post_decision_record({"id": "d1", "seq": 1})
        assert any("decision-record POST failed" in rec.message for rec in caplog.records)
    finally:
        emitter.close(timeout=1.0)


def test_noop_emitter_post_decision_record_silent():
    from trading.ingest.event_emitter import NoopEmitter
    NoopEmitter().post_decision_record({"id": "d1", "seq": 1})
