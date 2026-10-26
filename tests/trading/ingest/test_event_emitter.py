import pytest, json, time, os
from pathlib import Path
from trading.ingest.ulid import new_ulid
from trading.ingest.event_emitter import EventEmitter, NoopEmitter

def test_new_ulid_is_26_chars():
    u = new_ulid()
    assert isinstance(u, str) and len(u) == 26

def test_ulids_are_monotonic():
    ulids = [new_ulid() for _ in range(10)]
    assert ulids == sorted(ulids)

def test_noop_emitter_does_nothing(tmp_path):
    e = NoopEmitter()
    e.emit("x", {"y": 1})   # must not raise
    e.close()

def test_event_emitter_writes_jsonl(tmp_path):
    e = EventEmitter(run_dir=tmp_path, run_id="run_abc", ingest_url=None)
    e.emit("decision.started", {"decision_id": "d_1"})
    e.emit("decision.completed", {"decision_id": "d_1", "has_trade": False})
    e.close(timeout=5.0)

    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    assert row0["subprocess_seq"] == 1
    assert row0["kind"] == "decision.started"
    assert row0["ts"].endswith("Z")
    row1 = json.loads(lines[1])
    assert row1["subprocess_seq"] == 2

def test_event_emitter_flock_single_writer(tmp_path):
    """A second EventEmitter on the same run_dir must fail to acquire flock."""
    e1 = EventEmitter(run_dir=tmp_path, run_id="r", ingest_url=None)
    with pytest.raises(BlockingIOError):
        e2 = EventEmitter(run_dir=tmp_path, run_id="r", ingest_url=None)
    e1.close()

def test_event_emitter_overflow_synchronous_jsonl_fallback(tmp_path, monkeypatch):
    """When queue fills, events still reach JSONL (spec 4.4 overflow contract)."""
    e = EventEmitter(run_dir=tmp_path, run_id="r", ingest_url=None, queue_max=5)
    # Pause flusher to force overflow
    e._pause_for_test()
    for i in range(20):
        e.emit("x", {"i": i})
    e._resume_for_test()
    e.close(timeout=5.0)
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 20  # ZERO loss
    seqs = [json.loads(l)["subprocess_seq"] for l in lines]
    assert sorted(seqs) == list(range(1, 21))

def test_event_emitter_drains_all_on_close(tmp_path):
    """Close must drain the entire queue, not just one batch_size worth."""
    # queue_max large enough to buffer 250 events; batch_size default 100
    e = EventEmitter(run_dir=tmp_path, run_id="drain-all", ingest_url=None,
                     queue_max=500, batch_size=100)
    e._pause_for_test()
    for i in range(250):
        e.emit("x", {"i": i})
    # While paused, queue holds all 250 (no overflow since queue_max=500 > 250)
    e._resume_for_test()
    e.close(timeout=5.0)
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 250
    seqs = [json.loads(l)["subprocess_seq"] for l in lines]
    assert sorted(seqs) == list(range(1, 251))
