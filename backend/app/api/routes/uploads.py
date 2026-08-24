from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, text
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
    DocumentStatus,
    ModelProvider,
    ProcessingRun,
    RunStatus,
    Workspace,
    WorkspaceMember,
)
from app.schemas.uploads import (
    AllowedUploadMimeType,
    CompleteUploadResponse,
    CreateUploadRequest,
    CreateUploadResponse,
    StorageQuotaResponse,
    UploadCapabilitiesResponse,
    UploadTargetResponse,
)
from app.services.auth import CurrentAuth
from app.services.object_deletion import enqueue_object_deletion, flush_enqueued_deletions
from app.services.object_keys import permanent_key_for_incoming
from app.services.object_storage import ObjectNotFoundError, ObjectStorage, ObjectStorageError
from app.services.upload_verification import UploadValidationError, verify_upload

router = APIRouter(prefix="/uploads")
_STORAGE_QUOTA_ADVISORY_LOCK_ID = 1_434_152_835
_ACCEPTED_MIME_TYPES: list[AllowedUploadMimeType] = [
    "application/pdf",
    "image/jpeg",
    "image/png",
]
_ACCEPTED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png"]


async def _used_storage_bytes(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(Document.byte_size), 0)).where(
                Document.r2_object_key.is_not(None)
            )
        )
        or 0
    )


def _storage_quota_response(used_bytes: int, limit_bytes: int) -> StorageQuotaResponse:
    available_bytes = max(0, limit_bytes - used_bytes)
    return StorageQuotaResponse(
        used_bytes=used_bytes,
        limit_bytes=limit_bytes,
        available_bytes=available_bytes,
        usage_percent=min(100.0, (used_bytes / limit_bytes) * 100),
        upload_allowed=available_bytes > 0,
    )


async def _discard_invalid_upload(
    session: AsyncSession,
    storage: ObjectStorage,
    document_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    incoming_key: str,
) -> None:
    await session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    document = await session.scalar(
        select(Document)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(
            Document.id == document_id,
            WorkspaceMember.user_id == user_id,
        )
        .with_for_update(of=Document)
    )
    if (
        document is None
        or document.status != DocumentStatus.UPLOAD_PENDING
        or document.r2_object_key != incoming_key
    ):
        await session.rollback()
        return
    pending = enqueue_object_deletion(session, incoming_key)
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=user_id,
            action="document.upload_rejected",
            entity_type="document",
            entity_id=document.id,
            metadata_={},
        )
    )
    await session.delete(document)
    await session.flush()
    deletion_ids = [pending.id] if pending is not None else []
    await session.commit()
    await flush_enqueued_deletions(session, storage, deletion_ids)


@router.get("/quota", response_model=StorageQuotaResponse)
async def get_storage_quota(
    response: Response,
    _: Annotated[CurrentAuth, Depends(get_current_auth)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> StorageQuotaResponse:
    response.headers["Cache-Control"] = "no-store"
    return _storage_quota_response(
        await _used_storage_bytes(session),
        app_settings.r2_storage_quota_bytes,
    )


@router.get("/capabilities", response_model=UploadCapabilitiesResponse)
async def get_upload_capabilities(
    response: Response,
    _: Annotated[CurrentAuth, Depends(get_current_auth)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
) -> UploadCapabilitiesResponse:
    response.headers["Cache-Control"] = "no-store"
    return UploadCapabilitiesResponse(
        accepted_mime_types=_ACCEPTED_MIME_TYPES,
        accepted_extensions=_ACCEPTED_EXTENSIONS,
        maximum_file_bytes=app_settings.upload_max_bytes,
        maximum_batch_files=app_settings.upload_max_files,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_upload(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> Response:
    """Cancel an unfinished upload and remove its object and database record."""
    workspace_id = await session.scalar(
        select(Document.workspace_id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(
            Document.id == document_id,
            WorkspaceMember.user_id == current_auth.user.id,
        )
    )
    if workspace_id is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Upload completion uses the same lock order, making cancellation authoritative
    # even when it races the verification request.
    await session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    document = await session.scalar(
        select(Document)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(
            Document.id == document_id,
            WorkspaceMember.user_id == current_auth.user.id,
        )
        .with_for_update(of=Document)
    )
    if document is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    runs = list(
        (
            await session.scalars(
                select(ProcessingRun)
                .where(ProcessingRun.document_id == document.id)
                .with_for_update()
            )
        ).all()
    )
    is_pending = document.status == DocumentStatus.UPLOAD_PENDING
    is_completed_but_unclaimed = (
        document.status == DocumentStatus.UPLOADED
        and bool(runs)
        and all(run.status == RunStatus.QUEUED for run in runs)
    )
    if not is_pending and not is_completed_but_unclaimed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The upload has already started processing",
        )

    object_key = document.r2_object_key
    pending_deletions = [enqueue_object_deletion(session, object_key)]
    if is_pending and object_key is not None:
        pending_deletions.append(
            enqueue_object_deletion(session, permanent_key_for_incoming(object_key))
        )

    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="document.upload_cancelled",
            entity_type="document",
            entity_id=document.id,
            metadata_={},
        )
    )
    await session.delete(document)
    await session.flush()
    deletion_ids = [pending.id for pending in pending_deletions if pending is not None]
    await session.commit()
    await flush_enqueued_deletions(session, storage, deletion_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=CreateUploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload(
    payload: CreateUploadRequest,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> CreateUploadResponse:
    if payload.byte_size > app_settings.upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Document exceeds the upload size limit",
        )

    workspace_id = await session.scalar(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == current_auth.user.id)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.workspace_id)
        .limit(1)
    )
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A workspace membership is required",
        )

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _STORAGE_QUOTA_ADVISORY_LOCK_ID},
    )
    used_bytes = await _used_storage_bytes(session)
    if used_bytes + payload.byte_size > app_settings.r2_storage_quota_bytes:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail=("Shared document storage is full. Delete stored files before uploading more."),
        )

    current_time = datetime.now(UTC)
    expires_at = current_time + timedelta(seconds=app_settings.r2_presigned_url_ttl_seconds)
    object_key = f"incoming/{uuid4().hex}"
    document = Document(
        workspace_id=workspace_id,
        uploaded_by_user_id=current_auth.user.id,
        original_filename=payload.filename,
        mime_type=payload.mime_type,
        byte_size=payload.byte_size,
        page_count=None,
        sha256=None,
        r2_object_key=object_key,
        status=DocumentStatus.UPLOAD_PENDING,
        upload_expires_at=expires_at,
        original_deleted_at=None,
        original_deleted_by=None,
    )
    session.add(document)

    try:
        await session.flush()
        upload = storage.create_upload_url(
            object_key,
            payload.mime_type,
            payload.byte_size,
            app_settings.r2_presigned_url_ttl_seconds,
        )
        await session.commit()
    except ObjectStorageError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is temporarily unavailable",
        ) from exc

    return CreateUploadResponse(
        document_id=document.id,
        upload=UploadTargetResponse(
            url=upload.url,
            headers=upload.headers,
            expires_at=expires_at,
        ),
    )


@router.post(
    "/{document_id}/complete",
    response_model=CompleteUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_upload(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> CompleteUploadResponse:
    document = await session.scalar(
        select(Document)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(
            Document.id == document_id,
            WorkspaceMember.user_id == current_auth.user.id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if document.status != DocumentStatus.UPLOAD_PENDING:
        return CompleteUploadResponse(document_id=document.id, status=document.status)
    if document.r2_object_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document has no pending upload object",
        )

    user_id = current_auth.user.id
    incoming_key = document.r2_object_key
    workspace_id = document.workspace_id
    expected_byte_size = document.byte_size
    expected_mime_type = document.mime_type
    await session.rollback()

    try:
        verified = await run_in_threadpool(
            verify_upload,
            storage,
            incoming_key,
            expected_byte_size,
            expected_mime_type,
            app_settings.upload_max_bytes,
        )
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The uploaded object was not found",
        ) from exc
    except UploadValidationError as exc:
        await _discard_invalid_upload(
            session,
            storage,
            document_id,
            user_id,
            workspace_id,
            incoming_key,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is temporarily unavailable",
        ) from exc

    permanent_key: str | None = None
    committed = False
    try:
        await session.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
        )
        document = await session.scalar(
            select(Document)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
            .where(
                Document.id == document_id,
                WorkspaceMember.user_id == user_id,
            )
            .with_for_update()
        )
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        if document.status != DocumentStatus.UPLOAD_PENDING:
            return CompleteUploadResponse(document_id=document.id, status=document.status)
        if document.r2_object_key != incoming_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pending upload object changed during completion",
            )

        existing_document = await session.scalar(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.sha256 == verified.sha256,
                Document.id != document_id,
            )
        )
        if existing_document is not None:
            existing_id = existing_document.id
            existing_status = existing_document.status
            if existing_document.r2_object_key is None:
                permanent_key = permanent_key_for_incoming(incoming_key)
                await run_in_threadpool(
                    storage.put_object,
                    permanent_key,
                    verified.body,
                    verified.mime_type,
                    verified.byte_size,
                )
                existing_document.r2_object_key = permanent_key
                existing_document.original_deleted_at = None
                existing_document.original_deleted_by = None
                existing_document.updated_at = datetime.now(UTC)
            pending_deletion = enqueue_object_deletion(session, incoming_key)
            await session.delete(document)
            await session.flush()
            deletion_ids = [pending_deletion.id] if pending_deletion is not None else []
            await session.commit()
            committed = True
            await flush_enqueued_deletions(session, storage, deletion_ids)
            return CompleteUploadResponse(
                document_id=existing_id,
                status=existing_status,
                deduplicated=True,
            )

        permanent_key = permanent_key_for_incoming(incoming_key)
        await run_in_threadpool(
            storage.put_object,
            permanent_key,
            verified.body,
            verified.mime_type,
            verified.byte_size,
        )
        document.r2_object_key = permanent_key
        document.sha256 = verified.sha256
        document.status = DocumentStatus.UPLOADED
        document.upload_expires_at = None
        document.updated_at = datetime.now(UTC)
        session.add(
            ProcessingRun(
                document_id=document.id,
                requested_by_user_id=user_id,
                provider=ModelProvider(app_settings.model_provider),
                model_id=app_settings.model_id,
                prompt_version=app_settings.prompt_version,
                schema_version=app_settings.schema_version,
                preprocessing_version=app_settings.preprocessing_version,
                status=RunStatus.QUEUED,
                attempt_count=0,
            )
        )
        pending_deletion = enqueue_object_deletion(session, incoming_key)
        await session.flush()
        deletion_ids = [pending_deletion.id] if pending_deletion is not None else []
        await session.commit()
        committed = True
        await flush_enqueued_deletions(session, storage, deletion_ids)
        return CompleteUploadResponse(document_id=document.id, status=document.status)
    except ObjectStorageError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is temporarily unavailable",
        ) from exc
    finally:
        if permanent_key is not None and not committed:
            try:
                await run_in_threadpool(storage.delete_object, permanent_key)
            except ObjectStorageError:
                pass
        verified.close()
