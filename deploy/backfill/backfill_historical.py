"""Backfill historical (pre-Plan-B) batch runs into the liveoption.db.

This script scans a historical results root (``~/LiveOption/results/`` style)
and inserts a ``runs`` row, optional ``decision_records`` rows, and a
small set of synthesized ``events`` for each legacy run it finds so they
show up in the Web UI.

Design goals
------------
- Safe: never mutates the on-disk results, only reads them. Writes to the
  DB only via ``INSERT OR IGNORE`` so re-running never duplicates a row.
- Idempotent: the ULID assigned to each run is deterministic
  (``HIST_`` prefix + content-hash of the results dir path + a stable
  relative-id) so re-running the script produces the same primary keys
  and the ``INSERT OR IGNORE`` is a no-op on already-imported runs.
- Resumable: ``--limit N`` + ``--since YYYY-MM-DD`` let an operator
  chunk the import. A ``backfill_audit.jsonl`` file (next to the DB)
  records every ULID inserted so the operator can reverse the import
  by deleting rows whose ``id`` starts with ``HIST_``.
- Fault-tolerant: malformed / partially-written historical dirs are
  skipped with a warning rather than crashing the whole import.

Usage
-----
    python -m deploy.backfill.backfill_historical \
        --source-dir ~/LiveOption/results \
        --db-path ~/LiveOption/web/liveoption.db \
        [--dry-run] [--limit N] [--since 2025-01-01]

The script expects the ``liveoption.db`` to already exist with migration
001 applied; it does **not** create the schema on its own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is a hard dep everywhere else
    yaml = None  # type: ignore[assignment]


log = logging.getLogger("deploy.backfill")

HIST_PREFIX = "HIST_"
RUN_AUDIT_ACTION = "backfill.insert"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HistoricalRun:
    """All the fields we can extract from a historical results dir."""

    source_dir: Path
    run_key: str  # stable relative identifier used to derive the ULID
    scenario: str
    model_id: str
    provider: str
    date_start: str  # YYYY-MM-DD
    date_end: str  # YYYY-MM-DD
    universe_json: Optional[str]
    config_path: Optional[str]
    created_at: str  # RFC3339 Z
    decisions: list["HistoricalDecision"] = field(default_factory=list)
    days_completed: list[str] = field(default_factory=list)  # YYYY-MM-DD list

    @property
    def ulid(self) -> str:
        return derive_hist_ulid(self.run_key)


@dataclass
class HistoricalDecision:
    seq: int
    trading_day: str  # YYYY-MM-DD
    decision_ts: str  # RFC3339 with offset (schema requires ±HH:MM)
    model_id: str
    provider: str
    prompt_sha256: str
    response_sha256: str
    storage_dir: str
    has_trade: bool


# ---------------------------------------------------------------------------
# ULID derivation
# ---------------------------------------------------------------------------


def derive_hist_ulid(run_key: str) -> str:
    """Deterministic ``HIST_<21-char-hex>`` id for a given run key.

    We do not use the real Crockford ULID helper because we need stable
    ids across repeated runs (idempotency). The ``HIST_`` prefix also
    makes bulk deletion during rollback trivial:
    ``DELETE FROM runs WHERE id LIKE 'HIST\\_%' ESCAPE '\\';``.
    """

    digest = hashlib.sha256(run_key.encode("utf-8")).hexdigest()
    return f"{HIST_PREFIX}{digest[:21].upper()}"


def derive_decision_id(run_ulid: str, seq: int) -> str:
    base = f"{run_ulid}:{seq}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"HDEC_{digest[:21].upper()}"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _rfc3339_utc(ts: float | datetime) -> str:
    if isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    else:
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    # Match schema CHECK: '????-??-??T??:??:??*Z'
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rfc3339_with_offset(day: str) -> str:
    """Produce an RFC3339 timestamp with +00:00 offset for a given YYYY-MM-DD.

    The ``decision_records.decision_ts`` CHECK requires a ``[-+]HH:MM``
    suffix, not ``Z``. For historical runs where we only know the
    trading day, we synthesize a fixed 21:00:00+00:00 timestamp (post
    US-market-close UTC).
    """

    return f"{day}T21:00:00.000+00:00"


# ---------------------------------------------------------------------------
# Historical layout parsing
# ---------------------------------------------------------------------------


_KNOWN_CONFIG_NAMES = (
    "config_frozen.yaml",
    "config_frozen.yml",
    "config.yaml",
    "config.yml",
)


def _find_config_file(run_dir: Path) -> Optional[Path]:
    for name in _KNOWN_CONFIG_NAMES:
        p = run_dir / name
        if p.is_file():
            return p
    return None


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return {}
            return data
    except Exception as exc:  # malformed YAML
        log.warning("failed to parse %s: %s", path, exc)
        return {}


def _iter_trade_days(path: Path) -> Iterator[str]:
    """Yield distinct ``current_date`` YYYY-MM-DD seen in trades.jsonl."""

    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                day = row.get("current_date")
                if isinstance(day, str) and len(day) >= 10:
                    day = day[:10]
                    if day not in seen:
                        seen.add(day)
                        yield day
    except OSError as exc:
        log.warning("could not read %s: %s", path, exc)


def parse_historical_run(run_dir: Path) -> Optional[HistoricalRun]:
    """Best-effort parse of a single historical run directory.

    Returns ``None`` (and logs a warning) when the directory does not
    look like a completed legacy run.
    """

    run_dir = run_dir.resolve()
    trades = run_dir / "trades.jsonl"
    portfolio = run_dir / "portfolio.jsonl"
    if not trades.exists() and not portfolio.exists():
        log.debug("skip %s: no trades.jsonl or portfolio.jsonl", run_dir)
        return None

    cfg_path = _find_config_file(run_dir)
    cfg = _load_yaml(cfg_path) if cfg_path else {}

    # --- scenario / model / provider ---------------------------------
    scenario = (
        cfg.get("scenario")
        or cfg.get("strategy")
        or cfg.get("strategy_name")
        or run_dir.parent.name
        or run_dir.name
        or "unknown"
    )
    # llm block is a common shape, but be permissive.
    llm = cfg.get("llm") or cfg.get("model") or {}
    if isinstance(llm, str):
        model_id = llm
        provider = cfg.get("provider") or "unknown"
    else:
        model_id = (
            llm.get("model_id")
            or llm.get("model")
            or cfg.get("model_id")
            or "unknown"
        )
        provider = (
            llm.get("provider")
            or cfg.get("provider")
            or "unknown"
        )

    # --- universe -----------------------------------------------------
    universe = cfg.get("universe") or cfg.get("tickers") or cfg.get("symbols")
    if universe is not None:
        try:
            universe_json = json.dumps(universe, sort_keys=True)
        except TypeError:
            universe_json = None
    else:
        universe_json = None

    # --- date range (prefer config, fall back to trades.jsonl) --------
    date_start = _coerce_day(cfg.get("date_start") or cfg.get("start_date"))
    date_end = _coerce_day(cfg.get("date_end") or cfg.get("end_date"))

    days_from_trades: list[str] = []
    if trades.exists():
        days_from_trades = sorted(set(_iter_trade_days(trades)))
    if not date_start and days_from_trades:
        date_start = days_from_trades[0]
    if not date_end and days_from_trades:
        date_end = days_from_trades[-1]
    if not date_start or not date_end:
        log.warning(
            "skip %s: cannot determine date range (config=%s, trades=%d rows)",
            run_dir,
            cfg_path.name if cfg_path else "<none>",
            len(days_from_trades),
        )
        return None

    # --- created_at from dir mtime ------------------------------------
    try:
        mtime = run_dir.stat().st_mtime
    except OSError:
        mtime = datetime.now(timezone.utc).timestamp()
    created_at = _rfc3339_utc(mtime)

    run_key = str(run_dir)

    hist = HistoricalRun(
        source_dir=run_dir,
        run_key=run_key,
        scenario=str(scenario),
        model_id=str(model_id),
        provider=str(provider),
        date_start=date_start,
        date_end=date_end,
        universe_json=universe_json,
        config_path=str(cfg_path) if cfg_path else None,
        created_at=created_at,
        days_completed=days_from_trades,
    )

    # --- decisions (Plan-A artifact dir, optional) --------------------
    decisions_dir = run_dir / "decisions"
    if decisions_dir.is_dir():
        hist.decisions = list(_parse_decision_artifacts(hist, decisions_dir))
    return hist


def _coerce_day(value) -> str:
    if not value:
        return ""
    s = str(value)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _parse_decision_artifacts(
    run: HistoricalRun, decisions_dir: Path
) -> Iterator[HistoricalDecision]:
    for artifact_dir in sorted(decisions_dir.iterdir()):
        if not artifact_dir.is_dir():
            continue
        record_path = artifact_dir / "record.json"
        if not record_path.is_file():
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - tolerate any parse failure
            log.warning("malformed decision record %s: %s", record_path, exc)
            continue

        try:
            seq = int(record.get("seq"))
        except (TypeError, ValueError):
            log.warning("decision %s: missing/invalid seq", record_path)
            continue
        trading_day = _coerce_day(record.get("trading_day"))
        if not trading_day:
            log.warning("decision %s: missing trading_day", record_path)
            continue
        decision_ts = record.get("decision_ts") or _rfc3339_with_offset(trading_day)
        # Schema CHECK requires ±HH:MM — if artifact stored as ...Z,
        # rewrite it to the +00:00 equivalent.
        if decision_ts.endswith("Z"):
            decision_ts = decision_ts[:-1] + "+00:00"
        yield HistoricalDecision(
            seq=seq,
            trading_day=trading_day,
            decision_ts=decision_ts,
            model_id=str(record.get("model_id") or run.model_id),
            provider=str(record.get("provider") or run.provider),
            prompt_sha256=str(record.get("prompt_sha256") or ""),
            response_sha256=str(record.get("response_sha256") or ""),
            storage_dir=str(artifact_dir),
            has_trade=bool(record.get("trades_executed")),
        )


# ---------------------------------------------------------------------------
# Insertion
# ---------------------------------------------------------------------------


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Fail loudly if the DB has not been migrated."""

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
    ).fetchone()
    if not row:
        raise RuntimeError(
            "target DB is missing the 'runs' table — run liveoption_web "
            "migrations (001_init.sql) before backfilling"
        )


def insert_run(
    conn: sqlite3.Connection,
    run: HistoricalRun,
    audit_writer: Optional["AuditWriter"],
) -> bool:
    """Insert one historical run + its decisions + synthetic events.

    Uses a single transaction so a failure half-way doesn't leave the
    run partially imported. Returns ``True`` when a new run row was
    created, ``False`` when the run was already present.
    """

    ulid = run.ulid
    now = _rfc3339_utc(datetime.now(timezone.utc))

    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute(
            """
            INSERT OR IGNORE INTO runs (
              id, kind, scenario, config_path, model_id, provider,
              universe_json, date_start, date_end, status,
              status_updated_at, created_at, storage_dir, is_featured,
              title, notes
            ) VALUES (?, 'batch', ?, ?, ?, ?, ?, ?, ?, 'completed',
                      ?, ?, ?, 0, ?, ?)
            """,
            (
                ulid,
                run.scenario,
                run.config_path or "",
                run.model_id,
                run.provider,
                run.universe_json,
                run.date_start,
                run.date_end,
                run.created_at,  # status_updated_at
                run.created_at,  # created_at
                str(run.source_dir),
                f"[backfill] {run.source_dir.name}",
                "Imported by deploy/backfill/backfill_historical.py",
            ),
        )
        inserted_run = cur.rowcount > 0

        if not inserted_run:
            conn.commit()
            return False

        # Decision records
        for dec in run.decisions:
            dec_id = derive_decision_id(ulid, dec.seq)
            cur.execute(
                """
                INSERT OR IGNORE INTO decision_records (
                  id, run_id, seq, trading_day, decision_ts, model_id,
                  provider, prompt_sha256, response_sha256, storage_dir,
                  has_trade, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dec_id,
                    ulid,
                    dec.seq,
                    dec.trading_day,
                    dec.decision_ts,
                    dec.model_id,
                    dec.provider,
                    dec.prompt_sha256,
                    dec.response_sha256,
                    dec.storage_dir,
                    1 if dec.has_trade else 0,
                    now,
                ),
            )

        # Synthesized events: run.started, day.completed*, run.finished
        seq = 0
        cur.execute(
            """
            INSERT OR IGNORE INTO events
              (run_id, subprocess_seq, ts, kind, payload_json)
            VALUES (?, ?, ?, 'run.started', ?)
            """,
            (
                ulid,
                seq,
                run.created_at,
                json.dumps({"source": "backfill", "scenario": run.scenario}),
            ),
        )
        for day in run.days_completed:
            seq += 1
            cur.execute(
                """
                INSERT OR IGNORE INTO events
                  (run_id, subprocess_seq, ts, kind, payload_json)
                VALUES (?, ?, ?, 'day.completed', ?)
                """,
                (
                    ulid,
                    seq,
                    run.created_at,
                    json.dumps({"trading_day": day, "source": "backfill"}),
                ),
            )
        seq += 1
        cur.execute(
            """
            INSERT OR IGNORE INTO events
              (run_id, subprocess_seq, ts, kind, payload_json)
            VALUES (?, ?, ?, 'run.finished', ?)
            """,
            (
                ulid,
                seq,
                run.created_at,
                json.dumps(
                    {
                        "source": "backfill",
                        "status": "completed",
                        "n_days": len(run.days_completed),
                    }
                ),
            ),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if audit_writer is not None:
        audit_writer.write(
            {
                "ts": now,
                "action": RUN_AUDIT_ACTION,
                "run_id": ulid,
                "source_dir": str(run.source_dir),
                "scenario": run.scenario,
                "n_decisions": len(run.decisions),
                "n_days": len(run.days_completed),
            }
        )
    return True


# ---------------------------------------------------------------------------
# Audit writer
# ---------------------------------------------------------------------------


class AuditWriter:
    """Append-only JSONL audit log next to the target DB."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, row: dict) -> None:
        self._fh.write(json.dumps(row, sort_keys=True) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "AuditWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Walk helpers
# ---------------------------------------------------------------------------


def iter_candidate_dirs(source_dir: Path) -> Iterable[Path]:
    """Yield directories that look like historical runs.

    We accept both ``<root>/<run>`` and ``<root>/<bucket>/<run>`` layouts
    by walking two levels deep and filtering to directories containing
    ``trades.jsonl`` or ``portfolio.jsonl``.
    """

    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        return
    for entry in sorted(source_dir.iterdir()):
        if not entry.is_dir():
            continue
        if _looks_like_run(entry):
            yield entry
            continue
        # one more level
        for sub in sorted(entry.iterdir()):
            if sub.is_dir() and _looks_like_run(sub):
                yield sub


def _looks_like_run(path: Path) -> bool:
    return (path / "trades.jsonl").exists() or (path / "portfolio.jsonl").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_backfill(
    *,
    source_dir: Path,
    db_path: Path,
    dry_run: bool = False,
    limit: Optional[int] = None,
    since: Optional[str] = None,
    audit_path: Optional[Path] = None,
) -> dict:
    """Programmatic entry point. Returns a stats dict."""

    stats = {"scanned": 0, "skipped": 0, "inserted": 0, "already_present": 0}

    if not source_dir.exists():
        raise FileNotFoundError(f"source dir does not exist: {source_dir}")

    if not dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
    else:
        conn = None

    audit_writer = None
    if not dry_run:
        audit_path = audit_path or db_path.with_name("backfill_audit.jsonl")
        audit_writer = AuditWriter(audit_path)

    try:
        from tqdm import tqdm  # local import so the module is importable
                                 # in environments without tqdm.
    except Exception:  # pragma: no cover
        def tqdm(it, **_k):  # type: ignore[no-redef]
            return it

    try:
        candidates = list(iter_candidate_dirs(source_dir))
        for run_dir in tqdm(candidates, desc="backfill"):
            stats["scanned"] += 1
            try:
                hist = parse_historical_run(run_dir)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to parse %s: %s", run_dir, exc)
                stats["skipped"] += 1
                continue
            if hist is None:
                stats["skipped"] += 1
                continue
            if since and hist.date_end < since:
                stats["skipped"] += 1
                continue
            if dry_run:
                log.info(
                    "[dry-run] would insert run %s scenario=%s %s..%s "
                    "decisions=%d days=%d",
                    hist.ulid,
                    hist.scenario,
                    hist.date_start,
                    hist.date_end,
                    len(hist.decisions),
                    len(hist.days_completed),
                )
                stats["inserted"] += 1
            else:
                assert conn is not None
                inserted = insert_run(conn, hist, audit_writer)
                if inserted:
                    stats["inserted"] += 1
                else:
                    stats["already_present"] += 1
            if limit is not None and stats["inserted"] >= limit:
                log.info("hit --limit=%d, stopping", limit)
                break
    finally:
        if audit_writer is not None:
            audit_writer.close()
        if conn is not None:
            conn.close()

    return stats


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="backfill_historical",
        description="Backfill pre-Plan-B batch runs into liveoption.db",
    )
    ap.add_argument("--source-dir", required=True, type=Path)
    ap.add_argument("--db-path", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--since",
        type=str,
        default=None,
        help="Skip runs whose date_end < YYYY-MM-DD",
    )
    ap.add_argument(
        "--audit-path",
        type=Path,
        default=None,
        help="Where to write backfill_audit.jsonl (default: next to DB)",
    )
    ap.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase log level"
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    level = logging.WARNING - (args.verbose * 10)
    logging.basicConfig(
        level=max(level, logging.DEBUG),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    stats = run_backfill(
        source_dir=args.source_dir,
        db_path=args.db_path,
        dry_run=args.dry_run,
        limit=args.limit,
        since=args.since,
        audit_path=args.audit_path,
    )
    log.warning("backfill complete: %s", json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
