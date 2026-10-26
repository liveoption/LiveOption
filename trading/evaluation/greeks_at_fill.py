"""Greeks-at-fill enrichment — post-run BSM snapshot per executed fill.

"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import requests

from trading.core.greeks_math import compute_all_greeks, compute_time_to_expiry, parse_opra_contract
from trading.core.endpoints import qfz_headers

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def _et_to_utc_wallclock(fill_ts_et: str) -> datetime:
    """Convert an ET wall-clock string (no tz) to a UTC-aware datetime.

    UPQ's ``/option/ticker_query`` and ``/stock`` endpoints interpret the
    ``start``/``end`` query params as UTC wall-clock (verified 2026-04-24 on
    qlib UPQ pid 3663109). Passing the ET string directly reads bars from
    ~5 hours before the fill (EST) — a silent correctness bug. All call
    sites that pass fill_ts_et to UPQ must convert first.
    """
    return (
        datetime.fromisoformat(fill_ts_et)
        .replace(tzinfo=_ET)
        .astimezone(_UTC)
    )


def normalize_fill_ts(fill_ts: str) -> tuple[str, str, str] | None:
    """Normalize FillEvent.fill_ts to the ET-shaped form BSM helpers expect.

    Returns (fill_ts_original, fill_ts_et, trade_date) or None if the input
    string has no timezone offset (see spec §3.2 — silent ET assumption
    would shift T by 4-5h for the UTC-offset timestamps SessionClock emits).
    """
    try:
        dt = datetime.fromisoformat(fill_ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    et = dt.astimezone(_ET)
    fill_ts_et = et.strftime("%Y-%m-%dT%H:%M:%S")
    trade_date = et.strftime("%Y-%m-%d")
    return (fill_ts, fill_ts_et, trade_date)


@dataclass(frozen=True)
class FillRow:
    step_id: int
    event_ts: str
    current_date: str
    fill: dict  # raw FillEvent dict


def iter_fills(trades_path: Path) -> Iterator[FillRow]:
    """Stream every fill from a trades.jsonl file in file order.

    Each yielded FillRow carries the enclosing step's step_id/event_ts/
    current_date so downstream consumers don't need to re-thread them.
    """
    trades_path = Path(trades_path)
    if not trades_path.exists() or trades_path.stat().st_size == 0:
        return
    with trades_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            step = json.loads(line)
            fills = step.get("fills") or []
            for fill in fills:
                yield FillRow(
                    step_id=step["step_id"],
                    event_ts=step["event_ts"],
                    current_date=step["current_date"],
                    fill=fill,
                )


@dataclass(frozen=True)
class Snapshot:
    """Raw UPQ pulls for one fill. Not written to output directly —
    enrich_fill derives main Greeks locally from these inputs."""
    S: float | None
    sigma: float | None
    r: float | None
    iv_source: str            # "upq_minute" | "upq_daily_chain" | "newton_from_fill_price" | "missing"
    spot_mode: str            # "minute" | "upq_daily"
    rate_mode: str            # "upq_treasury" | "fixed"
    upq_reference: dict | None
    notes: list[str] | None = None  # accumulated during fetch (rates fallback, retries)


_MULTIPLIER = 100


def enrich_fill(
    fill_row: FillRow,
    fill_ts_norm: tuple[str, str, str],
    contract_parts: tuple[str, str, str, float] | None,
    snapshot: Snapshot,
    T: float | None,
    multiplier: int = _MULTIPLIER,
) -> dict:
    """Compose the final output row.

    Main Greeks are ALWAYS computed locally from the (S, K, T, sigma, r, q=0)
    snapshot — never copied from UPQ. UPQ's own Greek values (if any) go into
    row["upq_reference"] for cross-checking. See spec §4.2.
    """
    fill = fill_row.fill
    fill_ts_orig, fill_ts_et, trade_date = fill_ts_norm
    notes_parts: list[str] = list(snapshot.notes or [])

    if contract_parts is None:
        underlying = expiry = right = None
        strike = None
        notes_parts.append("opra_parse_failed")
    else:
        underlying, expiry, right, strike = contract_parts

    # Compute Greeks from snapshot (all None when inputs are bad)
    if (
        contract_parts is not None
        and snapshot.S is not None
        and snapshot.sigma is not None
        and snapshot.r is not None
        and T is not None
        and T > 0
    ):
        greeks = compute_all_greeks(
            S=snapshot.S, K=strike, T=T, sigma=snapshot.sigma,
            r=snapshot.r, right=right,
        )
    else:
        greeks = {k: None for k in ("delta", "gamma", "theta", "vega", "volga", "vanna")}
        if T is not None and T <= 0:
            notes_parts.append("T_below_min")

    # Dollar exposures
    side = fill["side"]
    qty = int(fill["qty"])
    qty_signed = qty if side.upper() == "BUY" else -qty
    scale = qty_signed * multiplier
    dollars = {
        f"dollar_{g}": (scale * v if v is not None else None)
        for g, v in greeks.items()
    }

    return {
        "trade_id": fill["trade_id"],
        "order_id": fill["order_id"],
        "step_id": fill_row.step_id,
        "fill_ts": fill_ts_orig,
        "fill_ts_et": fill_ts_et,
        "trade_date": trade_date,
        "contract": fill["contract"],

        "underlying": underlying,
        "expiry": expiry,
        "right": right,
        "strike": strike,
        "multiplier": multiplier,

        "side": side,
        "qty": qty,
        "fill_price": fill["price"],

        "S": snapshot.S,
        "T": T,
        "sigma": snapshot.sigma,
        "r": snapshot.r,
        "q": 0.0,

        **greeks,
        **dollars,

        "provenance": {
            "spot_mode": snapshot.spot_mode,
            "rate_mode": snapshot.rate_mode,
            "iv_source": snapshot.iv_source,
            "greek_source": "local_bsm_from_snapshot",
        },
        "upq_reference": snapshot.upq_reference,
        "notes": "; ".join(notes_parts) if notes_parts else None,
    }


_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # 1s, 2s, 4s


_RETRYABLE_4XX = frozenset({408, 429})


def _get_with_retry(
    session: requests.Session, url: str, params: dict, notes: list[str],
) -> dict | list | None:
    """GET with up to _MAX_RETRIES on 5xx / connection errors and transient 4xx.

    Deterministic client errors (4xx other than 408/429) are not retried.
    Retryable statuses (5xx, 408, 429) get exponential backoff.
    """
    last_err: str | None = None
    last_status: int | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = session.get(url, params=params, headers=qfz_headers(), timeout=30)
            if resp.status_code >= 500 or resp.status_code in _RETRYABLE_4XX:
                last_err = f"HTTP {resp.status_code}"
                last_status = resp.status_code
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                continue
            if resp.status_code >= 400:
                # Deterministic client error — do not retry
                notes.append(f"upq_client_error: HTTP {resp.status_code}")
                return None
            return resp.json()
        except requests.RequestException as exc:
            last_err = str(exc)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_BACKOFF_BASE * (2 ** attempt))
    # Exhausted retries on retryable errors — record as upq_error
    if last_status in _RETRYABLE_4XX:
        notes.append(f"upq_error: HTTP {last_status} (exhausted retries)")
    else:
        notes.append(f"upq_error: {last_err}")
    return None


_MINUTE_WINDOW_MIN = 5  # widen to ±N minutes; UPQ only emits bars for minutes
                         # with trade activity, so exact 1-min windows are
                         # frequently empty for less-liquid contracts
                         # (verified on qlib, see POC report 2026-04-24).


def _parse_bar_window_ns(window_start) -> int | None:
    """Best-effort parse of a UPQ bar's ``window_start`` field into UTC epoch ns.

    Real UPQ responses carry an integer (UTC nanoseconds). Test fixtures may
    use an ISO-8601 string, which we interpret as ET wall-clock if no offset
    is attached.
    """
    if isinstance(window_start, (int, float)):
        return int(window_start)
    if isinstance(window_start, str):
        try:
            dt = datetime.fromisoformat(window_start)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_ET)
        return int(dt.timestamp() * 1_000_000_000)
    return None


def _fetch_option_minute(
    session: requests.Session, upq_url: str, contract: str, fill_ts_et: str,
    notes: list[str],
) -> dict | None:
    """Return the UPQ minute bar nearest to ``fill_ts_et`` within ±5 minutes.

    UPQ only emits a bar for minutes where trade activity occurred, so the
    exact-minute window is often empty. The fetcher queries a 11-minute
    window centred on ``fill_ts_et`` and picks the bar whose ``window_start``
    is closest. When the chosen bar's offset is non-zero, a
    ``minute_bar_offset_sec:<signed int>`` note is recorded so downstream
    analysis can filter or weight by staleness.
    """
    fill_utc = _et_to_utc_wallclock(fill_ts_et)
    start_dt = fill_utc - timedelta(minutes=_MINUTE_WINDOW_MIN)
    end_dt = fill_utc + timedelta(minutes=_MINUTE_WINDOW_MIN + 1)
    start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    data = _get_with_retry(
        session, f"{upq_url}/option/ticker_query",
        {"contract": contract, "start": start, "end": end,
         "resolution": "minute", "include_greeks": "true"},
        notes,
    )
    if not data or not isinstance(data, list):
        notes.append("no_minute_bar")
        return None

    fill_ns = int(fill_utc.timestamp() * 1_000_000_000)

    best_bar = None
    best_abs_ns = None
    best_ns = None
    for bar in data:
        bar_ns = _parse_bar_window_ns(bar.get("window_start"))
        # Unparseable window_start → treat as exact hit so we still return a bar
        ref_ns = bar_ns if bar_ns is not None else fill_ns
        d = abs(ref_ns - fill_ns)
        if best_abs_ns is None or d < best_abs_ns:
            best_abs_ns = d
            best_bar = bar
            best_ns = bar_ns

    if best_ns is not None and best_ns != fill_ns:
        offset_sec = (best_ns - fill_ns) // 1_000_000_000
        notes.append(f"minute_bar_offset_sec:{offset_sec}")
    return best_bar


def _fetch_spot(
    session: requests.Session, upq_url: str, underlying: str,
    fill_ts_et: str, trade_date: str, spot_mode: str, notes: list[str],
) -> float | None:
    if spot_mode == "minute":
        fill_utc = _et_to_utc_wallclock(fill_ts_et)
        start = fill_utc.strftime("%Y-%m-%dT%H:%M:%S")
        end_dt = fill_utc + timedelta(minutes=1)
        end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        data = _get_with_retry(
            session, f"{upq_url}/stock",
            {"tickers": underlying, "start": start, "end": end,
             "fields": "ticker,window_start,close"},
            notes,
        )
    else:  # "upq_daily"
        # UPQ /stock requires full ISO datetime for start/end (verified on
        # qlib 2026-04-24: passing just YYYY-MM-DD returns HTTP 400
        # "start/end must be ISO datetime: YYYY-MM-DDTHH:MM:SS").
        start = f"{trade_date}T00:00:00"
        end = f"{trade_date}T23:59:59"
        data = _get_with_retry(
            session, f"{upq_url}/stock",
            {"tickers": underlying, "start": start, "end": end,
             "fields": "ticker,window_start,close"},
            notes,
        )
    if not data or not isinstance(data, list) or not data:
        return None
    if spot_mode == "minute":
        return data[0].get("close")
    # Daily mode: the response is ~840 minute bars across the 24h window.
    # Prefer the last bar whose window_start is at or before 21:00 UTC
    # (16:00 ET EST regular-session close; also covers EDT's 20:00 UTC close).
    # Falls back to the last bar in the response when no RTH bar is found.
    rth_close_ns = (
        datetime.fromisoformat(f"{trade_date}T21:00:00")
        .replace(tzinfo=_UTC).timestamp() * 1_000_000_000
    )
    best = None
    for bar in data:
        ws = _parse_bar_window_ns(bar.get("window_start"))
        if ws is None:
            continue
        if ws <= rth_close_ns:
            if best is None or ws > _parse_bar_window_ns(best.get("window_start")):
                best = bar
    if best is None:
        best = data[-1]
    return best.get("close")


_TENOR_DAYS: dict[str, int] = {
    "yield_1_month": 30,
    "yield_3_month": 91,
    "yield_6_month": 182,
    "yield_1_year": 365,
    "yield_2_year": 730,
    "yield_3_year": 1095,
    "yield_5_year": 1825,
    "yield_7_year": 2555,
    "yield_10_year": 3650,
    "yield_20_year": 7300,
    "yield_30_year": 10950,
}


def _interpolate_rate(row: dict, DTE: int) -> float | None:
    """Linear interpolation across Treasury tenors for the given DTE (days).

    UPQ returns yields in percent (e.g. 4.44 = 4.44%); this function returns
    the decimal form (0.0444). DTE outside the bracketed range clamps to the
    nearest tenor. Returns None if the row contains no usable tenor fields.
    """
    pts: list[tuple[int, float]] = []
    for key, days in _TENOR_DAYS.items():
        v = row.get(key)
        if isinstance(v, (int, float)):
            pts.append((days, float(v)))
    if not pts:
        return None
    pts.sort()
    if DTE <= pts[0][0]:
        return pts[0][1] / 100.0
    if DTE >= pts[-1][0]:
        return pts[-1][1] / 100.0
    for i in range(1, len(pts)):
        d0, y0 = pts[i - 1]
        d1, y1 = pts[i]
        if d0 <= DTE <= d1:
            frac = (DTE - d0) / (d1 - d0)
            return (y0 + frac * (y1 - y0)) / 100.0
    return None


def _fetch_rate(
    session: requests.Session, upq_url: str, trade_date: str, DTE: int,
    fixed_rate: float, notes: list[str],
) -> tuple[float, str]:
    """Return (r, rate_mode_used). Falls back to fixed on /rates/query failure.

    UPQ's /rates/query returns a row per date in the requested range with
    Treasury yields at standard tenors (1M/3M/6M/1Y/2Y/3Y/5Y/7Y/10Y/20Y/30Y,
    in percent). The client picks the single row for ``trade_date`` and
    interpolates linearly to the contract's DTE.

    Rates 4xx are common (missing date in parquet) and are NOT treated as
    client errors — they silently fall back to fixed_rate without polluting
    notes with upq_client_error, which would otherwise trigger all-client-
    error failure at the run level.
    """
    # Use a separate notes list so rates 4xx don't pollute the main notes
    rate_notes: list[str] = []
    data = _get_with_retry(
        session, f"{upq_url}/rates/query",
        {"start": trade_date, "end": trade_date},
        rate_notes,
    )
    if isinstance(data, list) and data:
        rate = _interpolate_rate(data[0], DTE)
        if rate is not None:
            return rate, "upq_treasury"
    # Only propagate actual UPQ server errors (5xx/connection) to main notes;
    # 4xx on rates is an expected miss, not a failure
    for note in rate_notes:
        if "upq_error:" in note:
            notes.append(note)
            break
    notes.append("rates_query_failed_used_fixed")
    return fixed_rate, "fixed"


def _fetch_daily_chain_iv(
    session: requests.Session, upq_url: str, contract: str, trade_date: str,
    right: str, notes: list[str],
) -> float | None:
    from trading.core.greeks_math import parse_opra_contract
    parsed = parse_opra_contract(contract)
    if not parsed:
        return None
    underlying, expiry, _, strike = parsed
    data = _get_with_retry(
        session, f"{upq_url}/option/chain_query",
        {"underlying": underlying, "date": trade_date, "type": right,
         "expiry_min": expiry, "expiry_max": expiry, "include_greeks": "true"},
        notes,
    )
    if not data:
        return None
    for c in (data if isinstance(data, list) else []):
        if abs(float(c.get("strike", 0)) - strike) < 1e-6:
            iv = c.get("iv")
            if iv is not None:
                return float(iv)
    return None


def fetch_snapshot(
    upq_url: str,
    contract: str,
    underlying: str,
    fill_ts_et: str,
    trade_date: str,
    DTE: int,
    spot_mode: str,
    rate_mode: str,
    iv_fallback: str,
    *,
    session: requests.Session | None = None,
    fill_price: float | None = None,
    K: float | None = None,
    T: float | None = None,
    right: str | None = None,
    fixed_rate: float = 0.05,
) -> Snapshot:
    """Fetch everything needed to build one output row from UPQ.

    See spec §6 data-flow diagram for ordering. Returns a Snapshot; notes
    are accumulated so enrich_fill can merge them into the row.
    """
    from trading.core.greeks_math import brent_implied_vol

    sess = session if session is not None else requests.Session()
    notes: list[str] = []

    # 1) Option minute bar (always needed — may carry IV + vendor Greeks)
    bar = _fetch_option_minute(sess, upq_url, contract, fill_ts_et, notes)
    upq_reference: dict | None = None
    sigma: float | None = None
    iv_source = "missing"
    if bar is not None:
        greek_status = bar.get("greek_status")
        upq_reference = {
            "delta": bar.get("delta"), "gamma": bar.get("gamma"),
            "theta": bar.get("theta"), "vega":  bar.get("vega"),
            "iv":    bar.get("iv"),    "greek_status": greek_status,
        }
        iv = bar.get("iv")
        if iv is not None:
            sigma = float(iv)
            iv_source = "upq_minute"

    # 2) Spot
    S = _fetch_spot(sess, upq_url, underlying, fill_ts_et, trade_date, spot_mode, notes)

    # 3) Rate
    if rate_mode == "upq_treasury":
        r, rate_mode_used = _fetch_rate(sess, upq_url, trade_date, DTE, fixed_rate, notes)
    else:
        r, rate_mode_used = fixed_rate, "fixed"

    # 4) IV fallback if needed
    if sigma is None and iv_fallback == "daily_chain" and right is not None:
        sigma = _fetch_daily_chain_iv(sess, upq_url, contract, trade_date, right, notes)
        if sigma is not None:
            iv_source = "upq_daily_chain"
    elif (
        sigma is None and iv_fallback == "newton"
        and fill_price is not None and S is not None and K is not None
        and T is not None and right is not None
    ):
        iv, status = brent_implied_vol(
            price=fill_price, S=S, K=K, T=T, r=r, q=0.0, right=right,
        )
        if iv is not None:
            sigma = iv
            iv_source = "newton_from_fill_price"
        else:
            notes.append(f"newton_{status}")

    return Snapshot(
        S=S, sigma=sigma, r=r,
        iv_source=iv_source, spot_mode=spot_mode, rate_mode=rate_mode_used,
        upq_reference=upq_reference,
        notes=notes or None,
    )


class ExistingOutputError(RuntimeError):
    """Raised when greeks_at_fill.jsonl exists and neither --skip-existing
    nor --overwrite was supplied."""


def _prepare_enrichment_inputs(fill_row: FillRow) -> tuple[
    tuple[str, str, str] | None,
    tuple[str, str, str, float] | None,
    float | None,
    int | None,
]:
    """Pure prep: normalize fill_ts, parse OPRA, compute T and DTE."""
    fill_ts_norm = normalize_fill_ts(fill_row.fill["fill_ts"])
    if fill_ts_norm is None:
        return None, None, None, None
    _, fill_ts_et, trade_date = fill_ts_norm
    contract_parts = parse_opra_contract(fill_row.fill["contract"])
    if contract_parts is None:
        return fill_ts_norm, None, None, None
    _, expiry, _, _ = contract_parts
    T = compute_time_to_expiry(expiry, fill_ts_et)
    from datetime import date
    DTE = (date.fromisoformat(expiry) - date.fromisoformat(trade_date)).days
    return fill_ts_norm, contract_parts, T, DTE


def process_run(
    run_dir: Path,
    upq_url: str,
    spot_mode: str,
    rate_mode: str,
    iv_fallback: str,
    rate: float,
    workers: int = 8,
    *,
    skip_existing: bool = False,
    overwrite: bool = False,
) -> dict:
    """Process one run directory: stream fills → fetch snapshots → write rows.

    Returns {"fills_written": int, "skipped": bool} on success.
    Raises ExistingOutputError if output file exists and neither flag is set.
    Raises FileNotFoundError if trades.jsonl is missing.
    """
    run_dir = Path(run_dir)
    trades_path = run_dir / "trades.jsonl"
    out_path = run_dir / "greeks_at_fill.jsonl"

    if not trades_path.exists():
        raise FileNotFoundError(f"no trades.jsonl in {run_dir}")

    if out_path.exists():
        if skip_existing:
            return {"fills_written": 0, "skipped": True}
        if not overwrite:
            raise ExistingOutputError(
                f"{out_path} exists; pass --skip-existing or --overwrite"
            )

    fills = list(iter_fills(trades_path))
    if not fills:
        out_path.write_text("")
        return {"fills_written": 0, "skipped": False}

    session = requests.Session()

    def _one_fill(idx: int, fill_row: FillRow) -> tuple[int, dict]:
        fill_ts_norm, contract_parts, T, DTE = _prepare_enrichment_inputs(fill_row)
        if fill_ts_norm is None:
            placeholder_norm = (fill_row.fill["fill_ts"], "", "")
            snap = Snapshot(
                S=None, sigma=None, r=None, iv_source="missing",
                spot_mode=spot_mode, rate_mode=rate_mode,
                upq_reference=None, notes=["fill_ts_missing_tz"],
            )
            return idx, enrich_fill(fill_row, placeholder_norm, None, snap, None)

        if contract_parts is None:
            snap = Snapshot(
                S=None, sigma=None, r=None, iv_source="missing",
                spot_mode=spot_mode, rate_mode=rate_mode,
                upq_reference=None, notes=None,
            )
            return idx, enrich_fill(fill_row, fill_ts_norm, None, snap, None)

        _, fill_ts_et, trade_date = fill_ts_norm
        underlying, _, right, K = contract_parts
        snap = fetch_snapshot(
            upq_url=upq_url, contract=fill_row.fill["contract"],
            underlying=underlying, fill_ts_et=fill_ts_et,
            trade_date=trade_date, DTE=DTE,
            spot_mode=spot_mode, rate_mode=rate_mode, iv_fallback=iv_fallback,
            session=session, fill_price=float(fill_row.fill["price"]),
            K=K, T=T, right=right, fixed_rate=rate,
        )
        return idx, enrich_fill(fill_row, fill_ts_norm, contract_parts, snap, T)

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_one_fill, i, fr) for i, fr in enumerate(fills)]
        for fut in as_completed(futures):
            idx, row = fut.result()
            results[idx] = row

    # A row failed because of UPQ if its notes mention "upq_error:" —
    # distinct from local skips like fill_ts_missing_tz or opra_parse_failed
    # which never reach the network.
    upq_failed_rows = sum(
        1 for row in results.values()
        if "upq_error:" in (row.get("notes") or "")
    )
    upq_client_error_rows = sum(
        1 for row in results.values()
        if "upq_client_error:" in (row.get("notes") or "")
    )
    upq_unreachable = upq_failed_rows == len(fills) and len(fills) > 0
    all_client_errors = upq_client_error_rows == len(fills) and len(fills) > 0

    if upq_unreachable or all_client_errors:
        # Don't leave a stale null-filled output behind — the next invocation
        # would hit the existing-output guard and need --overwrite to retry.
        return {
            "fills_written": 0,
            "skipped": False,
            "upq_unreachable": upq_unreachable,
            "all_client_errors": all_client_errors,
        }

    with out_path.open("w") as fh:
        for i in range(len(fills)):
            fh.write(json.dumps(results[i]) + "\n")

    return {
        "fills_written": len(fills),
        "skipped": False,
        "upq_unreachable": False,
        "all_client_errors": False,
    }
