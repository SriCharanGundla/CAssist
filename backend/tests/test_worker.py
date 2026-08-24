import asyncio
import hashlib
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.models import (
    Document,
    DocumentStatus,
    ExtractionResult,
    MemberRole,
    ModelProvider,
    ProcessingRun,
    ProcessingStage,
    RunStatus,
    User,
    Workspace,
    WorkspaceMember,
)
from app.schemas.extraction import (
    DocumentClassification,
    DocumentPresentation,
    ExtractedField,
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTableRow,
    GenericDocumentExtraction,
    QualityIssue,
)
from app.services.model_provider import (
    ProviderCancellationError,
    ProviderExtraction,
    ProviderExtractionError,
    ProviderRateLimitError,
    ProviderScopeBlocked,
)
from app.services.object_storage import ObjectNotFoundError, PresignedUpload, StoredObject
from app.workers.processor import (
    ClaimedRun,
    _maintain_lease,
    claim_next_run,
    process_next_document,
)


class WorkerObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def create_upload_url(
        self,
        object_key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> PresignedUpload:
        raise NotImplementedError

    def open_object(self, object_key: str) -> StoredObject:
        stored = self.objects.get(object_key)
        if stored is None:
            raise ObjectNotFoundError("missing")
        content, content_type = stored
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

    def delete_object(self, object_key: str) -> None:
        raise NotImplementedError


class FakeExtractionProvider:
    def __init__(self, *, should_fail: bool = False, rate_limited: bool = False) -> None:
        self.should_fail = should_fail
        self.rate_limited = rate_limited
        self.observed_paths: tuple[Path, ...] = ()

    def extract_document(
        self,
        page_paths,
        page_text,
        on_stage=None,
        classification_override=False,
    ) -> ProviderExtraction:
        self.observed_paths = tuple(page_paths)
        assert all(path.exists() for path in self.observed_paths)
        assert tuple(page_text) == (None,)
        if on_stage is not None:
            on_stage(ProcessingStage.CLASSIFYING)
            on_stage(ProcessingStage.EXTRACTING)
            on_stage(ProcessingStage.ORGANIZING)
        if self.rate_limited:
            raise ProviderRateLimitError("simulated provider rate limit")
        if self.should_fail:
            raise ProviderExtractionError("simulated provider failure")
        return ProviderExtraction(
            document=GenericDocumentExtraction(
                document_type="invoice",
                fields=[
                    ExtractedField(
                        id="field-0001",
                        label="Invoice No.",
                        value="INV-100",
                        page_number=1,
                    )
                ],
                tables=[
                    ExtractedTable(
                        id="table-0001",
                        title="Items",
                        headers=["Description", "Amount"],
                        rows=[
                            ExtractedTableRow(
                                id="table-0001-row-0001",
                                cells=[
                                    ExtractedTableCell(
                                        id="table-0001-r0001-c0001",
                                        value="Professional services",
                                    ),
                                    ExtractedTableCell(
                                        id="table-0001-r0001-c0002",
                                        value="118.00",
                                    ),
                                ],
                            )
                        ],
                        page_numbers=[1],
                    )
                ],
            ),
            quality_issues=[
                QualityIssue(
                    target_id="field-0001",
                    code="possible_ocr_error",
                    message="Check one character",
                )
            ],
            presentation=DocumentPresentation(
                sections=[
                    {
                        "id": "section-0001",
                        "title": "Invoice details",
                        "target_ids": ["field-0001", "table-0001"],
                    }
                ]
            ),
            raw_provider_output={"provider_response": "structured"},
            input_tokens=120,
            output_tokens=80,
        )


class ScopeBlockingProvider(FakeExtractionProvider):
    def __init__(self, scope: str) -> None:
        super().__init__()
        self.scope = scope

    def extract_document(
        self,
        page_paths,
        page_text,
        on_stage=None,
        classification_override=None,
    ) -> ProviderExtraction:
        if classification_override is not None:
            return super().extract_document(
                page_paths,
                page_text,
                on_stage,
                classification_override,
            )
        raise ProviderScopeBlocked(
            DocumentClassification(
                scope=self.scope,
                document_type="unknown",
                confidence=0.92,
                reason_code=(
                    "unrelated_content"
                    if self.scope == "unrelated"
                    else "insufficient_visible_content"
                ),
            ),
            input_tokens=9,
            output_tokens=3,
        )


class CancellableExtractionProvider(FakeExtractionProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def extract_document(
        self,
        page_paths,
        page_text,
        on_stage=None,
        classification_override=False,
    ) -> ProviderExtraction:
        self.started.set()
        if not self.cancelled.wait(timeout=5):
            raise AssertionError("Cancellation was not forwarded to the provider")
        raise ProviderCancellationError("cancelled")


class CancellationIgnoringExtractionProvider(FakeExtractionProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.release = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    def extract_document(
        self,
        page_paths,
        page_text,
        on_stage=None,
        classification_override=False,
    ) -> ProviderExtraction:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("Test did not release the provider")
        return super().extract_document(
            page_paths,
            page_text,
            on_stage,
            classification_override,
        )


@pytest_asyncio.fixture
async def worker_database() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings]
]:
    settings = Settings(
        app_env="test",
        _env_file=None,
        r2_endpoint_url="https://r2.invalid",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        r2_bucket_name="test-originals",
    )
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = uuid4().hex
    try:
        async with factory() as session:
            user = User(
                external_auth_id=f"worker|{identity}",
                email=f"worker-{identity}@example.com",
                display_name="Worker Test",
            )
            session.add(user)
            await session.flush()
            workspace = Workspace(name="Worker Test", created_by_user_id=user.id)
            session.add(workspace)
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=MemberRole.OWNER,
                )
            )
            await session.commit()
            user_id = user.id
            workspace_id = workspace.id
    except OSError:
        await engine.dispose()
        pytest.skip("Local PostgreSQL is unavailable")

    try:
        yield factory, user_id, workspace_id, settings
    finally:
        async with factory() as session:
            await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


async def _queue_document(
    factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
    workspace_id: UUID,
    content: bytes,
    mime_type: str,
    object_key: str,
) -> tuple[UUID, UUID]:
    extension = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[mime_type]
    async with factory() as session:
        document = Document(
            workspace_id=workspace_id,
            uploaded_by_user_id=user_id,
            original_filename=f"test.{extension}",
            mime_type=mime_type,
            byte_size=len(content),
            page_count=None,
            sha256=hashlib.sha256(content).hexdigest(),
            r2_object_key=object_key,
            status=DocumentStatus.UPLOADED,
            upload_expires_at=None,
            original_deleted_at=None,
            original_deleted_by=None,
        )
        session.add(document)
        await session.flush()
        run = ProcessingRun(
            document_id=document.id,
            requested_by_user_id=user_id,
            provider=ModelProvider.GEMINI,
            model_id="test-model",
            prompt_version="test-prompt",
            schema_version="test-schema",
            preprocessing_version="test-preprocessing",
            status=RunStatus.QUEUED,
            attempt_count=0,
        )
        session.add(run)
        await session.commit()
        return document.id, run.id


def _png_bytes() -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (120, 80), "white")
    try:
        image.save(output, format="PNG")
    finally:
        image.close()
    return output.getvalue()


@pytest.mark.asyncio
async def test_claims_queued_run_and_reclaims_only_after_lease_expiry(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, _ = worker_database
    _, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        _png_bytes(),
        "image/png",
        "originals/claim",
    )
    claimed_at = datetime.now(UTC)

    async with factory() as session:
        first = await claim_next_run(
            session,
            "worker-a",
            lease_seconds=300,
            current_time=claimed_at,
        )
    async with factory() as session:
        before_expiry = await claim_next_run(
            session,
            "worker-b",
            lease_seconds=300,
            current_time=claimed_at + timedelta(seconds=299),
        )
    async with factory() as session:
        reclaimed = await claim_next_run(
            session,
            "worker-b",
            lease_seconds=300,
            current_time=claimed_at + timedelta(seconds=301),
        )

    assert first is not None and first.run_id == run_id
    assert first.attempt_count == 1
    assert before_expiry is None
    assert reclaimed is not None and reclaimed.run_id == run_id
    assert reclaimed.attempt_count == 2
    async with factory() as session:
        run = await session.get(ProcessingRun, run_id)
        assert run is not None
        assert run.worker_id == "worker-b"
        assert run.lease_expires_at == claimed_at + timedelta(seconds=601)


@pytest.mark.asyncio
async def test_skip_locked_claims_the_next_available_run(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, _ = worker_database
    _, first_run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        _png_bytes(),
        "image/png",
        "originals/locked",
    )
    _, second_run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        _png_bytes() + b"second",
        "image/png",
        "originals/available",
    )

    async with factory() as locking_session:
        locked_run = await locking_session.scalar(
            select(ProcessingRun).where(ProcessingRun.id == first_run_id).with_for_update()
        )
        assert locked_run is not None
        async with factory() as claiming_session:
            claim = await claim_next_run(claiming_session, "worker-b", lease_seconds=300)

        assert claim is not None
        assert claim.run_id == second_run_id


@pytest.mark.asyncio
async def test_active_worker_renews_its_lease_independently(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, _ = worker_database
    _, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        _png_bytes(),
        "image/png",
        "originals/heartbeat",
    )
    claimed_at = datetime.now(UTC)
    async with factory() as session:
        claim = await claim_next_run(
            session,
            "worker-heartbeat",
            lease_seconds=3,
            current_time=claimed_at,
        )
    assert claim is not None

    heartbeat = asyncio.create_task(
        _maintain_lease(factory, claim, "worker-heartbeat", lease_seconds=3)
    )
    await asyncio.sleep(1.1)
    heartbeat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await heartbeat

    async with factory() as session:
        run = await session.get(ProcessingRun, run_id)
        assert run is not None
        assert run.lease_expires_at is not None
        assert run.lease_expires_at > claimed_at + timedelta(seconds=3)


@pytest.mark.asyncio
async def test_processes_one_image_to_result_and_removes_temporary_pages(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = _png_bytes()
    object_key = "originals/preprocess-image"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")
    provider = FakeExtractionProvider()
    observed_paths: tuple[Path, ...] = ()

    async def inspect_preprocessed(_: ClaimedRun, preprocessed) -> None:
        nonlocal observed_paths
        observed_paths = preprocessed.page_paths
        assert preprocessed.page_count == 1
        assert all(path.exists() for path in observed_paths)

    processed = await process_next_document(
        session_factory=factory,
        app_settings=settings,
        storage=storage,
        extraction_provider=provider,
        worker_id="worker-image",
        on_preprocessed=inspect_preprocessed,
    )

    assert processed is True
    assert observed_paths
    assert all(not path.exists() for path in observed_paths)
    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        assert document is not None and document.page_count == 1
        assert document.status == DocumentStatus.READY
        assert run is not None and run.status == RunStatus.SUCCEEDED
        assert run.progress_stage == ProcessingStage.COMPLETE.value
        assert run.worker_id is None
        assert run.lease_expires_at is None
        assert run.attempt_count == 1
        assert run.input_tokens == 120
        assert run.output_tokens == 80
        result = await session.scalar(
            select(ExtractionResult).where(ExtractionResult.processing_run_id == run.id)
        )
        assert result is not None
        assert result.document_type == "invoice"
        assert result.canonical_data["fields"][0]["value"] == "INV-100"
        assert result.canonical_data["tables"][0]["rows"][0]["cells"][1]["value"] == "118.00"
        assert result.presentation_data["sections"][0]["title"] == "Invoice details"
        assert result.raw_provider_output == {"provider_response": "structured"}
        assert [issue["code"] for issue in result.validation_issues] == ["possible_ocr_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "run_status", "document_status"),
    [
        ("uncertain", RunStatus.NEEDS_CONFIRMATION, DocumentStatus.NEEDS_CONFIRMATION),
        ("unrelated", RunStatus.UNSUPPORTED, DocumentStatus.UNSUPPORTED),
    ],
)
async def test_classification_gate_stops_before_extraction(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
    scope: str,
    run_status: RunStatus,
    document_status: DocumentStatus,
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = _png_bytes()
    object_key = f"originals/{scope}"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")

    assert await process_next_document(
        session_factory=factory,
        app_settings=settings,
        storage=storage,
        extraction_provider=ScopeBlockingProvider(scope),
        worker_id=f"worker-{scope}",
    )

    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        result = await session.scalar(
            select(ExtractionResult).where(ExtractionResult.processing_run_id == run_id)
        )
        assert document is not None and document.status == document_status
        assert run is not None and run.status == run_status
        assert run.classification_scope == scope
        assert run.input_tokens == 9
        assert result is None


@pytest.mark.asyncio
async def test_worker_forwards_cancellation_to_provider_and_acknowledges_stop(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = _png_bytes()
    object_key = "originals/cancel-active"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")
    provider = CancellableExtractionProvider()

    processing = asyncio.create_task(
        process_next_document(
            session_factory=factory,
            app_settings=settings,
            storage=storage,
            extraction_provider=provider,
            worker_id="worker-cancel",
        )
    )
    assert await asyncio.to_thread(provider.started.wait, 5)
    async with factory() as session:
        run = await session.get(ProcessingRun, run_id)
        assert run is not None and run.status == RunStatus.EXTRACTING
        run.cancellation_requested_at = datetime.now(UTC)
        run.progress_stage = ProcessingStage.STOPPING.value
        await session.commit()

    assert await processing is True
    assert provider.cancelled.is_set()
    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        result = await session.scalar(
            select(ExtractionResult).where(ExtractionResult.processing_run_id == run_id)
        )
        assert run is not None and run.status == RunStatus.CANCELLED
        assert run.progress_stage == ProcessingStage.CANCELLED.value
        assert run.worker_id is None
        assert run.completed_at is not None
        assert document is not None and document.status == DocumentStatus.FAILED
        assert result is None


@pytest.mark.asyncio
async def test_worker_discards_result_when_provider_ignores_cancellation(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = _png_bytes()
    object_key = "originals/cancel-ignored"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")
    provider = CancellationIgnoringExtractionProvider()

    processing = asyncio.create_task(
        process_next_document(
            session_factory=factory,
            app_settings=settings,
            storage=storage,
            extraction_provider=provider,
            worker_id="worker-cancel-ignored",
        )
    )
    assert await asyncio.to_thread(provider.started.wait, 5)
    async with factory() as session:
        run = await session.get(ProcessingRun, run_id)
        assert run is not None
        run.cancellation_requested_at = datetime.now(UTC)
        run.progress_stage = ProcessingStage.STOPPING.value
        await session.commit()
    assert await asyncio.to_thread(provider.cancelled.wait, 5)
    provider.release.set()

    assert await processing is False
    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        result = await session.scalar(
            select(ExtractionResult).where(ExtractionResult.processing_run_id == run_id)
        )
        assert run is not None and run.status == RunStatus.CANCELLED
        assert run.progress_stage == ProcessingStage.CANCELLED.value
        assert document is not None and document.status == DocumentStatus.FAILED
        assert result is None


@pytest.mark.asyncio
async def test_preprocessing_failure_is_safe_and_terminal(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = b"not-an-image"
    object_key = "originals/private-object-key"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")

    processed = await process_next_document(
        session_factory=factory,
        app_settings=settings,
        storage=storage,
        extraction_provider=FakeExtractionProvider(),
        worker_id="worker-failure",
    )

    assert processed is True
    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        assert document is not None and document.status == DocumentStatus.FAILED
        assert run is not None and run.status == RunStatus.FAILED
        assert run.error_code == "INVALID_DOCUMENT"
        assert run.error_message_safe == "Document could not be preprocessed"
        assert object_key not in run.error_message_safe
        assert run.worker_id is None
        assert run.lease_expires_at is None
        assert run.completed_at is not None


@pytest.mark.asyncio
async def test_process_next_document_returns_false_when_queue_is_empty(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, _, _, settings = worker_database
    assert (
        await process_next_document(
            session_factory=factory,
            app_settings=settings,
            storage=WorkerObjectStorage(),
            extraction_provider=FakeExtractionProvider(),
            worker_id="worker-empty",
        )
        is False
    )


@pytest.mark.asyncio
async def test_provider_failure_is_safe_and_does_not_persist_partial_result(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = _png_bytes()
    object_key = "originals/provider-failure"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")

    assert await process_next_document(
        session_factory=factory,
        app_settings=settings,
        storage=storage,
        extraction_provider=FakeExtractionProvider(should_fail=True),
        worker_id="worker-provider-failure",
    )

    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        assert document is not None and document.status == DocumentStatus.FAILED
        assert run is not None and run.status == RunStatus.FAILED
        assert run.error_code == "PROVIDER_EXTRACTION_FAILED"
        assert run.error_message_safe == "The model provider could not extract this document"
        assert (
            await session.scalar(
                select(ExtractionResult.id).where(ExtractionResult.processing_run_id == run_id)
            )
            is None
        )


@pytest.mark.asyncio
async def test_provider_rate_limit_requeues_with_a_bounded_delay(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = _png_bytes()
    object_key = "originals/provider-rate-limit"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")
    before_processing = datetime.now(UTC)

    assert await process_next_document(
        session_factory=factory,
        app_settings=settings,
        storage=storage,
        extraction_provider=FakeExtractionProvider(rate_limited=True),
        worker_id="worker-provider-rate-limit",
    )

    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        assert document is not None and document.status == DocumentStatus.UPLOADED
        assert run is not None and run.status == RunStatus.QUEUED
        assert run.worker_id is None
        assert run.lease_expires_at is None
        assert run.attempt_count == 1
        assert run.queued_at >= before_processing + timedelta(
            seconds=settings.provider_rate_limit_retry_seconds - 1
        )
        assert run.error_code == "PROVIDER_RATE_LIMITED"
        assert run.error_message_safe == "The model provider is busy; retrying automatically"

    async with factory() as session:
        claim = await claim_next_run(
            session,
            "worker-too-early",
            lease_seconds=settings.worker_lease_seconds,
            current_time=before_processing,
        )
        assert claim is None


@pytest.mark.asyncio
async def test_missing_provider_key_fails_safely_after_preprocessing(
    worker_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, Settings],
) -> None:
    factory, user_id, workspace_id, settings = worker_database
    content = _png_bytes()
    object_key = "originals/provider-not-configured"
    document_id, run_id = await _queue_document(
        factory,
        user_id,
        workspace_id,
        content,
        "image/png",
        object_key,
    )
    storage = WorkerObjectStorage()
    storage.objects[object_key] = (content, "image/png")

    assert await process_next_document(
        session_factory=factory,
        app_settings=settings,
        storage=storage,
        worker_id="worker-provider-not-configured",
    )

    async with factory() as session:
        document = await session.get(Document, document_id)
        run = await session.get(ProcessingRun, run_id)
        assert document is not None and document.status == DocumentStatus.FAILED
        assert run is not None and run.status == RunStatus.FAILED
        assert run.error_code == "PROVIDER_NOT_CONFIGURED"
        assert run.error_message_safe == "The configured model provider is unavailable"
        assert (
            await session.scalar(
                select(ExtractionResult.id).where(ExtractionResult.processing_run_id == run_id)
            )
            is None
        )
