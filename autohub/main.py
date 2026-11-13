"""AutoHub API application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from autohub import __version__
from autohub.api import legacy
from autohub.api.schemas import HealthOut
from autohub.api.v1 import admin, auth, catalog, news
from autohub.core.errors import register_exception_handlers
from autohub.core.logging import configure_logging
from autohub.core.settings import get_settings
from autohub.db.session import get_db

logger = logging.getLogger("autohub.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    scheduler_started = False
    if get_settings().scheduler_enabled:
        from autohub.jobs.scheduler import start_scheduler

        start_scheduler()
        scheduler_started = True
    try:
        yield
    finally:
        if scheduler_started:
            from autohub.jobs.scheduler import stop_scheduler

            stop_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title="AutoHub API",
        version=__version__,
        description=(
            "Automotive intelligence platform. Versioned API under `/api/v1`; "
            "routes marked *deprecated* are V1 compatibility routes."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request id=%s method=%s path=%s status=%s ms=%.1f",
            request_id, request.method, request.url.path, response.status_code, elapsed_ms,
        )  # fmt: skip
        return response

    register_exception_handlers(app)

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(auth.router)
    v1.include_router(catalog.router)
    v1.include_router(news.router)
    v1.include_router(admin.router)
    _include_optional_routers(v1)
    app.include_router(v1)
    app.include_router(legacy.router)

    @app.get("/", tags=["meta"])
    def home() -> dict[str, str]:
        return {"message": "Welcome to AutoHub", "version": __version__, "docs": "/docs"}

    @app.get("/health", response_model=HealthOut, tags=["meta"])
    def health(response: Response, db: Session = Depends(get_db)) -> HealthOut:
        try:
            db.execute(text("select 1"))
            database = "ok"
        except Exception:
            logger.exception("health_check_database_failed")
            database = "unavailable"
            response.status_code = 503
        return HealthOut(
            status="ok" if database == "ok" else "degraded", database=database, version=__version__
        )

    return app


def _include_optional_routers(v1: APIRouter) -> None:
    """Routers added by later milestones (search, assistant, pipeline)."""
    from autohub.api.v1 import optional_routers

    for router in optional_routers():
        v1.include_router(router)


app = create_app()
