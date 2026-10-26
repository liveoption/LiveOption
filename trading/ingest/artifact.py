"""Decision artifact writer — crash-safe tmp dir + rename protocol. Spec 3.6, 6.6."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile
from datetime import datetime
from pathlib import Path
from trading.core.time_contract import to_iso_z


def _sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def write_decision_artifact(
    run_dir: Path,
    *,
    decision_id: str,
    seq: int,
    trading_day: str,
    decision_ts: datetime,
    model_id: str,
    provider: str,
    prompt: dict,
    response: dict,
    context: dict,
    trades_executed: list,
    market_snapshot: dict,
) -> dict[str, str]:
    """Write decision artifact atomically.

    Returns dict with the fields needed for the /_ingest/decision-record
    POST: storage_dir (absolute path), prompt_sha256, response_sha256.

    Layout: <run_dir>/decisions/<decision_id>/{record.json, market.json, context.json}
    """
    from trading.prompts.provenance import capture_prompt
    prompt = capture_prompt(prompt)
    final_dir = Path(run_dir) / "decisions" / decision_id
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        raise FileExistsError(f"decision artifact already exists: {final_dir}")

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"{decision_id}.tmp.", dir=final_dir.parent))
    try:
        record = {
            "decision_id": decision_id,
            "seq": seq,
            "trading_day": trading_day,
            "decision_ts": to_iso_z(decision_ts),
            "model_id": model_id,
            "provider": provider,
            "prompt_sha256": _sha256(prompt),
            "response_sha256": _sha256(response),
            "trades_executed": trades_executed,
            "prompt": prompt,
            "response": response,
        }
        for name, data in [
            ("record.json", record),
            ("market.json", market_snapshot),
            ("context.json", context),
        ]:
            f = tmp_dir / name
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            with open(f, "rb") as fp:
                os.fsync(fp.fileno())
        # fsync tmp_dir itself
        dir_fd = os.open(tmp_dir, os.O_RDONLY)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
        os.rename(tmp_dir, final_dir)
        # fsync parent
        parent_fd = os.open(final_dir.parent, os.O_RDONLY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return {
        "storage_dir": str(final_dir),
        "prompt_sha256": _sha256(prompt),
        "response_sha256": _sha256(response),
    }
