import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.database import idempotency_session_factory
from app.models import IdempotencyRecord

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_REPLAYED_HEADERS = frozenset({"cache-control", "content-disposition", "content-type", "location"})
logger = logging.getLogger("cassist.idempotency")


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status_code)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
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
        body = await request.body()
        request_target = request.url.path
        if request.url.query:
            request_target = f"{request_target}?{request.url.query}"
        session_hash = hashlib.sha256(session_token.encode()).hexdigest()
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        request_hash = hashlib.sha256(
            request.method.encode() + b"\0" + request_target.encode() + b"\0" + body
        ).hexdigest()
        now = datetime.now(UTC)

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
            response_body = b"".join([chunk async for chunk in response.body_iterator])
        except Exception:
            async with idempotency_session_factory() as session:
                await session.execute(
                    delete(IdempotencyRecord).where(IdempotencyRecord.id == record.id)
                )
                await session.commit()
            raise

        if response.status_code >= 500:
            async with idempotency_session_factory() as session:
                await session.execute(
                    delete(IdempotencyRecord).where(IdempotencyRecord.id == record.id)
                )
                await session.commit()
        else:
            replayed_headers = {
                name: value
                for name, value in response.headers.items()
                if name.casefold() in _REPLAYED_HEADERS
            }
            try:
                async with idempotency_session_factory() as session:
                    stored = await session.get(IdempotencyRecord, record.id, with_for_update=True)
                    if stored is not None:
                        stored.completed = True
                        stored.response_status = response.status_code
                        stored.response_headers = replayed_headers
                        stored.response_body = base64.b64encode(response_body).decode()
                        await session.commit()
            except Exception:
                logger.error("Idempotency response persistence failed")
        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
