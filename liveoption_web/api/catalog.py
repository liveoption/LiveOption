"""Read-only catalog + user-capability endpoints.

Serves the web launcher's pre-render needs:
  * ``GET /v1/scenarios`` — list of launchable scenarios + allowed LLM
    models, used to render the launch form's scenario picker.
  * ``GET /v1/me`` — caller's email + ``can_launch`` flag, used to
    gate the launch CTA without exposing the full allowlist.

Auth: both endpoints require a valid ``X-Internal-Secret``
(:func:`verify_internal`). ``/v1/me`` tolerates a missing
``X-Actor-Email`` header by returning ``{"email": "", "can_launch":
false}`` — the secret has been verified, the user is simply not yet
identified, and we prefer 200 over 401 so probes from unidentified
callers still succeed.

"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from liveoption_web.api.deps import Actor, verify_internal
from liveoption_web.runs.catalog import (
    LAUNCH_ALLOWLIST,
    LLM_MODEL_CHOICES,
    SCENARIOS,
    SUPPORTED_UNDERLYINGS,
)


router = APIRouter(tags=["catalog"])


@router.get("/scenarios")
async def list_scenarios(_actor: Actor = Depends(verify_internal)) -> dict:
    """Return the catalog of launchable scenarios + permitted LLM models."""

    items = [
        {
            "id": sc.id,
            "display_name": sc.display_name,
            "description": sc.description,
            "config_path": sc.config_path,
            "overridable": sorted(sc.overridable),
            "family": sc.family.value if sc.family else None,
            "defaults": dict(sc.defaults),
        }
        for sc in SCENARIOS.values()
    ]
    return {
        "items": items,
        "llm_model_choices": sorted(LLM_MODEL_CHOICES),
        "supported_underlyings": sorted(SUPPORTED_UNDERLYINGS),
    }


@router.get("/me")
async def get_me(actor: Actor = Depends(verify_internal)) -> dict:
    """Return the caller's identity + ``can_launch`` flag.

    Clerk is currently invite-only, so any authenticated session is
    allowed to launch. The auth signal is ``X-Actor-User`` (Clerk
    userId) — NOT ``X-Actor-Email``, because Clerk doesn't put email
    into the session JWT by default. If the SSR proxy managed to
    resolve an email (via session claims or future backend lookup) we
    surface it for display; otherwise it's empty. ``can_launch`` is
    tied strictly to ``user_id`` presence.

    ``LAUNCH_ALLOWLIST`` is retained as second-gate infrastructure in
    case Clerk registration opens later.
    """

    user_id = (actor.user_id or "").strip()
    email = (actor.email or "").strip().lower()
    return {
        "user_id": user_id,
        "email": email,
        "can_launch": bool(user_id),
    }
