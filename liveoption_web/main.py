"""FastAPI application factory for the LiveOption Job Server.

``create_app()`` is an async factory so startup can ``await`` the SQLite
migration runner before any request is served.  Shared state — the
in-memory :class:`Pubsub` and resolved :class:`Settings` — is attached to
``app.state`` for downstream dependencies.

A global exception handler translates :class:`HTTPException` instances
whose ``detail`` is a dict (as produced by
:func:`liveoption_web.api.problem.problem_response`) into RFC 7807
``application/problem+json`` responses.
"""

from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("liveoption_web.access")

from liveoption_web.api import (
    audit,
    catalog,
    checkpoints,
    decisions,
    forks,
    health,
    ingest,
    questions,
    runs,
    tasks,
)
from liveoption_web.config import get_settings
from liveoption_web.db.migrations import apply_migrations
from liveoption_web.events.pubsub import Pubsub
from liveoption_web.events.reconcile import reconcile_on_startup


async def create_app() -> FastAPI:
    """Build and initialize the FastAPI application.

    Applies pending migrations up front (so ``/healthz`` can immediately
    report the schema version) and wires a lifespan that keeps the
    :class:`Pubsub` alive for the process lifetime.
    """
    settings = get_settings()

    # Apply migrations before the app starts accepting traffic.  Done
    # before constructing the app so creation-time failures surface as
    # ordinary exceptions rather than opaque lifespan errors.
    await apply_migrations(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Re-run migrations on lifespan enter — idempotent and cheap, but
        # guarantees correctness if create_app() is used without the
        # pre-await above (e.g. tests that tweak env mid-run).
        await apply_migrations(settings.db_path)
        # Reconcile any non-terminal rows left behind by a previous
        # server lifetime: dead pids → orphaned, partial checkpoints →
        # failed, streaming questions → synthesized, JSONL backfill.
        # Spec 3.6 / 4.4 — this is the restart-recovery entrypoint.
        await reconcile_on_startup(settings)
        yield

    app = FastAPI(title="LiveOption Web API", version="0.1.0", lifespan=lifespan)

    # Shared in-process state.
    app.state.pubsub = Pubsub()
    app.state.settings = settings

    # Trace-aware access middleware. Echoes / generates X-Trace-Id and emits
    # a single structured INFO line per request so we can correlate with the
    # frontend `[backendFetch]` log entries via the trace id.
    @app.middleware("http")
    async def _trace_log(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or secrets.token_hex(4)
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.exception(
                "trace=%s %s %s -> EXCEPTION (%dms)",
                trace_id, request.method, request.url.path, elapsed_ms,
            )
            raise
        elapsed_ms = int((time.monotonic() - start) * 1000)
        response.headers["X-Trace-Id"] = trace_id
        # Skip the noisy ingest hot path; everything else gets one INFO line.
        if not request.url.path.startswith("/_ingest/"):
            logger.info(
                "trace=%s %s %s -> %d (%dms)",
                trace_id, request.method, request.url.path,
                response.status_code, elapsed_ms,
            )
        return response

    # RFC 7807 handler: only translate HTTPExceptions whose detail is a
    # dict (the shape produced by problem_response).  Plain-string detail
    # falls through to FastAPI's default handling.
    @app.exception_handler(HTTPException)
    async def _problem_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
                media_type="application/problem+json",
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    # Routers.
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(audit.router, prefix="/v1")
    app.include_router(checkpoints.router, prefix="/v1")
    app.include_router(runs.router, prefix="/v1")
    app.include_router(tasks.router, prefix="/v1")
    app.include_router(tasks.settings_router, prefix="/v1")
    app.include_router(catalog.router, prefix="/v1")
    app.include_router(forks.router, prefix="/v1")
    app.include_router(questions.router, prefix="/v1")
    app.include_router(decisions.router)

    return app


def main() -> None:
    """Entry for ``python -m liveoption_web``."""
    import asyncio

    import uvicorn

    # Ensure the `liveoption_web.*` loggers (access middleware, etc.) emit
    # at INFO. Uvicorn's default dictConfig only configures its own
    # `uvicorn`/`uvicorn.access` loggers and leaves the root logger silent,
    # so without this our trace-id INFO lines would be swallowed.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    app = asyncio.run(create_app())
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()
