from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus
from app.services.object_deletion import enqueue_object_deletion, flush_enqueued_deletions
from app.services.object_keys import permanent_key_for_incoming
from app.services.object_storage import ObjectStorage


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

    pending_deletions = [enqueue_object_deletion(session, document.r2_object_key)]
    if document.r2_object_key is not None:
        pending_deletions.append(
            enqueue_object_deletion(
                session,
                permanent_key_for_incoming(document.r2_object_key),
            )
        )

    await session.delete(document)
    await session.flush()
    deletion_ids = [pending.id for pending in pending_deletions if pending is not None]
    await session.commit()
    await flush_enqueued_deletions(session, storage, deletion_ids)
    return True
