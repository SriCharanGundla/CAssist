from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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
from app.services.auth import establish_session, hash_token
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
            callback = await client.get(
                "/api/v1/auth/callback",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh) Firefox/141.0",
                },
            )
            assert callback.status_code == 303
            assert callback.headers["location"] == "http://localhost:5173/documents"
            first_session_token = client.cookies.get("cassist_session")
            assert first_session_token

            reauthenticated = await client.get(
                "/api/v1/auth/callback",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh) Chrome/140.0 Safari/537.36",
                },
            )
            assert reauthenticated.status_code == 303
            assert client.cookies.get("cassist_session") != first_session_token
            first_session = await database_session.scalar(
                select(AuthSession).where(
                    AuthSession.token_hash == hash_token(first_session_token)
                )
            )
            assert first_session is not None
            assert first_session.revoked_at is None

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

            sessions = await client.get("/api/v1/auth/sessions?page=1&page_size=1")
            assert sessions.status_code == 200
            assert sessions.headers["cache-control"] == "no-store"
            assert sessions.json()["total"] == 2
            assert sessions.json()["total_pages"] == 2
            assert len(sessions.json()["items"]) == 1

            all_sessions = await client.get("/api/v1/auth/sessions?page_size=5")
            other_session = next(
                item for item in all_sessions.json()["items"] if not item["is_current"]
            )
            assert other_session["device_label"] == "Firefox on macOS"
            revoked = await client.delete(
                f"/api/v1/auth/sessions/{other_session['id']}",
                headers={
                    "Origin": "http://localhost:5173",
                    "X-CSRF-Token": csrf_token,
                },
            )
            assert revoked.status_code == 204
            assert await cleanup_expired_sessions(database_session) == 1

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

            capped_settings = Settings(
                app_env="test",
                _env_file=None,
                auth_allowed_emails={"sessions@example.test"},
                auth_max_active_sessions=10,
            )
            identity = VerifiedIdentity(
                issuer="https://identity.example/",
                subject="multi-device-user",
                email="sessions@example.test",
                display_name="Session Tester",
                return_to="/",
            )
            start = datetime(2026, 8, 13, 8, tzinfo=UTC)
            tokens: list[str] = []
            capped_user_id = None
            for offset in range(11):
                capped_user, credentials = await establish_session(
                    database_session,
                    identity,
                    capped_settings,
                    user_agent=f"test-device-{offset}",
                    now=start + timedelta(minutes=offset),
                )
                capped_user_id = capped_user.id
                tokens.append(credentials.session_token)

            active_count = await database_session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(
                    AuthSession.user_id == capped_user_id,
                    AuthSession.revoked_at.is_(None),
                )
            )
            assert active_count == 10
            oldest = await database_session.scalar(
                select(AuthSession).where(
                    AuthSession.token_hash == hash_token(tokens[0])
                )
            )
            second = await database_session.scalar(
                select(AuthSession).where(
                    AuthSession.token_hash == hash_token(tokens[1])
                )
            )
            assert oldest is not None
            assert oldest.revoked_at == start + timedelta(minutes=10)
            assert second is not None and second.revoked_at is None
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
