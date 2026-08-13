from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models import PendingObjectDeletion
from app.services.object_storage import ObjectStorage, ObjectStorageError


def enqueue_object_deletion(
    session: AsyncSession, object_key: str | None
) -> PendingObjectDeletion | None:
    if object_key:
        pending = PendingObjectDeletion(object_key=object_key)
        session.add(pending)
        return pending
    return None


async def process_one_object_deletion(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    deletion_id=None,
) -> bool:
    statement = select(PendingObjectDeletion).order_by(PendingObjectDeletion.created_at).limit(1)
    if deletion_id is not None:
        statement = select(PendingObjectDeletion).where(PendingObjectDeletion.id == deletion_id)
    pending = await session.scalar(statement.with_for_update(skip_locked=True))
    if pending is None:
        await session.rollback()
        return False
    pending.attempt_count += 1
    pending.last_attempt_at = datetime.now(UTC)
    try:
        await run_in_threadpool(storage.delete_object, pending.object_key)
    except ObjectStorageError:
        await session.commit()
        return False
    await session.delete(pending)
    await session.commit()
    return True


async def flush_enqueued_deletions(
    session: AsyncSession,
    storage: ObjectStorage,
    ids: list,
) -> None:
    for deletion_id in ids:
        await process_one_object_deletion(session, storage, deletion_id=deletion_id)
