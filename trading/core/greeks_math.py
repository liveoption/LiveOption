# trading/core/greeks_math.py
"""BSM higher-order Greeks and PnL attribution utilities.

Pure functions — no side effects, no dependencies beyond stdlib math.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# OPRA format: O:{TICKER}{YYMMDD}{C|P}{STRIKE*1000 padded to 8 digits}
# Ticker is 1-6 uppercase letters.
_OPRA_RE = re.compile(
    r"^O:([A-Z]{1,6})(\d{6})([CP])(\d{8})$"
)


def parse_opra_contract(
    contract: str,
) -> tuple[str, str, str, float] | None:
    """Parse OPRA contract string.

    Returns (underlying, expiry_date, right, strike) or None if invalid.
    expiry_date is 'YYYY-MM-DD', right is 'C' or 'P', strike is float.
    """
    m = _OPRA_RE.match(contract)
    if m is None:
        return None
    underlying = m.group(1)
    date_raw = m.group(2)  # YYMMDD
    right = m.group(3)
    strike_raw = m.group(4)
    # Parse date
    yy, mm, dd = int(date_raw[:2]), int(date_raw[2:4]), int(date_raw[4:6])
    year = 2000 + yy
    expiry_date = f"{year:04d}-{mm:02d}-{dd:02d}"
    # Parse strike (last 8 digits = strike * 1000)
    strike = int(strike_raw) / 1000.0
    return underlying, expiry_date, right, strike


# Expiry anchor: 15:00 ET — matches UPQ's expiry_anchor: 3pm_et
_EXPIRY_HOUR = 15
_SECONDS_PER_YEAR = 365.25 * 86400


_UTC = ZoneInfo("UTC")


def compute_time_to_expiry(expiry_date: str, current_ts) -> float:
    """Compute T in years from ``current_ts`` to ``expiry_date`` at 15:00 ET.

    Args:
        expiry_date: 'YYYY-MM-DD' — interpreted in ET, anchored at 15:00 ET.
        current_ts: tz-aware ``datetime`` (preferred) or ISO timestamp string.
            Naive datetimes are rejected (tz-aware contract).  Strings without
            an offset are assumed ET for backward compatibility with legacy
            callers; callers passing UTC strings should include an offset.

    Returns:
        T in years, clamped to >= 0.
    """
    expiry_dt = datetime.strptime(expiry_date, "%Y-%m-%d").replace(
        hour=_EXPIRY_HOUR, tzinfo=_ET
    )

    if isinstance(current_ts, datetime):
        if current_ts.tzinfo is None:
            raise ValueError(
                "compute_time_to_expiry requires tz-aware datetime for current_ts"
            )
        current_dt = current_ts
    else:
        # Legacy ISO string path.
        s = str(current_ts)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            parsed = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        if parsed.tzinfo is None:
            # Legacy PMB convention: assumed ET.
            parsed = parsed.replace(tzinfo=_ET)
        current_dt = parsed

    # Normalize both ends via UTC for a stable timedelta.
    diff_seconds = (
        expiry_dt.astimezone(_UTC) - current_dt.astimezone(_UTC)
    ).total_seconds()
    return max(diff_seconds / _SECONDS_PER_YEAR, 0.0)


_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)
_T_MIN = 1e-6  # below this, T is effectively zero


def _norm_pdf(x: float) -> float:
    """Standard normal PDF: N'(x)."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF: N(x). Uses math.erf; same accuracy as scipy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def compute_higher_order_greeks(
    S: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.05,
    q: float = 0.0,
) -> dict[str, float | None]:
    """Compute volga and vanna from BSM parameters.

    Returns {"volga": float|None, "vanna": float|None}.
    Returns None values when inputs are invalid or near-expiry.
    """
    if S <= 0 or K <= 0 or sigma <= 0 or T < _T_MIN:
        return {"volga": None, "vanna": None}

    sqrt_T = math.sqrt(T)
    sigma_sqrt_T = sigma * sqrt_T

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
    d2 = d1 - sigma_sqrt_T

    npd1 = _norm_pdf(d1)
    eq_T = math.exp(-q * T)

    # vega = S * e^(-qT) * N'(d1) * sqrt(T)
    vega = S * eq_T * npd1 * sqrt_T

    # volga = vega * d1 * d2 / sigma
    volga = vega * d1 * d2 / sigma

    # vanna = -e^(-qT) * N'(d1) * d2 / sigma
    vanna = -eq_T * npd1 * d2 / sigma

    return {"volga": volga, "vanna": vanna}


def compute_first_order_greeks(
    S: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.05,
    q: float = 0.0,
    right: str = "C",
) -> dict[str, float | None]:
    """Compute delta, gamma, theta, vega via BSM.

    Units match UPQ's convention (docs/upq-greeks-data-quality.md):
      - theta: per calendar day (divided by 365.25)
      - vega:  per 1 percentage point change in sigma (divided by 100)
      - gamma: per 1 unit change in S
      - delta: per 1 unit change in S (signed by option type)

    Returns all None when inputs invalid or T < 1e-6.
    """
    if S <= 0 or K <= 0 or sigma <= 0 or T < _T_MIN:
        return {"delta": None, "gamma": None, "theta": None, "vega": None}

    is_call = right.upper() == "C"
    sqrt_T = math.sqrt(T)
    sigma_sqrt_T = sigma * sqrt_T

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
    d2 = d1 - sigma_sqrt_T

    npd1 = _norm_pdf(d1)
    eq_T = math.exp(-q * T)
    er_T = math.exp(-r * T)

    if is_call:
        delta = eq_T * _norm_cdf(d1)
        theta_raw = (
            -S * eq_T * npd1 * sigma / (2.0 * sqrt_T)
            - r * K * er_T * _norm_cdf(d2)
            + q * S * eq_T * _norm_cdf(d1)
        )
    else:
        delta = eq_T * (_norm_cdf(d1) - 1.0)
        theta_raw = (
            -S * eq_T * npd1 * sigma / (2.0 * sqrt_T)
            + r * K * er_T * _norm_cdf(-d2)
            - q * S * eq_T * _norm_cdf(-d1)
        )

    gamma = eq_T * npd1 / (S * sigma_sqrt_T)
    vega_raw = S * eq_T * npd1 * sqrt_T  # per unit sigma
    vega = vega_raw / 100.0                # per 1%-sigma
    theta = theta_raw / 365.25             # per calendar day

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def compute_all_greeks(
    S: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.05,
    q: float = 0.0,
    right: str = "C",
) -> dict[str, float | None]:
    """Combined first + higher-order Greeks from one BSM snapshot.

    Returns {delta, gamma, theta, vega, volga, vanna}. All None when
    inputs invalid or T < 1e-6.
    """
    first = compute_first_order_greeks(S, K, T, sigma, r, q, right)
    if first["delta"] is None:
        return {**first, "volga": None, "vanna": None}
    higher = compute_higher_order_greeks(S, K, T, sigma, r, q)
    return {**first, **higher}


def _bsm_price(
    S: float, K: float, T: float, sigma: float, r: float, q: float, right: str,
) -> float:
    """BSM European option price. Returns NaN if inputs invalid."""
    if S <= 0 or K <= 0 or sigma <= 0 or T < _T_MIN:
        return float("nan")
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    eq_T = math.exp(-q * T)
    er_T = math.exp(-r * T)
    if right.upper() == "C":
        return S * eq_T * _norm_cdf(d1) - K * er_T * _norm_cdf(d2)
    return K * er_T * _norm_cdf(-d2) - S * eq_T * _norm_cdf(-d1)


_IV_LO = 0.001   # 0.1%
_IV_HI = 10.0    # 1000%
_IV_TOL = 1e-10
_IV_MAX_ITER = 100


def brent_implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float = 0.0,
    right: str = "C",
) -> tuple[float | None, str]:
    """Invert BSM to recover sigma from observed option price.

    Returns (iv, status) where status is:
      - "ok"              — Brent converged
      - "below_intrinsic" — price < intrinsic value (no real solution)
      - "no_bracket"      — sigma required is outside [_IV_LO, _IV_HI]
      - "no_convergence"  — Brent didn't converge within _IV_MAX_ITER
      - "bad_input"       — S/K/T invalid
    """
    if S <= 0 or K <= 0 or T < _T_MIN or price <= 0:
        return None, "bad_input"

    # Intrinsic check
    er_T = math.exp(-r * T)
    eq_T = math.exp(-q * T)
    if right.upper() == "C":
        intrinsic = max(S * eq_T - K * er_T, 0.0)
    else:
        intrinsic = max(K * er_T - S * eq_T, 0.0)
    if price < intrinsic - _IV_TOL:
        return None, "below_intrinsic"

    def f(sigma: float) -> float:
        return _bsm_price(S, K, T, sigma, r, q, right) - price

    f_lo = f(_IV_LO)
    f_hi = f(_IV_HI)
    if f_lo * f_hi > 0:
        return None, "no_bracket"

    # Brent's method (hand-rolled — small, self-contained)
    a, b = _IV_LO, _IV_HI
    fa, fb = f_lo, f_hi
    if abs(fa) < abs(fb):
        a, b = b, a
        fa, fb = fb, fa
    c, fc = a, fa
    mflag = True
    d = 0.0
    for _ in range(_IV_MAX_ITER):
        if abs(fb) < _IV_TOL:
            return b, "ok"
        if fa != fc and fb != fc:
            # Inverse quadratic interpolation
            s = (
                a * fb * fc / ((fa - fb) * (fa - fc))
                + b * fa * fc / ((fb - fa) * (fb - fc))
                + c * fa * fb / ((fc - fa) * (fc - fb))
            )
        else:
            # Secant
            s = b - fb * (b - a) / (fb - fa)
        cond1 = not ((3 * a + b) / 4 < s < b or b < s < (3 * a + b) / 4)
        cond2 = mflag and abs(s - b) >= abs(b - c) / 2
        cond3 = (not mflag) and abs(s - b) >= abs(c - d) / 2
        cond4 = mflag and abs(b - c) < _IV_TOL
        cond5 = (not mflag) and abs(c - d) < _IV_TOL
        if cond1 or cond2 or cond3 or cond4 or cond5:
            s = (a + b) / 2
            mflag = True
        else:
            mflag = False
        fs = f(s)
        d, c, fc = c, b, fb
        if fa * fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b = b, a
            fa, fb = fb, fa
    return None, "no_convergence"


@dataclass
class StepPnLResult:
    """Result of step PnL attribution for a single position."""

    contract: str
    delta_pnl: float | None
    gamma_pnl: float | None
    theta_pnl: float | None
    vega_pnl: float | None
    volga_pnl: float | None
    vanna_pnl: float | None
    residual: float | None
    total_pnl: float


def compute_step_pnl_attribution(
    contract: str,
    qty: int,
    multiplier: int,
    delta: float | None,
    gamma: float | None,
    theta: float | None,
    vega: float | None,
    volga: float | None,
    vanna: float | None,
    dS: float,
    d_sigma: float,
    dt: float,
    actual_pnl: float,
) -> StepPnLResult:
    """Compute step-level PnL attribution for one position.

    All formulas: component = qty * multiplier * greek * driver.
    Residual = actual_pnl - sum(non-None components).
    """
    scale = qty * multiplier

    delta_pnl = scale * delta * dS if delta is not None else None
    gamma_pnl = scale * 0.5 * gamma * dS * dS if gamma is not None else None
    theta_pnl = scale * theta * dt if theta is not None else None
    vega_pnl = scale * vega * d_sigma if vega is not None else None
    volga_pnl = scale * 0.5 * volga * d_sigma * d_sigma if volga is not None else None
    vanna_pnl = scale * vanna * dS * d_sigma if vanna is not None else None

    components = [delta_pnl, gamma_pnl, theta_pnl, vega_pnl, volga_pnl, vanna_pnl]
    non_none = [c for c in components if c is not None]
    residual = (actual_pnl - sum(non_none)) if non_none else None

    return StepPnLResult(
        contract=contract,
        delta_pnl=delta_pnl,
        gamma_pnl=gamma_pnl,
        theta_pnl=theta_pnl,
        vega_pnl=vega_pnl,
        volga_pnl=volga_pnl,
        vanna_pnl=vanna_pnl,
        residual=residual,
        total_pnl=actual_pnl,
    )
