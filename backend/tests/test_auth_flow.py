from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.api.dependencies import (
    get_app_settings,
    get_database_session,
    get_identity_provider,
)
from app.core.config import Settings
from app.core.database import engine
from app.main import app
from app.models import AuthSession
from app.services.auth import hash_token
from app.services.identity_provider import VerifiedIdentity
from app.services.session_cleanup import cleanup_expired_sessions


class FakeIdentityProvider:
    async def start_login(
        self,
        request: Request,
        redirect_uri: str,
        return_to: str,
    ) -> Response:
        return RedirectResponse(f"https://identity.example/login?return_to={return_to}")

    async def complete_login(self, request: Request) -> VerifiedIdentity:
        return VerifiedIdentity(
            issuer="https://identity.example/",
            subject="user-123",
            email="auth-flow@example.test",
            display_name="Example User",
            return_to="/documents",
        )

    def logout_url(self, return_to: str) -> str:
        return f"https://identity.example/logout?return_to={return_to}"


class RestrictedIdentityProvider(FakeIdentityProvider):
    async def complete_login(self, request: Request) -> VerifiedIdentity:
        return VerifiedIdentity(
            issuer="https://identity.example/",
            subject="user-456",
            email="someone-else@example.test",
            display_name="Restricted User",
            return_to="/",
        )


@pytest_asyncio.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    try:
        connection = await engine.connect()
    except OSError:
        pytest.skip("Local PostgreSQL is unavailable")

    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.mark.asyncio
async def test_callback_me_and_csrf_protected_logout(
    database_session: AsyncSession,
) -> None:
    test_settings = Settings(
        app_env="test",
        _env_file=None,
        auth_issuer_url="https://identity.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
        auth_allowed_emails={"auth-flow@example.test"},
    )

    async def override_database_session() -> AsyncIterator[AsyncSession]:
        yield database_session

    app.dependency_overrides[get_database_session] = override_database_session
    app.dependency_overrides[get_app_settings] = lambda: test_settings
    app.dependency_overrides[get_identity_provider] = FakeIdentityProvider
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            callback = await client.get("/api/v1/auth/callback")
            assert callback.status_code == 303
            assert callback.headers["location"] == "http://localhost:5173/documents"
            first_session_token = client.cookies.get("cassist_session")
            assert first_session_token

            reauthenticated = await client.get("/api/v1/auth/callback")
            assert reauthenticated.status_code == 303
            assert client.cookies.get("cassist_session") != first_session_token
            first_session = await database_session.scalar(
                select(AuthSession).where(
                    AuthSession.token_hash == hash_token(first_session_token)
                )
            )
            assert first_session is not None
            assert first_session.revoked_at is not None
            assert await cleanup_expired_sessions(database_session) >= 1
            assert (
                await database_session.scalar(
                    select(AuthSession).where(
                        AuthSession.token_hash == hash_token(first_session_token)
                    )
                )
                is None
            )

            me = await client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json() == {
                "user": {
                    "id": me.json()["user"]["id"],
                    "email": "auth-flow@example.test",
                    "display_name": "Example User",
                },
                "workspaces": [
                    {
                        "id": me.json()["workspaces"][0]["id"],
                        "name": "My workspace",
                        "role": "owner",
                    }
                ],
            }

            rejected_csrf = await client.get("/api/v1/auth/csrf")
            assert rejected_csrf.status_code == 403

            csrf = await client.get(
                "/api/v1/auth/csrf",
                headers={"Origin": "http://localhost:5173"},
            )
            assert csrf.status_code == 200
            assert csrf.headers["cache-control"] == "no-store"
            csrf_token = csrf.json()["csrf_token"]

            rejected_logout = await client.post("/api/v1/auth/logout")
            assert rejected_logout.status_code == 403

            logout = await client.post(
                "/api/v1/auth/logout",
                headers={
                    "Origin": "http://localhost:5173",
                    "X-CSRF-Token": csrf_token,
                },
            )
            assert logout.status_code == 200
            assert logout.json()["logout_url"].startswith("https://identity.example/logout")
            assert client.cookies.get("cassist_session") is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unauthenticated_me_is_rejected() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_callback_rejects_an_email_outside_the_locked_allowlist(
) -> None:
    test_settings = Settings(
        app_env="development",
        _env_file=None,
        auth_issuer_url="https://identity.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
    )

    async def override_database_session() -> AsyncIterator[AsyncSession]:
        yield None  # type: ignore[misc]

    app.dependency_overrides[get_database_session] = override_database_session
    app.dependency_overrides[get_app_settings] = lambda: test_settings
    app.dependency_overrides[get_identity_provider] = RestrictedIdentityProvider
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            callback = await client.get("/api/v1/auth/callback")

        assert callback.status_code == 403
        assert callback.json()["error"]["message"] == "Access to CAssist is restricted"
        assert client.cookies.get("cassist_session") is None
    finally:
        app.dependency_overrides.clear()
