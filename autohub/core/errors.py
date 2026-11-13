"""Application error types and the single error-response shape.

Services raise :class:`AppError` subclasses; they never import FastAPI.
``register_exception_handlers`` turns them (and framework errors) into::

    {"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: Any = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers = headers


class ConfigurationError(AppError):
    status_code = 503
    code = "upstream_unavailable"


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthenticatedError(AppError):
    status_code = 401
    code = "unauthenticated"

    def __init__(self, message: str = "Could not validate credentials.") -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_error"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many requests. Please retry later.",
            headers={"Retry-After": str(max(1, retry_after_seconds))},
        )


class UpstreamError(AppError):
    """An external provider (LLM, image search, brand site) failed."""

    status_code = 502
    code = "upstream_error"


class UpstreamUnavailableError(AppError):
    """An external provider is out of quota or not configured."""

    status_code = 503
    code = "upstream_unavailable"


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}

# Legacy V1 routes keep FastAPI's original ``{"detail": ...}`` body.
LEGACY_PREFIXES: tuple[str, ...] = ("/login", "/signup", "/catalog", "/news", "/pipeline", "/status")


def _is_legacy(request: Request) -> bool:
    path = request.url.path
    return not path.startswith("/api/") and any(path.startswith(p) for p in LEGACY_PREFIXES)


def _body(request: Request, code: str, message: str, details: Any = None) -> dict[str, Any]:
    if _is_legacy(request):
        return {"detail": details if (code == "validation_error" and details) else message}
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("app_error code=%s message=%s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(request, exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(request, code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_body(request, "validation_error", "Request validation failed.", details),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error path=%s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_body(request, "internal_error", "An unexpected error occurred."),
        )
