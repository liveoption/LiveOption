from pathlib import Path
from trading.ingest.runner_checkpoint import RunnerCheckpoint


def test_runner_checkpoint_roundtrip(tmp_path):
    ckpt = RunnerCheckpoint(
        version=1,
        trading_state={"step_id": 100},
        accumulators={"last_portfolio": {"cash": 1000}, "sod_accumulator": 0.0},
        pmb_session_id="sess_1",
        pmb_account_id="acct_1",
        strategy_checkpoint={"_version": 1, "rebalance_dates": []},
        llm_client_state={},
        run_config={"strategy": {"name": "weekly_llm"}},
    )
    p = tmp_path / "runner_ckpt.json"
    ckpt.save(p)
    ckpt2 = RunnerCheckpoint.load(p)
    assert ckpt2.pmb_session_id == "sess_1"
    assert ckpt2.strategy_checkpoint["_version"] == 1
