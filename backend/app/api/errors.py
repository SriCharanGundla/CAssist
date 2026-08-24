import logging
import re
from collections.abc import Mapping
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.safe_logging import safe_exception_context

_SAFE_CODE = re.compile(r"[^A-Z0-9]+")
logger = logging.getLogger("cassist.api")


def new_request_id() -> str:
    return f"req_{uuid4().hex}"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", new_request_id())


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "details": details or {},
            }
        },
        headers={**dict(headers or {}), "X-Request-ID": request_id},
    )


def error_code_for_http_exception(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict) and isinstance(exc.detail.get("code"), str):
        return exc.detail["code"]
    if isinstance(exc.detail, str):
        candidate = _SAFE_CODE.sub("_", exc.detail.upper()).strip("_")
        if candidate:
            return candidate[:100]
    return f"HTTP_{exc.status_code}"


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return _error_response(
        request,
        status_code=exc.status_code,
        code=error_code_for_http_exception(exc),
        message=message,
        headers=exc.headers,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    issues = [
        {
            "location": [str(part) for part in error["loc"]],
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return _error_response(
        request,
        status_code=422,
        code="REQUEST_VALIDATION_FAILED",
        message="The request is invalid",
        details={"issues": issues},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled API exception",
        extra={
            "request_id": _request_id(request),
            "request_method": request.method,
            "request_path": request.url.path,
            **safe_exception_context(exc),
        },
    )
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_SERVER_ERROR",
        message="The request could not be completed",
    )
