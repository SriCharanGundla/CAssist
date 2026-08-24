import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated
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
    ConfirmDocumentResponse,
    CreateRunRequest,
    CreateRunResponse,
    DocumentDetailResponse,
    DocumentListItemResponse,
    DocumentListResponse,
    RetryDocumentResponse,
    ViewOriginalResponse,
)
from app.services.auth import CurrentAuth
from app.services.document_access import (
    authorized_document,
    require_available_original,
    require_scope_allows_new_run,
)
from app.services.model_provider import ModelSelection, resolve_model_selection
from app.services.object_deletion import enqueue_object_deletion, flush_enqueued_deletions
from app.services.object_storage import ObjectStorage, ObjectStorageError
from app.services.processing_runs import find_configured_run, queue_processing_run
from app.services.run_status import run_summary

router = APIRouter(prefix="/documents")
_ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.PREPROCESSING,
    RunStatus.EXTRACTING,
    RunStatus.VALIDATING,
}


async def _require_no_active_run(session: AsyncSession, document_id: UUID) -> None:
    active_run = await session.scalar(
        select(ProcessingRun.id)
        .where(
            ProcessingRun.document_id == document_id,
            ProcessingRun.status.in_(_ACTIVE_RUN_STATUSES),
        )
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stop document processing before deleting it",
        )


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
        latest_run=(run_summary(latest_run, latest_result) if latest_run is not None else None),
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    document_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
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
    if search is not None:
        statement = statement.where(
            Document.original_filename.icontains(search.strip(), autoescape=True)
        )
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
                statement.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit + 1)
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
        latest_by_document = {run.document_id: (run, result) for run, result in latest_rows}

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
    row = await authorized_document(session, document_id, current_auth.user.id, lock=False)
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


@router.post("/{document_id}/runs", response_model=CreateRunResponse)
async def create_processing_run(
    document_id: UUID,
    payload: CreateRunRequest,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CreateRunResponse:
    row = await authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    require_available_original(document)
    require_scope_allows_new_run(document)
    try:
        selection = resolve_model_selection(
            app_settings,
            payload.provider,
            payload.model_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider override is disabled",
        ) from exc

    existing = None
    if not payload.force:
        existing = await find_configured_run(
            session,
            document.id,
            selection,
            app_settings,
        )
    if existing is not None:
        cache_hit = existing.status == RunStatus.SUCCEEDED
        response.status_code = status.HTTP_200_OK if cache_hit else status.HTTP_202_ACCEPTED
        return CreateRunResponse(
            run_id=existing.id,
            status=existing.status,
            cache_hit=cache_hit,
        )

    run = queue_processing_run(
        session,
        document,
        current_auth.user.id,
        selection,
        app_settings,
        force=payload.force,
    )
    document.status = DocumentStatus.UPLOADED
    document.updated_at = datetime.now(UTC)
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="document.processing_requested",
            entity_type="document",
            entity_id=document.id,
            metadata_={"forced": payload.force},
        )
    )
    await session.commit()
    response.status_code = status.HTTP_202_ACCEPTED
    return CreateRunResponse(run_id=run.id, status=run.status, cache_hit=False)


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
    row = await authorized_document(session, document_id, current_auth.user.id, lock=True)
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
        classification_scope=latest_run.classification_scope,
        classification_document_type=latest_run.classification_document_type,
        classification_confidence=latest_run.classification_confidence,
        classification_reason_code=latest_run.classification_reason_code,
        classification_override=latest_run.classification_override,
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


@router.post(
    "/{document_id}/confirm-processing",
    response_model=ConfirmDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_document_processing(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ConfirmDocumentResponse:
    row = await authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    require_available_original(document)
    latest_run = await session.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.document_id == document.id)
        .order_by(ProcessingRun.queued_at.desc(), ProcessingRun.id.desc())
        .limit(1)
    )
    if (
        document.status != DocumentStatus.NEEDS_CONFIRMATION
        or latest_run is None
        or latest_run.status != RunStatus.NEEDS_CONFIRMATION
        or latest_run.classification_scope != "uncertain"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only uncertain documents can be confirmed",
        )

    selection = ModelSelection(
        provider=latest_run.provider.value,
        model_id=latest_run.model_id,
    )
    confirmed_run = queue_processing_run(
        session,
        document,
        current_auth.user.id,
        selection,
        app_settings,
        force=True,
        classification_override=True,
    )
    confirmed_run.classification_scope = latest_run.classification_scope
    confirmed_run.classification_document_type = latest_run.classification_document_type
    confirmed_run.classification_confidence = latest_run.classification_confidence
    confirmed_run.classification_reason_code = latest_run.classification_reason_code
    document.status = DocumentStatus.UPLOADED
    document.updated_at = datetime.now(UTC)
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="document.classification_override_confirmed",
            entity_type="document",
            entity_id=document.id,
            metadata_={"prior_run_id": str(latest_run.id)},
        )
    )
    await session.commit()
    return ConfirmDocumentResponse(document_id=document.id, run_id=confirmed_run.id)


@router.post("/{document_id}/view-url", response_model=ViewOriginalResponse)
async def create_view_url(
    document_id: UUID,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> ViewOriginalResponse:
    row = await authorized_document(session, document_id, current_auth.user.id, lock=False)
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
    row = await authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    document, _ = row
    await _require_no_active_run(session, document.id)
    if document.r2_object_key is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if document.sha256 is None or document.original_deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Original document is unavailable",
        )

    deleted_at = datetime.now(UTC)
    pending_deletion = enqueue_object_deletion(session, document.r2_object_key)
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
    await session.flush()
    deletion_ids = [pending_deletion.id] if pending_deletion is not None else []
    await session.commit()
    await flush_enqueued_deletions(session, storage, deletion_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_document(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> Response:
    row = await authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    document, role = row
    await _require_no_active_run(session, document.id)
    if role not in {MemberRole.OWNER, MemberRole.ADMIN} and (
        document.uploaded_by_user_id != current_auth.user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace role does not permit this action",
        )

    pending_deletion = enqueue_object_deletion(session, document.r2_object_key)

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
    await session.flush()
    deletion_ids = [pending_deletion.id] if pending_deletion is not None else []
    await session.commit()
    await flush_enqueued_deletions(session, storage, deletion_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
