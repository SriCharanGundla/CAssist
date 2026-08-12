import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, settings
from app.core.database import async_session_factory
from app.models import Document, DocumentStatus, ProcessingRun, RunStatus
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


@dataclass(frozen=True)
class ClaimedRun:
    run_id: UUID
    document_id: UUID
    object_key: str
    byte_size: int
    trusted_sha256: str
    mime_type: str
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
                    ProcessingRun.status == RunStatus.QUEUED,
                    and_(
                        ProcessingRun.status == RunStatus.PREPROCESSING,
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
    run.worker_id = worker_id
    run.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
    run.started_at = run.started_at or claimed_at
    run.attempt_count += 1
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
        attempt_count=run.attempt_count,
    )


async def _finish_preprocessing(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedRun,
    worker_id: str,
    page_count: int,
) -> bool:
    async with session_factory() as session:
        run_and_document = (
            await session.execute(
                select(ProcessingRun, Document)
                .join(Document, Document.id == ProcessingRun.document_id)
                .where(
                    ProcessingRun.id == claim.run_id,
                    ProcessingRun.status == RunStatus.PREPROCESSING,
                    ProcessingRun.worker_id == worker_id,
                )
                .with_for_update()
            )
        ).first()
        if run_and_document is None:
            await session.rollback()
            return False

        run, document = run_and_document
        run.status = RunStatus.EXTRACTING
        run.worker_id = None
        run.lease_expires_at = None
        document.page_count = page_count
        document.updated_at = datetime.now(UTC)
        await session.commit()
        return True


async def _fail_preprocessing(
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
                    ProcessingRun.status == RunStatus.PREPROCESSING,
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
        run.worker_id = None
        run.lease_expires_at = None
        run.error_code = error_code
        run.error_message_safe = safe_message
        run.completed_at = completed_at
        document.status = DocumentStatus.FAILED
        document.updated_at = completed_at
        await session.commit()


async def process_next_document(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    app_settings: Settings = settings,
    storage: ObjectStorage | None = None,
    worker_id: str = DEFAULT_WORKER_ID,
    on_preprocessed: PreprocessedConsumer | None = None,
) -> bool:
    """Claim and preprocess one document, leaving it ready for extraction."""
    object_storage = storage or R2ObjectStorage(app_settings)
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
        return await _finish_preprocessing(
            session_factory,
            claim,
            worker_id,
            preprocessed.page_count,
        )
    except ObjectNotFoundError:
        await _fail_preprocessing(
            session_factory,
            claim,
            worker_id,
            "SOURCE_NOT_FOUND",
            "The stored original is unavailable",
        )
        return True
    except ObjectStorageError:
        await _fail_preprocessing(
            session_factory,
            claim,
            worker_id,
            "OBJECT_STORAGE_ERROR",
            "The stored original could not be read",
        )
        return True
    except PreprocessingError as exc:
        await _fail_preprocessing(
            session_factory,
            claim,
            worker_id,
            exc.code,
            exc.safe_message,
        )
        return True
    except Exception:
        await _fail_preprocessing(
            session_factory,
            claim,
            worker_id,
            "PREPROCESSING_FAILED",
            "Document preprocessing failed",
        )
        return True
    finally:
        if preprocessed is not None:
            preprocessed.close()
