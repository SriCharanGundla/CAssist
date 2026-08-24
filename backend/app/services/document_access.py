from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, MemberRole, WorkspaceMember


async def authorized_document(
    session: AsyncSession,
    document_id: UUID,
    user_id: UUID,
    *,
    lock: bool,
) -> tuple[Document, MemberRole] | None:
    statement = (
        select(Document, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(
            Document.id == document_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if lock:
        statement = statement.with_for_update(of=Document)
    row = (await session.execute(statement)).one_or_none()
    return tuple(row) if row is not None else None


def require_available_original(document: Document) -> None:
    if (
        document.r2_object_key is None
        or document.sha256 is None
        or document.original_deleted_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Original document is unavailable",
        )


def require_scope_allows_new_run(document: Document) -> None:
    if document.status == DocumentStatus.UNSUPPORTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document was classified as unrelated and cannot be processed",
        )
    if document.status == DocumentStatus.NEEDS_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirm this document before processing it",
        )
