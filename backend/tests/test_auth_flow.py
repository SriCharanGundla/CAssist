from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
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
from app.services.identity_provider import VerifiedIdentity


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
            email="user@example.com",
            display_name="Example User",
            return_to="/documents",
        )

    def logout_url(self, return_to: str) -> str:
        return f"https://identity.example/logout?return_to={return_to}"


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
        auth_issuer_url="https://identity.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
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
            assert client.cookies.get("cassist_session")

            me = await client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json() == {
                "user": {
                    "id": me.json()["user"]["id"],
                    "email": "user@example.com",
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
