"""Bounded, backward-paginated worker log reads with secret redaction."""
from __future__ import annotations

import os
import re
from pathlib import Path


def redact(text: str, secrets: list[str] = ()) -> str:
    for value in sorted((value for value in secrets if len(value) >= 4), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\b(?:sk|pk)[-_][A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", text)
    text = re.sub(r'''(?i)((?:[A-Z_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)|authorization|x-api-key)["']?\s*[:=]\s*["']?)[^\s,"'}]+''', r"\1[REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    return text


def log_path(row: dict, root: Path) -> Path | None:
    path = (Path(row["storage_dir"]) / "stdout.log").resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    return path


def log_page(path: Path, before: int | None, limit: int, secrets: list[str]) -> dict:
    size = path.stat().st_size
    end = min(before, size) if before is not None else size
    start = max(0, end - limit)
    omitted = False
    with path.open("rb") as handle:
        # Extend a page back to a line boundary so secrets are never split across pages.
        handle.seek(max(0, start - 65536))
        prefix = handle.read(start - max(0, start - 65536))
        boundary = prefix.rfind(b"\n")
        if start and boundary < 0:
            # An exceptionally long line has no safe pagination boundary. Omit it.
            handle.seek(start)
            body = handle.read(end - start)
            newline = body.find(b"\n")
            omitted = True
            if newline < 0:
                return {"text": "", "start": start, "end": end, "total_bytes": size,
                        "next_before": start if start > 0 else None, "long_line_omitted": True}
            start += newline + 1
        elif start:
            start = max(0, start - 65536) + boundary + 1
        handle.seek(start)
        body = handle.read(end - start)
        if before is not None and end < size and not body.endswith(b"\n"):
            # Caller-provided byte cursors must not cut through a credential.
            last_newline = body.rfind(b"\n")
            body = body[:last_newline + 1] if last_newline >= 0 else b""
            omitted = True
        text = body.decode("utf-8", errors="replace")
    return {"text": redact(text, secrets), "start": start, "end": end, "total_bytes": size,
            "next_before": start if start > 0 else None,
            "long_line_omitted": omitted}


def environment_secrets() -> list[str]:
    return [value for key, value in os.environ.items() if re.search(r"(?i)(token|secret|password|api_key)", key)]
