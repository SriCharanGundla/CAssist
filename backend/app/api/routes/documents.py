import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, or_, select
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
    ExtractionResult,
    MemberRole,
    ModelProvider,
    ProcessingRun,
    RunStatus,
    WorkspaceMember,
)
from app.schemas.documents import (
    DocumentDetailResponse,
    DocumentListItemResponse,
    DocumentListResponse,
    RetryDocumentResponse,
    ViewOriginalResponse,
)
from app.services.auth import CurrentAuth
from app.services.object_storage import ObjectStorage, ObjectStorageError
from app.services.run_status import run_summary

router = APIRouter(prefix="/documents")


def _encode_cursor(document: Document) -> str:
    payload = json.dumps(
        {"created_at": document.created_at.isoformat(), "id": str(document.id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(payload["id"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid document cursor",
        ) from exc


def _document_response(
    document: Document,
    latest_run: ProcessingRun | None,
    latest_result: ExtractionResult | None,
) -> DocumentListItemResponse:
    return DocumentListItemResponse(
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
        latest_run=(
            run_summary(latest_run, latest_result) if latest_run is not None else None
        ),
    )


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


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    document_type: Annotated[Literal["tax_invoice"] | None, Query()] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> DocumentListResponse:
    statement = (
        select(Document)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(WorkspaceMember.user_id == current_auth.user.id)
    )
    if document_status is not None:
        statement = statement.where(Document.status == document_status)
    if document_type is not None:
        typed_document_ids = (
            select(ProcessingRun.document_id)
            .join(ExtractionResult, ExtractionResult.processing_run_id == ProcessingRun.id)
            .where(ExtractionResult.document_type == document_type)
        )
        statement = statement.where(Document.id.in_(typed_document_ids))
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                Document.created_at < cursor_created_at,
                and_(Document.created_at == cursor_created_at, Document.id < cursor_id),
            )
        )
    documents = list(
        (
            await session.scalars(
                statement.order_by(Document.created_at.desc(), Document.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    has_more = len(documents) > limit
    documents = documents[:limit]

    latest_by_document: dict[UUID, tuple[ProcessingRun, ExtractionResult | None]] = {}
    if documents:
        latest_rows = (
            await session.execute(
                select(ProcessingRun, ExtractionResult)
                .outerjoin(
                    ExtractionResult,
                    ExtractionResult.processing_run_id == ProcessingRun.id,
                )
                .where(ProcessingRun.document_id.in_([document.id for document in documents]))
                .order_by(
                    ProcessingRun.document_id,
                    ProcessingRun.queued_at.desc(),
                    ProcessingRun.id.desc(),
                )
                .distinct(ProcessingRun.document_id)
            )
        ).all()
        latest_by_document = {
            run.document_id: (run, result) for run, result in latest_rows
        }

    response.headers["Cache-Control"] = "no-store"
    return DocumentListResponse(
        items=[
            _document_response(
                document,
                latest_by_document.get(document.id, (None, None))[0],
                latest_by_document.get(document.id, (None, None))[1],
            )
            for document in documents
        ],
        next_cursor=_encode_cursor(documents[-1]) if has_more else None,
    )


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


@router.post(
    "/{document_id}/retry",
    response_model=RetryDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document_processing(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RetryDocumentResponse:
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=True)
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
    latest_run = await session.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.document_id == document.id)
        .order_by(ProcessingRun.queued_at.desc(), ProcessingRun.id.desc())
        .limit(1)
    )
    if latest_run is None or latest_run.status != RunStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed processing can be retried",
        )

    retry_run = ProcessingRun(
        document_id=document.id,
        requested_by_user_id=current_auth.user.id,
        provider=ModelProvider(app_settings.model_provider),
        model_id=app_settings.model_id,
        prompt_version=app_settings.prompt_version,
        schema_version=app_settings.schema_version,
        preprocessing_version=app_settings.preprocessing_version,
        status=RunStatus.QUEUED,
        attempt_count=0,
    )
    session.add(retry_run)
    await session.flush()
    document.status = DocumentStatus.UPLOADED
    document.updated_at = datetime.now(UTC)
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="document.processing_retried",
            entity_type="document",
            entity_id=document.id,
            metadata_={},
        )
    )
    await session.commit()
    return RetryDocumentResponse(document_id=document.id, run_id=retry_run.id)


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
