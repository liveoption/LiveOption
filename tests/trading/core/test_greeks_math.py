"""Unit tests for trading.core.greeks_math."""

from __future__ import annotations

import pytest


class TestParseOpraContract:
    def test_spy_call(self):
        from trading.core.greeks_math import parse_opra_contract

        result = parse_opra_contract("O:SPY250106C00595000")
        assert result == ("SPY", "2025-01-06", "C", 595.0)

    def test_qqq_put_fractional_strike(self):
        from trading.core.greeks_math import parse_opra_contract

        result = parse_opra_contract("O:QQQ250321P00450500")
        assert result == ("QQQ", "2025-03-21", "P", 450.5)

    def test_aapl_call(self):
        from trading.core.greeks_math import parse_opra_contract

        result = parse_opra_contract("O:AAPL260115C00200000")
        assert result == ("AAPL", "2026-01-15", "C", 200.0)

    def test_invalid_no_prefix(self):
        from trading.core.greeks_math import parse_opra_contract

        assert parse_opra_contract("SPY250106C00595000") is None

    def test_invalid_too_short(self):
        from trading.core.greeks_math import parse_opra_contract

        assert parse_opra_contract("O:SP") is None

    def test_stock_symbol(self):
        from trading.core.greeks_math import parse_opra_contract

        assert parse_opra_contract("SPY") is None


class TestComputeTimeToExpiry:
    def test_normal_dte(self):
        from trading.core.greeks_math import compute_time_to_expiry

        # 2025-01-06 10:30 ET → expiry 2025-01-10 15:00 ET
        T = compute_time_to_expiry("2025-01-10", "2025-01-06T10:30:00")
        # 4 days + 4.5 hours = 4.1875 days = 4.1875 / 365.25 ≈ 0.01146
        assert 0.0114 < T < 0.0116

    def test_0dte_morning(self):
        from trading.core.greeks_math import compute_time_to_expiry

        # 2025-01-06 10:30 ET → expiry 2025-01-06 15:00 ET (same day)
        T = compute_time_to_expiry("2025-01-06", "2025-01-06T10:30:00")
        # 4.5 hours / (365.25 * 24) = 0.000513
        assert 0.0005 < T < 0.00053

    def test_0dte_near_expiry(self):
        from trading.core.greeks_math import compute_time_to_expiry

        # 2025-01-06 14:59 ET → expiry 2025-01-06 15:00 ET
        T = compute_time_to_expiry("2025-01-06", "2025-01-06T14:59:00")
        # 1 minute / (365.25 * 24 * 60) ≈ 1.9e-6
        assert T < 1e-5
        assert T > 0

    def test_past_expiry(self):
        from trading.core.greeks_math import compute_time_to_expiry

        T = compute_time_to_expiry("2025-01-06", "2025-01-06T15:01:00")
        assert T == 0.0


class TestComputeHigherOrderGreeks:
    def test_atm_call_standard(self):
        from trading.core.greeks_math import compute_higher_order_greeks

        result = compute_higher_order_greeks(
            S=100, K=100, T=30 / 365, sigma=0.20
        )
        assert result["volga"] is not None
        assert result["vanna"] is not None
        # ATM: d1 ≈ 0.058, d2 ≈ 0.001 (small), so volga is small but nonzero
        # Verify approximate values via analytical BSM:
        # d1 = (ln(1) + (0.05 + 0.02)*30/365) / (0.20*sqrt(30/365))
        #    = (0 + 0.00575) / 0.05727 ≈ 0.1004
        # d2 = 0.1004 - 0.05727 ≈ 0.0431
        # N'(d1) ≈ 0.3970
        # vega = 100 * 0.3970 * sqrt(30/365) ≈ 11.38
        # volga = vega * d1 * d2 / sigma = 11.38 * 0.1004 * 0.0431 / 0.20 ≈ 0.246
        assert abs(result["volga"] - 0.246) < 0.05  # within 0.05 tolerance

    def test_deep_itm_volga_near_zero(self):
        from trading.core.greeks_math import compute_higher_order_greeks

        result = compute_higher_order_greeks(
            S=100, K=60, T=30 / 365, sigma=0.20
        )
        # Deep ITM: d1 is very large, N'(d1) → 0, vega → 0, volga → 0
        assert result["volga"] is not None
        assert abs(result["volga"]) < 0.01

    def test_boundary_T_zero(self):
        from trading.core.greeks_math import compute_higher_order_greeks

        result = compute_higher_order_greeks(
            S=100, K=100, T=0, sigma=0.20
        )
        assert result == {"volga": None, "vanna": None}

    def test_boundary_T_tiny(self):
        from trading.core.greeks_math import compute_higher_order_greeks

        result = compute_higher_order_greeks(
            S=100, K=100, T=1e-7, sigma=0.20
        )
        assert result == {"volga": None, "vanna": None}

    def test_boundary_sigma_zero(self):
        from trading.core.greeks_math import compute_higher_order_greeks

        result = compute_higher_order_greeks(
            S=100, K=100, T=30 / 365, sigma=0
        )
        assert result == {"volga": None, "vanna": None}

    def test_boundary_S_zero(self):
        from trading.core.greeks_math import compute_higher_order_greeks

        result = compute_higher_order_greeks(S=0, K=100, T=0.1, sigma=0.2)
        assert result == {"volga": None, "vanna": None}

    def test_boundary_K_zero(self):
        from trading.core.greeks_math import compute_higher_order_greeks

        result = compute_higher_order_greeks(S=100, K=0, T=0.1, sigma=0.2)
        assert result == {"volga": None, "vanna": None}


class TestComputeStepPnlAttribution:
    def test_all_greeks_present(self):
        from trading.core.greeks_math import compute_step_pnl_attribution

        result = compute_step_pnl_attribution(
            contract="O:SPY250106C00595000",
            qty=5,
            multiplier=100,
            delta=0.50,
            gamma=0.05,
            theta=-0.02,
            vega=0.10,
            volga=0.01,
            vanna=0.005,
            dS=1.0,
            d_sigma=0.01,
            dt=1 / (365.25 * 390),  # ~1 minute
            actual_pnl=260.0,
        )
        # delta_pnl = 5 * 100 * 0.50 * 1.0 = 250.0
        assert result.delta_pnl == pytest.approx(250.0)
        # gamma_pnl = 5 * 100 * 0.5 * 0.05 * 1.0^2 = 12.5
        assert result.gamma_pnl == pytest.approx(12.5)
        # theta_pnl = 5 * 100 * (-0.02) * dt
        assert result.theta_pnl is not None and result.theta_pnl < 0
        # vega_pnl = 5 * 100 * 0.10 * 0.01 = 0.5
        assert result.vega_pnl == pytest.approx(0.5)
        # volga_pnl = 5 * 100 * 0.5 * 0.01 * 0.01^2 = 0.00025
        assert result.volga_pnl == pytest.approx(0.00025, abs=1e-7)
        # vanna_pnl = 5 * 100 * 0.005 * 1.0 * 0.01 = 0.025
        assert result.vanna_pnl == pytest.approx(0.025)
        # residual = 260.0 - sum(components)
        components = [
            result.delta_pnl, result.gamma_pnl, result.theta_pnl,
            result.vega_pnl, result.volga_pnl, result.vanna_pnl,
        ]
        assert result.residual == pytest.approx(260.0 - sum(components))
        assert result.total_pnl == pytest.approx(260.0)

    def test_partial_greeks(self):
        from trading.core.greeks_math import compute_step_pnl_attribution

        result = compute_step_pnl_attribution(
            contract="O:SPY250106C00595000",
            qty=5,
            multiplier=100,
            delta=0.50,
            gamma=None,
            theta=-0.02,
            vega=None,
            volga=None,
            vanna=None,
            dS=1.0,
            d_sigma=0.01,
            dt=0.001,
            actual_pnl=260.0,
        )
        assert result.delta_pnl == pytest.approx(250.0)
        assert result.gamma_pnl is None
        assert result.theta_pnl is not None
        assert result.vega_pnl is None
        assert result.volga_pnl is None
        assert result.vanna_pnl is None
        # residual = actual - sum(non-None)
        non_none_sum = result.delta_pnl + result.theta_pnl
        assert result.residual == pytest.approx(260.0 - non_none_sum)

    def test_all_greeks_none(self):
        from trading.core.greeks_math import compute_step_pnl_attribution

        result = compute_step_pnl_attribution(
            contract="O:SPY250106C00595000",
            qty=5,
            multiplier=100,
            delta=None, gamma=None, theta=None,
            vega=None, volga=None, vanna=None,
            dS=1.0, d_sigma=0.01, dt=0.001,
            actual_pnl=260.0,
        )
        assert result.delta_pnl is None
        assert result.residual is None
        assert result.total_pnl == pytest.approx(260.0)

    def test_short_position(self):
        from trading.core.greeks_math import compute_step_pnl_attribution

        result = compute_step_pnl_attribution(
            contract="O:SPY250106C00595000",
            qty=-5,
            multiplier=100,
            delta=0.50, gamma=0.05, theta=-0.02,
            vega=0.10, volga=0.01, vanna=0.005,
            dS=1.0, d_sigma=0.01, dt=0.001,
            actual_pnl=-260.0,
        )
        # Signs flip for short
        assert result.delta_pnl == pytest.approx(-250.0)
        assert result.gamma_pnl == pytest.approx(-12.5)

    def test_zero_movement(self):
        from trading.core.greeks_math import compute_step_pnl_attribution

        result = compute_step_pnl_attribution(
            contract="O:SPY250106C00595000",
            qty=5,
            multiplier=100,
            delta=0.50, gamma=0.05, theta=-0.02,
            vega=0.10, volga=0.01, vanna=0.005,
            dS=0.0, d_sigma=0.0, dt=0.0,
            actual_pnl=0.5,  # small actual movement
        )
        assert result.delta_pnl == pytest.approx(0.0)
        assert result.gamma_pnl == pytest.approx(0.0)
        assert result.theta_pnl == pytest.approx(0.0)
        assert result.vega_pnl == pytest.approx(0.0)
        assert result.residual == pytest.approx(0.5)


class TestComputeFirstOrderGreeks:
    """Hand-computed reference values for S=K=100, T=0.25, sigma=0.20, r=0.05, q=0.

    Using standard BSM with N(d1), N(d2):
      d1 = (ln(1) + (0.05 + 0.02)*0.25) / (0.20*0.5) = 0.175
      d2 = 0.175 - 0.10 = 0.075
      Call:  delta = N(0.175) ≈ 0.5695
             gamma = N'(d1)/(S*sigma*sqrt(T)) = 0.3933 / (100*0.20*0.5) ≈ 0.03933
             vega  = S*N'(d1)*sqrt(T)/100 = 100*0.3933*0.5/100 ≈ 0.1967  (per 1%-sigma)
             theta = (-S*N'(d1)*sigma/(2*sqrt(T)) - r*K*exp(-rT)*N(d2)) / 365.25
                   ≈ (-100*0.3933*0.20/1.0 - 0.05*100*exp(-0.0125)*N(0.075)) / 365.25
                   ≈ (-7.866 - 0.05*98.76*0.5299) / 365.25
                   ≈ (-7.866 - 2.616) / 365.25 ≈ -0.02869
    """

    def test_atm_call_quarter_year(self):
        from trading.core.greeks_math import compute_first_order_greeks

        g = compute_first_order_greeks(S=100.0, K=100.0, T=0.25, sigma=0.20, r=0.05, right="C")
        assert g["delta"] == pytest.approx(0.5695, abs=1e-3)
        assert g["gamma"] == pytest.approx(0.03933, abs=1e-4)
        assert g["vega"] == pytest.approx(0.1967, abs=1e-3)
        assert g["theta"] == pytest.approx(-0.02869, abs=1e-3)

    def test_atm_put_put_call_parity_delta(self):
        from trading.core.greeks_math import compute_first_order_greeks

        c = compute_first_order_greeks(S=100.0, K=100.0, T=0.25, sigma=0.20, r=0.05, right="C")
        p = compute_first_order_greeks(S=100.0, K=100.0, T=0.25, sigma=0.20, r=0.05, right="P")
        # q=0 so put delta = call delta - 1
        assert p["delta"] == pytest.approx(c["delta"] - 1.0, abs=1e-6)
        # gamma, vega same for call and put
        assert p["gamma"] == pytest.approx(c["gamma"], abs=1e-6)
        assert p["vega"] == pytest.approx(c["vega"], abs=1e-6)

    def test_returns_none_below_t_min(self):
        from trading.core.greeks_math import compute_first_order_greeks

        g = compute_first_order_greeks(S=100.0, K=100.0, T=0.0, sigma=0.20, r=0.05, right="C")
        assert g == {"delta": None, "gamma": None, "theta": None, "vega": None}

    def test_returns_none_on_bad_inputs(self):
        from trading.core.greeks_math import compute_first_order_greeks

        g = compute_first_order_greeks(S=0.0, K=100.0, T=0.25, sigma=0.20, r=0.05, right="C")
        assert g == {"delta": None, "gamma": None, "theta": None, "vega": None}


class TestComputeAllGreeks:
    def test_merges_first_and_higher_order(self):
        from trading.core.greeks_math import compute_all_greeks

        g = compute_all_greeks(S=100.0, K=100.0, T=0.25, sigma=0.20, r=0.05, right="C")
        assert set(g.keys()) == {"delta", "gamma", "theta", "vega", "volga", "vanna"}
        # Spot-check delta against first-order reference
        assert g["delta"] == pytest.approx(0.5695, abs=1e-3)
        # volga and vanna are non-None for ATM quarter-year
        assert g["volga"] is not None
        assert g["vanna"] is not None

    def test_all_none_at_expiry(self):
        from trading.core.greeks_math import compute_all_greeks

        g = compute_all_greeks(S=100.0, K=100.0, T=0.0, sigma=0.20, r=0.05, right="C")
        assert all(v is None for v in g.values())


class TestBrentImpliedVol:
    def test_round_trip_atm_call(self):
        """Price a known call via BSM, then invert — should recover sigma."""
        from trading.core.greeks_math import brent_implied_vol, _bsm_price

        S, K, T, sigma, r = 100.0, 100.0, 0.25, 0.20, 0.05
        price = _bsm_price(S, K, T, sigma, r, 0.0, "C")
        iv, status = brent_implied_vol(price, S, K, T, r, q=0.0, right="C")
        assert status == "ok"
        assert iv == pytest.approx(0.20, abs=1e-6)

    def test_round_trip_otm_put(self):
        from trading.core.greeks_math import brent_implied_vol, _bsm_price

        S, K, T, sigma, r = 100.0, 90.0, 0.1, 0.30, 0.05
        price = _bsm_price(S, K, T, sigma, r, 0.0, "P")
        iv, status = brent_implied_vol(price, S, K, T, r, q=0.0, right="P")
        assert status == "ok"
        assert iv == pytest.approx(0.30, abs=1e-6)

    def test_below_intrinsic(self):
        """Price below intrinsic value → no real solution."""
        from trading.core.greeks_math import brent_implied_vol

        # ITM call intrinsic = S - K*exp(-rT); pricing below it is impossible
        iv, status = brent_implied_vol(
            price=0.01, S=110.0, K=100.0, T=0.25, r=0.05, q=0.0, right="C",
        )
        assert iv is None
        assert status == "below_intrinsic"

    def test_no_bracket(self):
        """Very high price requires sigma > 10.0 (our max) → unsolvable."""
        from trading.core.greeks_math import brent_implied_vol

        iv, status = brent_implied_vol(
            price=99.9, S=100.0, K=100.0, T=0.25, r=0.05, q=0.0, right="C",
        )
        assert iv is None
        assert status == "no_bracket"
