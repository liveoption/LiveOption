"""Enrich a user's trades.jsonl with per-fill Greeks from QFinZero UPQ."""
from __future__ import annotations

import argparse
import glob
import logging
from pathlib import Path

from trading.core.endpoints import DEFAULT_UPQ_URL
from trading.evaluation.greeks_at_fill import ExistingOutputError, process_run

logger = logging.getLogger(__name__)


def _expand(path_or_glob: str) -> list[Path]:
    path = Path(path_or_glob)
    if path.is_dir():
        return [path]
    if path.is_file() and path.name == "trades.jsonl":
        return [path.parent]
    runs = []
    for value in glob.glob(path_or_glob, recursive=True):
        item = Path(value)
        if item.is_dir():
            runs.append(item)
        elif item.name == "trades.jsonl":
            runs.append(item.parent)
    return runs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path_or_glob", help="Run directory, trades.jsonl or glob")
    parser.add_argument("--upq-url", default=DEFAULT_UPQ_URL)
    parser.add_argument("--spot-mode", choices=["minute", "upq_daily"], default="minute")
    parser.add_argument("--rate-mode", choices=["upq_treasury", "fixed"], default="upq_treasury")
    parser.add_argument("--rate", type=float, default=0.05)
    parser.add_argument("--iv-fallback", choices=["skip", "newton", "daily_chain"], default="skip")
    parser.add_argument("--workers", type=int, default=8)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--skip-existing", action="store_true")
    group.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    runs = _expand(args.path_or_glob)
    if not runs:
        logger.error("No run directories matched %s", args.path_or_glob)
        return 1
    failures = 0
    for directory in runs:
        try:
            stats = process_run(run_dir=directory, upq_url=args.upq_url,
                spot_mode=args.spot_mode, rate_mode=args.rate_mode,
                iv_fallback=args.iv_fallback, rate=args.rate, workers=args.workers,
                skip_existing=args.skip_existing, overwrite=args.overwrite)
        except (ExistingOutputError, FileNotFoundError) as exc:
            logger.error("%s", exc)
            failures += 1
            continue
        if stats.get("upq_unreachable") or stats.get("all_client_errors"):
            logger.error("%s: UPQ requests failed", directory)
            failures += 1
        else:
            logger.info("%s: %s", directory, stats)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
