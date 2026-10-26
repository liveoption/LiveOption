"""RFC 7807 ``application/problem+json`` helpers.

Endpoints raise :class:`fastapi.HTTPException` via
:func:`problem_response` so error payloads carry the standard
``type``/``title``/``status``/``detail``/``instance`` fields.
"""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel


class Problem(BaseModel):
    """RFC 7807 problem details model."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


def problem_response(
    status: int,
    type_: str,
    title: str,
    detail: str | None = None,
    instance: str | None = None,
) -> HTTPException:
    """Build an :class:`HTTPException` with an RFC 7807 ``detail`` body.

    Callers typically ``raise problem_response(...)``; FastAPI serializes
    the ``detail`` dict into the response body.
    """

    return HTTPException(
        status_code=status,
        detail={
            "type": type_,
            "title": title,
            "status": status,
            "detail": detail,
            "instance": instance,
        },
    )
