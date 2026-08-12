from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_app_settings, get_database_session, get_object_storage
from app.core.config import Settings
from app.main import app
from app.models import Document, DocumentStatus, WorkspaceMember
from app.services.auth import establish_session
from app.services.identity_provider import VerifiedIdentity
from app.services.object_storage import ObjectStorageError, PresignedUpload


class FakeObjectStorage:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[str, str, int]] = []

    def create_upload_url(
        self,
        object_key: str,
        content_type: str,
        expires_in: int,
    ) -> PresignedUpload:
        self.calls.append((object_key, content_type, expires_in))
        if self.should_fail:
            raise ObjectStorageError("simulated signing failure")
        return PresignedUpload(
            url="https://upload.invalid/signed-target",
            headers={"Content-Type": content_type},
        )


@pytest_asyncio.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    test_engine = create_async_engine(
        Settings().database_url,
        poolclass=NullPool,
    )
    try:
        connection = await test_engine.connect()
    except OSError:
        await test_engine.dispose()
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
        await test_engine.dispose()


@pytest_asyncio.fixture
async def authenticated_upload_client(
    database_session: AsyncSession,
) -> AsyncIterator[tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings]]:
    settings = Settings(
        app_env="test",
        _env_file=None,
        auth_issuer_url="https://identity.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
        r2_endpoint_url="https://r2.invalid",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        r2_bucket_name="test-originals",
    )
    identity_id = uuid4().hex
    _, credentials = await establish_session(
        database_session,
        VerifiedIdentity(
            issuer="https://identity.example/",
            subject=identity_id,
            email=f"{identity_id}@example.com",
            display_name="Upload Tester",
            return_to="/",
        ),
        settings,
    )
    storage = FakeObjectStorage()

    async def override_database_session() -> AsyncIterator[AsyncSession]:
        yield database_session

    app.dependency_overrides[get_database_session] = override_database_session
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_object_storage] = lambda: storage
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.auth_session_cookie_name, credentials.session_token)
        client.headers["Origin"] = "http://localhost:5173"
        csrf_response = await client.get("/api/v1/auth/csrf")
        assert csrf_response.status_code == 200
        client.headers["X-CSRF-Token"] = csrf_response.json()["csrf_token"]
        yield client, database_session, storage, settings

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_upload_authorizes_workspace_and_uses_an_opaque_key(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, settings = authenticated_upload_client

    response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "invoice-1042.pdf",
            "mime_type": "application/pdf",
            "byte_size": 483_921,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["upload"]["method"] == "PUT"
    assert payload["upload"]["url"] == "https://upload.invalid/signed-target"
    assert payload["upload"]["headers"] == {"Content-Type": "application/pdf"}

    document = await session.get(Document, payload["document_id"])
    assert document is not None
    membership = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == document.workspace_id,
            WorkspaceMember.user_id == document.uploaded_by_user_id,
        )
    )
    assert membership is not None
    assert document.status == DocumentStatus.UPLOAD_PENDING
    assert document.original_filename == "invoice-1042.pdf"
    assert document.r2_object_key is not None
    assert document.r2_object_key.startswith("originals/")
    assert len(document.r2_object_key) == len("originals/") + 32
    assert "invoice" not in document.r2_object_key
    assert "example.com" not in document.r2_object_key
    assert storage.calls == [
        (
            document.r2_object_key,
            "application/pdf",
            settings.r2_presigned_url_ttl_seconds,
        )
    ]


@pytest.mark.asyncio
async def test_create_upload_rejects_oversized_documents_before_signing(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, _, storage, settings = authenticated_upload_client

    response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "large.pdf",
            "mime_type": "application/pdf",
            "byte_size": settings.upload_max_bytes + 1,
        },
    )

    assert response.status_code == 413
    assert storage.calls == []


@pytest.mark.asyncio
async def test_create_upload_rejects_unsupported_file_types(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, _, storage, _ = authenticated_upload_client

    response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "archive.zip",
            "mime_type": "application/zip",
            "byte_size": 1_024,
        },
    )

    assert response.status_code == 422
    assert storage.calls == []


@pytest.mark.asyncio
async def test_create_upload_rolls_back_when_signing_fails(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, _ = authenticated_upload_client
    storage.should_fail = True

    response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "rollback.png",
            "mime_type": "image/png",
            "byte_size": 1_024,
        },
    )

    assert response.status_code == 503
    document = await session.scalar(
        select(Document).where(Document.original_filename == "rollback.png")
    )
    assert document is None


@pytest.mark.asyncio
async def test_create_upload_requires_authentication() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/uploads",
            json={
                "filename": "invoice.pdf",
                "mime_type": "application/pdf",
                "byte_size": 1_024,
            },
        )

    assert response.status_code == 401
