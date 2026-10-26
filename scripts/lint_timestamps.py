"""Lint: reject datetime.now() without tz, datetime.utcnow(), and raw ts slicing."""
import re, sys
from pathlib import Path

BAD_PATTERNS = [
    (r"datetime\.now\(\s*\)", "use datetime.now(UTC_TZ) or utc_now()"),
    (r"datetime\.utcnow\(\)", "deprecated in Python 3.12; use utc_now()"),
    (r"current_ts\[:10\]", "use trading_day_from_ts(state.current_ts)"),
    (r"current_ts\[11:16\]", "use et_time_from_ts(state.current_ts)"),
]

def main() -> int:
    violations = []
    for py in Path("trading").rglob("*.py"):
        if "tests" in py.parts or py.name == "time_contract.py":
            continue
        text = py.read_text()
        for pat, msg in BAD_PATTERNS:
            for m in re.finditer(pat, text):
                line_no = text[:m.start()].count("\n") + 1
                violations.append(f"{py}:{line_no}: {m.group(0)} — {msg}")
    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
