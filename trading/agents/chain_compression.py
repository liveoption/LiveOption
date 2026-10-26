"""ChainCompressor — compress raw option chains into structured summaries.

Reduces thousands of option contracts into a compact representation suitable
for LLM prompts.  Provides:
- Implied volatility surface characteristics (level, skew, term structure)
- Liquidity distribution across moneyness buckets
- Filtered shortlist of tradable strikes with Greeks
- Simple regime tags (trend, range-bound, volatility expansion)

"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

BUCKET_LABELS = ("deep_itm", "itm", "atm", "otm", "deep_otm")


@dataclass
class MoneynessBucket:
    """Aggregated statistics for a moneyness bucket."""

    label: str  # "deep_itm", "itm", "atm", "otm", "deep_otm"
    contracts: int = 0  # number of contracts in bucket
    avg_iv: float = 0.0  # average implied volatility
    avg_delta: float = 0.0  # average delta
    avg_vega: float = 0.0  # average vega
    total_volume: int = 0  # total volume
    representative: dict | None = None  # best contract (highest volume)


@dataclass
class AVSRSurface:
    """Anchor-Based Volatility Surface Representation (AVSR).

    A compact 3x3 matrix summarising the implied volatility surface using
    fixed moneyness (K/S) and time-to-expiry anchors.

    """

    grid: list[list[float]] = field(default_factory=lambda: [[0.0] * 3 for _ in range(3)])
    moneyness_anchors: list[float] = field(default_factory=lambda: [0.95, 1.0, 1.05])
    expiry_anchors: list[int] = field(default_factory=lambda: [7, 30, 90])
    # Derived statistics — one per expiry anchor
    risk_reversals: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    butterflies: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    term_spread: float = 0.0


@dataclass
class ChainSummary:
    """Compressed representation of an option chain."""

    underlying: str = ""
    spot_price: float = 0.0
    # IV surface
    atm_iv: float = 0.0  # ATM implied volatility
    iv_skew: float = 0.0  # put IV - call IV (at ATM)
    iv_term_structure: float = 0.0  # far-term IV - near-term IV
    # AVSR (anchor-based volatility surface)
    avsr: AVSRSurface | None = None
    # Liquidity
    call_buckets: list[MoneynessBucket] = field(default_factory=list)
    put_buckets: list[MoneynessBucket] = field(default_factory=list)
    # Tradable shortlist
    top_calls: list[dict] = field(default_factory=list)
    top_puts: list[dict] = field(default_factory=list)
    # Regime
    regime: str = "range_bound"


# ---------------------------------------------------------------------------
# Moneyness bucketing
# ---------------------------------------------------------------------------

def _classify_by_delta(delta: float, right: str) -> str:
    """Classify a contract into a moneyness bucket using delta.

    Call delta is positive; put delta is negative.  We use absolute delta
    for threshold comparisons.

    Buckets:
      deep_itm: |delta| > 0.80
      itm:      0.60 < |delta| <= 0.80
      atm:      0.40 < |delta| <= 0.60
      otm:      0.20 < |delta| <= 0.40
      deep_otm: |delta| <= 0.20
    """
    ad = abs(delta)
    if ad > 0.80:
        return "deep_itm"
    if ad > 0.60:
        return "itm"
    if ad > 0.40:
        return "atm"
    if ad > 0.20:
        return "otm"
    return "deep_otm"


def _classify_by_strike(strike: float, spot: float, right: str) -> str:
    """Fallback: classify by strike distance from spot.

    ATM: within 2% of spot
    For calls:  strike > spot is OTM, strike < spot is ITM
    For puts:   strike < spot is OTM, strike > spot is ITM

    Distance thresholds:
      deep_itm: > 5% ITM
      itm:      2-5% ITM
      atm:      within 2%
      otm:      2-5% OTM
      deep_otm: > 5% OTM
    """
    if spot <= 0:
        return "atm"
    pct = (strike - spot) / spot  # positive when strike > spot
    # For calls: positive pct means OTM; for puts: positive pct means ITM
    if right.upper() in ("P", "PUT"):
        pct = -pct  # flip so positive = OTM for puts
    if abs(pct) <= 0.02:
        return "atm"
    if pct > 0.05:
        return "deep_otm"
    if pct > 0.02:
        return "otm"
    if pct < -0.05:
        return "deep_itm"
    return "itm"  # -0.05 <= pct < -0.02


def _classify_contract(contract: dict, spot: float) -> str:
    """Classify a contract into a moneyness bucket.

    Uses delta when available, otherwise falls back to strike-based
    classification.
    """
    right = _get_right(contract)
    delta = contract.get("delta")
    if delta is not None:
        return _classify_by_delta(delta, right)
    strike = contract.get("strike")
    if strike is not None:
        return _classify_by_strike(strike, spot, right)
    return "atm"


def _get_right(contract: dict) -> str:
    """Determine option right (C/P) from contract dict.

    Checks ``right``, ``type``, or parses from ``ticker`` (OPRA format).
    """
    right = contract.get("right") or contract.get("type") or ""
    if right.upper() in ("C", "CALL"):
        return "C"
    if right.upper() in ("P", "PUT"):
        return "P"
    # Try to parse from OPRA ticker: O:SPY250106C00595000
    # The C/P right character comes after the date digits (YYMMDD),
    # so we must skip the prefix and look for C/P after the first digit run.
    ticker = contract.get("ticker", "")
    # Strip optional "O:" prefix
    raw = ticker.lstrip("O:") if ticker.startswith("O:") else ticker
    # Walk past the alphabetic symbol prefix, then find the C/P after digits
    found_digit = False
    for ch in raw:
        if ch.isdigit():
            found_digit = True
        elif found_digit and ch in ("C", "P"):
            return ch
    return "C"  # default


def _is_call(contract: dict) -> bool:
    return _get_right(contract) == "C"


# ---------------------------------------------------------------------------
# Regime detection
# ---------------------------------------------------------------------------

def _detect_regime(recent_prices: list[float] | None) -> str:
    """Detect market regime from the last 20 price bars.

    Rules (applied in order):
    - trending_up:    20-bar return > +1%
    - trending_down:  20-bar return < -1%
    - vol_expansion:  20-bar realized vol > 1.5x average
    - range_bound:    otherwise
    """
    if not recent_prices or len(recent_prices) < 2:
        return "range_bound"

    prices = recent_prices[-20:] if len(recent_prices) >= 20 else recent_prices

    # 20-bar return
    ret = (prices[-1] - prices[0]) / prices[0] if prices[0] != 0 else 0.0
    if ret > 0.01:
        return "trending_up"
    if ret < -0.01:
        return "trending_down"

    # Realized volatility: std of log returns
    if len(prices) >= 3:
        log_returns = []
        for i in range(1, len(prices)):
            if prices[i] > 0 and prices[i - 1] > 0:
                log_returns.append(math.log(prices[i] / prices[i - 1]))
        if len(log_returns) >= 2:
            mean_lr = sum(log_returns) / len(log_returns)
            var = sum((lr - mean_lr) ** 2 for lr in log_returns) / len(log_returns)
            realized_vol = math.sqrt(var)
            # "Average" baseline: use a fraction of the window's own vol
            # If realized vol is high relative to the return magnitude, flag it
            # Heuristic: annualized vol > 30% (intraday: ~0.019 per minute bar)
            # Simpler: compare vol to typical ATM IV level (~0.002 per bar for 20% ann)
            avg_vol = 0.002  # rough baseline per-bar vol
            if realized_vol > 1.5 * avg_vol:
                return "vol_expansion"

    return "range_bound"


# ---------------------------------------------------------------------------
# IV surface helpers
# ---------------------------------------------------------------------------

def _calc_atm_iv(contracts: list[dict], spot: float) -> float:
    """Average IV of ATM contracts (those within 2% of spot with IV data)."""
    atm_ivs = []
    for c in contracts:
        iv = c.get("iv")
        strike = c.get("strike")
        if iv is None or iv <= 0 or strike is None:
            continue
        if spot > 0 and abs(strike - spot) / spot <= 0.02:
            atm_ivs.append(iv)
    return sum(atm_ivs) / len(atm_ivs) if atm_ivs else 0.0


def _calc_iv_skew(contracts: list[dict], spot: float) -> float:
    """IV skew = ATM put IV - ATM call IV.  Positive = put premium."""
    atm_call_ivs = []
    atm_put_ivs = []
    for c in contracts:
        iv = c.get("iv")
        strike = c.get("strike")
        if iv is None or iv <= 0 or strike is None:
            continue
        if spot > 0 and abs(strike - spot) / spot <= 0.02:
            if _is_call(c):
                atm_call_ivs.append(iv)
            else:
                atm_put_ivs.append(iv)
    avg_call_iv = sum(atm_call_ivs) / len(atm_call_ivs) if atm_call_ivs else 0.0
    avg_put_iv = sum(atm_put_ivs) / len(atm_put_ivs) if atm_put_ivs else 0.0
    if avg_call_iv == 0 and avg_put_iv == 0:
        return 0.0
    return avg_put_iv - avg_call_iv


def _calc_iv_term_structure(contracts: list[dict]) -> float:
    """IV term structure = avg far-term IV - avg near-term IV.

    Split by median DTE.
    """
    with_dte = [(c.get("dte", 0), c.get("iv", 0)) for c in contracts
                if c.get("iv") is not None and c.get("iv", 0) > 0
                and c.get("dte") is not None]
    if len(with_dte) < 2:
        return 0.0
    with_dte.sort(key=lambda x: x[0])
    mid = len(with_dte) // 2
    near = [iv for _, iv in with_dte[:mid]]
    far = [iv for _, iv in with_dte[mid:]]
    avg_near = sum(near) / len(near) if near else 0.0
    avg_far = sum(far) / len(far) if far else 0.0
    return avg_far - avg_near


# ---------------------------------------------------------------------------
# Bucket aggregation
# ---------------------------------------------------------------------------

def _build_buckets(contracts: list[dict], spot: float) -> list[MoneynessBucket]:
    """Group contracts into moneyness buckets and aggregate statistics."""
    buckets: dict[str, list[dict]] = {label: [] for label in BUCKET_LABELS}
    for c in contracts:
        label = _classify_contract(c, spot)
        buckets[label].append(c)

    result = []
    for label in BUCKET_LABELS:
        group = buckets[label]
        if not group:
            result.append(MoneynessBucket(label=label))
            continue

        ivs = [c.get("iv", 0) for c in group if c.get("iv") is not None and c.get("iv", 0) > 0]
        deltas = [c.get("delta", 0) for c in group if c.get("delta") is not None]
        vegas = [c.get("vega", 0) for c in group if c.get("vega") is not None]
        volumes = [c.get("volume", 0) for c in group]

        # Representative: highest volume contract
        rep = max(group, key=lambda c: c.get("volume", 0))

        result.append(MoneynessBucket(
            label=label,
            contracts=len(group),
            avg_iv=sum(ivs) / len(ivs) if ivs else 0.0,
            avg_delta=sum(deltas) / len(deltas) if deltas else 0.0,
            avg_vega=sum(vegas) / len(vegas) if vegas else 0.0,
            total_volume=sum(volumes),
            representative=rep,
        ))
    return result


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_volume(volume: int | float) -> str:
    """Format volume with K/M suffixes."""
    if volume >= 1_000_000:
        return f"{volume / 1_000_000:.1f}M"
    if volume >= 1_000:
        return f"{volume / 1_000:.0f}K"
    return str(int(volume))


# ---------------------------------------------------------------------------
# ChainCompressor
# ---------------------------------------------------------------------------

class ChainCompressor:
    """Compress raw option chains into structured summaries for LLM prompts.

    Usage::

        compressor = ChainCompressor()
        summary = compressor.compress(raw_chain, spot_price=595.20,
                                       recent_prices=[594.0, 595.0, ...])
        prompt_text = compressor.format_for_prompt(summary)
    """

    # Default AVSR parameters
    MONEYNESS_ANCHORS = [0.95, 1.0, 1.05]
    EXPIRY_ANCHORS = [7, 30, 90]  # days
    AVSR_LAMBDA = 0.01  # DTE weighting in distance metric

    def __init__(self, avsr_config=None) -> None:
        # Instance settings avoid changing other concurrent runs' AVSR surface.
        if avsr_config is not None:
            self.MONEYNESS_ANCHORS = list(avsr_config.moneyness_anchors)
            self.EXPIRY_ANCHORS = list(avsr_config.expiry_anchors)
            self.AVSR_LAMBDA = float(avsr_config.distance_lambda)

    def compress(
        self,
        raw_chain: list[dict],
        spot_price: float,
        recent_prices: list[float] | None = None,
        underlying: str = "",
        top_n: int = 5,
    ) -> ChainSummary:
        """Compress raw option chain into structured summary.

        Args:
            raw_chain: List of contract dicts, each with keys like
                ``ticker``, ``strike``, ``expiry``, ``dte``, ``close``,
                ``volume``, ``iv``, ``delta``, ``gamma``, ``vega``, ``theta``,
                ``right`` or ``type`` (C/P).
            spot_price: Current underlying spot price.
            recent_prices: Recent underlying close prices (for regime detection).
            underlying: Underlying ticker symbol (e.g. "SPY").

        Returns:
            A ChainSummary with IV surface, bucketed liquidity, top contracts,
            and regime tag.
        """
        if not raw_chain:
            return ChainSummary(
                underlying=underlying,
                spot_price=spot_price,
                regime=_detect_regime(recent_prices),
            )

        # Infer underlying from first contract if not provided
        if not underlying:
            underlying = self._infer_underlying(raw_chain[0])

        # Split by right
        calls = [c for c in raw_chain if _is_call(c)]
        puts = [c for c in raw_chain if not _is_call(c)]

        # IV surface
        atm_iv = _calc_atm_iv(raw_chain, spot_price)
        iv_skew = _calc_iv_skew(raw_chain, spot_price)
        iv_term_structure = _calc_iv_term_structure(raw_chain)

        # AVSR
        avsr = self.compute_avsr(raw_chain, spot_price)

        # Buckets
        call_buckets = _build_buckets(calls, spot_price)
        put_buckets = _build_buckets(puts, spot_price)

        # Top tradable contracts by volume
        top_calls = sorted(calls, key=lambda c: c.get("volume", 0), reverse=True)[:top_n]
        top_puts = sorted(puts, key=lambda c: c.get("volume", 0), reverse=True)[:top_n]

        # Regime
        regime = _detect_regime(recent_prices)

        return ChainSummary(
            underlying=underlying,
            spot_price=spot_price,
            atm_iv=atm_iv,
            iv_skew=iv_skew,
            iv_term_structure=iv_term_structure,
            avsr=avsr,
            call_buckets=call_buckets,
            put_buckets=put_buckets,
            top_calls=top_calls,
            top_puts=top_puts,
            regime=regime,
        )

    def compute_avsr(
        self,
        raw_chain: list[dict],
        spot_price: float,
    ) -> AVSRSurface | None:
        """Compute the Anchor-Based Volatility Surface Representation.

        For each anchor point (moneyness m, expiry tau), find the nearest
        contract by combined distance:

            dist = |K/S - m| + lambda * |DTE - tau|

        and use that contract's IV.  Then derive risk reversal, butterfly,
        and term spread statistics.

        Returns None if the chain has no contracts with valid IV and strike.
        """
        if not raw_chain or spot_price <= 0:
            return None

        # Pre-filter to contracts with usable IV and strike/DTE
        usable = []
        for c in raw_chain:
            iv = c.get("iv")
            strike = c.get("strike")
            dte = c.get("dte")
            if iv is not None and iv > 0 and strike is not None and dte is not None:
                usable.append(c)

        if not usable:
            return None

        m_anchors = self.MONEYNESS_ANCHORS
        t_anchors = self.EXPIRY_ANCHORS
        lam = self.AVSR_LAMBDA

        grid: list[list[float]] = []
        for m in m_anchors:
            row: list[float] = []
            for tau in t_anchors:
                best_iv = self._nearest_iv(usable, spot_price, m, tau, lam)
                row.append(best_iv)
            grid.append(row)

        # Derived statistics
        # Risk reversal per expiry: sigma(m3, tau_j) - sigma(m1, tau_j)
        risk_reversals = [
            grid[2][j] - grid[0][j] for j in range(len(t_anchors))
        ]
        # Butterfly per expiry: (sigma(m1, tau_j) + sigma(m3, tau_j)) / 2 - sigma(m2, tau_j)
        butterflies = [
            (grid[0][j] + grid[2][j]) / 2 - grid[1][j] for j in range(len(t_anchors))
        ]
        # Term spread: sigma(m2, tau3) - sigma(m2, tau1)
        term_spread = grid[1][2] - grid[1][0]

        return AVSRSurface(
            grid=grid,
            moneyness_anchors=list(m_anchors),
            expiry_anchors=list(t_anchors),
            risk_reversals=risk_reversals,
            butterflies=butterflies,
            term_spread=term_spread,
        )

    @staticmethod
    def _nearest_iv(
        contracts: list[dict],
        spot: float,
        m: float,
        tau: int,
        lam: float,
    ) -> float:
        """Find the IV of the contract nearest to anchor (m, tau)."""
        best_dist = float("inf")
        best_iv = 0.0
        for c in contracts:
            strike = c["strike"]
            dte = c["dte"]
            iv = c["iv"]
            moneyness = strike / spot
            dist = abs(moneyness - m) + lam * abs(dte - tau)
            if dist < best_dist:
                best_dist = dist
                best_iv = iv
        return best_iv

    def format_for_prompt(self, summary: ChainSummary, show_top_tradable: bool = True) -> str:
        """Format ChainSummary as concise text for LLM prompt.

        Produces a compact block like::

            == Option Chain Summary (SPY @ $595.20) ==
            Regime: range_bound | ATM IV: 18.2% | Skew: +1.3% (put premium)
            ...
        """
        lines: list[str] = []

        # Header
        lines.append(
            f"== Option Chain Summary ({summary.underlying} @ ${summary.spot_price:.2f}) =="
        )

        # Regime + IV surface
        skew_label = "(put premium)" if summary.iv_skew > 0 else "(call premium)" if summary.iv_skew < 0 else ""
        lines.append(
            f"Regime: {summary.regime} | "
            f"ATM IV: {summary.atm_iv * 100:.1f}% | "
            f"Skew: {summary.iv_skew * 100:+.1f}% {skew_label}"
        )
        if summary.iv_term_structure != 0:
            lines.append(
                f"Term structure: {summary.iv_term_structure * 100:+.1f}% (far - near)"
            )

        # AVSR surface
        if summary.avsr is not None:
            lines.append("")
            avsr = summary.avsr
            # Header row with expiry labels
            hdr = "     " + "  ".join(f"{d:>5}D" for d in avsr.expiry_anchors)
            lines.append("== IV Surface (AVSR 3x3) ==")
            lines.append(hdr)
            for i, m in enumerate(avsr.moneyness_anchors):
                row_vals = "  ".join(f"{v * 100:5.1f}%" for v in avsr.grid[i])
                lines.append(f"{m:.2f}  {row_vals}")
            # Risk reversal
            rr_str = ", ".join(f"{v * 100:+.1f}%" for v in avsr.risk_reversals)
            lines.append(f"Risk Reversal: {rr_str}")
            # Butterfly
            bf_str = ", ".join(f"{v * 100:+.1f}%" for v in avsr.butterflies)
            lines.append(f"Butterfly: {bf_str}")
            # Term spread
            lines.append(f"Term Spread: {avsr.term_spread * 100:+.1f}%")

        # Call buckets
        lines.append("")
        lines.append("Calls by moneyness:")
        for b in summary.call_buckets:
            if b.contracts > 0:
                lines.append(
                    f"  {b.label.upper()}: {b.contracts} contracts, "
                    f"avg_iv={b.avg_iv * 100:.1f}%, "
                    f"avg_delta={b.avg_delta:.2f}, "
                    f"vol={_format_volume(b.total_volume)}"
                )

        # Put buckets
        lines.append("")
        lines.append("Puts by moneyness:")
        for b in summary.put_buckets:
            if b.contracts > 0:
                lines.append(
                    f"  {b.label.upper()}: {b.contracts} contracts, "
                    f"avg_iv={b.avg_iv * 100:.1f}%, "
                    f"avg_delta={b.avg_delta:.2f}, "
                    f"vol={_format_volume(b.total_volume)}"
                )

        # Top tradable
        if show_top_tradable:
            all_top = []
            for c in summary.top_calls:
                all_top.append(("C", c))
            for c in summary.top_puts:
                all_top.append(("P", c))
            if all_top:
                lines.append("")
                lines.append("Top tradable:")
                for right_label, c in all_top:
                    ticker = c.get("ticker", "?")
                    strike = c.get("strike", 0)
                    price = c.get("close")
                    delta = c.get("delta")
                    iv = c.get("iv")
                    vol = c.get("volume", 0)
                    price_str = f"px=${price:.2f}" if price is not None else "px=?"
                    delta_str = f"delta={delta:.2f}" if delta is not None else "delta=?"
                    iv_str = f"IV={iv * 100:.1f}%" if iv is not None and iv > 0 else "IV=?"
                    lines.append(
                        f"  {ticker} {right_label} K=${strike:.0f} {price_str} "
                        f"{delta_str} {iv_str} vol={_format_volume(vol)}"
                    )

        return "\n".join(lines)

    def _infer_underlying(self, contract: dict) -> str:
        """Try to extract underlying ticker from a contract dict."""
        # Check direct field
        underlying = contract.get("underlying", "")
        if underlying:
            return underlying
        # Parse from OPRA ticker
        ticker = contract.get("ticker", "")
        if ticker.startswith("O:"):
            rest = ticker[2:]
            sym = ""
            for ch in rest:
                if ch.isdigit():
                    break
                sym += ch
            return sym
        return ""
