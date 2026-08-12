import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, settings
from app.core.database import async_session_factory
from app.models import (
    Document,
    DocumentStatus,
    ExtractionResult,
    ProcessingRun,
    ProcessingStage,
    RunStatus,
)
from app.services.model_provider import (
    ExtractionProvider,
    ModelSelection,
    ProviderConfigurationError,
    ProviderExtraction,
    ProviderExtractionError,
    ProviderRateLimitError,
    create_extraction_provider,
)
from app.services.object_storage import (
    ObjectNotFoundError,
    ObjectStorage,
    ObjectStorageError,
    R2ObjectStorage,
)
from app.services.preprocessing import (
    PreprocessedDocument,
    PreprocessingError,
    preprocess_document,
)

DEFAULT_WORKER_ID = f"worker-{uuid4().hex}"
_ACTIVE_STATUSES = (
    RunStatus.PREPROCESSING,
    RunStatus.EXTRACTING,
    RunStatus.VALIDATING,
)


@dataclass(frozen=True)
class ClaimedRun:
    run_id: UUID
    document_id: UUID
    object_key: str
    byte_size: int
    trusted_sha256: str
    mime_type: str
    model_selection: ModelSelection
    attempt_count: int


PreprocessedConsumer = Callable[[ClaimedRun, PreprocessedDocument], Awaitable[None]]


async def claim_next_run(
    session: AsyncSession,
    worker_id: str,
    lease_seconds: int,
    *,
    current_time: datetime | None = None,
) -> ClaimedRun | None:
    claimed_at = current_time or datetime.now(UTC)
    run_and_document = (
        await session.execute(
            select(ProcessingRun, Document)
            .join(Document, Document.id == ProcessingRun.document_id)
            .where(
                Document.r2_object_key.is_not(None),
                Document.sha256.is_not(None),
                or_(
                    and_(
                        ProcessingRun.status == RunStatus.QUEUED,
                        ProcessingRun.queued_at <= claimed_at,
                    ),
                    and_(
                        ProcessingRun.status.in_(_ACTIVE_STATUSES),
                        or_(
                            ProcessingRun.lease_expires_at.is_(None),
                            ProcessingRun.lease_expires_at < claimed_at,
                        ),
                    ),
                ),
            )
            .order_by(ProcessingRun.queued_at, ProcessingRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).first()
    if run_and_document is None:
        await session.rollback()
        return None

    run, document = run_and_document
    run.status = RunStatus.PREPROCESSING
    run.progress_stage = ProcessingStage.PREPARING.value
    run.worker_id = worker_id
    run.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
    run.started_at = run.started_at or claimed_at
    run.attempt_count += 1
    run.error_code = None
    run.error_message_safe = None
    document.status = DocumentStatus.PROCESSING
    document.updated_at = claimed_at
    await session.commit()

    return ClaimedRun(
        run_id=run.id,
        document_id=document.id,
        object_key=document.r2_object_key or "",
        byte_size=document.byte_size,
        trusted_sha256=document.sha256 or "",
        mime_type=document.mime_type,
        model_selection=ModelSelection(provider=run.provider.value, model_id=run.model_id),
        attempt_count=run.attempt_count,
    )


async def _advance_stage(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedRun,
    worker_id: str,
    expected_status: RunStatus,
    next_status: RunStatus,
    lease_seconds: int,
    *,
    page_count: int | None = None,
    progress_stage: ProcessingStage | None = None,
) -> bool:
    async with session_factory() as session:
        run_and_document = (
            await session.execute(
                select(ProcessingRun, Document)
                .join(Document, Document.id == ProcessingRun.document_id)
                .where(
                    ProcessingRun.id == claim.run_id,
                    ProcessingRun.status == expected_status,
                    ProcessingRun.worker_id == worker_id,
                )
                .with_for_update()
            )
        ).first()
        if run_and_document is None:
            await session.rollback()
            return False

        current_time = datetime.now(UTC)
        run, document = run_and_document
        run.status = next_status
        if progress_stage is not None:
            run.progress_stage = progress_stage.value
        run.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
        if page_count is not None:
            document.page_count = page_count
        document.updated_at = current_time
        await session.commit()
        return True


async def _set_progress_stage(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedRun,
    worker_id: str,
    stage: ProcessingStage,
    lease_seconds: int,
) -> None:
    async with session_factory() as session:
        run = await session.scalar(
            select(ProcessingRun)
            .where(
                ProcessingRun.id == claim.run_id,
                ProcessingRun.status == RunStatus.EXTRACTING,
                ProcessingRun.worker_id == worker_id,
            )
            .with_for_update()
        )
        if run is None:
            await session.rollback()
            return
        run.progress_stage = stage.value
        run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        await session.commit()


async def _complete_run(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedRun,
    worker_id: str,
    extraction: ProviderExtraction,
) -> bool:
    async with session_factory() as session:
        run_and_document = (
            await session.execute(
                select(ProcessingRun, Document)
                .join(Document, Document.id == ProcessingRun.document_id)
                .where(
                    ProcessingRun.id == claim.run_id,
                    ProcessingRun.status == RunStatus.VALIDATING,
                    ProcessingRun.worker_id == worker_id,
                )
                .with_for_update()
            )
        ).first()
        if run_and_document is None:
            await session.rollback()
            return False

        completed_at = datetime.now(UTC)
        run, document = run_and_document
        session.add(
            ExtractionResult(
                processing_run_id=run.id,
                document_type=extraction.document.document_type,
                raw_provider_output=extraction.raw_provider_output,
                canonical_data=extraction.document.model_dump(mode="json"),
                evidence_data=[],
                validation_issues=[
                    issue.model_dump(mode="json") for issue in extraction.quality_issues
                ],
            )
        )
        run.status = RunStatus.SUCCEEDED
        run.progress_stage = ProcessingStage.COMPLETE.value
        run.worker_id = None
        run.lease_expires_at = None
        run.input_tokens = extraction.input_tokens
        run.output_tokens = extraction.output_tokens
        run.completed_at = completed_at
        document.status = DocumentStatus.READY
        document.updated_at = completed_at
        await session.commit()
        return True


async def _fail_run(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedRun,
    worker_id: str,
    error_code: str,
    safe_message: str,
) -> None:
    async with session_factory() as session:
        run_and_document = (
            await session.execute(
                select(ProcessingRun, Document)
                .join(Document, Document.id == ProcessingRun.document_id)
                .where(
                    ProcessingRun.id == claim.run_id,
                    ProcessingRun.status.in_(_ACTIVE_STATUSES),
                    ProcessingRun.worker_id == worker_id,
                )
                .with_for_update()
            )
        ).first()
        if run_and_document is None:
            await session.rollback()
            return

        completed_at = datetime.now(UTC)
        run, document = run_and_document
        run.status = RunStatus.FAILED
        run.progress_stage = ProcessingStage.FAILED.value
        run.worker_id = None
        run.lease_expires_at = None
        run.error_code = error_code
        run.error_message_safe = safe_message
        run.completed_at = completed_at
        document.status = DocumentStatus.FAILED
        document.updated_at = completed_at
        await session.commit()


async def _requeue_rate_limited_run(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedRun,
    worker_id: str,
    retry_seconds: int,
) -> None:
    async with session_factory() as session:
        run_and_document = (
            await session.execute(
                select(ProcessingRun, Document)
                .join(Document, Document.id == ProcessingRun.document_id)
                .where(
                    ProcessingRun.id == claim.run_id,
                    ProcessingRun.status.in_(_ACTIVE_STATUSES),
                    ProcessingRun.worker_id == worker_id,
                )
                .with_for_update()
            )
        ).first()
        if run_and_document is None:
            await session.rollback()
            return

        retry_at = datetime.now(UTC) + timedelta(seconds=retry_seconds)
        run, document = run_and_document
        run.status = RunStatus.QUEUED
        run.progress_stage = ProcessingStage.QUEUED.value
        run.worker_id = None
        run.lease_expires_at = None
        run.queued_at = retry_at
        run.error_code = "PROVIDER_RATE_LIMITED"
        run.error_message_safe = "The model provider is busy; retrying automatically"
        document.status = DocumentStatus.UPLOADED
        document.updated_at = datetime.now(UTC)
        await session.commit()


async def process_next_document(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    app_settings: Settings = settings,
    storage: ObjectStorage | None = None,
    extraction_provider: ExtractionProvider | None = None,
    worker_id: str = DEFAULT_WORKER_ID,
    on_preprocessed: PreprocessedConsumer | None = None,
) -> bool:
    """Claim, preprocess, extract, validate, and persist one document."""
    async with session_factory() as session:
        claim = await claim_next_run(
            session,
            worker_id,
            app_settings.worker_lease_seconds,
        )
    if claim is None:
        return False

    preprocessed: PreprocessedDocument | None = None
    try:
        object_storage = storage or R2ObjectStorage(app_settings)
        preprocessed = await asyncio.to_thread(
            preprocess_document,
            object_storage,
            claim.object_key,
            claim.byte_size,
            claim.trusted_sha256,
            claim.mime_type,
            app_settings.preprocessing_max_pages,
            app_settings.preprocessing_render_dpi,
            app_settings.preprocessing_max_pixels,
            app_settings.preprocessing_max_total_pixels,
        )
        if on_preprocessed is not None:
            await on_preprocessed(claim, preprocessed)
        if not await _advance_stage(
            session_factory,
            claim,
            worker_id,
            RunStatus.PREPROCESSING,
            RunStatus.EXTRACTING,
            app_settings.worker_lease_seconds,
            page_count=preprocessed.page_count,
            progress_stage=ProcessingStage.CLASSIFYING,
        ):
            return False

        provider = extraction_provider or create_extraction_provider(
            app_settings,
            claim.model_selection,
        )
        event_loop = asyncio.get_running_loop()

        def report_stage(stage: ProcessingStage) -> None:
            future = asyncio.run_coroutine_threadsafe(
                _set_progress_stage(
                    session_factory,
                    claim,
                    worker_id,
                    stage,
                    app_settings.worker_lease_seconds,
                ),
                event_loop,
            )
            try:
                future.result(timeout=5)
            except Exception:
                future.cancel()

        extraction = await asyncio.to_thread(
            provider.extract_document,
            preprocessed.page_paths,
            preprocessed.page_text,
            report_stage,
        )
        if not await _advance_stage(
            session_factory,
            claim,
            worker_id,
            RunStatus.EXTRACTING,
            RunStatus.VALIDATING,
            app_settings.worker_lease_seconds,
            progress_stage=ProcessingStage.SAVING,
        ):
            return False
        return await _complete_run(session_factory, claim, worker_id, extraction)
    except ProviderConfigurationError:
        await _fail_run(
            session_factory,
            claim,
            worker_id,
            "PROVIDER_NOT_CONFIGURED",
            "The configured model provider is unavailable",
        )
        return True
    except ProviderRateLimitError:
        if claim.attempt_count < app_settings.provider_rate_limit_max_attempts:
            await _requeue_rate_limited_run(
                session_factory,
                claim,
                worker_id,
                app_settings.provider_rate_limit_retry_seconds,
            )
        else:
            await _fail_run(
                session_factory,
                claim,
                worker_id,
                "PROVIDER_RATE_LIMITED",
                "The model provider remained busy after automatic retries",
            )
        return True
    except ProviderExtractionError:
        await _fail_run(
            session_factory,
            claim,
            worker_id,
            "PROVIDER_EXTRACTION_FAILED",
            "The model provider could not extract this document",
        )
        return True
    except ObjectNotFoundError:
        await _fail_run(
            session_factory,
            claim,
            worker_id,
            "SOURCE_NOT_FOUND",
            "The stored original is unavailable",
        )
        return True
    except ObjectStorageError:
        await _fail_run(
            session_factory,
            claim,
            worker_id,
            "OBJECT_STORAGE_ERROR",
            "The stored original could not be read",
        )
        return True
    except PreprocessingError as exc:
        await _fail_run(
            session_factory,
            claim,
            worker_id,
            exc.code,
            exc.safe_message,
        )
        return True
    except Exception:
        await _fail_run(
            session_factory,
            claim,
            worker_id,
            "PROCESSING_FAILED",
            "Document processing failed",
        )
        return True
    finally:
        if preprocessed is not None:
            preprocessed.close()
