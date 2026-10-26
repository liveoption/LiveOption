"""Canonical QFinZero service endpoints.

QFinZero now runs as a single unified hub (default ``http://127.0.0.1:19777``)
that fronts every backend service under ``/svc/<name>``:

    /svc/upq   — market data (stocks, option chains, Greeks, rates)
    /svc/pmb   — paper money broker (accounts, sessions, orders, /v1/*)
    /svc/esp   — events & news (formerly NPP; same schemas, /esp/* paths)

All LiveOption defaults resolve through this module so the stack can be
repointed with environment variables instead of code edits:

    QFZ_HUB_URL   — hub base (default http://127.0.0.1:19777)
    QFZ_UPQ_URL   — override UPQ directly (e.g. internal http://127.0.0.1:19350)
    QFZ_PMB_URL   — override PMB directly
    QFZ_ESP_URL   — override ESP directly

LiveOption consumes these services over HTTP. QFinZero is deployed and managed
as a separate project; this repository does not start or vendor its services.
"""

from __future__ import annotations

import os

_HUB_ENV = os.environ.get("QFZ_HUB_URL", "")

QFZ_HUB_URL = (_HUB_ENV or "http://127.0.0.1:19777").rstrip("/")


def service_url(name: str, configured: str = "") -> str:
    """Resolve a service base URL with a single, consistent precedence:

    1. ``QFZ_<NAME>_URL``          — explicit per-service env override
    2. ``QFZ_HUB_URL`` + /svc/name — operator moved the whole hub
    3. ``configured``              — value from services.yaml / run config
    4. built-in hub default        — http://127.0.0.1:19777/svc/<name>
    """
    env = os.environ.get(f"QFZ_{name.upper()}_URL", "")
    if env:
        return env.rstrip("/")
    if _HUB_ENV:
        return f"{QFZ_HUB_URL}/svc/{name}"
    return (configured or f"{QFZ_HUB_URL}/svc/{name}").rstrip("/")


DEFAULT_UPQ_URL = service_url("upq")
DEFAULT_PMB_URL = service_url("pmb")
DEFAULT_ESP_URL = service_url("esp")


def qfz_headers(api_key: str | None = None) -> dict[str, str]:
    """Authenticate QFinZero calls without mixing in model or Web credentials.

    Read at request time so workers inherit the operator's current key. An
    explicit empty key supports private legacy hubs without authentication.
    """
    key = (os.environ.get("QFZ_API_KEY", "") if api_key is None else api_key).strip()
    return {"Authorization": f"Bearer {key}"} if key else {}
