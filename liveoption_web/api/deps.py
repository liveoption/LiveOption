"""FastAPI dependencies for the LiveOption Web Job Server.

The :func:`verify_internal` dependency enforces the shared
``X-Internal-Secret`` header on privileged endpoints and extracts
actor identity headers (``X-Actor-User`` / ``X-Actor-Email``) into an
:class:`Actor` dataclass for downstream handlers.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from liveoption_web.config import Settings, get_settings


@dataclass
class Actor:
    """Identity extracted from request headers.

    Both fields may be ``None`` when upstream callers (e.g. internal
    engine ingest) do not attach actor headers.
    """

    user_id: str | None
    email: str | None


async def verify_internal(
    x_internal_secret: str = Header(default=""),
    x_actor_user: str | None = Header(default=None),
    x_actor_email: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Actor:
    """Validate the shared secret and return the request :class:`Actor`.

    A missing or wrong ``X-Internal-Secret`` yields HTTP 403 (not 401);
    the server treats this as an authorization failure against a
    pre-shared key rather than a user-authentication challenge.

    The comparison uses :func:`hmac.compare_digest` for constant-time
    safety against timing attacks.
    """

    expected = settings.internal_secret.get_secret_value()
    if not hmac.compare_digest(x_internal_secret or "", expected):
        raise HTTPException(status_code=403, detail="invalid internal secret")
    return Actor(user_id=x_actor_user, email=x_actor_email)
