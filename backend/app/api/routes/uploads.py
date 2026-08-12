from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_app_settings,
    get_database_session,
    get_object_storage,
    require_csrf,
)
from app.core.config import Settings
from app.models import Document, DocumentStatus, WorkspaceMember
from app.schemas.uploads import CreateUploadRequest, CreateUploadResponse, UploadTargetResponse
from app.services.auth import CurrentAuth
from app.services.object_storage import ObjectStorage, ObjectStorageError

router = APIRouter(prefix="/uploads")


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

    current_time = datetime.now(UTC)
    expires_at = current_time + timedelta(seconds=app_settings.r2_presigned_url_ttl_seconds)
    object_key = f"originals/{uuid4().hex}"
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
