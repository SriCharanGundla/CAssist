from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models import Document, DocumentStatus
from app.services.object_keys import permanent_key_for_incoming
from app.services.object_storage import ObjectStorage, ObjectStorageError


async def cleanup_one_expired_upload(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    current_time: datetime | None = None,
) -> bool:
    """Delete one expired, unfinished upload and its pending database row."""
    expired_at = current_time or datetime.now(UTC)
    document = await session.scalar(
        select(Document)
        .where(
            Document.status == DocumentStatus.UPLOAD_PENDING,
            Document.upload_expires_at.is_not(None),
            Document.upload_expires_at < expired_at,
        )
        .order_by(Document.upload_expires_at, Document.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if document is None:
        await session.rollback()
        return False

    try:
        if document.r2_object_key is not None:
            await run_in_threadpool(storage.delete_object, document.r2_object_key)
            await run_in_threadpool(
                storage.delete_object,
                permanent_key_for_incoming(document.r2_object_key),
            )
    except ObjectStorageError:
        await session.rollback()
        raise

    await session.delete(document)
    await session.commit()
    return True
