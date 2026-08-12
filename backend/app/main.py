from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.errors import (
    http_exception_handler,
    new_request_id,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.api.router import api_router
from app.core.access_logging import configure_safe_access_logging
from app.core.config import settings
from app.core.database import engine

configure_safe_access_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request.state.request_id = new_request_id()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.auth_configured:
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.auth_state_secret or "",
        session_cookie=(
            "__Host-cassist_oidc_state" if settings.auth_cookie_secure else "cassist_oidc_state"
        ),
        max_age=10 * 60,
        same_site="lax",
        https_only=settings.auth_cookie_secure,
    )

app.include_router(api_router, prefix=settings.api_v1_prefix)
