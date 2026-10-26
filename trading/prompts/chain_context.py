"""chain_context -- build compressed chain context for LLM prompts.

Combines ChainCompressor (AVSR: IV surface, moneyness buckets, regime) and
QOCREncoder (quantized action grid) into a single text block that replaces
raw chain dumps in strategy prompts.

Usage::

    from trading.prompts.chain_context import build_chain_context

    text = build_chain_context(
        raw_chain=contracts,
        spot_price=595.20,
        recent_prices=[594.0, 595.0, ...],
        underlying="SPY",
    )
    # text is a string ready to inject into the user prompt
"""

from __future__ import annotations


def build_chain_context(
    raw_chain: list[dict],
    spot_price: float,
    recent_prices: list[float] | None = None,
    underlying: str = "",
    qocr_config=None,
    show_top_tradable: bool = True,
    avsr_config=None,
) -> str:
    """Build compressed chain context combining AVSR and QOCR.

    Args:
        raw_chain: List of contract dicts (from UPQ or market event bars).
        spot_price: Current underlying spot price.
        recent_prices: Recent underlying close prices for regime detection.
        underlying: Underlying ticker symbol (e.g. "SPY").
        qocr_config: Optional QOCRConfig instance; uses default if None.
        show_top_tradable: Whether to include top-tradable section in AVSR output.

    Returns:
        Formatted string with:
        - ChainCompressor summary (IV surface, moneyness buckets, top tradable)
        - QOCR action grid (strike x DTE matrix of best contracts)
        Returns empty string if raw_chain is empty.
    """
    if not raw_chain:
        return ""

    from trading.agents.chain_compression import ChainCompressor
    from trading.agents.qocr import QOCREncoder

    compressor = ChainCompressor(avsr_config=avsr_config)
    if qocr_config is not None:
        encoder = QOCREncoder(config=qocr_config)
        # Use qocr_config.show_top_tradable unless caller explicitly set show_top_tradable=False
        effective_top_tradable = qocr_config.show_top_tradable and show_top_tradable
    else:
        encoder = QOCREncoder()
        effective_top_tradable = show_top_tradable

    # Apply price range filter to both AVSR and QOCR when configured
    from trading.agents.chain_compression import _is_call as _cc_is_call
    otm_r = encoder.config.otm_range
    itm_r = encoder.config.itm_range
    pr    = encoder.config.price_range
    if (otm_r > 0 or itm_r > 0) and spot_price > 0:
        # Asymmetric: calls show more OTM, puts show more OTM on the other side
        filtered_chain = []
        for c in raw_chain:
            k = c.get("strike") or 0
            if k <= 0:
                continue
            if _cc_is_call(c):
                lo_c = spot_price * (1.0 - itm_r) if itm_r > 0 else 0
                hi_c = spot_price * (1.0 + otm_r) if otm_r > 0 else float("inf")
                if lo_c <= k <= hi_c:
                    filtered_chain.append(c)
            else:
                lo_p = spot_price * (1.0 - otm_r) if otm_r > 0 else 0
                hi_p = spot_price * (1.0 + itm_r) if itm_r > 0 else float("inf")
                if lo_p <= k <= hi_p:
                    filtered_chain.append(c)
    elif pr > 0 and spot_price > 0:
        lo_k = spot_price * (1.0 - pr)
        hi_k = spot_price * (1.0 + pr)
        filtered_chain = [c for c in raw_chain if lo_k <= (c.get("strike") or 0) <= hi_k]
    else:
        filtered_chain = raw_chain

    # AVSR: IV surface + moneyness buckets + regime (uses filtered chain)
    top_n = qocr_config.top_tradable_n if qocr_config is not None else 5
    summary = compressor.compress(
        filtered_chain,
        spot_price=spot_price,
        recent_prices=recent_prices,
        underlying=underlying,
        top_n=top_n,
    )
    avsr_text = compressor.format_for_prompt(summary, show_top_tradable=effective_top_tradable)

    # QOCR / flat chain: encode and format
    grids = encoder.encode_chain(filtered_chain, spot_price=spot_price)
    qocr_text = encoder.format_for_prompt(grids, underlying=underlying, spot_price=spot_price)

    # Combine
    parts: list[str] = [avsr_text]
    if qocr_text:
        parts.append(qocr_text)

    return "\n\n".join(parts)


def build_executor_chain_context(
    raw_chain: list[dict],
    spot_price: float,
    underlying: str = "",
    price_range: float = 0.0,
    otm_range: float = 0.0,
    itm_range: float = 0.0,
    dense_range: float = 0.0,
    sparse_gap: int = 0,
    max_per_dte: int = 0,
    vol_filter: bool = False,
) -> str:
    """Build detailed per-contract chain context for the EXECUTOR.

    Contracts are grouped by DTE, then Calls / Puts within each expiry,
    sorted by strike ascending.

    Args:
        price_range:  If > 0, only include contracts within ±price_range of spot.
        dense_range:  Contracts within ±dense_range are shown in full.  Contracts
                      between dense_range and price_range are sampled every
                      (sparse_gap+1) contracts.  0 = no zone distinction (show all).
        sparse_gap:   Step size for the sparse zone: 0 = every contract,
                      1 = every 2nd, 2 = every 3rd.  Ignored when dense_range=0.
        otm_range:    Asymmetric OTM filter: calls shown up to spot*(1+otm_range),
                      puts shown down to spot*(1-otm_range). Overrides price_range when > 0.
        itm_range:    Asymmetric ITM filter: calls shown down to spot*(1-itm_range),
                      puts shown up to spot*(1+itm_range). Pair with otm_range.
        max_per_dte:  Max calls and max puts to show per DTE bucket.  0 = no cap.
                      When capped, keeps the contracts nearest to spot (ATM-centric).
        vol_filter:   If True (0DTE intraday), split into two sections:
                      1. Tradable (vol > 0): liquid contracts you can order right now.
                      2. Reference chain: full chain with dense/sparse sampling for
                         price context. Greeks shown where available.
    """
    if not raw_chain:
        return "(no option contracts available)"

    from collections import defaultdict
    from trading.agents.chain_compression import _is_call

    valid = [c for c in raw_chain if c.get("strike") is not None]
    if not valid:
        return "(no tradable contracts in strike/expiry range)"

    # Apply price range filter (asymmetric takes priority)
    if (otm_range > 0 or itm_range > 0) and spot_price > 0:
        filtered: list[dict] = []
        for c in valid:
            k = c.get("strike") or 0
            if _is_call(c):
                lo_c = spot_price * (1.0 - itm_range) if itm_range > 0 else 0
                hi_c = spot_price * (1.0 + otm_range) if otm_range > 0 else float("inf")
                if lo_c <= k <= hi_c:
                    filtered.append(c)
            else:
                lo_p = spot_price * (1.0 - otm_range) if otm_range > 0 else 0
                hi_p = spot_price * (1.0 + itm_range) if itm_range > 0 else float("inf")
                if lo_p <= k <= hi_p:
                    filtered.append(c)
        valid = filtered
    elif price_range > 0 and spot_price > 0:
        lo = spot_price * (1.0 - price_range)
        hi = spot_price * (1.0 + price_range)
        valid = [c for c in valid if lo <= (c.get("strike") or 0) <= hi]
    if not valid:
        return f"(no contracts within range of spot ${spot_price:.2f})"

    def _fmt(c: dict, show_stale: bool = True) -> str:
        delta_s = f"Δ={c['delta']:.2f}" if c.get("delta") is not None else ""
        iv_s    = f"IV={c['iv']:.1%}"   if c.get("iv")    is not None else ""
        vol     = c.get("volume", 0)
        vol_s   = f"vol={vol/1000:.0f}K" if vol >= 1000 else f"vol={vol}"
        stale_m = c.get("stale_minutes", 0) or 0
        stale_s = f"stale:{stale_m}m" if (show_stale and stale_m > 0) else ""
        parts   = [f"    {c['ticker']}  K=${c.get('strike',0):.0f}  px=${c['close']:.2f}"]
        if iv_s:    parts.append(iv_s)
        if delta_s: parts.append(delta_s)
        parts.append(vol_s)
        if stale_s: parts.append(stale_s)
        return "  ".join(parts)

    def _apply_sampling(contracts: list[dict]) -> list[dict]:
        if dense_range <= 0 or sparse_gap <= 0 or spot_price <= 0:
            return contracts
        dense_lo = spot_price * (1.0 - dense_range)
        dense_hi = spot_price * (1.0 + dense_range)
        result: list[dict] = []
        sparse_counter = 0
        for c in contracts:
            k = c.get("strike") or 0
            if dense_lo <= k <= dense_hi:
                result.append(c)
                sparse_counter = 0
            else:
                if sparse_counter % (sparse_gap + 1) == 0:
                    result.append(c)
                sparse_counter += 1
        return result

    def _apply_cap(contracts: list[dict]) -> list[dict]:
        if max_per_dte <= 0 or len(contracts) <= max_per_dte or spot_price <= 0:
            return contracts
        by_atm = sorted(contracts, key=lambda c: abs((c.get("strike") or 0) - spot_price))
        kept = by_atm[:max_per_dte]
        kept.sort(key=lambda c: c.get("strike") or 0)
        return kept

    def _render_dte_sections(by_dte: dict) -> list[str]:
        lines: list[str] = []
        for dte in sorted(by_dte.keys()):
            label = f"DTE={dte}" if dte != 9999 else "DTE=?"
            bucket = by_dte[dte]
            calls, puts = bucket["C"], bucket["P"]
            lines.append(f"\n{label}:")
            if calls:
                lines.append(f"  Calls ({len(calls)}):")
                lines.extend(_fmt(c) for c in calls)
            else:
                lines.append("  Calls: (none)")
            if puts:
                lines.append(f"  Puts ({len(puts)}):")
                lines.extend(_fmt(c) for c in puts)
            else:
                lines.append("  Puts: (none)")
        return lines

    # ── vol_filter mode (intraday 0DTE) ──────────────────────────────────────
    if vol_filter:
        tradable = [c for c in valid if (c.get("volume") or 0) > 0]
        # Section 1: Tradable contracts (vol > 0)
        lines: list[str] = [
            f"== Tradable Contracts (vol>0) — {underlying or '?'} @ ${spot_price:.2f} =="
        ]
        if tradable:
            by_dte_t: dict[int, dict[str, list]] = defaultdict(lambda: {"C": [], "P": []})
            for c in tradable:
                dte = c.get("dte") if c.get("dte") is not None else 9999
                by_dte_t[dte]["C" if _is_call(c) else "P"].append(c)
            for bucket in by_dte_t.values():
                for side in ("C", "P"):
                    bucket[side].sort(key=lambda c: c.get("strike") or 0)
            lines.extend(_render_dte_sections(by_dte_t))
        else:
            lines.append("  (none yet — pre-market or no trades this bar)")

        # Section 2: Full reference chain with sampling
        by_dte_r: dict[int, dict[str, list]] = defaultdict(lambda: {"C": [], "P": []})
        for c in valid:
            dte = c.get("dte") if c.get("dte") is not None else 9999
            by_dte_r[dte]["C" if _is_call(c) else "P"].append(c)
        for bucket in by_dte_r.values():
            for side in ("C", "P"):
                bucket[side].sort(key=lambda c: c.get("strike") or 0)
                bucket[side] = _apply_sampling(bucket[side])

        n_ref = sum(len(v["C"]) + len(v["P"]) for v in by_dte_r.values())
        dense_pct = f"±{dense_range:.0%}" if dense_range > 0 else f"±{price_range:.0%}"
        lines.append(
            f"\n== Reference Chain ({n_ref} contracts, {dense_pct} dense"
            + (f" / gap={sparse_gap}" if sparse_gap > 0 else "") + ") =="
        )
        lines.extend(_render_dte_sections(by_dte_r))
        return "\n".join(lines)

    # ── Standard mode ────────────────────────────────────────────────────────
    by_dte: dict[int, dict[str, list]] = defaultdict(lambda: {"C": [], "P": []})
    for c in valid:
        dte = c.get("dte") if c.get("dte") is not None else 9999
        side = "C" if _is_call(c) else "P"
        by_dte[dte][side].append(c)

    for dte_bucket in by_dte.values():
        for side in ("C", "P"):
            dte_bucket[side].sort(key=lambda c: c.get("strike") or 0)
            dte_bucket[side] = _apply_sampling(dte_bucket[side])
            dte_bucket[side] = _apply_cap(dte_bucket[side])

    n_calls = sum(len(v["C"]) for v in by_dte.values())
    n_puts  = sum(len(v["P"]) for v in by_dte.values())

    lines = [
        f"Underlying: {underlying or '?'}  Spot: ${spot_price:.2f}  "
        f"({n_calls} calls, {n_puts} puts across {len(by_dte)} expiries)",
    ]
    lines.extend(_render_dte_sections(by_dte))
    return "\n".join(lines)


def build_multi_ticker_chain_context(
    chains_by_ticker: dict[str, list[dict]],
    spot_map: dict[str, float],
    recent_map: dict[str, list[float]] | None = None,
    avsr_config=None,
) -> str | None:
    """Build compressed chain context for multiple tickers.

    Convenience wrapper that calls ``build_chain_context`` per ticker
    and joins results.

    Args:
        chains_by_ticker: Mapping of ticker symbol to list of contract dicts.
        spot_map: Mapping of ticker symbol to spot price.
        recent_map: Optional mapping of ticker symbol to recent close prices.

    Returns:
        Combined formatted string, or None if no data.
    """
    if recent_map is None:
        recent_map = {}

    parts: list[str] = []
    for sym, contracts in chains_by_ticker.items():
        spot = spot_map.get(sym, 0.0)
        recent = recent_map.get(sym, [])
        text = build_chain_context(
            raw_chain=contracts,
            spot_price=spot,
            recent_prices=recent,
            underlying=sym,
            avsr_config=avsr_config,
        )
        if text:
            parts.append(text)

    return "\n\n".join(parts) if parts else None
