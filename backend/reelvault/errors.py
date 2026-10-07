"""Language-independent API error contract; legacy detail remains available."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("reelvault.errors")

STATUS_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
    507: "insufficient_storage",
}


class APIError(HTTPException):
    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        *,
        code: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code, detail, headers)
        self.code = code
        self.params = params or {}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = (
            error.code
            if isinstance(error, APIError)
            else STATUS_CODES.get(error.status_code, "request_failed")
        )
        params = error.params if isinstance(error, APIError) else {}
        return JSONResponse(
            jsonable_encoder({"detail": error.detail, "code": code, "params": params}),
            status_code=error.status_code,
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        errors = error.errors()
        return JSONResponse(
            jsonable_encoder(
                {
                    "detail": errors,
                    "code": "validation_error",
                    "params": {
                        "errors": [
                            {"loc": item["loc"], "type": item["type"], "ctx": item.get("ctx", {})}
                            for item in errors
                        ]
                    },
                }
            ),
            status_code=422,
        )

    from .locations import LocationUnavailable

    @app.exception_handler(LocationUnavailable)
    async def location_error(request: Request, error: LocationUnavailable) -> JSONResponse:
        return JSONResponse(
            {
                "detail": str(error),
                "code": "storage_location_unavailable",
                "params": {"location_id": error.location_id},
            },
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        log.error("Unhandled request error on %s", request.url.path, exc_info=error)
        return JSONResponse(
            {"detail": "Internal server error", "code": "internal_error", "params": {}},
            status_code=500,
        )
