from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.core.config import Settings
from app.services.identity_provider import (
    AUTH0_GOOGLE_CONNECTION,
    RETURN_TO_SESSION_KEY,
    Auth0IdentityProvider,
    IdentityProviderError,
)


def _provider() -> Auth0IdentityProvider:
    return Auth0IdentityProvider(
        Settings(
            app_env="test",
            _env_file=None,
            auth_issuer_url="https://tenant.example/",
            auth_client_id="client-id",
            auth_client_secret="client-secret",
            auth_state_secret="x" * 32,
        )
    )


def _request(session: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "session": session or {},
        }
    )


@pytest.mark.asyncio
async def test_login_redirect_forces_the_google_social_connection() -> None:
    provider = _provider()
    redirect = RedirectResponse("https://accounts.google.com/")
    provider.client = SimpleNamespace(authorize_redirect=AsyncMock(return_value=redirect))
    request = _request()

    response = await provider.start_login(
        request,
        "http://localhost:8000/api/v1/auth/callback",
        "/documents",
    )

    assert response is redirect
    assert request.session[RETURN_TO_SESSION_KEY] == "/documents"
    provider.client.authorize_redirect.assert_awaited_once_with(
        request,
        "http://localhost:8000/api/v1/auth/callback",
        connection=AUTH0_GOOGLE_CONNECTION,
    )


@pytest.mark.asyncio
async def test_callback_accepts_only_a_verified_google_subject() -> None:
    provider = _provider()
    valid_token = {
        "id_token": "opaque",
        "userinfo": {
            "iss": "https://tenant.example/",
            "sub": "google-oauth2|123",
            "email": "owner@example.test",
            "email_verified": True,
            "name": "Allowed User",
        },
    }
    provider.client = SimpleNamespace(authorize_access_token=AsyncMock(return_value=valid_token))
    request = _request({RETURN_TO_SESSION_KEY: "/documents"})

    identity = await provider.complete_login(request)

    assert identity.subject == "google-oauth2|123"
    assert identity.return_to == "/documents"

    non_google_token = {
        **valid_token,
        "userinfo": {**valid_token["userinfo"], "sub": "auth0|123"},
    }
    provider.client = SimpleNamespace(
        authorize_access_token=AsyncMock(return_value=non_google_token)
    )
    with pytest.raises(IdentityProviderError, match="incomplete"):
        await provider.complete_login(_request())
