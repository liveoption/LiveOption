"""QOCR — Quantized Option Chain Representation.

Discretized action space for option trading.  Contracts are first placed
in expiry buckets (e.g. 0-14 DTE, 15-45 DTE), then classified as
ITM / ATM / OTM based on K/S vs the configured ATM thresholds.

ITM/ATM/OTM semantics (from the contract's perspective):
  Calls:  ITM = K < S×atm_lo,   ATM = atm_lo ≤ K/S ≤ atm_hi,  OTM = K > S×atm_hi
  Puts:   OTM = K < S×atm_lo,   ATM = atm_lo ≤ K/S ≤ atm_hi,  ITM = K > S×atm_hi

All contracts within an expiry bucket are included (no pre-moneyness filter).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from trading.agents.chain_compression import _is_call


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class QOCRConfig:
    """Configuration for the QOCR discretization.

    ATM thresholds define the ITM/ATM/OTM boundary via K/S ratios.
    Expiry buckets are defined as (low_dte, high_dte) in calendar days.
    All contracts within an expiry bucket are included regardless of
    how deep ITM or OTM they are.
    """

    # ATM boundary thresholds (K/S ratio)
    atm_lo: float = 0.99   # K/S below this = ITM for calls, OTM for puts
    atm_hi: float = 1.01   # K/S above this = OTM for calls, ITM for puts

    # Expiry buckets
    expiry_buckets: list[tuple[int, int]] = field(default_factory=lambda: [
        (0, 14),
        (15, 45),
        (46, 120),
    ])
    expiry_labels: list[str] = field(default_factory=lambda: [
        "short", "medium", "long",
    ])
    qty_levels: list[int] = field(default_factory=lambda: [1, 2, 5, 10])
    # Per-DTE per-moneyness-group cap (0 = show all)
    max_contracts_per_bucket: int = 0
    # Whether to show top-tradable section (ChainCompressor shortlist)
    show_top_tradable: bool = True
    # How many contracts to include in the top-tradable shortlist (per side)
    top_tradable_n: int = 5
    # Symmetric price range filter: K/S in [1-r, 1+r]. Used when otm_range/itm_range not set.
    price_range: float = 0.0
    # Asymmetric range (overrides price_range when > 0):
    #   Calls shown for K in [spot*(1-itm_range), spot*(1+otm_range)]
    #   Puts  shown for K in [spot*(1-otm_range), spot*(1+itm_range)]
    # Set to 0 to fall back to symmetric price_range.
    otm_range: float = 0.0   # how far OTM to show (calls above spot, puts below spot)
    itm_range: float = 0.0   # how far ITM to show (calls below spot, puts above spot)
    # Flat format: show a flat sorted list (no ITM/ATM/OTM grouping) — suited for 0DTE
    flat_chain_format: bool = False


def _load_scenario_cfg(scenario: str) -> dict:
    """Load chain context settings for a scenario from configs/chain_context.yaml.

    Falls back to empty dict (hard-coded defaults apply) if file or key is missing.
    """
    try:
        from pathlib import Path
        import yaml
        cfg_path = Path(__file__).parent.parent.parent / "configs" / "chain_context.yaml"
        if not cfg_path.exists():
            return {}
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("scenarios", {}).get(scenario, {})
    except Exception:
        return {}


def get_executor_price_range(scenario: str) -> float:
    """Return the executor price range (±fraction) for a scenario."""
    return float(_load_scenario_cfg(scenario).get("executor_price_range", 0.10))


def get_executor_chain_config(scenario: str) -> dict:
    """Return executor chain display config for a scenario.

    Returns dict with keys:
      price_range:    ±fraction of spot for symmetric contract filter (default 0.10)
      otm_range:      asymmetric OTM fraction (calls above, puts below spot); 0 = use price_range
      itm_range:      asymmetric ITM fraction (calls below, puts above spot); 0 = use price_range
      dense_range:    ±fraction of spot for dense zone — show all (default = price_range)
      sparse_gap:     step size outside dense zone; 0 = show all, 1 = every 2nd (default 0)
      max_per_dte:    max calls and max puts per DTE bucket; 0 = no cap (default 0)
      vol_filter:     if True, split executor into tradable (vol>0) + reference sections
    """
    cfg = _load_scenario_cfg(scenario)
    pr  = float(cfg.get("executor_price_range", 0.10))
    otr = float(cfg.get("executor_otm_range", 0.0))
    itr = float(cfg.get("executor_itm_range", 0.0))
    dr  = float(cfg.get("executor_dense_range", pr))
    sg  = int(cfg.get("executor_sparse_gap", 0))
    mp  = int(cfg.get("executor_max_per_dte", 0))
    vf  = bool(cfg.get("executor_vol_filter", False))
    return {
        "price_range": pr, "otm_range": otr, "itm_range": itr,
        "dense_range": dr, "sparse_gap": sg, "max_per_dte": mp, "vol_filter": vf,
    }


def _build_config(cfg: dict, defaults: dict) -> QOCRConfig:
    """Build a QOCRConfig from a YAML scenario dict with fallback defaults."""
    raw_buckets = cfg.get("qocr_expiry_buckets", defaults["buckets"])
    return QOCRConfig(
        atm_lo=float(cfg.get("qocr_atm_lo", 0.99)),
        atm_hi=float(cfg.get("qocr_atm_hi", 1.01)),
        expiry_buckets=[tuple(b) for b in raw_buckets],
        expiry_labels=cfg.get("qocr_expiry_labels", defaults["labels"]),
        qty_levels=[1, 2, 5, 10],
        max_contracts_per_bucket=int(cfg.get("qocr_max_per_bucket", defaults["max_per"])),
        show_top_tradable=bool(cfg.get("qocr_show_top_tradable", defaults["top_tradable"])),
        top_tradable_n=int(cfg.get("qocr_top_tradable_n", defaults.get("top_tradable_n", 5))),
        price_range=float(cfg.get("qocr_price_range", defaults.get("price_range", 0.0))),
        otm_range=float(cfg.get("qocr_otm_range", defaults.get("otm_range", 0.0))),
        itm_range=float(cfg.get("qocr_itm_range", defaults.get("itm_range", 0.0))),
        flat_chain_format=bool(cfg.get("qocr_flat_format", defaults.get("flat_format", False))),
    )


def qocr_config_0dte() -> QOCRConfig:
    """0DTE intraday: only today's expiry (DTE=0-1). Reads configs/chain_context.yaml."""
    cfg = _load_scenario_cfg("intraday_0dte")
    return _build_config(cfg, {
        "buckets": [[0, 1]], "labels": ["0dte"],
        "max_per": 0, "top_tradable": False,
        "price_range": 0.05, "flat_format": True,
    })


def qocr_config_earnings() -> QOCRConfig:
    """Earnings: this-week + next-week expiries. Reads configs/chain_context.yaml."""
    cfg = _load_scenario_cfg("earnings")
    return _build_config(cfg, {
        "buckets": [[0, 7], [8, 14]], "labels": ["this_week", "next_week"],
        "max_per": 50, "top_tradable": True,
        "price_range": 0.15, "flat_format": False,
    })


def qocr_config_overlay() -> QOCRConfig:
    """Overlay (hedge/profit): short+medium expiries. Reads configs/chain_context.yaml."""
    cfg = _load_scenario_cfg("overlay")
    return _build_config(cfg, {
        "buckets": [[0, 14], [15, 60]], "labels": ["short", "medium"],
        "max_per": 0, "top_tradable": True,
        "price_range": 0.10, "flat_format": False,
    })


def qocr_config_earnings_post() -> QOCRConfig:
    """Post-earnings: intraday-like flat chain, tighter range. Reads configs/chain_context.yaml."""
    cfg = _load_scenario_cfg("earnings_post")
    return _build_config(cfg, {
        "buckets": [[0, 14]], "labels": ["active"],
        "max_per": 0, "top_tradable": False,
        "price_range": 0.10, "flat_format": True,
    })


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class QOCRBucket:
    """A single cell in the (side, moneyness, expiry) grid.

    moneyness_label is one of: "itm", "atm", "otm"
    The meaning is from the contract's perspective:
      call ITM = strike below spot,  put ITM = strike above spot.
    """

    moneyness_label: str  # "itm", "atm", "otm"
    expiry_label: str
    contracts: list[dict] = field(default_factory=list)
    representative: dict | None = None
    avg_iv: float = 0.0
    contract_count: int = 0


@dataclass
class QOCRAction:
    """An abstract trading action in the QOCR action space."""

    underlying: str
    side: str          # "call" or "put"
    moneyness_bucket: str  # "itm", "atm", "otm"
    expiry_bucket: str
    qty: int


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

class QOCREncoder:
    """Encode raw option chains into the QOCR discretized action space.

    Usage::

        encoder = QOCREncoder()
        buckets = encoder.encode_chain(raw_chain, spot_price=595.0)
        prompt  = encoder.format_for_prompt(buckets, underlying="SPY",
                                            spot_price=595.0)
        action  = QOCRAction("SPY", "call", "atm", "short", 2)
        contract = encoder.decode_action(action, buckets)
    """

    _MONEYNESS_LABELS = ("itm", "atm", "otm")

    def __init__(self, config: QOCRConfig | None = None) -> None:
        self.config = config or QOCRConfig()

    # ----- classification helpers -------------------------------------------

    def _classify_moneyness(self, strike: float, spot: float, side: str) -> str:
        """Classify a contract as 'itm', 'atm', or 'otm'.

        For calls: ITM = K < S×atm_lo, ATM = in [atm_lo, atm_hi], OTM = K > S×atm_hi
        For puts:  OTM = K < S×atm_lo, ATM = in [atm_lo, atm_hi], ITM = K > S×atm_hi
        """
        if spot <= 0:
            return "atm"
        ratio = strike / spot
        if ratio < self.config.atm_lo:
            return "itm" if side == "call" else "otm"
        elif ratio <= self.config.atm_hi:
            return "atm"
        else:
            return "otm" if side == "call" else "itm"

    def _expiry_bucket(self, dte: int) -> str | None:
        """Return the expiry label for a DTE value, or None if outside range."""
        for (lo, hi), label in zip(
            self.config.expiry_buckets, self.config.expiry_labels
        ):
            if lo <= dte <= hi:
                return label
        return None

    def _moneyness_ideal(self, m_label: str) -> float:
        """Return ideal K/S ratio for representative contract selection.

        For ITM: the least ITM contract (K/S just below atm_lo for calls, just above atm_hi for puts).
        For ATM: center of ATM window.
        For OTM: the least OTM contract (symmetric to ITM).
        In practice we use atm_lo for 'itm' and atm_hi for 'otm' so the selection
        always picks the contract closest to the ATM boundary regardless of side.
        """
        if m_label == "itm":
            return self.config.atm_lo
        elif m_label == "otm":
            return self.config.atm_hi
        else:
            return (self.config.atm_lo + self.config.atm_hi) / 2.0

    def _expiry_range_str(self, label: str) -> str:
        """Human-readable expiry range string, e.g. '0-14D'."""
        for (lo, hi), lbl in zip(
            self.config.expiry_buckets, self.config.expiry_labels
        ):
            if lbl == label:
                return f"{lo}-{hi}D"
        return "?D"

    def _bucket_center_expiry(self, label: str) -> float:
        """Return the midpoint of the expiry bucket range (tau_bar)."""
        for (lo, hi), lbl in zip(
            self.config.expiry_buckets, self.config.expiry_labels
        ):
            if lbl == label:
                return (lo + hi) / 2.0
        return 7.0

    # ----- core API ---------------------------------------------------------

    def encode_chain(
        self,
        raw_chain: list[dict],
        spot_price: float,
    ) -> dict[str, QOCRBucket]:
        """Map a raw option chain into (side × moneyness × expiry) buckets.

        All contracts within an expiry bucket are included regardless of how
        deep ITM or OTM they are.  Moneyness (itm/atm/otm) is computed
        dynamically from K vs spot.

        Returns a dict keyed by ``"call:<m_label>:<e_label>"`` or
        ``"put:<m_label>:<e_label>"`` where m_label ∈ {itm, atm, otm}.
        """
        # Initialize all possible buckets
        buckets: dict[str, QOCRBucket] = {}
        for side in ("call", "put"):
            for m_label in self._MONEYNESS_LABELS:
                for e_label in self.config.expiry_labels:
                    key = f"{side}:{m_label}:{e_label}"
                    buckets[key] = QOCRBucket(
                        moneyness_label=m_label,
                        expiry_label=e_label,
                    )

        # Pre-filter by price range (symmetric or asymmetric per side)
        otm_r = self.config.otm_range
        itm_r = self.config.itm_range
        pr    = self.config.price_range
        if (otm_r > 0 or itm_r > 0) and spot_price > 0:
            # Asymmetric: calls [spot*(1-itm), spot*(1+otm)], puts [spot*(1-otm), spot*(1+itm)]
            filtered: list[dict] = []
            for c in raw_chain:
                k = c.get("strike") or 0
                if k <= 0:
                    continue
                if _is_call(c):
                    lo_c = spot_price * (1.0 - itm_r) if itm_r > 0 else 0
                    hi_c = spot_price * (1.0 + otm_r) if otm_r > 0 else float("inf")
                    if lo_c <= k <= hi_c:
                        filtered.append(c)
                else:
                    lo_p = spot_price * (1.0 - otm_r) if otm_r > 0 else 0
                    hi_p = spot_price * (1.0 + itm_r) if itm_r > 0 else float("inf")
                    if lo_p <= k <= hi_p:
                        filtered.append(c)
            working_chain = filtered
        elif pr > 0 and spot_price > 0:
            lo_k = spot_price * (1.0 - pr)
            hi_k = spot_price * (1.0 + pr)
            working_chain = [c for c in raw_chain if lo_k <= (c.get("strike") or 0) <= hi_k]
        else:
            working_chain = raw_chain

        # Assign contracts to buckets
        for contract in working_chain:
            strike = contract.get("strike")
            dte = contract.get("dte")
            if strike is None or dte is None:
                continue

            e_label = self._expiry_bucket(dte)
            if e_label is None:
                continue  # outside all expiry buckets → skip

            side = "call" if _is_call(contract) else "put"
            m_label = self._classify_moneyness(strike, spot_price, side)
            key = f"{side}:{m_label}:{e_label}"
            buckets[key].contracts.append(contract)

        # Compute stats and representative for each non-empty bucket
        for bucket in buckets.values():
            bucket.contract_count = len(bucket.contracts)
            if bucket.contracts:
                ivs = [
                    c.get("iv", 0)
                    for c in bucket.contracts
                    if c.get("iv") is not None and c.get("iv", 0) > 0
                ]
                bucket.avg_iv = sum(ivs) / len(ivs) if ivs else 0.0
                bucket.representative = self.select_representative(
                    bucket, spot_price
                )

        return buckets

    def select_representative(
        self, bucket: QOCRBucket, spot_price: float
    ) -> dict | None:
        """Pick the contract closest to the ATM boundary in this bucket.

        Score = |K/S - m_ideal| + lambda × |DTE - tau_bar|
        where m_ideal = atm_lo for ITM, atm_hi for OTM, midpoint for ATM.
        This always picks the least ITM / least OTM / most-ATM contract.
        """
        if not bucket.contracts:
            return None

        m_ideal = self._moneyness_ideal(bucket.moneyness_label)
        e_center = self._bucket_center_expiry(bucket.expiry_label)
        lam = 0.01

        best: dict | None = None
        best_score = float("inf")

        for c in bucket.contracts:
            strike = c.get("strike", 0)
            dte = c.get("dte", 0)
            moneyness = strike / spot_price if spot_price > 0 else 1.0
            score = abs(moneyness - m_ideal) + lam * abs(dte - e_center)
            if score < best_score:
                best_score = score
                best = c

        return best

    def decode_action(
        self,
        action: QOCRAction,
        chain_buckets: dict[str, QOCRBucket],
    ) -> dict | None:
        """Map an abstract QOCR action to a concrete contract dict."""
        key = f"{action.side}:{action.moneyness_bucket}:{action.expiry_bucket}"
        bucket = chain_buckets.get(key)
        if bucket is None or bucket.representative is None:
            return None
        return bucket.representative

    # ----- prompt formatting ------------------------------------------------

    def format_for_prompt(
        self,
        buckets: dict[str, QOCRBucket],
        underlying: str = "",
        spot_price: float = 0.0,
    ) -> str:
        """Format the QOCR bucket grid as prompt text.

        When flat_chain_format=True (e.g. 0DTE):
          Flat sorted list per DTE — no ITM/ATM/OTM grouping.
          Header shows ATM window for context only.

        Otherwise (QOCR mode):
          Layout: CALLS / PUTS → expiry bucket → DTE=N → ITM / ATM / OTM
        """
        # Collect all contracts for emptiness check
        total_contracts = sum(b.contract_count for b in buckets.values())
        if total_contracts == 0:
            return "== Option Chain ==\n(no contracts in chain)"

        def _fmt_contract(c: dict) -> str:
            delta_s  = f"Δ={c['delta']:.2f}" if c.get("delta") is not None else ""
            iv_s     = f"IV={c['iv']:.1%}"   if c.get("iv")    is not None else ""
            vol      = c.get("volume", 0)
            vol_s    = f"vol={vol/1000:.0f}K" if vol >= 1000 else f"vol={vol}"
            stale_m  = c.get("stale_minutes", 0) or 0
            stale_s  = f"stale:{stale_m}m" if stale_m > 0 else ""
            px       = c.get("close", 0)
            parts    = [f"  {c['ticker']}  K=${c.get('strike',0):.0f}  px=${px:.2f}"]
            if iv_s:    parts.append(iv_s)
            if delta_s: parts.append(delta_s)
            parts.append(vol_s)
            if stale_s: parts.append(stale_s)
            return "  ".join(parts)

        if self.config.flat_chain_format:
            return self._format_flat(buckets, underlying, spot_price, _fmt_contract)
        return self._format_qocr(buckets, underlying, spot_price, _fmt_contract)

    def _format_flat(
        self,
        buckets: dict[str, QOCRBucket],
        underlying: str,
        spot_price: float,
        _fmt_contract,
    ) -> str:
        """Flat format: calls then puts, sorted by strike, no ITM/ATM/OTM grouping.

        Groups by DTE within each side. ATM window shown as reference only.
        Used for 0DTE intraday where the LLM just needs a clean sorted list.
        """
        lines: list[str] = []
        header = "== Option Chain"
        if underlying:
            header += f" ({underlying} @ ${spot_price:.2f})" if spot_price > 0 else f" ({underlying})"
        header += " =="
        lines.append(header)

        if spot_price > 0:
            atm_lo_px = spot_price * self.config.atm_lo
            atm_hi_px = spot_price * self.config.atm_hi
            lines.append(
                f"Spot: ${spot_price:.2f}  ATM: ${atm_lo_px:.2f}–${atm_hi_px:.2f}"
            )

        for side in ("call", "put"):
            side_label = "CALLS" if side == "call" else "PUTS"

            # Collect all contracts for this side across all buckets
            all_contracts: list[dict] = []
            for m_label in self._MONEYNESS_LABELS:
                for e_label in self.config.expiry_labels:
                    key = f"{side}:{m_label}:{e_label}"
                    b = buckets.get(key)
                    if b:
                        all_contracts.extend(b.contracts)

            if not all_contracts:
                lines.append(f"\n{side_label}: (none)")
                continue

            # Group by DTE, sort strikes within each DTE
            by_dte: dict[int, list[dict]] = defaultdict(list)
            for c in all_contracts:
                by_dte[c.get("dte", 9999)].append(c)

            lines.append(f"\n{side_label}:")
            for dte in sorted(by_dte.keys()):
                dte_str = f"DTE={dte}" if dte != 9999 else "DTE=?"
                group = sorted(by_dte[dte], key=lambda c: c.get("strike") or 0)
                lines.append(f"  {dte_str} ({len(group)} contracts):")
                for c in group:
                    lines.append(_fmt_contract(c))

        return "\n".join(lines)

    def _format_qocr(
        self,
        buckets: dict[str, QOCRBucket],
        underlying: str,
        spot_price: float,
        _fmt_contract,
    ) -> str:
        """QOCR grouped format: CALLS/PUTS → expiry bucket → DTE=N → ITM/ATM/OTM."""
        lines: list[str] = []
        header = "== Tradable Action Space (QOCR)"
        if underlying:
            header += f" ({underlying}"
            if spot_price > 0:
                header += f" @ ${spot_price:.2f}"
            header += ")"
        header += " =="
        lines.append(header)

        if spot_price > 0:
            atm_lo_px = spot_price * self.config.atm_lo
            atm_hi_px = spot_price * self.config.atm_hi
            lines.append(
                f"ATM window: ${atm_lo_px:.2f} – ${atm_hi_px:.2f}  "
                f"(K/S in [{self.config.atm_lo:.2f}, {self.config.atm_hi:.2f}])"
            )

        for side in ("call", "put"):
            side_label = "CALLS" if side == "call" else "PUTS"
            lines.append(f"\n{side_label}:")

            for e_label in self.config.expiry_labels:
                e_range = self._expiry_range_str(e_label)

                all_tagged: list[tuple[str, dict]] = []
                for m_label in self._MONEYNESS_LABELS:
                    key = f"{side}:{m_label}:{e_label}"
                    b = buckets.get(key)
                    if b:
                        for c in b.contracts:
                            all_tagged.append((m_label, c))

                if not all_tagged:
                    lines.append(f"  {e_label.upper()} ({e_range}): (none)")
                    continue

                lines.append(f"  {e_label.upper()} ({e_range}):")

                by_dte: dict[int, dict[str, list[dict]]] = defaultdict(
                    lambda: {"itm": [], "atm": [], "otm": []}
                )
                for m_label, c in all_tagged:
                    dte = c.get("dte", 9999)
                    by_dte[dte][m_label].append(c)

                for dte in sorted(by_dte.keys()):
                    dte_str = f"DTE={dte}" if dte != 9999 else "DTE=?"
                    lines.append(f"    {dte_str}:")
                    for m_label in self._MONEYNESS_LABELS:
                        group = sorted(
                            by_dte[dte][m_label],
                            key=lambda c: c.get("strike") or 0,
                        )
                        if not group:
                            continue
                        total = len(group)
                        cap = self.config.max_contracts_per_bucket
                        shown = group[:cap] if cap and total > cap else group
                        lines.append(f"      {m_label.upper()} ({total}):")
                        for c in shown:
                            lines.append(_fmt_contract(c))
                        if cap and total > cap:
                            lines.append(f"        ... ({total - cap} more)")

        qty_str = ", ".join(str(q) for q in self.config.qty_levels)
        lines.append(f"\nAvailable qty levels: {qty_str} contracts")

        return "\n".join(lines)
