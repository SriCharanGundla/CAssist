from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import (
    get_app_settings,
    get_current_auth,
    get_database_session,
    get_object_storage,
    require_csrf,
)
from app.core.config import Settings
from app.models import (
    AuditEvent,
    Document,
    ExtractionResult,
    MemberRole,
    ProcessingRun,
    WorkspaceMember,
)
from app.schemas.documents import DocumentDetailResponse, ViewOriginalResponse
from app.services.auth import CurrentAuth
from app.services.object_storage import ObjectStorage, ObjectStorageError
from app.services.run_status import run_summary

router = APIRouter(prefix="/documents")


async def _authorized_document(
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
    return (await session.execute(statement)).one_or_none()


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: UUID,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> DocumentDetailResponse:
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=False)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    latest_run_row = (
        await session.execute(
            select(ProcessingRun, ExtractionResult)
            .outerjoin(
                ExtractionResult,
                ExtractionResult.processing_run_id == ProcessingRun.id,
            )
            .where(ProcessingRun.document_id == document.id)
            .order_by(ProcessingRun.queued_at.desc(), ProcessingRun.id.desc())
            .limit(1)
        )
    ).one_or_none()
    latest_run = None
    if latest_run_row is not None:
        run, result = latest_run_row
        latest_run = run_summary(run, result)

    response.headers["Cache-Control"] = "no-store"
    return DocumentDetailResponse(
        id=document.id,
        workspace_id=document.workspace_id,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        byte_size=document.byte_size,
        page_count=document.page_count,
        status=document.status,
        original_available=(
            document.r2_object_key is not None
            and document.sha256 is not None
            and document.original_deleted_at is None
        ),
        original_deleted_at=document.original_deleted_at,
        created_at=document.created_at,
        updated_at=document.updated_at,
        latest_run=latest_run,
    )


@router.post("/{document_id}/view-url", response_model=ViewOriginalResponse)
async def create_view_url(
    document_id: UUID,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> ViewOriginalResponse:
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=False)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    if (
        document.r2_object_key is None
        or document.sha256 is None
        or document.original_deleted_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Original document is unavailable",
        )
    signed_at = datetime.now(UTC)
    try:
        signed = await run_in_threadpool(
            storage.create_download_url,
            document.r2_object_key,
            app_settings.r2_presigned_url_ttl_seconds,
        )
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is temporarily unavailable",
        ) from exc

    response.headers["Cache-Control"] = "no-store"
    return ViewOriginalResponse(
        url=signed.url,
        expires_at=signed_at + timedelta(seconds=app_settings.r2_presigned_url_ttl_seconds),
    )


@router.delete("/{document_id}/original", status_code=status.HTTP_204_NO_CONTENT)
async def delete_original(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> Response:
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    document, _ = row
    if document.r2_object_key is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if document.sha256 is None or document.original_deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Original document is unavailable",
        )

    try:
        await run_in_threadpool(storage.delete_object, document.r2_object_key)
    except ObjectStorageError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is temporarily unavailable",
        ) from exc

    deleted_at = datetime.now(UTC)
    document.r2_object_key = None
    document.original_deleted_at = deleted_at
    document.original_deleted_by = current_auth.user.id
    document.updated_at = deleted_at
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="document.original_deleted",
            entity_type="document",
            entity_id=document.id,
            metadata_={},
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_document(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> Response:
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    document, role = row
    if role not in {MemberRole.OWNER, MemberRole.ADMIN} and (
        document.uploaded_by_user_id != current_auth.user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace role does not permit this action",
        )

    if document.r2_object_key is not None:
        try:
            await run_in_threadpool(storage.delete_object, document.r2_object_key)
        except ObjectStorageError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Object storage is temporarily unavailable",
            ) from exc

    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="document.permanently_deleted",
            entity_type="document",
            entity_id=document.id,
            metadata_={},
        )
    )
    await session.delete(document)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
