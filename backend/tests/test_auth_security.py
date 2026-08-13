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
    is_allowed_user_email,
    validate_return_to,
    verify_csrf,
    verify_request_origin,
)


def test_user_email_allowlist_is_exact_and_case_insensitive() -> None:
    settings = Settings(app_env="development", _env_file=None)
    assert is_allowed_user_email("owner@example.test", settings)
    assert is_allowed_user_email("REVIEWER@EXAMPLE.TEST", settings)
    assert not is_allowed_user_email("someone@example.test", settings)
    assert not is_allowed_user_email("owner+test@example.test", settings)

    with pytest.raises(ValueError, match="AUTH_ALLOWED_EMAILS"):
        Settings(
            app_env="development",
            _env_file=None,
            auth_allowed_emails={"someone@example.test"},
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
    settings = Settings(app_env="test", _env_file=None)
    current_auth = CurrentAuth(
        session_id=uuid4(),
        user=User(
            id=uuid4(),
            external_auth_id="test",
            email="owner@example.test",
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
    settings = Settings(app_env="test", _env_file=None)
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
        Settings(app_env="production", _env_file=None)

    production = Settings(
        app_env="production",
        _env_file=None,
        auth_issuer_url="https://tenant.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
        edge_proxy_secret="e" * 32,
        openai_api_key="openai-key",
        r2_endpoint_url="https://account.r2.cloudflarestorage.com",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        frontend_url="https://cassist.pages.dev",
        cors_origins=["https://cassist.pages.dev"],
        auth_callback_url="https://cassist.pages.dev/api/v1/auth/callback",
        auth_post_logout_redirect_url="https://cassist.pages.dev",
    )
    assert production.auth_session_cookie_name == "__Host-cassist_session"
    assert production.auth_cookie_secure is True


def test_production_runtime_and_pages_proxy_configuration_fails_closed() -> None:
    common = {
        "app_env": "production",
        "_env_file": None,
        "auth_issuer_url": "https://tenant.example/",
        "auth_client_id": "client-id",
        "auth_client_secret": "client-secret",
        "auth_state_secret": "x" * 32,
        "openai_api_key": "openai-key",
        "r2_endpoint_url": "https://account.r2.cloudflarestorage.com",
        "r2_access_key_id": "access-key",
        "r2_secret_access_key": "secret-key",
        "frontend_url": "https://cassist.pages.dev",
        "cors_origins": ["https://cassist.pages.dev"],
        "auth_callback_url": "https://cassist.pages.dev/api/v1/auth/callback",
        "auth_post_logout_redirect_url": "https://cassist.pages.dev",
    }
    with pytest.raises(ValueError, match="EDGE_PROXY_SECRET"):
        Settings(**common)
    with pytest.raises(ValueError, match="Pages API proxy"):
        Settings(
            **{
                **common,
                "edge_proxy_secret": "e" * 32,
                "auth_callback_url": "https://nas.example.ts.net/api/v1/auth/callback",
            }
        )
