from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass
class RunnerCheckpoint:
    version: int
    trading_state: dict
    accumulators: dict           # _last_portfolio, _start_of_day_equity, _prev_step_snapshot, _current_step_snapshot, _sod_accumulator
    pmb_session_id: str
    pmb_account_id: str
    strategy_checkpoint: dict    # output of strategy.save_checkpoint()
    llm_client_state: dict
    run_config: dict             # snapshot of RunConfig

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, default=str))
        import os
        os.rename(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "RunnerCheckpoint":
        data = json.loads(Path(path).read_text())
        if data.get("version") != 1:
            raise ValueError(f"RunnerCheckpoint schema version mismatch: {data.get('version')}")
        return cls(**data)
