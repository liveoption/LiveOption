"""Unit + smoke tests for trading.evaluation.greeks_at_fill."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "trades_sample.jsonl"


class TestNormalizeFillTs:
    def test_utc_input(self):
        from trading.evaluation.greeks_at_fill import normalize_fill_ts

        got = normalize_fill_ts("2026-04-13T14:31:00+00:00")
        assert got == ("2026-04-13T14:31:00+00:00", "2026-04-13T10:31:00", "2026-04-13")

    def test_et_input(self):
        from trading.evaluation.greeks_at_fill import normalize_fill_ts

        got = normalize_fill_ts("2026-04-13T10:31:00-04:00")
        # Same instant — result ET form and date must match
        assert got[1] == "2026-04-13T10:31:00"
        assert got[2] == "2026-04-13"

    def test_no_offset_returns_none(self):
        from trading.evaluation.greeks_at_fill import normalize_fill_ts

        assert normalize_fill_ts("2026-04-13T10:31:00") is None

    def test_expiry_boundary_15_et(self):
        """20:00Z on expiry day == 15:00 ET (the expiry anchor); T should be 0."""
        from trading.evaluation.greeks_at_fill import normalize_fill_ts
        from trading.core.greeks_math import compute_time_to_expiry

        _, fill_ts_et, trade_date = normalize_fill_ts("2026-05-16T19:00:00+00:00")
        assert fill_ts_et == "2026-05-16T15:00:00"
        assert trade_date == "2026-05-16"
        # T at expiry anchor must be exactly 0
        assert compute_time_to_expiry("2026-05-16", fill_ts_et) == 0.0

    def test_crosses_midnight_utc(self):
        """23:30 ET Apr 13 == 03:30Z Apr 14; trade_date must follow ET."""
        from trading.evaluation.greeks_at_fill import normalize_fill_ts

        _, fill_ts_et, trade_date = normalize_fill_ts("2026-04-14T03:30:00+00:00")
        assert fill_ts_et.startswith("2026-04-13T23:30:00")
        assert trade_date == "2026-04-13"


class TestIterFills:
    def test_fixture_yields_three_fills_in_order(self):
        from trading.evaluation.greeks_at_fill import iter_fills

        rows = list(iter_fills(FIXTURE))
        assert len(rows) == 3
        assert [r.fill["trade_id"] for r in rows] == ["t-001", "t-002", "t-003"]
        assert [r.step_id for r in rows] == [2, 3, 3]
        assert rows[0].current_date == "2026-04-13"
        assert rows[0].event_ts == "2026-04-13T14:31:00+00:00"

    def test_empty_file(self, tmp_path):
        from trading.evaluation.greeks_at_fill import iter_fills

        empty = tmp_path / "trades.jsonl"
        empty.write_text("")
        assert list(iter_fills(empty)) == []

    def test_skips_steps_with_no_fills(self, tmp_path):
        from trading.evaluation.greeks_at_fill import iter_fills
        import json

        p = tmp_path / "t.jsonl"
        p.write_text(
            json.dumps({"step_id": 1, "event_ts": "x", "current_date": "d",
                        "intents": [], "risk_decisions": [], "orders": [],
                        "fills": [], "errors": []}) + "\n"
        )
        assert list(iter_fills(p)) == []


class TestEnrichFill:
    def _make_fill_row(self, side="BUY", qty=2):
        from trading.evaluation.greeks_at_fill import FillRow
        return FillRow(
            step_id=2,
            event_ts="2026-04-13T14:31:00+00:00",
            current_date="2026-04-13",
            fill={
                "trade_id": "t-001", "order_id": "o-001",
                "contract": "O:AAPL260516C00185000",
                "side": side, "qty": qty, "price": 3.45, "fees": 1.30,
                "fill_ts": "2026-04-13T14:31:00+00:00",
            },
        )

    def test_reproducibility(self):
        """enrich_fill must write Greeks that are exactly what compute_all_greeks
        returns on the same (S, K, T, sigma, r, q) snapshot — that's the
        row-level reproducibility guarantee from spec §4.2."""
        from trading.evaluation.greeks_at_fill import enrich_fill, Snapshot
        from trading.core.greeks_math import compute_all_greeks

        fill_row = self._make_fill_row()
        fill_ts_norm = ("2026-04-13T14:31:00+00:00", "2026-04-13T10:31:00", "2026-04-13")
        contract_parts = ("AAPL", "2026-05-16", "C", 185.0)
        snap = Snapshot(
            S=183.27, sigma=0.2814, r=0.0478,
            iv_source="upq_minute", spot_mode="minute", rate_mode="upq_treasury",
            upq_reference={"delta": 0.4835, "gamma": 0.0310, "theta": -0.0871,
                           "vega": 0.2218, "iv": 0.2814, "greek_status": "ok"},
        )
        T = 0.0904
        row = enrich_fill(fill_row, fill_ts_norm, contract_parts, snap, T)

        expected = compute_all_greeks(S=183.27, K=185.0, T=0.0904, sigma=0.2814,
                                      r=0.0478, right="C")
        for g in ("delta", "gamma", "theta", "vega", "volga", "vanna"):
            assert row[g] == expected[g]

        # Snapshot inputs are persisted verbatim
        assert row["S"] == 183.27
        assert row["strike"] == 185.0
        assert row["T"] == 0.0904
        assert row["sigma"] == 0.2814
        assert row["r"] == 0.0478
        assert row["q"] == 0.0

        # upq_reference carried through untouched
        assert row["upq_reference"]["delta"] == 0.4835

        # Provenance recorded
        assert row["provenance"] == {
            "spot_mode": "minute", "rate_mode": "upq_treasury",
            "iv_source": "upq_minute", "greek_source": "local_bsm_from_snapshot",
        }

        # Dollar delta: BUY qty=2 * 100 * delta
        assert row["dollar_delta"] == pytest.approx(2 * 100 * expected["delta"], abs=1e-9)

    def test_dollar_delta_sign_sell(self):
        from trading.evaluation.greeks_at_fill import enrich_fill, Snapshot

        fill_row = self._make_fill_row(side="SELL", qty=3)
        fill_ts_norm = ("2026-04-13T14:31:00+00:00", "2026-04-13T10:31:00", "2026-04-13")
        contract_parts = ("AAPL", "2026-05-16", "C", 185.0)
        snap = Snapshot(
            S=183.27, sigma=0.28, r=0.05,
            iv_source="upq_minute", spot_mode="minute", rate_mode="upq_treasury",
            upq_reference=None,
        )
        row = enrich_fill(fill_row, fill_ts_norm, contract_parts, snap, T=0.0904)
        assert row["dollar_delta"] < 0  # SELL flips sign

    def test_opra_parse_failed(self):
        from trading.evaluation.greeks_at_fill import enrich_fill, Snapshot, FillRow

        fill_row = FillRow(
            step_id=1, event_ts="x", current_date="2026-04-13",
            fill={"trade_id": "t-bad", "order_id": "o-bad", "contract": "NOT_OPRA",
                  "side": "BUY", "qty": 1, "price": 1.0, "fees": 0.0,
                  "fill_ts": "2026-04-13T14:31:00+00:00"},
        )
        fill_ts_norm = ("2026-04-13T14:31:00+00:00", "2026-04-13T10:31:00", "2026-04-13")
        snap = Snapshot(S=None, sigma=None, r=None,
                        iv_source="missing", spot_mode="minute",
                        rate_mode="upq_treasury", upq_reference=None)
        row = enrich_fill(fill_row, fill_ts_norm, None, snap, T=None)
        assert row["underlying"] is None
        assert row["expiry"] is None
        assert row["strike"] is None
        assert row["right"] is None
        assert "opra_parse_failed" in (row["notes"] or "")
        assert all(row[g] is None for g in ("delta", "gamma", "theta", "vega", "volga", "vanna"))

    def test_t_below_min(self):
        from trading.evaluation.greeks_at_fill import enrich_fill, Snapshot

        fill_row = self._make_fill_row()
        fill_ts_norm = ("2026-05-16T19:00:00+00:00", "2026-05-16T15:00:00", "2026-05-16")
        contract_parts = ("AAPL", "2026-05-16", "C", 185.0)
        snap = Snapshot(S=183.0, sigma=0.2, r=0.05,
                        iv_source="upq_minute", spot_mode="minute",
                        rate_mode="upq_treasury", upq_reference=None)
        row = enrich_fill(fill_row, fill_ts_norm, contract_parts, snap, T=0.0)
        assert all(row[g] is None for g in ("delta", "gamma", "theta", "vega", "volga", "vanna"))
        assert all(row[d] is None for d in ("dollar_delta", "dollar_gamma", "dollar_theta",
                                             "dollar_vega", "dollar_volga", "dollar_vanna"))
        assert "T_below_min" in (row["notes"] or "")

    def test_sigma_none_keeps_upq_reference(self):
        from trading.evaluation.greeks_at_fill import enrich_fill, Snapshot

        fill_row = self._make_fill_row()
        fill_ts_norm = ("2026-04-13T14:31:00+00:00", "2026-04-13T10:31:00", "2026-04-13")
        contract_parts = ("AAPL", "2026-05-16", "C", 185.0)
        snap = Snapshot(S=183.0, sigma=None, r=0.05,
                        iv_source="missing", spot_mode="minute",
                        rate_mode="upq_treasury",
                        upq_reference={"greek_status": "below_intrinsic"})
        row = enrich_fill(fill_row, fill_ts_norm, contract_parts, snap, T=0.0904)
        assert row["sigma"] is None
        assert all(row[g] is None for g in ("delta", "gamma", "theta", "vega", "volga", "vanna"))
        assert row["upq_reference"] == {"greek_status": "below_intrinsic"}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _rates_resp(rate_decimal: float, date: str = "2026-04-13") -> "_FakeResp":
    """Shorthand for a /rates/query response that interpolates to ``rate_decimal``.

    Fills every supported tenor column with the same percent value so the
    client's DTE interpolation returns exactly ``rate_decimal`` regardless of DTE.
    """
    pct = rate_decimal * 100
    return _FakeResp([{
        "date": date,
        "yield_1_month": pct, "yield_3_month": pct, "yield_6_month": pct,
        "yield_1_year": pct, "yield_2_year": pct, "yield_3_year": pct,
        "yield_5_year": pct, "yield_7_year": pct, "yield_10_year": pct,
        "yield_20_year": pct, "yield_30_year": pct,
    }])


class _FakeSession:
    """Minimal stand-in for requests.Session used by fetch_snapshot.

    Configure with a mapping from (method, url_path) → [list of responses in order]
    so a test can simulate retries by enqueuing failures followed by a success.
    """
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url, params=None, timeout=None, headers=None):
        from urllib.parse import urlparse
        path = urlparse(url).path
        self.calls.append(("GET", path, params or {}))
        resps = self.routes.get(path)
        if not resps:
            raise AssertionError(f"unexpected GET {path}")
        return resps.pop(0)


class TestFetchSnapshot:
    def test_minute_iv_present_minute_spot_treasury_rate(self):
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"window_start": "2026-04-13T10:31:00", "close": 3.45,
                 "delta": 0.4835, "gamma": 0.0310, "theta": -0.0871,
                 "vega": 0.2218, "iv": 0.2814, "greek_status": "ok"},
            ])],
            "/stock": [_FakeResp([
                {"window_start": "2026-04-13T10:31:00", "close": 183.27},
            ])],
            "/rates/query": [_rates_resp(0.0478)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.S == 183.27
        assert snap.sigma == 0.2814
        assert snap.r == 0.0478
        assert snap.iv_source == "upq_minute"
        assert snap.spot_mode == "minute"
        assert snap.rate_mode == "upq_treasury"
        assert snap.upq_reference["delta"] == 0.4835

    def test_minute_iv_null_fallback_skip(self):
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"close": 3.45, "delta": None, "gamma": None, "theta": None,
                 "vega": None, "iv": None, "greek_status": "below_intrinsic"},
            ])],
            "/stock": [_FakeResp([{"close": 183.27}])],
            "/rates/query": [_rates_resp(0.0478)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.sigma is None
        assert snap.iv_source == "missing"
        assert snap.upq_reference["greek_status"] == "below_intrinsic"

    def test_minute_iv_null_fallback_newton_converges(self):
        from trading.evaluation.greeks_at_fill import fetch_snapshot
        from trading.core.greeks_math import _bsm_price

        # Set fill_price to a well-priced ATM option — Brent will recover sigma.
        price = _bsm_price(100.0, 100.0, 0.25, 0.22, 0.05, 0.0, "C")
        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"close": price, "iv": None, "greek_status": "no_bracket"},
            ])],
            "/stock": [_FakeResp([{"close": 100.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:XYZ260413C00100000",
            underlying="XYZ", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=91,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="newton",
            session=session, K=100.0, T=0.25, right="C", fill_price=price,
        )
        assert snap.iv_source == "newton_from_fill_price"
        assert snap.sigma == pytest.approx(0.22, abs=1e-6)

    def test_newton_fails_records_note(self):
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"close": 0.01, "iv": None, "greek_status": "below_intrinsic"},
            ])],
            "/stock": [_FakeResp([{"close": 110.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:XYZ260413C00100000",
            underlying="XYZ", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=1,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="newton",
            session=session, K=100.0, T=0.003, right="C", fill_price=0.01,
        )
        assert snap.sigma is None
        assert snap.iv_source == "missing"
        assert any("newton" in n for n in (snap.notes or []))

    def test_rates_query_failure_falls_back_to_fixed(self):
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"close": 3.45, "iv": 0.28, "greek_status": "ok"},
            ])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_FakeResp({}, status=500),
                              _FakeResp({}, status=500),
                              _FakeResp({}, status=500)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
            fixed_rate=0.05,
        )
        assert snap.r == 0.05
        assert snap.rate_mode == "fixed"
        assert any("rates_query_failed_used_fixed" in n for n in (snap.notes or []))

    def test_ticker_query_5xx_retries_then_succeeds(self, monkeypatch):
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [
                _FakeResp({}, status=500),
                _FakeResp({}, status=502),
                _FakeResp([{"close": 3.45, "iv": 0.28, "greek_status": "ok"}]),
            ],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        # Patch the sleep so the test doesn't block for 3s
        monkeypatch.setattr("trading.evaluation.greeks_at_fill.time.sleep", lambda s: None)

        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.sigma == 0.28

    def test_ticker_query_404_does_not_retry(self, monkeypatch):
        """4xx responses are deterministic — break immediately, don't retry."""
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        sleep_calls: list[float] = []
        monkeypatch.setattr(
            "trading.evaluation.greeks_at_fill.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        # Two 404s: primary minute bar and previous-minute fallback both 404.
        # Neither should sleep (4xx = no retry).
        session = _FakeSession({
            "/option/ticker_query": [
                _FakeResp({}, status=404),
                _FakeResp({}, status=404),
            ],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert sleep_calls == [], f"4xx should not sleep, got {sleep_calls}"
        assert snap.sigma is None
        assert snap.iv_source == "missing"

    def test_ticker_query_500_final_attempt_no_wasted_sleep(self, monkeypatch):
        """On three consecutive 5xx responses, we should sleep twice (between
        attempts 0-1 and 1-2) — NOT three times. The final attempt's failure
        must not trigger a 4s sleep before giving up."""
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        sleep_calls: list[float] = []
        monkeypatch.setattr(
            "trading.evaluation.greeks_at_fill.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        session = _FakeSession({
            "/option/ticker_query": [
                _FakeResp({}, status=500),
                _FakeResp({}, status=500),
                _FakeResp({}, status=500),
            ],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        # One call to _fetch_option_minute (widened ±5min window, no second
        # previous-minute retry) → _get_with_retry does 3 attempts → 2 sleeps
        # [1.0, 2.0]. Zero sleeps of 4.0 means the final attempt didn't waste time.
        assert 4.0 not in sleep_calls, f"wasted final-attempt sleep: {sleep_calls}"
        assert sleep_calls == [1.0, 2.0], f"got {sleep_calls}"

    def test_daily_chain_fallback(self):
        """When iv_fallback='daily_chain', /option/chain_query is queried and
        the matching strike's IV becomes sigma. iv_source must be 'upq_daily_chain'."""
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"close": 3.45, "iv": None, "greek_status": "below_intrinsic"},
            ])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
            "/option/chain_query": [_FakeResp([
                {"ticker": "O:AAPL260516C00180000", "strike": 180.0, "iv": 0.31},
                {"ticker": "O:AAPL260516C00185000", "strike": 185.0, "iv": 0.29},
                {"ticker": "O:AAPL260516C00190000", "strike": 190.0, "iv": 0.27},
            ])],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury",
            iv_fallback="daily_chain",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.sigma == 0.29  # Matching strike 185.0
        assert snap.iv_source == "upq_daily_chain"

    def test_widened_minute_window_picks_nearest_bar(self):
        """The fetcher queries a ±5-minute window and picks the bar whose
        window_start is nearest to fill_ts_et. A non-zero offset is recorded
        in notes as 'minute_bar_offset_sec:<signed int>'.

        Rationale: UPQ returns minute bars only for minutes with trade activity,
        so an exact 1-minute window is frequently empty even when surrounding
        minutes have bars (verified on qlib, see POC 2026-04-24).
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        fill_ns = int(datetime.fromisoformat("2026-04-13T10:31:00")
                      .replace(tzinfo=ZoneInfo("America/New_York"))
                      .timestamp() * 1_000_000_000)
        # Offer 3 bars at offsets -120s, +60s, +180s → nearest is +60s
        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"window_start": fill_ns - 120 * 10**9, "close": 3.20,
                 "iv": 0.25, "greek_status": "ok"},
                {"window_start": fill_ns + 60 * 10**9, "close": 3.45,
                 "iv": 0.28, "greek_status": "ok"},
                {"window_start": fill_ns + 180 * 10**9, "close": 3.60,
                 "iv": 0.30, "greek_status": "ok"},
            ])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.sigma == 0.28  # bar at +60s
        assert any("minute_bar_offset_sec:60" in n for n in (snap.notes or []))
        # One ticker_query call — the widened window subsumes the old
        # primary + previous-minute two-call pattern.
        tq_calls = [c for c in session.calls if c[1] == "/option/ticker_query"]
        assert len(tq_calls) == 1

    def test_query_sends_utc_wallclock_not_et(self):
        """UPQ's /option/ticker_query and /stock interpret start/end as UTC
        wall-clock (verified on qlib 2026-04-24). fill_ts_et is ET wall-clock,
        so the library MUST convert before querying. Reading bars from the
        wrong UTC offset was a silent correctness bug in the pre-widen code
        (see POC 2026-04-24 offsets of -17640 sec = -4.9 h)."""
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"window_start": 0, "close": 3.45, "iv": 0.28,
                 "greek_status": "ok"},
            ])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        # 2026-04-13 10:31 ET (EDT, UTC-4) → 14:31 UTC. The option ticker
        # query window must be centred at 14:31 UTC, not 10:31 UTC.
        tq_calls = [c for c in session.calls if c[1] == "/option/ticker_query"]
        assert len(tq_calls) == 1
        params = tq_calls[0][2]
        assert params["start"] == "2026-04-13T14:26:00"  # 14:31 − 5 min
        assert params["end"] == "2026-04-13T14:37:00"    # 14:31 + 6 min
        # Spot query uses fill-minute UTC start too
        stock_calls = [c for c in session.calls if c[1] == "/stock"]
        assert stock_calls[0][2]["start"] == "2026-04-13T14:31:00"
        assert stock_calls[0][2]["end"] == "2026-04-13T14:32:00"

    def test_upq_daily_spot_uses_full_iso_datetime_and_picks_rth_close(self):
        """UPQ /stock requires start/end as full ISO datetime (not date-only,
        verified on qlib 2026-04-24 — date-only returns HTTP 400). In
        ``--spot-mode upq_daily``, the response is ~840 minute bars across
        a 24h window; the library picks the last bar whose window_start is
        at or before 21:00 UTC (16:00 ET EST RTH close)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        def _ns(ts: str) -> int:
            return int(datetime.fromisoformat(ts).replace(
                tzinfo=ZoneInfo("UTC")).timestamp() * 1_000_000_000)

        session = _FakeSession({
            # Minute option bar empty — after-hours fill, triggers daily_chain fallback
            "/option/ticker_query": [_FakeResp([])],
            "/option/chain_query": [_FakeResp([
                {"ticker": "O:AMD250221P00120000", "strike": 120.0, "iv": 0.34},
            ])],
            "/stock": [_FakeResp([
                {"window_start": _ns("2025-01-08T14:30:00"), "close": 128.0},
                {"window_start": _ns("2025-01-08T18:00:00"), "close": 122.5},
                {"window_start": _ns("2025-01-08T20:59:00"), "close": 120.8},
                # Post-RTH close bar — must NOT be selected
                {"window_start": _ns("2025-01-08T23:50:00"), "close": 119.3},
            ])],
            "/rates/query": [_rates_resp(0.045, date="2025-01-08")],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AMD250221P00120000",
            underlying="AMD", fill_ts_et="2025-01-08T19:00:00",
            trade_date="2025-01-08", DTE=44,
            spot_mode="upq_daily", rate_mode="upq_treasury",
            iv_fallback="daily_chain",
            session=session, K=120.0, T=0.1205, right="P", fill_price=5.0,
        )
        assert snap.S == 120.8  # RTH close bar, not the 23:50 after-hours
        assert snap.iv_source == "upq_daily_chain"
        stock_calls = [c for c in session.calls if c[1] == "/stock"]
        assert stock_calls[0][2]["start"] == "2025-01-08T00:00:00"
        assert stock_calls[0][2]["end"] == "2025-01-08T23:59:59"

    def test_rates_query_new_api_and_dte_interpolation(self):
        """UPQ /rates/query now accepts ``start``/``end`` (date range) and returns
        per-day Treasury yields at standard tenors in percent. The client must
        (a) pass the new params, (b) parse the list response, and (c) linearly
        interpolate between bracketing tenors by DTE. Verified against UPQ on
        qlib 2026-04-24 (pid 3663109)."""
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        # DTE = 60 sits between 1M (30d, 4.40%) and 3M (91d, 5.00%).
        # Expected interp: 4.40 + (60-30)/(91-30) * (5.00 - 4.40)
        #                = 4.40 + (30/61) * 0.60 = 4.40 + 0.2951... = 4.6951...%
        #                = 0.04695081... (decimal)
        rates_row = {
            "date": "2026-04-13",
            "yield_1_month": 4.40, "yield_3_month": 5.00,
            "yield_1_year": 5.00,  "yield_2_year": 5.00,
            "yield_5_year": 5.00,  "yield_10_year": 5.00,
            "yield_30_year": 5.00,
        }
        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"window_start": 0, "close": 3.45,
                 "iv": 0.28, "greek_status": "ok"},
            ])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_FakeResp([rates_row])],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=60,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        # Outbound params must be start/end, not date/tenor_days
        rates_calls = [c for c in session.calls if c[1] == "/rates/query"]
        assert rates_calls == [("GET", "/rates/query",
                                {"start": "2026-04-13", "end": "2026-04-13"})]
        # Interpolated rate within tolerance
        assert snap.rate_mode == "upq_treasury"
        assert abs(snap.r - 0.046951) < 1e-5, f"got {snap.r}"

    def test_rates_query_empty_list_falls_back_to_fixed(self):
        """When /rates/query returns an empty list (date missing in parquet),
        fall back to fixed without marking as upq_error."""
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"window_start": 0, "close": 3.45, "iv": 0.28,
                 "greek_status": "ok"},
            ])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_FakeResp([])],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.r == 0.05  # default fixed_rate (fetch_snapshot's default is 0.05)
        assert snap.rate_mode == "fixed"
        assert any("rates_query_failed_used_fixed" in n for n in (snap.notes or []))

    def test_no_minute_bar_when_widened_window_empty(self):
        """If the ±5-minute window returns nothing, fetcher records
        'no_minute_bar' and does NOT retry a narrower window."""
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.sigma is None
        assert any("no_minute_bar" == n.strip() or n.strip().startswith("no_minute_bar")
                   for n in (snap.notes or []))
        tq_calls = [c for c in session.calls if c[1] == "/option/ticker_query"]
        assert len(tq_calls) == 1

    def test_exact_minute_hit_no_offset_note(self):
        """When the nearest bar lines up with fill_ts_et exactly, no
        offset note is emitted."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from trading.evaluation.greeks_at_fill import fetch_snapshot

        fill_ns = int(datetime.fromisoformat("2026-04-13T10:31:00")
                      .replace(tzinfo=ZoneInfo("America/New_York"))
                      .timestamp() * 1_000_000_000)
        session = _FakeSession({
            "/option/ticker_query": [_FakeResp([
                {"window_start": fill_ns, "close": 3.45,
                 "iv": 0.28, "greek_status": "ok"},
            ])],
            "/stock": [_FakeResp([{"close": 183.0}])],
            "/rates/query": [_rates_resp(0.05)],
        })
        snap = fetch_snapshot(
            upq_url="http://x", contract="O:AAPL260516C00185000",
            underlying="AAPL", fill_ts_et="2026-04-13T10:31:00",
            trade_date="2026-04-13", DTE=33,
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            session=session, K=185.0, T=0.0904, right="C", fill_price=3.45,
        )
        assert snap.sigma == 0.28
        assert not any("minute_bar_offset_sec" in n for n in (snap.notes or []))


class TestProcessRun:
    def _patch_fetch(self, monkeypatch):
        """Replace fetch_snapshot with a deterministic stand-in."""
        from trading.evaluation.greeks_at_fill import Snapshot

        def fake(**kw):
            return Snapshot(
                S=100.0, sigma=0.25, r=0.05,
                iv_source="upq_minute", spot_mode=kw["spot_mode"],
                rate_mode="upq_treasury",
                upq_reference={"delta": 0.55, "gamma": 0.02, "theta": -0.05,
                               "vega": 0.2, "iv": 0.25, "greek_status": "ok"},
            )
        monkeypatch.setattr("trading.evaluation.greeks_at_fill.fetch_snapshot", fake)

    def test_writes_one_row_per_fill(self, tmp_path, monkeypatch):
        from trading.evaluation.greeks_at_fill import process_run
        import json, shutil

        self._patch_fetch(monkeypatch)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")

        stats = process_run(
            run_dir=run_dir, upq_url="http://fake",
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            rate=0.05, workers=2, overwrite=False,
        )
        assert stats["fills_written"] == 3
        out = run_dir / "greeks_at_fill.jsonl"
        rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        assert len(rows) == 3
        assert [r["trade_id"] for r in rows] == ["t-001", "t-002", "t-003"]
        # All rows have full Greeks (fake snapshot is valid)
        for r in rows:
            assert r["delta"] is not None
            assert r["provenance"]["iv_source"] == "upq_minute"

    def test_skip_existing(self, tmp_path, monkeypatch):
        from trading.evaluation.greeks_at_fill import process_run
        import shutil

        self._patch_fetch(monkeypatch)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")
        (run_dir / "greeks_at_fill.jsonl").write_text('{"trade_id": "pre"}\n')

        stats = process_run(
            run_dir=run_dir, upq_url="http://fake",
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            rate=0.05, workers=2, skip_existing=True,
        )
        assert stats["skipped"] is True
        assert (run_dir / "greeks_at_fill.jsonl").read_text() == '{"trade_id": "pre"}\n'

    def test_overwrite(self, tmp_path, monkeypatch):
        from trading.evaluation.greeks_at_fill import process_run
        import shutil

        self._patch_fetch(monkeypatch)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")
        (run_dir / "greeks_at_fill.jsonl").write_text('{"trade_id": "pre"}\n')

        stats = process_run(
            run_dir=run_dir, upq_url="http://fake",
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            rate=0.05, workers=2, overwrite=True,
        )
        assert stats["fills_written"] == 3

    def test_no_flag_aborts_on_existing(self, tmp_path, monkeypatch):
        from trading.evaluation.greeks_at_fill import process_run, ExistingOutputError
        import shutil

        self._patch_fetch(monkeypatch)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")
        (run_dir / "greeks_at_fill.jsonl").write_text("existing\n")

        with pytest.raises(ExistingOutputError):
            process_run(
                run_dir=run_dir, upq_url="http://fake",
                spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
                rate=0.05, workers=2,
            )

    def test_upq_unreachable_marks_run(self, tmp_path, monkeypatch):
        """If every fill has S=None and sigma=None, mark run as upq_unreachable."""
        from trading.evaluation.greeks_at_fill import process_run, Snapshot
        import shutil

        def fake_unreachable(**kw):
            return Snapshot(
                S=None, sigma=None, r=0.05,
                iv_source="missing", spot_mode=kw["spot_mode"],
                rate_mode="fixed", upq_reference=None,
                notes=["upq_error: ConnectionError"],
            )
        monkeypatch.setattr(
            "trading.evaluation.greeks_at_fill.fetch_snapshot", fake_unreachable,
        )

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")

        stats = process_run(
            run_dir=run_dir, upq_url="http://nope",
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            rate=0.05, workers=2,
        )
        assert stats["fills_written"] == 0
        assert stats.get("upq_unreachable") is True
        assert not (run_dir / "greeks_at_fill.jsonl").exists()

    def test_opra_parse_failure_not_upq_unreachable(self, tmp_path, monkeypatch):
        """A run where every fill has a malformed OPRA contract must NOT
        be classified as upq_unreachable — UPQ was never contacted."""
        from trading.evaluation.greeks_at_fill import process_run
        import json

        # fetch_snapshot should NOT be called for parse-failed fills; patch
        # it to blow up if it is, so the test fails loudly on regression.
        monkeypatch.setattr(
            "trading.evaluation.greeks_at_fill.fetch_snapshot",
            lambda **kw: (_ for _ in ()).throw(AssertionError("fetch_snapshot should not be called")),
        )

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        # Build a trades.jsonl where every fill has a bad contract.
        bad_trades = {
            "step_id": 1,
            "event_ts": "2026-04-13T14:31:00+00:00",
            "current_date": "2026-04-13",
            "intents": [], "risk_decisions": [], "orders": [],
            "fills": [
                {"trade_id": "t-bad-1", "order_id": "o-1", "contract": "NOT_OPRA_A",
                 "side": "BUY", "qty": 1, "price": 1.0, "fees": 0.0,
                 "fill_ts": "2026-04-13T14:31:00+00:00"},
                {"trade_id": "t-bad-2", "order_id": "o-2", "contract": "NOT_OPRA_B",
                 "side": "SELL", "qty": 1, "price": 1.0, "fees": 0.0,
                 "fill_ts": "2026-04-13T14:32:00+00:00"},
            ],
            "errors": [],
        }
        (run_dir / "trades.jsonl").write_text(json.dumps(bad_trades) + "\n")

        stats = process_run(
            run_dir=run_dir, upq_url="http://x",
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            rate=0.05, workers=2,
        )
        assert stats["fills_written"] == 2
        assert stats.get("upq_unreachable") is False
        # Output file SHOULD exist — these are per-row skips, not a run failure
        assert (run_dir / "greeks_at_fill.jsonl").exists()
        rows = [json.loads(l) for l in (run_dir / "greeks_at_fill.jsonl").read_text().splitlines()]
        assert all("opra_parse_failed" in (r.get("notes") or "") for r in rows)

    def test_partial_upq_failure_not_marked(self, tmp_path, monkeypatch):
        """If at least one fill has S or sigma non-None, run is NOT upq_unreachable."""
        from trading.evaluation.greeks_at_fill import process_run, Snapshot
        import shutil

        call_count = {"n": 0}

        def fake_partial(**kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return Snapshot(
                    S=None, sigma=None, r=0.05,
                    iv_source="missing", spot_mode=kw["spot_mode"],
                    rate_mode="fixed", upq_reference=None,
                    notes=["upq_error: ConnectionError"],
                )
            return Snapshot(
                S=100.0, sigma=0.25, r=0.05,
                iv_source="upq_minute", spot_mode=kw["spot_mode"],
                rate_mode="upq_treasury", upq_reference=None,
            )
        monkeypatch.setattr(
            "trading.evaluation.greeks_at_fill.fetch_snapshot", fake_partial,
        )

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")

        stats = process_run(
            run_dir=run_dir, upq_url="http://flaky",
            spot_mode="minute", rate_mode="upq_treasury", iv_fallback="skip",
            rate=0.05, workers=2,
        )
        assert stats["fills_written"] == 3
        assert not stats.get("upq_unreachable", False)


class TestCLI:
    def test_cli_smoke(self, tmp_path, monkeypatch):
        """End-to-end: invoke main() with fetch_snapshot stubbed."""
        import shutil
        from trading.evaluation import greeks_at_fill as lib

        def fake(**kw):
            return lib.Snapshot(
                S=100.0, sigma=0.25, r=0.05,
                iv_source="upq_minute", spot_mode=kw["spot_mode"],
                rate_mode="upq_treasury", upq_reference=None,
            )
        monkeypatch.setattr(lib, "fetch_snapshot", fake)

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")

        # Import script's main via importlib so we can call it without subprocess.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "compute_greeks_at_fill",
            Path(__file__).resolve().parents[3] / "trading" / "evaluation" / "greeks_cli.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        rc = mod.main([str(run_dir), "--upq-url", "http://fake", "--workers", "2"])
        assert rc == 0
        assert (run_dir / "greeks_at_fill.jsonl").exists()
        lines = (run_dir / "greeks_at_fill.jsonl").read_text().splitlines()
        assert len(lines) == 3

    def test_cli_glob_batch(self, tmp_path, monkeypatch):
        import shutil
        from trading.evaluation import greeks_at_fill as lib

        def fake(**kw):
            return lib.Snapshot(
                S=100.0, sigma=0.25, r=0.05,
                iv_source="upq_minute", spot_mode=kw["spot_mode"],
                rate_mode="upq_treasury", upq_reference=None,
            )
        monkeypatch.setattr(lib, "fetch_snapshot", fake)

        for name in ("run_a", "run_b"):
            d = tmp_path / name
            d.mkdir()
            shutil.copy(FIXTURE, d / "trades.jsonl")

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "compute_greeks_at_fill",
            Path(__file__).resolve().parents[3] / "trading" / "evaluation" / "greeks_cli.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        rc = mod.main([str(tmp_path / "*" / "trades.jsonl"), "--upq-url", "http://fake"])
        assert rc == 0
        assert (tmp_path / "run_a" / "greeks_at_fill.jsonl").exists()
        assert (tmp_path / "run_b" / "greeks_at_fill.jsonl").exists()

    def test_cli_upq_unreachable_returns_2(self, tmp_path, monkeypatch):
        """When every fill fails UPQ, CLI returns exit code 2."""
        import shutil
        from trading.evaluation import greeks_at_fill as lib

        def fake(**kw):
            return lib.Snapshot(
                S=None, sigma=None, r=0.05,
                iv_source="missing", spot_mode=kw["spot_mode"],
                rate_mode="fixed", upq_reference=None,
                notes=["upq_error: ConnectionError"],
            )
        monkeypatch.setattr(lib, "fetch_snapshot", fake)

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        shutil.copy(FIXTURE, run_dir / "trades.jsonl")

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "compute_greeks_at_fill",
            Path(__file__).resolve().parents[3] / "trading" / "evaluation" / "greeks_cli.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        rc = mod.main([str(run_dir), "--upq-url", "http://nope", "--workers", "2"])
        assert rc == 2
