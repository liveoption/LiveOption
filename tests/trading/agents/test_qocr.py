"""Tests for QOCR — Quantized Option Chain Representation."""

from __future__ import annotations

import pytest

from trading.agents.qocr import (
    QOCRAction,
    QOCRBucket,
    QOCRConfig,
    QOCREncoder,
)


# ---------------------------------------------------------------------------
# Helpers — contract factory
# ---------------------------------------------------------------------------

def _make_contract(
    ticker: str = "O:SPY250407C00595000",
    strike: float = 595.0,
    right: str = "C",
    delta: float | None = 0.50,
    iv: float | None = 0.20,
    volume: int = 1000,
    dte: int = 1,
    close: float = 2.50,
) -> dict:
    return {
        "ticker": ticker,
        "strike": strike,
        "right": right,
        "delta": delta,
        "iv": iv,
        "volume": volume,
        "dte": dte,
        "close": close,
    }


def _make_chain(spot: float = 595.0) -> list[dict]:
    """Build a chain spanning multiple moneyness and expiry buckets."""
    contracts = []

    # --- Short-term calls (DTE=3, within 0-14D bucket) ---
    # Call ITM: K/S = 575/595 = 0.9664 < 0.99
    contracts.append(_make_contract(
        ticker="O:SPY250410C00575000", strike=575, right="C",
        delta=0.85, iv=0.25, volume=200, dte=3,
    ))
    # Call ATM: K/S = 594/595 = 0.9983 in [0.99, 1.01]
    contracts.append(_make_contract(
        ticker="O:SPY250410C00594000", strike=594, right="C",
        delta=0.52, iv=0.18, volume=5000, dte=3,
    ))
    contracts.append(_make_contract(
        ticker="O:SPY250410C00596000", strike=596, right="C",
        delta=0.48, iv=0.185, volume=8000, dte=3,
    ))
    # Call OTM: K/S = 610/595 = 1.0252 > 1.01
    contracts.append(_make_contract(
        ticker="O:SPY250410C00610000", strike=610, right="C",
        delta=0.20, iv=0.16, volume=1500, dte=3,
    ))

    # --- Medium-term calls (DTE=30, within 15-45D bucket) ---
    contracts.append(_make_contract(
        ticker="O:SPY250507C00595000", strike=595, right="C",
        delta=0.50, iv=0.20, volume=3000, dte=30,
    ))  # atm, medium
    contracts.append(_make_contract(
        ticker="O:SPY250507C00620000", strike=620, right="C",
        delta=0.25, iv=0.17, volume=1000, dte=30,
    ))  # otm, medium

    # --- Long-term calls (DTE=60, within 46-120D bucket) ---
    contracts.append(_make_contract(
        ticker="O:SPY250606C00595000", strike=595, right="C",
        delta=0.50, iv=0.22, volume=2000, dte=60,
    ))  # atm, long

    # --- Short-term puts (DTE=3) ---
    contracts.append(_make_contract(
        ticker="O:SPY250410P00594000", strike=594, right="P",
        delta=-0.48, iv=0.19, volume=6000, dte=3,
    ))  # atm put (K/S=0.9983 in ATM window)
    contracts.append(_make_contract(
        ticker="O:SPY250410P00580000", strike=580, right="P",
        delta=-0.20, iv=0.24, volume=2500, dte=3,
    ))  # otm put (K/S=0.9748 < 0.99)
    contracts.append(_make_contract(
        ticker="O:SPY250410P00610000", strike=610, right="P",
        delta=-0.80, iv=0.17, volume=500, dte=3,
    ))  # itm put (K/S=1.0252 > 1.01)

    # --- Medium-term puts (DTE=30) ---
    contracts.append(_make_contract(
        ticker="O:SPY250507P00570000", strike=570, right="P",
        delta=-0.15, iv=0.28, volume=800, dte=30,
    ))  # otm put (K/S=0.958 < 0.99)

    return contracts


# ---------------------------------------------------------------------------
# Test 1: Bucket assignment
# ---------------------------------------------------------------------------

class TestBucketAssignment:
    """Contracts are assigned to the correct moneyness x expiry bucket."""

    def test_call_atm_short(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["call:atm:short"]
        assert bucket.contract_count == 2
        strikes = sorted(c["strike"] for c in bucket.contracts)
        assert strikes == [594, 596]

    def test_call_itm_short(self):
        """ITM calls (K < spot×atm_lo) go into call:itm:short."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["call:itm:short"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 575

    def test_call_otm_short(self):
        """OTM calls (K > spot×atm_hi) go into call:otm:short."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["call:otm:short"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 610

    def test_call_atm_medium(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["call:atm:medium"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 595

    def test_call_otm_medium(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["call:otm:medium"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 620

    def test_call_atm_long(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["call:atm:long"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["dte"] == 60

    def test_put_atm_short(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["put:atm:short"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 594

    def test_put_otm_short(self):
        """OTM puts (K < spot×atm_lo) go into put:otm:short."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["put:otm:short"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 580

    def test_put_itm_short(self):
        """ITM puts (K > spot×atm_hi) go into put:itm:short."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["put:itm:short"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 610

    def test_put_otm_medium(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        bucket = buckets["put:otm:medium"]
        assert bucket.contract_count == 1
        assert bucket.contracts[0]["strike"] == 570

    def test_empty_buckets_exist(self):
        """All 18 bucket keys should exist even if empty (2 × 3 × 3)."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        assert len(buckets) == 18  # 2 sides × 3 moneyness × 3 expiry
        # Check an empty one
        bucket = buckets["put:atm:long"]
        assert bucket.contract_count == 0
        assert bucket.representative is None

    def test_deep_itm_call_included(self):
        """Deep ITM/OTM contracts are included (no pre-moneyness filter)."""
        encoder = QOCREncoder()
        chain = [
            _make_contract(strike=500, right="C", dte=5),  # 500/595=0.840 deep ITM
            _make_contract(strike=650, right="C", dte=5),  # 650/595=1.092 deep OTM
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        # Both should be included (deep ITM → itm bucket, deep OTM → otm bucket)
        assert buckets["call:itm:short"].contract_count == 1
        assert buckets["call:otm:short"].contract_count == 1

    def test_contract_outside_expiry_range_dropped(self):
        """Contract with DTE > max expiry range is not bucketed."""
        encoder = QOCREncoder()
        chain = [
            _make_contract(strike=595, right="C", dte=150),  # > 120
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        total = sum(b.contract_count for b in buckets.values())
        assert total == 0


# ---------------------------------------------------------------------------
# Test 2: Moneyness boundary conditions
# ---------------------------------------------------------------------------

class TestMoneynessBoundaries:
    """Boundary values for moneyness bucket assignment."""

    def test_exact_lower_bound(self):
        """K/S = 0.95 → ITM call (since 0.95 < atm_lo=0.99)."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=95, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["call:itm:short"].contract_count == 1

    def test_exact_upper_bound(self):
        """K/S = 1.05 → OTM call (since 1.05 > atm_hi=1.01)."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=105, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["call:otm:short"].contract_count == 1

    def test_boundary_0_99(self):
        """K/S = 0.99 → ATM (lower bound inclusive)."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=99, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["call:atm:short"].contract_count == 1

    def test_boundary_1_01(self):
        """K/S = 1.01 → ATM (upper bound inclusive)."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=101, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["call:atm:short"].contract_count == 1

    def test_just_below_0_99(self):
        """K/S = 0.989 → ITM call."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=98.9, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["call:itm:short"].contract_count == 1

    def test_just_above_1_01(self):
        """K/S = 1.011 → OTM call."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=101.1, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["call:otm:short"].contract_count == 1

    def test_put_itm_atm_boundary(self):
        """For puts: K/S = 1.01 → ATM (borderline, stays ATM)."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=101, right="P", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["put:atm:short"].contract_count == 1

    def test_put_itm_above_atm_hi(self):
        """For puts: K/S = 1.05 > atm_hi → ITM put."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=105, right="P", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["put:itm:short"].contract_count == 1


# ---------------------------------------------------------------------------
# Test 3: Representative selection
# ---------------------------------------------------------------------------

class TestRepresentativeSelection:
    """The representative contract minimizes the score function."""

    def test_representative_closest_to_center(self):
        """Among multiple ATM short contracts, pick the one closest to ATM
        center (K/S=1.0) and DTE center (7 for 0-14D bucket)."""
        encoder = QOCREncoder()
        chain = [
            _make_contract(strike=594, right="C", dte=3, iv=0.18),
            _make_contract(strike=595, right="C", dte=7, iv=0.19),  # closest
            _make_contract(strike=596, right="C", dte=10, iv=0.17),
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        rep = buckets["call:atm:short"].representative
        assert rep is not None
        assert rep["strike"] == 595
        assert rep["dte"] == 7

    def test_representative_expiry_matters(self):
        """With same moneyness, closer DTE to bucket center wins."""
        encoder = QOCREncoder()
        # medium bucket center = (15+45)/2 = 30
        chain = [
            _make_contract(strike=595, right="C", dte=20, iv=0.19),
            _make_contract(strike=595, right="C", dte=30, iv=0.20),  # exact center
            _make_contract(strike=595, right="C", dte=40, iv=0.18),
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        rep = buckets["call:atm:medium"].representative
        assert rep is not None
        assert rep["dte"] == 30

    def test_representative_empty_bucket(self):
        """Empty bucket should have no representative."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain([], spot_price=595.0)
        rep = buckets["call:atm:short"].representative
        assert rep is None

    def test_single_contract_is_representative(self):
        """A single OTM call in a bucket is always the representative."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=610, right="C", dte=3, iv=0.16)]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        rep = buckets["call:otm:short"].representative
        assert rep is not None
        assert rep["strike"] == 610

    def test_itm_representative_closest_to_atm_boundary(self):
        """For ITM bucket, picks the contract closest to atm_lo (least ITM)."""
        encoder = QOCREncoder()
        # Both are ITM calls (K < 595*0.99=589.05): K=575 and K=585
        chain = [
            _make_contract(strike=575, right="C", dte=5),  # deeper ITM
            _make_contract(strike=585, right="C", dte=5),  # less ITM (closer to atm_lo)
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        rep = buckets["call:itm:short"].representative
        assert rep is not None
        assert rep["strike"] == 585  # closest to atm_lo=595*0.99=589.05


# ---------------------------------------------------------------------------
# Test 4: Average IV computation
# ---------------------------------------------------------------------------

class TestAverageIV:
    """Bucket avg_iv should reflect the mean IV of contained contracts."""

    def test_avg_iv_computation(self):
        encoder = QOCREncoder()
        chain = [
            _make_contract(strike=594, right="C", dte=3, iv=0.18),
            _make_contract(strike=596, right="C", dte=3, iv=0.20),
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        bucket = buckets["call:atm:short"]
        assert abs(bucket.avg_iv - 0.19) < 1e-6

    def test_avg_iv_excludes_zero_and_none(self):
        encoder = QOCREncoder()
        chain = [
            _make_contract(strike=594, right="C", dte=3, iv=0.18),
            _make_contract(strike=596, right="C", dte=3, iv=0.0),
            _make_contract(strike=597, right="C", dte=3, iv=None),
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        bucket = buckets["call:atm:short"]
        assert abs(bucket.avg_iv - 0.18) < 1e-6

    def test_avg_iv_zero_when_no_iv(self):
        encoder = QOCREncoder()
        chain = [
            _make_contract(strike=594, right="C", dte=3, iv=None),
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        bucket = buckets["call:atm:short"]
        assert bucket.avg_iv == 0.0


# ---------------------------------------------------------------------------
# Test 5: Action decoding
# ---------------------------------------------------------------------------

class TestActionDecoding:
    """decode_action should return the representative contract."""

    def test_decode_valid_action(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)

        action = QOCRAction(
            underlying="SPY", side="call", moneyness_bucket="atm",
            expiry_bucket="short", qty=2,
        )
        contract = encoder.decode_action(action, buckets)
        assert contract is not None
        assert contract["right"] == "C"
        # Should be one of the ATM short-term contracts
        assert 589 < contract["strike"] < 601

    def test_decode_empty_bucket(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)

        action = QOCRAction(
            underlying="SPY", side="put", moneyness_bucket="atm",
            expiry_bucket="long", qty=1,
        )
        contract = encoder.decode_action(action, buckets)
        assert contract is None

    def test_decode_put_otm_action(self):
        """OTM put = K < spot×atm_lo → put:otm:short."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)

        action = QOCRAction(
            underlying="SPY", side="put", moneyness_bucket="otm",
            expiry_bucket="short", qty=5,
        )
        contract = encoder.decode_action(action, buckets)
        assert contract is not None
        assert contract["right"] == "P"
        assert contract["strike"] == 580

    def test_decode_nonexistent_key(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain([], spot_price=595.0)
        action = QOCRAction(
            underlying="SPY", side="call", moneyness_bucket="invalid",
            expiry_bucket="short", qty=1,
        )
        contract = encoder.decode_action(action, buckets)
        assert contract is None


# ---------------------------------------------------------------------------
# Test 6: Prompt formatting
# ---------------------------------------------------------------------------

class TestFormatForPrompt:
    """format_for_prompt should produce readable structured text."""

    def test_header(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets, underlying="SPY",
                                         spot_price=595.0)
        assert "Tradable Action Space (QOCR)" in text
        assert "SPY" in text
        assert "595.00" in text

    def test_contains_calls_and_puts(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "CALLS:" in text
        assert "PUTS:" in text

    def test_contains_itm_atm_otm_labels(self):
        """New labels should be ITM, ATM, OTM."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "ATM" in text
        assert "ITM" in text
        assert "OTM" in text

    def test_contains_dte_labels(self):
        """Should show DTE=N labels for actual DTE values."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "DTE=3" in text  # contracts with DTE=3 exist

    def test_contains_expiry_bucket_label(self):
        """Should show expiry bucket labels (SHORT, MEDIUM, LONG)."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "SHORT" in text

    def test_contains_atm_window(self):
        """Should show ATM price window."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets, spot_price=595.0)
        assert "ATM window:" in text

    def test_contains_iv(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "IV=" in text

    def test_contains_qty_levels(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain(_make_chain(), spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "Available qty levels:" in text
        assert "1, 2, 5, 10" in text

    def test_empty_chain_formatting(self):
        encoder = QOCREncoder()
        buckets = encoder.encode_chain([], spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "no contracts" in text


# ---------------------------------------------------------------------------
# Test 7: Custom config
# ---------------------------------------------------------------------------

class TestCustomConfig:
    """QOCREncoder should respect custom configuration."""

    def test_custom_atm_thresholds(self):
        """Custom atm_lo/atm_hi changes the ATM window."""
        config = QOCRConfig(atm_lo=0.98, atm_hi=1.02)
        encoder = QOCREncoder(config=config)
        # K=99 with spot=100: K/S=0.99, which is >= 0.98 and <= 1.02 → ATM
        chain = [_make_contract(strike=99, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        assert buckets["call:atm:short"].contract_count == 1

    def test_custom_atm_thresholds_itm(self):
        """With wider ATM window, K/S=0.985 stays ATM instead of ITM."""
        config_narrow = QOCRConfig(atm_lo=0.99, atm_hi=1.01)  # narrow
        config_wide = QOCRConfig(atm_lo=0.98, atm_hi=1.02)     # wide
        chain = [_make_contract(strike=98.5, right="C", dte=5)]  # K/S=0.985

        enc_narrow = QOCREncoder(config=config_narrow)
        enc_wide = QOCREncoder(config=config_wide)

        b_narrow = enc_narrow.encode_chain(chain, spot_price=100.0)
        b_wide = enc_wide.encode_chain(chain, spot_price=100.0)

        # Narrow: 0.985 < 0.99 → ITM
        assert b_narrow["call:itm:short"].contract_count == 1
        # Wide: 0.985 >= 0.98 → ATM
        assert b_wide["call:atm:short"].contract_count == 1

    def test_custom_qty_levels(self):
        config = QOCRConfig(qty_levels=[1, 3, 7])
        encoder = QOCREncoder(config=config)
        chain = [_make_contract(strike=595, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "1, 3, 7" in text

    def test_max_contracts_cap(self):
        """max_contracts_per_bucket should limit displayed contracts."""
        config = QOCRConfig(max_contracts_per_bucket=2)
        encoder = QOCREncoder(config=config)
        # Create 5 OTM calls
        chain = [
            _make_contract(strike=600 + i * 5, right="C", dte=5, iv=0.15)
            for i in range(5)
        ]
        buckets = encoder.encode_chain(chain, spot_price=595.0)
        text = encoder.format_for_prompt(buckets)
        assert "more)" in text  # should show truncation message


# ---------------------------------------------------------------------------
# Test 8: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: zero spot, missing fields, etc."""

    def test_zero_spot_price(self):
        """Should not crash with spot_price=0; contract goes to ATM."""
        encoder = QOCREncoder()
        chain = [_make_contract(strike=100, right="C", dte=5)]
        buckets = encoder.encode_chain(chain, spot_price=0.0)
        # _classify_moneyness returns 'atm' when spot=0
        total = sum(b.contract_count for b in buckets.values())
        assert total == 1
        assert buckets["call:atm:short"].contract_count == 1

    def test_missing_strike(self):
        """Contract without strike is skipped."""
        encoder = QOCREncoder()
        chain = [{"right": "C", "dte": 5, "iv": 0.20}]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        total = sum(b.contract_count for b in buckets.values())
        assert total == 0

    def test_missing_dte(self):
        """Contract without DTE is skipped."""
        encoder = QOCREncoder()
        chain = [{"right": "C", "strike": 100, "iv": 0.20}]
        buckets = encoder.encode_chain(chain, spot_price=100.0)
        total = sum(b.contract_count for b in buckets.values())
        assert total == 0

    def test_empty_chain(self):
        """Empty chain gives all-empty buckets."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain([], spot_price=100.0)
        assert all(b.contract_count == 0 for b in buckets.values())
        assert all(b.representative is None for b in buckets.values())

    def test_format_prompt_no_underlying(self):
        """format_for_prompt without underlying should not crash."""
        encoder = QOCREncoder()
        buckets = encoder.encode_chain([], spot_price=0.0)
        text = encoder.format_for_prompt(buckets)
        assert "Option Chain" in text or "QOCR" in text
