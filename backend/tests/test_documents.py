import json
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_app_settings, get_database_session, get_object_storage
from app.api.routes.documents import _comparison_differences
from app.core.config import Settings
from app.main import app
from app.models import (
    AuditEvent,
    Correction,
    Document,
    DocumentStatus,
    ExportEvent,
    ExportFormat,
    ExtractionResult,
    MemberRole,
    ModelProvider,
    ProcessingRun,
    ProcessingStage,
    RunStatus,
    User,
    WorkspaceMember,
)
from app.schemas.extraction import ExtractedField, GenericDocumentExtraction
from app.services.auth import establish_session
from app.services.identity_provider import VerifiedIdentity
from app.services.object_storage import (
    ObjectStorageError,
    PresignedDownload,
    PresignedUpload,
    StoredObject,
)


def test_comparison_differences_preserve_labels_values_and_provider_counts() -> None:
    shared = json.dumps(["field", "Invoice date", "12 August 2026"])
    gemini_only = json.dumps(["field", "Invoice number", "INV-1"])
    openai_only = json.dumps(["field", "Invoice number", "INV-I"])

    differences = _comparison_differences(
        Counter({shared: 1, gemini_only: 1}),
        Counter({shared: 1, openai_only: 1}),
    )

    assert [difference.model_dump() for difference in differences] == [
        {
            "kind": "field",
            "label": "Invoice number",
            "value": "INV-1",
            "gemini_count": 1,
            "openai_count": 0,
        },
        {
            "kind": "field",
            "label": "Invoice number",
            "value": "INV-I",
            "gemini_count": 0,
            "openai_count": 1,
        },
    ]


class DocumentObjectStorage:
    def __init__(self) -> None:
        self.download_calls: list[tuple[str, int]] = []
        self.deleted_keys: list[str] = []
        self.fail_sign = False
        self.fail_delete = False
        self.objects: dict[str, tuple[bytes, str]] = {}

    def create_download_url(self, object_key: str, expires_in: int) -> PresignedDownload:
        self.download_calls.append((object_key, expires_in))
        if self.fail_sign:
            raise ObjectStorageError("simulated signing failure")
        return PresignedDownload(url="https://download.invalid/object?temporary-signature")

    def delete_object(self, object_key: str) -> None:
        self.deleted_keys.append(object_key)
        if self.fail_delete:
            raise ObjectStorageError("simulated deletion failure")

    def create_upload_url(
        self,
        object_key: str,
        content_type: str,
        expires_in: int,
    ) -> PresignedUpload:
        raise NotImplementedError

    def open_object(self, object_key: str) -> StoredObject:
        content, content_type = self.objects.get(object_key, (b"", "application/octet-stream"))
        return StoredObject(
            body=BytesIO(content),
            content_length=len(content),
            content_type=content_type,
        )

    def put_object(
        self,
        object_key: str,
        body: BinaryIO,
        content_type: str,
        content_length: int,
    ) -> None:
        raise NotImplementedError


async def _set_client_identity(
    client: AsyncClient,
    session: AsyncSession,
    settings: Settings,
    *,
    label: str,
) -> User:
    identity = uuid4().hex
    user, credentials = await establish_session(
        session,
        VerifiedIdentity(
            issuer="https://identity.example/",
            subject=identity,
            email="document-owner@example.test"
            if label == "Document Owner"
            else "document-other@example.test",
            display_name=label,
            return_to="/",
        ),
        settings,
    )
    client.cookies.set(settings.auth_session_cookie_name, credentials.session_token)
    csrf_response = await client.get("/api/v1/auth/csrf")
    assert csrf_response.status_code == 200
    client.headers["X-CSRF-Token"] = csrf_response.json()["csrf_token"]
    return user


@pytest_asyncio.fixture
async def document_client() -> AsyncIterator[
    tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ]
]:
    settings = Settings(
        app_env="test",
        _env_file=None,
        auth_issuer_url="https://identity.example/",
        auth_client_id="client-id",
        auth_client_secret="client-secret",
        auth_state_secret="x" * 32,
        auth_allowed_emails={
            "document-owner@example.test",
            "document-other@example.test",
        },
        r2_endpoint_url="https://r2.invalid",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        r2_bucket_name="test-originals",
    )
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        connection = await engine.connect()
    except OSError:
        await engine.dispose()
        pytest.skip("Local PostgreSQL is unavailable")
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    storage = DocumentObjectStorage()

    async def override_database_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_database_session] = override_database_session
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_object_storage] = lambda: storage
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.headers["Origin"] = "http://localhost:5173"
            owner = await _set_client_identity(
                client,
                session,
                settings,
                label="Document Owner",
            )
            workspace_id = await session.scalar(
                select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == owner.id)
            )
            assert workspace_id is not None
            document = Document(
                workspace_id=workspace_id,
                uploaded_by_user_id=owner.id,
                original_filename="private-invoice.pdf",
                mime_type="application/pdf",
                byte_size=100,
                page_count=1,
                sha256="d" * 64,
                r2_object_key=f"originals/{uuid4().hex}",
                status=DocumentStatus.READY,
                upload_expires_at=None,
                original_deleted_at=None,
                original_deleted_by=None,
            )
            session.add(document)
            await session.flush()
            run = ProcessingRun(
                document_id=document.id,
                requested_by_user_id=owner.id,
                provider=ModelProvider.GEMINI,
                model_id="test-model",
                prompt_version="test-prompt",
                schema_version="test-schema",
                preprocessing_version="test-preprocessing",
                status=RunStatus.SUCCEEDED,
                attempt_count=1,
            )
            session.add(run)
            await session.flush()
            extraction = GenericDocumentExtraction(
                document_type="tax_invoice",
                fields=[
                    ExtractedField(
                        id="field-0001",
                        label="Invoice number",
                        value="INV-DELETE",
                        page_number=1,
                    )
                ],
            )
            result = ExtractionResult(
                processing_run_id=run.id,
                document_type="tax_invoice",
                raw_provider_output={"private": "provider data"},
                canonical_data=extraction.model_dump(mode="json"),
                validation_issues=[],
            )
            session.add(result)
            await session.flush()
            session.add(
                Correction(
                    extraction_result_id=result.id,
                    corrected_by_user_id=owner.id,
                    field_path="/fields/0/value",
                    previous_value="INV-DELETE",
                    corrected_value="INV-REVIEWED",
                    reason="Reviewed",
                )
            )
            session.add(
                ExportEvent(
                    extraction_result_id=result.id,
                    exported_by_user_id=owner.id,
                    format=ExportFormat.TALLY_JSON,
                    exporter_version="test-exporter",
                    options={},
                )
            )
            await session.commit()
            yield (
                client,
                session,
                storage,
                settings,
                owner.id,
                workspace_id,
                document.id,
                result.id,
            )
    finally:
        app.dependency_overrides.clear()
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_view_url_is_short_lived_narrow_and_not_cached(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, storage, settings, _, _, document_id, result_id = document_client
    document = await session.get(Document, document_id)
    assert document is not None and document.r2_object_key is not None

    response = await client.post(f"/api/v1/documents/{document_id}/view-url")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["url"] == "https://download.invalid/object?temporary-signature"
    assert response.json()["expires_at"].endswith("Z")
    expires_at = datetime.fromisoformat(response.json()["expires_at"].replace("Z", "+00:00"))
    assert 295 <= (expires_at - datetime.now(UTC)).total_seconds() <= 300
    assert storage.download_calls == [
        (document.r2_object_key, settings.r2_presigned_url_ttl_seconds)
    ]
    assert document.original_filename not in response.text


@pytest.mark.asyncio
async def test_document_detail_returns_latest_frontend_safe_status(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, _, _, workspace_id, document_id, result_id = document_client
    result = await session.get(ExtractionResult, result_id)
    assert result is not None

    response = await client.get(f"/api/v1/documents/{document_id}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["id"] == str(document_id)
    assert payload["workspace_id"] == str(workspace_id)
    assert payload["original_filename"] == "private-invoice.pdf"
    assert payload["mime_type"] == "application/pdf"
    assert payload["original_available"] is True
    assert payload["latest_run"]["id"] == str(result.processing_run_id)
    assert payload["latest_run"]["status"] == "succeeded"
    assert payload["latest_run"]["result_id"] == str(result_id)
    assert payload["latest_run"]["review_status"] == "unreviewed"
    for private_field in (
        "sha256",
        "r2_object_key",
        "raw_provider_output",
        "worker_id",
        "lease_expires_at",
    ):
        assert private_field not in response.text
    assert "provider data" not in response.text


@pytest.mark.asyncio
async def test_document_list_is_safe_filterable_and_cursor_paginated(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, _, owner_id, workspace_id, ready_id, _ = document_client
    now = datetime.now(UTC)
    pending_documents = [
        Document(
            workspace_id=workspace_id,
            uploaded_by_user_id=owner_id,
            original_filename=f"pending-{index}.png",
            mime_type="image/png",
            byte_size=50,
            page_count=None,
            sha256=None,
            r2_object_key=f"incoming/{uuid4().hex}",
            status=DocumentStatus.UPLOAD_PENDING,
            upload_expires_at=now + timedelta(minutes=5),
            original_deleted_at=None,
            original_deleted_by=None,
            created_at=now + timedelta(minutes=index + 1),
            updated_at=now + timedelta(minutes=index + 1),
        )
        for index in range(2)
    ]
    session.add_all(pending_documents)
    await session.commit()

    first_page = await client.get("/api/v1/documents", params={"limit": 1})
    assert first_page.status_code == 200
    assert first_page.headers["cache-control"] == "no-store"
    assert [item["id"] for item in first_page.json()["items"]] == [
        str(pending_documents[1].id)
    ]
    assert first_page.json()["next_cursor"] is not None

    second_page = await client.get(
        "/api/v1/documents",
        params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
    )
    assert second_page.status_code == 200
    assert [item["id"] for item in second_page.json()["items"]] == [
        str(pending_documents[0].id)
    ]

    ready = await client.get(
        "/api/v1/documents",
        params={"status": "ready", "document_type": "tax_invoice"},
    )
    assert ready.status_code == 200
    assert [item["id"] for item in ready.json()["items"]] == [str(ready_id)]
    assert ready.json()["items"][0]["latest_run"]["result_id"] is not None
    assert "sha256" not in ready.text
    assert "r2_object_key" not in ready.text

    result = await session.scalar(
        select(ExtractionResult)
        .join(ProcessingRun, ProcessingRun.id == ExtractionResult.processing_run_id)
        .where(ProcessingRun.document_id == ready_id)
    )
    assert result is not None
    result.document_type = "receipt"
    await session.commit()
    receipt = await client.get(
        "/api/v1/documents",
        params={"document_type": "receipt"},
    )
    assert receipt.status_code == 200
    assert [item["id"] for item in receipt.json()["items"]] == [str(ready_id)]

    invalid_cursor = await client.get(
        "/api/v1/documents", params={"cursor": "not-a-cursor"}
    )
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["message"] == "Invalid document cursor"
    assert invalid_cursor.json()["error"]["request_id"].startswith("req_")


@pytest.mark.asyncio
async def test_pending_document_detail_has_no_run_or_original(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, _, owner_id, workspace_id, _, _ = document_client
    pending = Document(
        workspace_id=workspace_id,
        uploaded_by_user_id=owner_id,
        original_filename="pending.png",
        mime_type="image/png",
        byte_size=50,
        page_count=None,
        sha256=None,
        r2_object_key=f"incoming/{uuid4().hex}",
        status=DocumentStatus.UPLOAD_PENDING,
        upload_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        original_deleted_at=None,
        original_deleted_by=None,
    )
    session.add(pending)
    await session.commit()
    await session.refresh(pending)

    response = await client.get(f"/api/v1/documents/{pending.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "upload_pending"
    assert response.json()["original_available"] is False
    assert response.json()["latest_run"] is None


@pytest.mark.asyncio
async def test_run_detail_reports_safe_progress_and_result_link(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, _, _, _, document_id, result_id = document_client
    result = await session.get(ExtractionResult, result_id)
    assert result is not None

    response = await client.get(f"/api/v1/runs/{result.processing_run_id}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["document_id"] == str(document_id)
    assert payload["status"] == "succeeded"
    assert payload["provider"] == "gemini"
    assert payload["result_id"] == str(result_id)
    assert payload["review_status"] == "unreviewed"
    assert payload["progress"] == {
        "stage": "complete",
        "completed_pages": 1,
        "total_pages": 1,
    }
    assert payload["error"] is None
    for private_field in (
        "worker_id",
        "lease_expires_at",
        "prompt_version",
        "schema_version",
        "preprocessing_version",
        "input_tokens",
        "output_tokens",
    ):
        assert private_field not in payload


@pytest.mark.asyncio
async def test_document_uses_newest_run_and_exposes_only_safe_failure(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, _, owner_id, _, document_id, result_id = document_client
    result = await session.get(ExtractionResult, result_id)
    assert result is not None
    successful_run = await session.get(ProcessingRun, result.processing_run_id)
    assert successful_run is not None
    failed_run = ProcessingRun(
        document_id=document_id,
        requested_by_user_id=owner_id,
        provider=ModelProvider.GEMINI,
        model_id="retry-model",
        prompt_version="retry-prompt",
        schema_version="retry-schema",
        preprocessing_version="retry-preprocessing",
        status=RunStatus.FAILED,
        attempt_count=2,
        error_code="PROVIDER_EXTRACTION_FAILED",
        error_message_safe="The model provider could not extract this document",
        queued_at=successful_run.queued_at + timedelta(seconds=1),
        started_at=successful_run.queued_at + timedelta(seconds=1),
        completed_at=successful_run.queued_at + timedelta(seconds=2),
    )
    session.add(failed_run)
    await session.commit()
    await session.refresh(failed_run)

    document_response = await client.get(f"/api/v1/documents/{document_id}")
    run_response = await client.get(f"/api/v1/runs/{failed_run.id}")

    assert document_response.status_code == 200
    assert document_response.json()["latest_run"]["id"] == str(failed_run.id)
    assert document_response.json()["latest_run"]["status"] == "failed"
    assert document_response.json()["latest_run"]["result_id"] is None
    assert run_response.status_code == 200
    assert run_response.json()["attempt_count"] == 2
    assert run_response.json()["error"] == {
        "code": "PROVIDER_EXTRACTION_FAILED",
        "message": "The model provider could not extract this document",
    }
    assert run_response.json()["progress"] == {
        "stage": "failed",
        "completed_pages": None,
        "total_pages": 1,
    }


@pytest.mark.asyncio
async def test_active_run_reports_stage_without_inventing_page_progress(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, _, owner_id, _, document_id, _ = document_client
    active_run = ProcessingRun(
        document_id=document_id,
        requested_by_user_id=owner_id,
        provider=ModelProvider.GEMINI,
        model_id="active-model",
        prompt_version="active-prompt",
        schema_version="active-schema",
        preprocessing_version="active-preprocessing",
        status=RunStatus.EXTRACTING,
        progress_stage=ProcessingStage.CLASSIFYING.value,
        attempt_count=1,
        started_at=datetime.now(UTC),
    )
    session.add(active_run)
    await session.commit()
    await session.refresh(active_run)

    response = await client.get(f"/api/v1/runs/{active_run.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "extracting"
    assert response.json()["result_id"] is None
    assert response.json()["review_status"] is None
    assert response.json()["progress"] == {
        "stage": "classifying",
        "completed_pages": None,
        "total_pages": 1,
    }
    assert response.json()["error"] is None


@pytest.mark.asyncio
async def test_manual_run_creation_reuses_cache_and_active_work(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, settings, _, _, document_id, _ = document_client

    first = await client.post(
        f"/api/v1/documents/{document_id}/runs",
        json={"provider": "openai", "model_id": "comparison-model", "force": False},
    )
    second = await client.post(
        f"/api/v1/documents/{document_id}/runs",
        json={"provider": "openai", "model_id": "comparison-model", "force": False},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["cache_hit"] is False
    run = await session.get(ProcessingRun, UUID(first.json()["run_id"]))
    assert run is not None
    assert run.provider == ModelProvider.OPENAI
    assert run.prompt_version == settings.prompt_version


@pytest.mark.asyncio
async def test_active_run_can_be_cancelled_idempotently(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, _, owner_id, _, document_id, _ = document_client
    run = ProcessingRun(
        document_id=document_id,
        requested_by_user_id=owner_id,
        provider=ModelProvider.GEMINI,
        model_id="cancel-model",
        prompt_version="cancel-prompt",
        schema_version="cancel-schema",
        preprocessing_version="cancel-preprocessing",
        status=RunStatus.QUEUED,
        attempt_count=0,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    first = await client.post(f"/api/v1/runs/{run.id}/cancel")
    second = await client.post(f"/api/v1/runs/{run.id}/cancel")

    assert first.status_code == 202
    assert second.status_code == 202
    await session.refresh(run)
    assert run.status == RunStatus.CANCELLED
    assert run.worker_id is None
    assert run.lease_expires_at is None


@pytest.mark.asyncio
async def test_development_comparison_queues_each_provider_once(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, settings, _, _, document_id, _ = document_client

    first = await client.post(f"/api/v1/documents/{document_id}/comparisons")
    second = await client.post(f"/api/v1/documents/{document_id}/comparisons")

    assert first.status_code == 200
    assert {item["provider"] for item in first.json()["runs"]} == {"gemini", "openai"}
    assert {item["run_id"] for item in second.json()["runs"]} == {
        item["run_id"] for item in first.json()["runs"]
    }
    configured_runs = list(
        (
            await session.scalars(
                select(ProcessingRun).where(
                    ProcessingRun.document_id == document_id,
                    ProcessingRun.prompt_version == settings.prompt_version,
                    ProcessingRun.schema_version == settings.schema_version,
                    ProcessingRun.preprocessing_version == settings.preprocessing_version,
                )
            )
        ).all()
    )
    assert len(configured_runs) == 2


@pytest.mark.asyncio
async def test_idempotency_key_is_accepted_and_validated(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, _, _, _, _, _, document_id, _ = document_client
    accepted = await client.post(
        f"/api/v1/documents/{document_id}/view-url",
        headers={"Idempotency-Key": "view-original-0001"},
    )
    rejected = await client.post(
        f"/api/v1/documents/{document_id}/view-url",
        headers={"Idempotency-Key": "short"},
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert rejected.headers["x-request-id"] == rejected.json()["error"]["request_id"]


@pytest.mark.asyncio
async def test_original_only_deletion_is_idempotent_audited_and_retains_history(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, storage, _, owner_id, _, document_id, result_id = document_client
    document = await session.get(Document, document_id)
    assert document is not None and document.r2_object_key is not None
    object_key = document.r2_object_key

    first = await client.delete(f"/api/v1/documents/{document_id}/original")
    second = await client.delete(f"/api/v1/documents/{document_id}/original")

    assert first.status_code == 204
    assert second.status_code == 204
    assert storage.deleted_keys == [object_key]
    await session.refresh(document)
    assert document.r2_object_key is None
    assert document.original_deleted_at is not None
    assert document.original_deleted_by == owner_id
    assert await session.get(ExtractionResult, result_id) is not None
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Correction)
            .where(Correction.extraction_result_id == result_id)
        )
        == 1
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ExportEvent)
            .where(ExportEvent.extraction_result_id == result_id)
        )
        == 1
    )
    audits = list(
        (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == document_id,
                    AuditEvent.action == "document.original_deleted",
                )
            )
        ).all()
    )
    assert len(audits) == 1
    assert audits[0].metadata_ == {}
    assert (await client.post(f"/api/v1/documents/{document_id}/view-url")).status_code == 409


@pytest.mark.asyncio
async def test_pending_upload_is_not_exposed_as_a_permanent_original(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, storage, _, _, _, document_id, _ = document_client
    document = await session.get(Document, document_id)
    assert document is not None
    document.status = DocumentStatus.UPLOAD_PENDING
    document.sha256 = None
    document.r2_object_key = f"incoming/{uuid4().hex}"
    await session.commit()

    assert (await client.post(f"/api/v1/documents/{document_id}/view-url")).status_code == 409
    assert (await client.delete(f"/api/v1/documents/{document_id}/original")).status_code == 409
    assert storage.download_calls == []
    assert storage.deleted_keys == []


@pytest.mark.asyncio
async def test_permanent_deletion_cascades_history_and_retains_only_safe_audit(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, storage, _, owner_id, workspace_id, document_id, result_id = document_client
    document = await session.get(Document, document_id)
    assert document is not None and document.r2_object_key is not None
    object_key = document.r2_object_key

    first = await client.delete(f"/api/v1/documents/{document_id}")
    second = await client.delete(f"/api/v1/documents/{document_id}")

    assert first.status_code == 204
    assert second.status_code == 204
    assert storage.deleted_keys == [object_key]
    assert await session.get(Document, document_id) is None
    assert (
        await session.scalar(select(ExtractionResult.id).where(ExtractionResult.id == result_id))
        is None
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Correction)
            .where(Correction.extraction_result_id == result_id)
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ExportEvent)
            .where(ExportEvent.extraction_result_id == result_id)
        )
        == 0
    )
    audits = list(
        (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == document_id,
                    AuditEvent.action == "document.permanently_deleted",
                )
            )
        ).all()
    )
    assert len(audits) == 1
    assert audits[0].workspace_id == workspace_id
    assert audits[0].actor_user_id == owner_id
    assert audits[0].metadata_ == {}


@pytest.mark.asyncio
async def test_storage_failures_leave_database_and_audit_unchanged(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, storage, _, _, _, document_id, _ = document_client
    storage.fail_sign = True
    assert (await client.post(f"/api/v1/documents/{document_id}/view-url")).status_code == 503

    storage.fail_sign = False
    storage.fail_delete = True
    assert (await client.delete(f"/api/v1/documents/{document_id}/original")).status_code == 503
    assert (await client.delete(f"/api/v1/documents/{document_id}")).status_code == 503

    document = await session.get(Document, document_id)
    assert document is not None and document.r2_object_key is not None
    assert document.original_deleted_at is None
    assert (
        await session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == document_id)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_nonmembers_cannot_observe_or_delete_document(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, storage, settings, _, _, document_id, result_id = document_client
    result = await session.get(ExtractionResult, result_id)
    assert result is not None
    await _set_client_identity(client, session, settings, label="Outsider")

    assert (await client.get(f"/api/v1/documents/{document_id}")).status_code == 404
    assert (await client.get(f"/api/v1/runs/{result.processing_run_id}")).status_code == 404
    assert (await client.post(f"/api/v1/documents/{document_id}/view-url")).status_code == 404
    assert (await client.delete(f"/api/v1/documents/{document_id}/original")).status_code == 204
    assert (await client.delete(f"/api/v1/documents/{document_id}")).status_code == 204
    assert storage.download_calls == []
    assert storage.deleted_keys == []
    assert await session.get(Document, document_id) is not None


@pytest.mark.asyncio
async def test_failed_document_can_queue_a_new_current_model_run(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, _, settings, owner_id, workspace_id, document_id, _ = document_client
    document = await session.get(Document, document_id)
    assert document is not None
    failed_run = ProcessingRun(
        document_id=document_id,
        requested_by_user_id=owner_id,
        provider=ModelProvider.GEMINI,
        model_id="previous-model",
        prompt_version="previous-prompt",
        schema_version="previous-schema",
        preprocessing_version="previous-preprocessing",
        status=RunStatus.FAILED,
        attempt_count=1,
        queued_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(failed_run)
    document.status = DocumentStatus.FAILED
    await session.commit()

    response = await client.post(f"/api/v1/documents/{document_id}/retry")

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_id"] == str(document_id)
    assert payload["status"] == "uploaded"
    retry_run = await session.get(ProcessingRun, UUID(payload["run_id"]))
    assert retry_run is not None
    assert retry_run.status == RunStatus.QUEUED
    assert retry_run.provider.value == settings.model_provider
    assert retry_run.model_id == "gemini-3.5-flash-lite"
    await session.refresh(document)
    assert document.status == DocumentStatus.UPLOADED
    audit = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action == "document.processing_retried",
        )
    )
    assert audit is not None


@pytest.mark.asyncio
async def test_members_can_delete_original_but_only_privileged_or_uploading_member_deletes_record(
    document_client: tuple[
        AsyncClient,
        AsyncSession,
        DocumentObjectStorage,
        Settings,
        UUID,
        UUID,
        UUID,
        UUID,
    ],
) -> None:
    client, session, storage, settings, _, workspace_id, document_id, _ = document_client
    member = await _set_client_identity(client, session, settings, label="Workspace Member")
    session.add(
        WorkspaceMember(
            workspace_id=workspace_id,
            user_id=member.id,
            role=MemberRole.MEMBER,
        )
    )
    await session.commit()

    assert (await client.delete(f"/api/v1/documents/{document_id}/original")).status_code == 204
    assert (await client.delete(f"/api/v1/documents/{document_id}")).status_code == 403
    document = await session.get(Document, document_id)
    assert document is not None
    document.uploaded_by_user_id = member.id
    await session.commit()

    assert (await client.delete(f"/api/v1/documents/{document_id}")).status_code == 204
    assert await session.get(Document, document_id) is None
    assert len(storage.deleted_keys) == 1
