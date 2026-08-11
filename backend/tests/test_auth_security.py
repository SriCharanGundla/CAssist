from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.config import Settings
from app.models import User
from app.services.auth import (
    CsrfValidationError,
    CurrentAuth,
    create_opaque_token,
    hash_token,
    validate_return_to,
    verify_csrf,
    verify_request_origin,
)


def make_request(*, origin: str, csrf_header: str) -> Request:
    headers = [
        (b"origin", origin.encode()),
        (b"x-csrf-token", csrf_header.encode()),
    ]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def test_return_to_accepts_only_relative_application_paths() -> None:
    assert validate_return_to(None) == "/"
    assert validate_return_to("/documents/123?tab=review") == "/documents/123?tab=review"

    for unsafe_value in (
        "https://attacker.example",
        "//attacker.example",
        "/\\attacker.example",
        "/safe\r\nLocation: https://attacker.example",
    ):
        with pytest.raises(ValueError):
            validate_return_to(unsafe_value)


def test_opaque_tokens_are_random_and_hash_to_fixed_length() -> None:
    first = create_opaque_token()
    second = create_opaque_token()

    assert first != second
    assert len(hash_token(first)) == 64
    assert hash_token(first) == hash_token(first)


def test_csrf_requires_origin_header_and_stored_hash() -> None:
    token = create_opaque_token()
    settings = Settings(app_env="test")
    current_auth = CurrentAuth(
        session_id=uuid4(),
        user=User(
            id=uuid4(),
            external_auth_id="test",
            email="user@example.com",
        ),
        csrf_token_hash=hash_token(token),
    )

    verify_csrf(
        make_request(
            origin="http://localhost:5173",
            csrf_header=token,
        ),
        current_auth,
        settings,
    )

    with pytest.raises(CsrfValidationError):
        verify_csrf(
            make_request(
                origin="https://attacker.example",
                csrf_header=token,
            ),
            current_auth,
            settings,
        )


def test_csrf_bootstrap_requires_the_frontend_origin() -> None:
    settings = Settings(app_env="test")
    verify_request_origin(
        make_request(origin="http://localhost:5173", csrf_header="unused"),
        settings,
    )

    with pytest.raises(CsrfValidationError):
        verify_request_origin(
            make_request(origin="https://attacker.example", csrf_header="unused"),
            settings,
        )


def test_production_authentication_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="authentication settings are missing"):
        Settings(app_env="production")

    production = Settings(
        app_env="production",
        auth_issuer_url="https://tenant.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
    )
    assert production.auth_session_cookie_name == "__Host-cassist_session"
    assert production.auth_cookie_secure is True
