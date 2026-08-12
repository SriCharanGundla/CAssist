import base64
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
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
    Correction,
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
    ComparisonAgreementResponse,
    ComparisonDifferenceResponse,
    ComparisonResponse,
    ComparisonRunResponse,
    CreateRunRequest,
    CreateRunResponse,
    DocumentDetailResponse,
    DocumentListItemResponse,
    DocumentListResponse,
    RetryDocumentResponse,
    SpreadsheetPreviewResponse,
    ViewOriginalResponse,
)
from app.services.auth import CurrentAuth
from app.services.model_provider import ModelSelection, resolve_model_selection
from app.services.object_storage import ObjectStorage, ObjectStorageError
from app.services.processing_runs import find_configured_run, queue_processing_run
from app.services.run_status import run_summary
from app.services.spreadsheet_preview import (
    SpreadsheetPreviewError,
    create_spreadsheet_preview,
)

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
    document_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
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


def _require_available_original(document: Document) -> None:
    if (
        document.r2_object_key is None
        or document.sha256 is None
        or document.original_deleted_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Original document is unavailable",
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
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    _require_available_original(document)
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


def _observations(result: ExtractionResult) -> Counter[str]:
    data = result.canonical_data
    observations: list[str] = []
    for field in data.get("fields", []):
        if isinstance(field, dict):
            observations.append(
                json.dumps(["field", field.get("label"), field.get("value")])
            )
    for table in data.get("tables", []):
        if not isinstance(table, dict):
            continue
        table_title = table.get("title") or "Table"
        headers = table.get("headers", [])
        observations.extend(
            json.dumps(["table_header", table_title, value]) for value in headers
        )
        for row in table.get("rows", []):
            if isinstance(row, dict):
                for index, cell in enumerate(row.get("cells", [])):
                    if not isinstance(cell, dict):
                        continue
                    header = headers[index] if index < len(headers) else f"Column {index + 1}"
                    observations.append(
                        json.dumps(
                            ["table_cell", f"{table_title} · {header}", cell.get("value")]
                        )
                    )
    for block in data.get("text_blocks", []):
        if isinstance(block, dict):
            observations.append(json.dumps(["text", None, block.get("text")]))
    return Counter(observations)


def _comparison_differences(
    gemini: Counter[str],
    openai: Counter[str],
) -> list[ComparisonDifferenceResponse]:
    differences: list[ComparisonDifferenceResponse] = []
    for observation in sorted(gemini.keys() | openai.keys()):
        gemini_count = gemini[observation]
        openai_count = openai[observation]
        if gemini_count == openai_count:
            continue
        kind, label, value = json.loads(observation)
        differences.append(
            ComparisonDifferenceResponse(
                kind=kind,
                label=label,
                value=value,
                gemini_count=gemini_count,
                openai_count=openai_count,
            )
        )
    return differences


@router.post("/{document_id}/comparisons", response_model=ComparisonResponse)
async def compare_document_models(
    document_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ComparisonResponse:
    if app_settings.app_env == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=True)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    _require_available_original(document)

    selections = (
        ModelSelection("gemini", app_settings.comparison_gemini_model_id),
        ModelSelection("openai", app_settings.comparison_openai_model_id),
    )
    runs: list[tuple[ProcessingRun, bool]] = []
    queued = False
    for selection in selections:
        run = await find_configured_run(
            session,
            document.id,
            selection,
            app_settings,
            include_failed=True,
        )
        cache_hit = run is not None and run.status == RunStatus.SUCCEEDED
        if run is None:
            run = queue_processing_run(
                session,
                document,
                current_auth.user.id,
                selection,
                app_settings,
            )
            queued = True
        runs.append((run, cache_hit))
    if queued:
        document.status = DocumentStatus.UPLOADED
        document.updated_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                workspace_id=document.workspace_id,
                actor_user_id=current_auth.user.id,
                action="document.comparison_requested",
                entity_type="document",
                entity_id=document.id,
                metadata_={"provider_count": len(selections)},
            )
        )
        await session.commit()

    run_responses: list[ComparisonRunResponse] = []
    successful_results: dict[ModelProvider, ExtractionResult] = {}
    for run, cache_hit in runs:
        result = await session.scalar(
            select(ExtractionResult).where(ExtractionResult.processing_run_id == run.id)
        )
        if result is not None:
            successful_results[run.provider] = result
        correction_count = None
        if result is not None:
            correction_count = await session.scalar(
                select(func.count()).where(Correction.extraction_result_id == result.id)
            )
        latency_ms = None
        if run.started_at is not None and run.completed_at is not None:
            latency_ms = max(0, int((run.completed_at - run.started_at).total_seconds() * 1000))
        run_responses.append(
            ComparisonRunResponse(
                provider=run.provider,
                model_id=run.model_id,
                run_id=run.id,
                status=run.status,
                cache_hit=cache_hit,
                latency_ms=latency_ms,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                estimated_cost_usd=(
                    str(run.estimated_cost_usd) if run.estimated_cost_usd is not None else None
                ),
                quality_issue_count=(
                    len(result.validation_issues) if result is not None else None
                ),
                correction_count=correction_count,
                structural_failure=run.status in {RunStatus.FAILED, RunStatus.CANCELLED},
            )
        )

    agreement = None
    if set(successful_results) == {ModelProvider.GEMINI, ModelProvider.OPENAI}:
        left = _observations(successful_results[ModelProvider.GEMINI])
        right = _observations(successful_results[ModelProvider.OPENAI])
        compared = max(sum(left.values()), sum(right.values()))
        matching = sum((left & right).values())
        differences = _comparison_differences(left, right)
        agreement = ComparisonAgreementResponse(
            compared_observations=compared,
            matching_observations=matching,
            match_rate=round(matching / compared, 4) if compared else 1.0,
            difference_count=len(differences),
            differences=differences[:200],
        )
    return ComparisonResponse(document_id=document.id, runs=run_responses, agreement=agreement)


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


@router.get("/{document_id}/spreadsheet-preview", response_model=SpreadsheetPreviewResponse)
async def get_spreadsheet_preview(
    document_id: UUID,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SpreadsheetPreviewResponse:
    row = await _authorized_document(session, document_id, current_auth.user.id, lock=False)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document, _ = row
    _require_available_original(document)
    if document.mime_type not in {
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is not a supported spreadsheet",
        )
    try:
        sheets, truncated = await run_in_threadpool(
            create_spreadsheet_preview,
            storage,
            document.r2_object_key,
            document.mime_type,
        )
    except SpreadsheetPreviewError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is temporarily unavailable",
        ) from exc
    response.headers["Cache-Control"] = "no-store"
    return SpreadsheetPreviewResponse(sheets=sheets, truncated=truncated)


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
