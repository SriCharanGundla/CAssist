
import pytest
from starlette.requests import Request

from app.core.idempotency import _read_bounded_body


def _request(body: bytes, *, declared_length: str | None = None) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = []
    if declared_length is not None:
        headers.append((b"content-length", declared_length.encode()))
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": headers},
        receive,
    )


@pytest.mark.asyncio
async def test_idempotency_body_reader_rejects_declared_and_streamed_overflow() -> None:
    assert await _read_bounded_body(_request(b"small", declared_length="1000"), 10) is None
    assert await _read_bounded_body(_request(b"too-large"), 4) is None
    assert await _read_bounded_body(_request(b"small", declared_length="invalid"), 10) is None


@pytest.mark.asyncio
async def test_idempotency_body_reader_replays_a_bounded_body() -> None:
    request = _request(b"bounded")

    assert await _read_bounded_body(request, 10) == b"bounded"
    assert await request.body() == b"bounded"
