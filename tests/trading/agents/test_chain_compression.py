"""Tests for ChainCompressor — option chain compression into structured summaries."""

from __future__ import annotations

import pytest

from trading.agents.chain_compression import (
    AVSRSurface,
    ChainCompressor,
    ChainSummary,
    MoneynessBucket,
    _classify_by_delta,
    _classify_by_strike,
    _detect_regime,
    _get_right,
)


# ---------------------------------------------------------------------------
# Helpers — contract factory
# ---------------------------------------------------------------------------

def _make_contract(
    ticker: str = "O:SPY250106C00595000",
    strike: float = 595.0,
    right: str = "C",
    delta: float | None = 0.50,
    iv: float | None = 0.20,
    vega: float | None = 0.10,
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
        "vega": vega,
        "volume": volume,
        "dte": dte,
        "close": close,
    }


def _make_chain(spot: float = 595.0) -> list[dict]:
    """Build a realistic small chain with calls and puts at various moneynesses."""
    return [
        # Calls
        _make_contract(ticker="O:SPY250106C00585000", strike=585, right="C",
                       delta=0.85, iv=0.22, volume=500, dte=1),  # deep_itm
        _make_contract(ticker="O:SPY250106C00590000", strike=590, right="C",
                       delta=0.70, iv=0.20, volume=800, dte=1),  # itm
        _make_contract(ticker="O:SPY250106C00594000", strike=594, right="C",
                       delta=0.52, iv=0.18, volume=5000, dte=1),  # atm
        _make_contract(ticker="O:SPY250106C00596000", strike=596, right="C",
                       delta=0.48, iv=0.18, volume=8000, dte=1),  # atm
        _make_contract(ticker="O:SPY250106C00600000", strike=600, right="C",
                       delta=0.30, iv=0.165, volume=3000, dte=1),  # otm
        _make_contract(ticker="O:SPY250106C00610000", strike=610, right="C",
                       delta=0.10, iv=0.15, volume=200, dte=1),  # deep_otm
        # Puts
        _make_contract(ticker="O:SPY250106P00605000", strike=605, right="P",
                       delta=-0.85, iv=0.23, volume=400, dte=1),  # deep_itm
        _make_contract(ticker="O:SPY250106P00600000", strike=600, right="P",
                       delta=-0.70, iv=0.21, volume=700, dte=1),  # itm
        _make_contract(ticker="O:SPY250106P00596000", strike=596, right="P",
                       delta=-0.48, iv=0.193, volume=6000, dte=1),  # atm
        _make_contract(ticker="O:SPY250106P00594000", strike=594, right="P",
                       delta=-0.42, iv=0.191, volume=6500, dte=1),  # atm
        _make_contract(ticker="O:SPY250106P00590000", strike=590, right="P",
                       delta=-0.30, iv=0.21, volume=2500, dte=1),  # otm
        _make_contract(ticker="O:SPY250106P00580000", strike=580, right="P",
                       delta=-0.10, iv=0.25, volume=150, dte=1),  # deep_otm
    ]


# ---------------------------------------------------------------------------
# Test 1: Moneyness bucketing
# ---------------------------------------------------------------------------

class TestMoneynessBucketing:
    """test_moneyness_bucketing — contracts sorted into correct buckets."""

    def test_call_buckets(self):
        compressor = ChainCompressor()
        chain = _make_chain(spot=595.0)
        summary = compressor.compress(chain, spot_price=595.0, underlying="SPY")

        # Verify call buckets exist for all labels
        bucket_map = {b.label: b for b in summary.call_buckets}
        assert "deep_itm" in bucket_map
        assert "itm" in bucket_map
        assert "atm" in bucket_map
        assert "otm" in bucket_map
        assert "deep_otm" in bucket_map

        # ATM bucket should have 2 contracts (delta 0.52 and 0.48)
        assert bucket_map["atm"].contracts == 2
        # Deep ITM should have 1 contract (delta 0.85)
        assert bucket_map["deep_itm"].contracts == 1
        # OTM should have 1 contract (delta 0.30)
        assert bucket_map["otm"].contracts == 1

    def test_put_buckets(self):
        compressor = ChainCompressor()
        chain = _make_chain(spot=595.0)
        summary = compressor.compress(chain, spot_price=595.0, underlying="SPY")

        bucket_map = {b.label: b for b in summary.put_buckets}
        # ATM puts: delta -0.48 and -0.42 are both in (0.40, 0.60] range
        assert bucket_map["atm"].contracts == 2
        assert bucket_map["deep_itm"].contracts == 1
        assert bucket_map["deep_otm"].contracts == 1

    def test_delta_classification_boundaries(self):
        """Verify boundary conditions for delta-based bucketing."""
        assert _classify_by_delta(0.81, "C") == "deep_itm"
        assert _classify_by_delta(0.80, "C") == "itm"  # exactly 0.80 is NOT > 0.80
        assert _classify_by_delta(0.61, "C") == "itm"
        assert _classify_by_delta(0.60, "C") == "atm"  # exactly 0.60 is NOT > 0.60
        assert _classify_by_delta(0.41, "C") == "atm"
        assert _classify_by_delta(0.40, "C") == "otm"  # exactly 0.40 is NOT > 0.40
        assert _classify_by_delta(0.21, "C") == "otm"
        assert _classify_by_delta(0.20, "C") == "deep_otm"
        assert _classify_by_delta(0.05, "C") == "deep_otm"

    def test_put_delta_classification(self):
        """Put deltas are negative; classification uses absolute value."""
        assert _classify_by_delta(-0.85, "P") == "deep_itm"
        assert _classify_by_delta(-0.50, "P") == "atm"
        assert _classify_by_delta(-0.10, "P") == "deep_otm"


# ---------------------------------------------------------------------------
# Test 2: IV skew calculation
# ---------------------------------------------------------------------------

class TestIvSkewCalculation:
    """test_iv_skew_calculation — put_iv > call_iv -> positive skew."""

    def test_positive_skew(self):
        compressor = ChainCompressor()
        chain = _make_chain(spot=595.0)
        summary = compressor.compress(chain, spot_price=595.0, underlying="SPY")

        # ATM window (within 2% of 595) includes strikes 585-605
        # ATM call IV avg: (0.22+0.20+0.18+0.18+0.165)/5 = 0.189
        # ATM put IV avg:  (0.23+0.21+0.193+0.191+0.21)/5 = 0.2068
        # Skew = 0.2068 - 0.189 = +0.0178
        assert summary.iv_skew > 0, "Expected positive skew (put premium)"
        assert abs(summary.iv_skew - 0.0178) < 0.002

    def test_no_skew_with_equal_iv(self):
        """If call IV == put IV at ATM, skew should be ~0."""
        chain = [
            _make_contract(right="C", strike=595, delta=0.50, iv=0.20),
            _make_contract(right="P", strike=595, delta=-0.50, iv=0.20),
        ]
        compressor = ChainCompressor()
        summary = compressor.compress(chain, spot_price=595.0)
        assert abs(summary.iv_skew) < 0.001


# ---------------------------------------------------------------------------
# Test 3: Regime detection — trending_up
# ---------------------------------------------------------------------------

class TestRegimeTrendingUp:
    """test_regime_trending_up — +2% return -> 'trending_up'."""

    def test_trending_up(self):
        # 20 bars going from 100 to 102 = +2%
        prices = [100.0 + i * 0.1 for i in range(20)]
        prices[-1] = 102.0  # ensure +2% return
        compressor = ChainCompressor()
        summary = compressor.compress([], spot_price=100.0, recent_prices=prices)
        assert summary.regime == "trending_up"


# ---------------------------------------------------------------------------
# Test 4: Regime detection — range_bound
# ---------------------------------------------------------------------------

class TestRegimeRangeBound:
    """test_regime_range_bound — flat -> 'range_bound'."""

    def test_range_bound(self):
        # Flat prices: small oscillation around 100, return < 1%, low vol
        prices = [100.0, 100.05, 99.95, 100.0, 100.02, 99.98,
                  100.01, 99.99, 100.0, 100.03, 99.97, 100.0,
                  100.01, 99.99, 100.0, 100.02, 99.98, 100.0,
                  100.01, 100.0]
        compressor = ChainCompressor()
        summary = compressor.compress([], spot_price=100.0, recent_prices=prices)
        assert summary.regime == "range_bound"


# ---------------------------------------------------------------------------
# Test 5: format_for_prompt — produces compact text with key info
# ---------------------------------------------------------------------------

class TestFormatForPrompt:
    """test_format_for_prompt — produces compact text with key info."""

    def test_format_contains_key_sections(self):
        compressor = ChainCompressor()
        chain = _make_chain(spot=595.0)
        summary = compressor.compress(chain, spot_price=595.0,
                                       underlying="SPY",
                                       recent_prices=[595.0] * 20)
        text = compressor.format_for_prompt(summary)

        # Header
        assert "Option Chain Summary" in text
        assert "SPY" in text
        assert "595.20" in text or "595.00" in text

        # Regime
        assert "Regime:" in text

        # IV info
        assert "ATM IV:" in text
        assert "Skew:" in text

        # Buckets
        assert "Calls by moneyness:" in text
        assert "Puts by moneyness:" in text
        assert "ATM:" in text

        # Top tradable
        assert "Top tradable:" in text

    def test_format_skew_label(self):
        """Positive skew should show '(put premium)'."""
        compressor = ChainCompressor()
        chain = _make_chain(spot=595.0)
        summary = compressor.compress(chain, spot_price=595.0, underlying="SPY")
        text = compressor.format_for_prompt(summary)
        assert "(put premium)" in text


# ---------------------------------------------------------------------------
# Test 6: Empty chain
# ---------------------------------------------------------------------------

class TestEmptyChain:
    """test_empty_chain — empty input -> sensible defaults."""

    def test_empty_chain_returns_defaults(self):
        compressor = ChainCompressor()
        summary = compressor.compress([], spot_price=595.0, underlying="SPY")

        assert summary.underlying == "SPY"
        assert summary.spot_price == 595.0
        assert summary.atm_iv == 0.0
        assert summary.iv_skew == 0.0
        assert summary.call_buckets == []
        assert summary.put_buckets == []
        assert summary.top_calls == []
        assert summary.top_puts == []
        assert summary.regime == "range_bound"

    def test_empty_chain_format(self):
        """format_for_prompt on empty chain should not crash."""
        compressor = ChainCompressor()
        summary = compressor.compress([], spot_price=0.0)
        text = compressor.format_for_prompt(summary)
        assert "Option Chain Summary" in text


# ---------------------------------------------------------------------------
# Test 7: No Greeks fallback — use strike-based bucketing
# ---------------------------------------------------------------------------

class TestNoGreeksFallback:
    """test_no_greeks_fallback — no delta -> use strike-based bucketing."""

    def test_strike_based_bucketing(self):
        """Contracts without delta should be bucketed by strike vs spot."""
        chain = [
            _make_contract(ticker="O:SPY250106C00595000", strike=595, right="C",
                           delta=None, iv=0.18, volume=5000),  # within 2% -> atm
            _make_contract(ticker="O:SPY250106C00600000", strike=600, right="C",
                           delta=None, iv=0.17, volume=3000),  # ~0.84% away -> atm
            _make_contract(ticker="O:SPY250106C00615000", strike=615, right="C",
                           delta=None, iv=0.15, volume=1000),  # 3.4% away -> otm
            _make_contract(ticker="O:SPY250106C00640000", strike=640, right="C",
                           delta=None, iv=0.14, volume=500),  # 7.6% away -> deep_otm
        ]
        compressor = ChainCompressor()
        summary = compressor.compress(chain, spot_price=595.0, underlying="SPY")

        bucket_map = {b.label: b for b in summary.call_buckets}
        # 595 and 600 are within 2% -> ATM
        assert bucket_map["atm"].contracts == 2
        # 615 is 3.4% OTM for calls -> OTM
        assert bucket_map["otm"].contracts == 1
        # 640 is 7.6% OTM for calls -> deep_otm
        assert bucket_map["deep_otm"].contracts == 1

    def test_strike_based_puts(self):
        """Put without delta: strike < spot is OTM for puts."""
        chain = [
            _make_contract(ticker="O:SPY250106P00594000", strike=594, right="P",
                           delta=None, iv=0.19, volume=4000),  # within 2% -> atm
            _make_contract(ticker="O:SPY250106P00575000", strike=575, right="P",
                           delta=None, iv=0.22, volume=1500),  # 3.4% OTM -> otm
            _make_contract(ticker="O:SPY250106P00550000", strike=550, right="P",
                           delta=None, iv=0.28, volume=200),  # 7.6% OTM -> deep_otm
        ]
        compressor = ChainCompressor()
        summary = compressor.compress(chain, spot_price=595.0, underlying="SPY")

        bucket_map = {b.label: b for b in summary.put_buckets}
        assert bucket_map["atm"].contracts == 1
        assert bucket_map["otm"].contracts == 1
        assert bucket_map["deep_otm"].contracts == 1

    def test_classify_by_strike_call_itm(self):
        """Call with strike below spot is ITM."""
        # 590 is 0.84% below 595 -> within 2% -> atm
        assert _classify_by_strike(590, 595.0, "C") == "atm"
        # 580 is 2.5% below 595 -> ITM for calls
        assert _classify_by_strike(580, 595.0, "C") == "itm"
        # 560 is 5.9% below 595 -> deep ITM for calls
        assert _classify_by_strike(560, 595.0, "C") == "deep_itm"


# ---------------------------------------------------------------------------
# Test 8: _get_right OPRA parsing — C/P must be after date digits
# ---------------------------------------------------------------------------

class TestGetRightOPRA:
    """_get_right should parse the C/P from OPRA format correctly, not hit
    the P in 'SPY' or other ticker letters."""

    def test_call_from_opra_ticker(self):
        """O:SPY250106C00595000 → C, not P from 'SPY'."""
        assert _get_right({"ticker": "O:SPY250106C00595000"}) == "C"

    def test_put_from_opra_ticker(self):
        """O:SPY250106P00595000 → P."""
        assert _get_right({"ticker": "O:SPY250106P00595000"}) == "P"

    def test_explicit_right_takes_priority(self):
        """If 'right' field is set, OPRA parsing is skipped."""
        assert _get_right({"ticker": "O:SPY250106P00595000", "right": "C"}) == "C"

    def test_explicit_type_field(self):
        """'type' field also works."""
        assert _get_right({"ticker": "O:SPY250106C00595000", "type": "PUT"}) == "P"

    def test_ticker_with_c_in_symbol(self):
        """Tickers like CSCO should not falsely return C from the ticker symbol."""
        assert _get_right({"ticker": "O:CSCO250106P00050000"}) == "P"

    def test_ticker_with_p_in_symbol(self):
        """Tickers like PFE should not falsely return P from the ticker symbol."""
        assert _get_right({"ticker": "O:PFE250106C00025000"}) == "C"

    def test_no_ticker_defaults_to_call(self):
        """No ticker, no right/type → default C."""
        assert _get_right({}) == "C"


# ---------------------------------------------------------------------------
# Test 9: AVSR computation
# ---------------------------------------------------------------------------

def _make_avsr_chain(spot: float = 100.0) -> list[dict]:
    """Build a chain spanning multiple moneyness/DTE for AVSR testing.

    Creates contracts at moneyness 0.95, 1.0, 1.05 and DTE 7, 30, 90
    with a realistic downward-sloping IV smile and term structure.
    """
    # IV values: higher for OTM puts (low moneyness), lower for OTM calls,
    # and declining with longer DTE (backwardation style for testing).
    iv_map = {
        # (moneyness, dte): iv
        (0.95, 7): 0.25,
        (0.95, 30): 0.23,
        (0.95, 90): 0.22,
        (1.00, 7): 0.20,
        (1.00, 30): 0.19,
        (1.00, 90): 0.185,
        (1.05, 7): 0.18,
        (1.05, 30): 0.175,
        (1.05, 90): 0.17,
    }
    chain = []
    for (m, dte), iv in iv_map.items():
        strike = m * spot
        right = "P" if m < 1.0 else "C"
        chain.append(_make_contract(
            strike=strike, right=right, iv=iv, dte=dte,
            delta=0.50 if right == "C" else -0.50, volume=1000,
        ))
    return chain


class TestAVSRComputation:
    """Test AVSR (Anchor-Based Volatility Surface Representation)."""

    def test_per_run_anchor_override_changes_surface_without_global_mutation(self):
        from trading.core.config import AvsrConfig

        chain = _make_avsr_chain(spot=100.0)
        chain.append(_make_contract(strike=90.0, right="P", delta=-0.2,
                                    iv=0.40, dte=5))
        baseline = ChainCompressor().compute_avsr(chain, spot_price=100.0)
        custom = ChainCompressor(AvsrConfig(
            moneyness_anchors=[0.90, 1.0, 1.10],
            expiry_anchors=[5, 21, 63], distance_lambda=0.02,
        )).compute_avsr(chain, spot_price=100.0)
        assert custom.moneyness_anchors == [0.90, 1.0, 1.10]
        assert custom.expiry_anchors == [5, 21, 63]
        assert custom.grid != baseline.grid
        assert ChainCompressor().MONEYNESS_ANCHORS == [0.95, 1.0, 1.05]

    def test_avsr_grid_shape(self):
        """AVSR grid should be 3x3."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        summary = compressor.compress(chain, spot_price=100.0, underlying="TEST")

        assert summary.avsr is not None
        assert len(summary.avsr.grid) == 3
        for row in summary.avsr.grid:
            assert len(row) == 3

    def test_avsr_grid_values_exact_match(self):
        """When contracts land exactly on anchors, IV should match exactly."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        avsr = compressor.compute_avsr(chain, spot_price=100.0)

        assert avsr is not None
        # Row 0 = moneyness 0.95 (strikes at 95)
        assert avsr.grid[0][0] == pytest.approx(0.25)   # 7D
        assert avsr.grid[0][1] == pytest.approx(0.23)   # 30D
        assert avsr.grid[0][2] == pytest.approx(0.22)   # 90D
        # Row 1 = moneyness 1.00 (strikes at 100)
        assert avsr.grid[1][0] == pytest.approx(0.20)   # 7D
        assert avsr.grid[1][1] == pytest.approx(0.19)   # 30D
        assert avsr.grid[1][2] == pytest.approx(0.185)  # 90D
        # Row 2 = moneyness 1.05 (strikes at 105)
        assert avsr.grid[2][0] == pytest.approx(0.18)   # 7D
        assert avsr.grid[2][1] == pytest.approx(0.175)  # 30D
        assert avsr.grid[2][2] == pytest.approx(0.17)   # 90D

    def test_avsr_risk_reversal(self):
        """Risk reversal = sigma(1.05, tau) - sigma(0.95, tau)."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        avsr = compressor.compute_avsr(chain, spot_price=100.0)

        assert avsr is not None
        # 7D: 0.18 - 0.25 = -0.07
        assert avsr.risk_reversals[0] == pytest.approx(-0.07)
        # 30D: 0.175 - 0.23 = -0.055
        assert avsr.risk_reversals[1] == pytest.approx(-0.055)
        # 90D: 0.17 - 0.22 = -0.05
        assert avsr.risk_reversals[2] == pytest.approx(-0.05)

    def test_avsr_butterfly(self):
        """Butterfly = (sigma(0.95,tau) + sigma(1.05,tau))/2 - sigma(1.0,tau)."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        avsr = compressor.compute_avsr(chain, spot_price=100.0)

        assert avsr is not None
        # 7D: (0.25 + 0.18)/2 - 0.20 = 0.215 - 0.20 = 0.015
        assert avsr.butterflies[0] == pytest.approx(0.015)
        # 30D: (0.23 + 0.175)/2 - 0.19 = 0.2025 - 0.19 = 0.0125
        assert avsr.butterflies[1] == pytest.approx(0.0125)
        # 90D: (0.22 + 0.17)/2 - 0.185 = 0.195 - 0.185 = 0.01
        assert avsr.butterflies[2] == pytest.approx(0.01)

    def test_avsr_term_spread(self):
        """Term spread = sigma(1.0, 90D) - sigma(1.0, 7D)."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        avsr = compressor.compute_avsr(chain, spot_price=100.0)

        assert avsr is not None
        # 0.185 - 0.20 = -0.015
        assert avsr.term_spread == pytest.approx(-0.015)

    def test_avsr_nearest_neighbor_selection(self):
        """When no exact anchor match, should pick nearest by distance metric."""
        compressor = ChainCompressor()
        # Only provide contracts that don't land exactly on anchors
        chain = [
            # Near moneyness=0.95: strike=96 -> K/S=0.96 (dist from 0.95 = 0.01)
            _make_contract(strike=96, right="P", iv=0.24, dte=8, delta=-0.40, volume=500),
            # Near moneyness=1.0: strike=101 -> K/S=1.01 (dist from 1.0 = 0.01)
            _make_contract(strike=101, right="C", iv=0.19, dte=28, delta=0.48, volume=500),
            # Near moneyness=1.05: strike=104 -> K/S=1.04 (dist from 1.05 = 0.01)
            _make_contract(strike=104, right="C", iv=0.17, dte=92, delta=0.30, volume=500),
        ]
        avsr = compressor.compute_avsr(chain, spot_price=100.0)

        assert avsr is not None
        # All 9 cells will pick the nearest from these 3 contracts.
        # For anchor (0.95, 7): dist to c0 = |0.96-0.95| + 0.01*|8-7| = 0.01+0.01 = 0.02
        #                       dist to c1 = |1.01-0.95| + 0.01*|28-7| = 0.06+0.21 = 0.27
        #                       dist to c2 = |1.04-0.95| + 0.01*|92-7| = 0.09+0.85 = 0.94
        # -> picks c0 with IV=0.24
        assert avsr.grid[0][0] == pytest.approx(0.24)

    def test_avsr_empty_chain_returns_none(self):
        """AVSR with empty chain returns None."""
        compressor = ChainCompressor()
        avsr = compressor.compute_avsr([], spot_price=100.0)
        assert avsr is None

    def test_avsr_no_valid_iv_returns_none(self):
        """AVSR returns None when no contracts have valid IV."""
        compressor = ChainCompressor()
        chain = [
            _make_contract(strike=100, right="C", iv=None, dte=7),
            _make_contract(strike=100, right="C", iv=0, dte=30),
            _make_contract(strike=100, right="C", iv=-0.1, dte=90),
        ]
        avsr = compressor.compute_avsr(chain, spot_price=100.0)
        assert avsr is None

    def test_avsr_zero_spot_returns_none(self):
        """AVSR returns None when spot price is zero."""
        compressor = ChainCompressor()
        chain = [_make_contract(strike=100, right="C", iv=0.20, dte=7)]
        avsr = compressor.compute_avsr(chain, spot_price=0.0)
        assert avsr is None

    def test_avsr_integrated_in_compress(self):
        """compress() populates the avsr field on ChainSummary."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        summary = compressor.compress(chain, spot_price=100.0, underlying="TEST")

        assert summary.avsr is not None
        assert isinstance(summary.avsr, AVSRSurface)
        assert len(summary.avsr.grid) == 3
        assert len(summary.avsr.risk_reversals) == 3
        assert len(summary.avsr.butterflies) == 3

    def test_avsr_empty_chain_compress_no_avsr(self):
        """compress() with empty chain should have avsr=None."""
        compressor = ChainCompressor()
        summary = compressor.compress([], spot_price=100.0, underlying="TEST")
        assert summary.avsr is None


# ---------------------------------------------------------------------------
# Test 10: AVSR formatting in prompt
# ---------------------------------------------------------------------------

class TestAVSRFormatForPrompt:
    """Test that AVSR section appears correctly in formatted prompt."""

    def test_avsr_section_present(self):
        """format_for_prompt should include AVSR block."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        summary = compressor.compress(chain, spot_price=100.0, underlying="TEST")
        text = compressor.format_for_prompt(summary)

        assert "IV Surface (AVSR 3x3)" in text
        assert "Risk Reversal:" in text
        assert "Butterfly:" in text
        assert "Term Spread:" in text

    def test_avsr_format_values(self):
        """Formatted AVSR should show percentages for the grid."""
        compressor = ChainCompressor()
        chain = _make_avsr_chain(spot=100.0)
        summary = compressor.compress(chain, spot_price=100.0, underlying="TEST")
        text = compressor.format_for_prompt(summary)

        # ATM 7D IV is 0.20 = 20.0%
        assert "20.0%" in text
        # OTM put 7D IV is 0.25 = 25.0%
        assert "25.0%" in text
        # Moneyness labels
        assert "0.95" in text
        assert "1.00" in text
        assert "1.05" in text
        # Expiry labels
        assert "7D" in text
        assert "30D" in text
        assert "90D" in text

    def test_avsr_absent_when_none(self):
        """No AVSR section when chain is empty."""
        compressor = ChainCompressor()
        summary = compressor.compress([], spot_price=100.0, underlying="TEST")
        text = compressor.format_for_prompt(summary)

        assert "AVSR" not in text
        assert "Risk Reversal" not in text
