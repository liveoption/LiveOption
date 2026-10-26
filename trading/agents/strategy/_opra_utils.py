"""OPRA contract parsing utilities shared across strategies."""

from __future__ import annotations

import re
from datetime import date, datetime


def parse_opra_expiry(contract: str) -> date | None:
    """Extract expiry date from an OPRA contract name.

    Format: O:<UNDERLYING><YYMMDD><C|P><STRIKE_8DIGITS>
    """
    m = re.search(r"O:\w+?(\d{6})[CP]\d{8}$", contract)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%y%m%d").date()
    except ValueError:
        return None


def parse_opra_dte(contract: str, current_date: str = "") -> int:
    """Compute DTE (days to expiry) from an OPRA contract and reference date.

    Returns 0 if the contract can't be parsed or the date is past.
    """
    expiry = parse_opra_expiry(contract)
    if expiry is None:
        return 0
    try:
        ref = date.fromisoformat(current_date[:10]) if current_date else date.today()
    except (ValueError, TypeError):
        ref = date.today()
    return max((expiry - ref).days, 0)


def parse_opra_strike(contract: str) -> float | None:
    """Return the dollar strike encoded in the eight-digit OPRA suffix."""
    match = re.search(r"O:[A-Z]+\d{6}[CP](\d{8})$", contract)
    return int(match.group(1)) / 1000 if match else None
