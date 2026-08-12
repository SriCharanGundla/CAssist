import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import BinaryIO
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
from app.models import Document, DocumentStatus, ProcessingRun, RunStatus, WorkspaceMember
from app.services.auth import establish_session
from app.services.identity_provider import VerifiedIdentity
from app.services.object_storage import (
    ObjectNotFoundError,
    ObjectStorageError,
    PresignedUpload,
    StoredObject,
)
from app.services.upload_cleanup import cleanup_one_expired_upload


class FakeObjectStorage:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[str, str, int]] = []
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.opened_keys: list[str] = []
        self.deleted_keys: list[str] = []

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

    def open_object(self, object_key: str) -> StoredObject:
        self.opened_keys.append(object_key)
        stored = self.objects.get(object_key)
        if stored is None:
            raise ObjectNotFoundError("simulated missing object")
        body, content_type = stored
        return StoredObject(
            body=BytesIO(body),
            content_length=len(body),
            content_type=content_type,
        )

    def put_object(
        self,
        object_key: str,
        body: BinaryIO,
        content_type: str,
        content_length: int,
    ) -> None:
        content = body.read()
        assert len(content) == content_length
        self.objects[object_key] = (content, content_type)

    def delete_object(self, object_key: str) -> None:
        self.deleted_keys.append(object_key)
        self.objects.pop(object_key, None)


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
        auth_allowed_emails={"upload-owner@example.test"},
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
            email="upload-owner@example.test",
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
    assert document.r2_object_key.startswith("incoming/")
    assert len(document.r2_object_key) == len("incoming/") + 32
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
@pytest.mark.parametrize(
    ("filename", "mime_type"),
    [
        ("archive.zip", "application/zip"),
        ("invoices.csv", "text/csv"),
        (
            "invoices.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ],
)
async def test_create_upload_rejects_unsupported_file_types(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
    filename: str,
    mime_type: str,
) -> None:
    client, _, storage, _ = authenticated_upload_client

    response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": filename,
            "mime_type": mime_type,
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
async def test_expired_unfinished_upload_cleanup_removes_object_and_row(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, _ = authenticated_upload_client
    response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "interrupted.pdf",
            "mime_type": "application/pdf",
            "byte_size": 10,
        },
    )
    document = await session.get(Document, response.json()["document_id"])
    assert document is not None and document.r2_object_key is not None
    incoming_key = document.r2_object_key
    storage.objects[incoming_key] = (b"unfinished", "application/pdf")
    document.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    cleaned = await cleanup_one_expired_upload(session, storage)

    assert cleaned is True
    assert await session.get(Document, document.id) is None
    assert incoming_key in storage.deleted_keys
    assert incoming_key not in storage.objects


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


@pytest.mark.asyncio
async def test_complete_upload_verifies_hash_moves_original_and_queues_run(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, settings = authenticated_upload_client
    content = b"%PDF-1.7\n1 0 obj\n%%EOF"
    create_response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "verified.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(content),
        },
    )
    document_id = create_response.json()["document_id"]
    document = await session.get(Document, document_id)
    assert document is not None
    incoming_key = document.r2_object_key
    assert incoming_key is not None
    storage.objects[incoming_key] = (content, "application/pdf")

    response = await client.post(f"/api/v1/uploads/{document_id}/complete")

    assert response.status_code == 202
    assert response.json() == {
        "document_id": document_id,
        "status": "uploaded",
        "deduplicated": False,
    }
    await session.refresh(document)
    assert document.status == DocumentStatus.UPLOADED
    assert document.sha256 == hashlib.sha256(content).hexdigest()
    assert document.upload_expires_at is None
    assert document.r2_object_key is not None
    assert document.r2_object_key.startswith("originals/")
    assert document.r2_object_key != incoming_key
    assert storage.objects[document.r2_object_key] == (content, "application/pdf")
    assert incoming_key not in storage.objects
    assert incoming_key in storage.deleted_keys

    processing_run = await session.scalar(
        select(ProcessingRun).where(ProcessingRun.document_id == document.id)
    )
    assert processing_run is not None
    assert processing_run.status == RunStatus.QUEUED
    assert processing_run.model_id == settings.model_id
    assert processing_run.prompt_version == settings.prompt_version
    assert processing_run.schema_version == settings.schema_version
    assert processing_run.preprocessing_version == settings.preprocessing_version


@pytest.mark.asyncio
async def test_complete_upload_is_idempotent_after_queueing(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, _ = authenticated_upload_client
    content = b"\x89PNG\r\n\x1a\nverified"
    create_response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "verified.png",
            "mime_type": "image/png",
            "byte_size": len(content),
        },
    )
    document_id = create_response.json()["document_id"]
    document = await session.get(Document, document_id)
    assert document is not None and document.r2_object_key is not None
    storage.objects[document.r2_object_key] = (content, "image/png")

    first = await client.post(f"/api/v1/uploads/{document_id}/complete")
    second = await client.post(f"/api/v1/uploads/{document_id}/complete")

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["status"] == "uploaded"
    runs = (
        await session.scalars(select(ProcessingRun).where(ProcessingRun.document_id == document.id))
    ).all()
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_complete_upload_rejects_missing_or_disguised_objects(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, _ = authenticated_upload_client
    content = b"not a pdf"
    create_response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "disguised.pdf",
            "mime_type": "application/pdf",
            "byte_size": len(content),
        },
    )
    document_id = create_response.json()["document_id"]
    document = await session.get(Document, document_id)
    assert document is not None and document.r2_object_key is not None
    incoming_key = document.r2_object_key

    missing_response = await client.post(f"/api/v1/uploads/{document_id}/complete")
    assert missing_response.status_code == 409

    storage.objects[incoming_key] = (b"%PDF-x", "application/pdf")
    wrong_size_response = await client.post(f"/api/v1/uploads/{document_id}/complete")
    assert wrong_size_response.status_code == 422

    storage.objects[incoming_key] = (content, "application/pdf")
    disguised_response = await client.post(f"/api/v1/uploads/{document_id}/complete")
    assert disguised_response.status_code == 422
    await session.refresh(document)
    assert document.status == DocumentStatus.UPLOAD_PENDING
    assert document.sha256 is None
    assert (
        await session.scalar(
            select(ProcessingRun.id).where(ProcessingRun.document_id == document.id)
        )
        is None
    )


@pytest.mark.asyncio
async def test_complete_upload_deduplicates_inside_workspace(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, _ = authenticated_upload_client
    content = b"\xff\xd8\xff\xe0duplicate-jpeg"
    create_response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "duplicate.jpg",
            "mime_type": "image/jpeg",
            "byte_size": len(content),
        },
    )
    pending_id = create_response.json()["document_id"]
    pending = await session.get(Document, pending_id)
    assert pending is not None and pending.r2_object_key is not None
    incoming_key = pending.r2_object_key
    storage.objects[incoming_key] = (content, "image/jpeg")

    existing = Document(
        workspace_id=pending.workspace_id,
        uploaded_by_user_id=pending.uploaded_by_user_id,
        original_filename="existing.jpg",
        mime_type="image/jpeg",
        byte_size=len(content),
        page_count=None,
        sha256=hashlib.sha256(content).hexdigest(),
        r2_object_key=None,
        status=DocumentStatus.READY,
        upload_expires_at=None,
        original_deleted_at=datetime.now(UTC),
        original_deleted_by=pending.uploaded_by_user_id,
    )
    session.add(existing)
    await session.commit()

    response = await client.post(f"/api/v1/uploads/{pending_id}/complete")

    assert response.status_code == 202
    assert response.json() == {
        "document_id": str(existing.id),
        "status": "ready",
        "deduplicated": True,
    }
    assert await session.get(Document, pending_id) is None
    await session.refresh(existing)
    assert existing.r2_object_key is not None
    assert existing.r2_object_key.startswith("originals/")
    assert existing.original_deleted_at is None
    assert existing.original_deleted_by is None
    assert storage.objects[existing.r2_object_key] == (content, "image/jpeg")
    assert incoming_key not in storage.objects
    assert incoming_key in storage.deleted_keys
    assert (
        await session.scalar(
            select(ProcessingRun.id).where(ProcessingRun.document_id == pending_id)
        )
        is None
    )


@pytest.mark.asyncio
async def test_complete_upload_requires_authentication() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/uploads/{uuid4()}/complete")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_complete_upload_hides_documents_outside_current_memberships(
    authenticated_upload_client: tuple[AsyncClient, AsyncSession, FakeObjectStorage, Settings],
) -> None:
    client, session, storage, _ = authenticated_upload_client
    create_response = await client.post(
        "/api/v1/uploads",
        json={
            "filename": "private.pdf",
            "mime_type": "application/pdf",
            "byte_size": 10,
        },
    )
    document = await session.get(Document, create_response.json()["document_id"])
    assert document is not None
    membership = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == document.workspace_id,
            WorkspaceMember.user_id == document.uploaded_by_user_id,
        )
    )
    assert membership is not None
    await session.delete(membership)
    await session.commit()

    response = await client.post(f"/api/v1/uploads/{document.id}/complete")

    assert response.status_code == 404
    assert storage.opened_keys == []
