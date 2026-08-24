import base64
import hashlib
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import ASGIApp

from app.core.config import Settings
from app.core.database import idempotency_session_factory
from app.core.safe_logging import safe_exception_context
from app.models import AuthSession, IdempotencyRecord
from app.services.auth import csrf_token_for_session

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_REPLAYED_HEADERS = frozenset({"cache-control", "content-disposition", "content-type", "location"})
logger = logging.getLogger("cassist.idempotency")


class _BodyIteratorResponse(Protocol):
    body_iterator: AsyncIterator[bytes]


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status_code)


async def _read_bounded_body(request: Request, limit: int) -> bytes | None:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > limit:
                return None
        except ValueError:
            return None

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            return None
        body.extend(chunk)
    bounded_body = bytes(body)
    request._body = bounded_body  # Starlette replays this body to downstream handlers.
    return bounded_body


async def _active_session_hash(
    session_hash: str,
    now: datetime,
) -> str | None:
    async with idempotency_session_factory() as session:
        active_session_hash = await session.scalar(
            select(AuthSession.token_hash).where(
                AuthSession.token_hash == session_hash,
                AuthSession.revoked_at.is_(None),
                AuthSession.idle_expires_at > now,
                AuthSession.absolute_expires_at > now,
            )
        )
    return active_session_hash


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        raw_key = request.headers.get("idempotency-key")
        if request.method not in _MUTATION_METHODS or raw_key is None:
            return await call_next(request)
        if not 8 <= len(raw_key) <= 200 or any(
            ord(character) < 33 or ord(character) > 126 for character in raw_key
        ):
            return await call_next(request)

        session_token = request.cookies.get(self.settings.auth_session_cookie_name)
        if not session_token:
            return await call_next(request)
        now = datetime.now(UTC)
        session_hash = hashlib.sha256(session_token.encode()).hexdigest()
        if await _active_session_hash(session_hash, now) is None:
            return await call_next(request)
        csrf_token = request.headers.get("x-csrf-token")
        if (
            request.headers.get("origin") not in self.settings.cors_origins
            or csrf_token is None
            or not secrets.compare_digest(csrf_token, csrf_token_for_session(session_token))
        ):
            return await call_next(request)
        body = await _read_bounded_body(request, self.settings.idempotency_max_request_bytes)
        if body is None:
            return _error("Request body is too large for idempotent processing", 413)
        request_target = request.url.path
        if request.url.query:
            request_target = f"{request_target}?{request.url.query}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        request_hash = hashlib.sha256(
            request.method.encode() + b"\0" + request_target.encode() + b"\0" + body
        ).hexdigest()

        async with idempotency_session_factory() as session:
            await session.execute(
                delete(IdempotencyRecord).where(IdempotencyRecord.expires_at <= now)
            )
            record = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.session_token_hash == session_hash,
                    IdempotencyRecord.request_method == request.method,
                    IdempotencyRecord.request_path == request_target,
                    IdempotencyRecord.idempotency_key_hash == key_hash,
                )
            )
            if record is not None:
                if not record.completed and record.created_at <= now - timedelta(
                    seconds=self.settings.idempotency_in_progress_seconds
                ):
                    await session.delete(record)
                    await session.commit()
                    record = None
            if record is not None:
                await session.commit()
                if record.request_hash != request_hash:
                    return _error("Idempotency-Key was reused with a different request", 409)
                if not record.completed:
                    return _error("An identical request is already in progress", 409)
                return Response(
                    content=base64.b64decode(record.response_body or ""),
                    status_code=record.response_status or 200,
                    headers=record.response_headers or {},
                )

            record = IdempotencyRecord(
                session_token_hash=session_hash,
                request_method=request.method,
                request_path=request_target,
                idempotency_key_hash=key_hash,
                request_hash=request_hash,
                expires_at=now + timedelta(seconds=self.settings.idempotency_ttl_seconds),
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return _error("An identical request is already in progress", 409)

        try:
            response = await call_next(request)
        except Exception:
            async with idempotency_session_factory() as session:
                await session.execute(
                    delete(IdempotencyRecord).where(IdempotencyRecord.id == record.id)
                )
                await session.commit()
            raise

        replayed_headers = {
            name: value
            for name, value in response.headers.items()
            if name.casefold() in _REPLAYED_HEADERS
        }

        async def bounded_response_body() -> AsyncIterator[bytes]:
            response_body = bytearray()
            replayable = response.status_code < 500
            completed = False
            try:
                body_iterator = cast(_BodyIteratorResponse, response).body_iterator
                async for chunk in body_iterator:
                    if replayable and (
                        len(response_body) + len(chunk)
                        <= self.settings.idempotency_max_response_bytes
                    ):
                        response_body.extend(chunk)
                    else:
                        replayable = False
                    yield chunk
                completed = True
            finally:
                try:
                    async with idempotency_session_factory() as session:
                        stored = await session.get(
                            IdempotencyRecord,
                            record.id,
                            with_for_update=True,
                        )
                        if stored is not None:
                            if not completed or not replayable:
                                await session.delete(stored)
                            else:
                                stored.completed = True
                                stored.response_status = response.status_code
                                stored.response_headers = replayed_headers
                                stored.response_body = base64.b64encode(response_body).decode()
                            await session.commit()
                except Exception as exc:
                    logger.error(
                        "Idempotency response finalization failed",
                        extra={
                            "idempotency_record_id": str(record.id),
                            **safe_exception_context(exc),
                        },
                    )

        streamed = StreamingResponse(
            bounded_response_body(),
            status_code=response.status_code,
            background=response.background,
        )
        streamed.raw_headers = response.raw_headers
        return streamed
