"""Save each LLM call as JSON and a human-readable text companion."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class LLMCallLogger:
    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._call_count = 0

    def log_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response: str,
        model: str,
        provider: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: float | None = None,
        metadata: dict | None = None,
    ) -> Path:
        self._call_count += 1
        timestamp = datetime.now(timezone.utc)
        call_id = f"{self._call_count:04d}_{timestamp:%Y%m%dT%H%M%S}_{uuid4().hex[:8]}"
        record = {
            "call_id": call_id,
            "timestamp": timestamp.isoformat(),
            "provider": provider,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response": response,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": total_tokens,
            },
            "latency_ms": latency_ms,
        }
        if metadata:
            record["metadata"] = metadata
        path = self.directory / f"{call_id}.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        separator = "=" * 80
        lines = [
            f"call_id : {call_id}",
            f"model   : {model}  provider: {provider}",
            f"time    : {record['timestamp']}",
            separator,
            "=== SYSTEM PROMPT ===",
            separator,
            system_prompt,
            separator,
            "=== USER PROMPT ===",
            separator,
            user_prompt,
            separator,
            "=== RESPONSE ===",
            separator,
            response,
            "",
        ]
        path.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
        return path
