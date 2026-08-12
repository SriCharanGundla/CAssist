from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_auth, get_database_session, require_csrf
from app.models import (
    AuditEvent,
    Correction,
    Document,
    ExtractionResult,
    ProcessingRun,
    ReviewStatus,
    WorkspaceMember,
)
from app.schemas.extraction import QualityIssue
from app.schemas.review import (
    ApplyCorrectionsRequest,
    CorrectionResponse,
    ResultResponse,
    ReviewRequest,
)
from app.services.auth import CurrentAuth
from app.services.corrections import InvalidCorrectionPath, apply_corrections, replace_pointer
from app.services.generic_extraction import (
    coerce_stored_extraction,
    coerce_stored_presentation,
    deterministic_quality_issues,
    normalize_extracted_text,
)

router = APIRouter()


async def _authorized_result_for_run(
    session: AsyncSession,
    run_id: UUID,
    user_id: UUID,
) -> tuple[ExtractionResult, ProcessingRun, Document] | None:
    return (
        await session.execute(
            select(ExtractionResult, ProcessingRun, Document)
            .join(ProcessingRun, ProcessingRun.id == ExtractionResult.processing_run_id)
            .join(Document, Document.id == ProcessingRun.document_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
            .where(
                ProcessingRun.id == run_id,
                WorkspaceMember.user_id == user_id,
            )
        )
    ).one_or_none()


async def _authorized_result_by_id(
    session: AsyncSession,
    result_id: UUID,
    user_id: UUID,
    *,
    lock: bool = False,
) -> tuple[ExtractionResult, ProcessingRun, Document] | None:
    statement = (
        select(ExtractionResult, ProcessingRun, Document)
        .join(ProcessingRun, ProcessingRun.id == ExtractionResult.processing_run_id)
        .join(Document, Document.id == ProcessingRun.document_id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(
            ExtractionResult.id == result_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if lock:
        statement = statement.with_for_update(of=ExtractionResult)
    return (await session.execute(statement)).one_or_none()


async def _corrections_for_result(
    session: AsyncSession,
    result_id: UUID,
) -> list[Correction]:
    return list(
        (
            await session.scalars(
                select(Correction)
                .where(Correction.extraction_result_id == result_id)
                .order_by(Correction.created_at, Correction.id)
            )
        ).all()
    )


def _response(
    result: ExtractionResult,
    run: ProcessingRun,
    document: Document,
    corrections: list[Correction],
) -> ResultResponse:
    effective_stored = apply_corrections(result.canonical_data, corrections)
    extracted_data, target_paths = coerce_stored_extraction(
        result.canonical_data,
        result.document_type,
    )
    effective_data, _ = coerce_stored_extraction(effective_stored, result.document_type)
    presentation = coerce_stored_presentation(result.presentation_data, extracted_data)
    path_targets = {path: target_id for target_id, path in target_paths.items()}
    known_target_ids = set(target_paths)
    quality_issues: list[QualityIssue] = []
    for issue_data in result.validation_issues:
        if not isinstance(issue_data, dict) or "target_id" not in issue_data:
            continue
        try:
            issue = QualityIssue.model_validate(issue_data)
            if issue.target_id in known_target_ids:
                quality_issues.append(issue)
        except ValueError:
            continue
    return ResultResponse(
        result_id=result.id,
        run_id=run.id,
        document_id=document.id,
        original_filename=document.original_filename,
        original_mime_type=document.mime_type,
        original_available=(
            document.r2_object_key is not None and document.original_deleted_at is None
        ),
        document_type=result.document_type,
        version=result.version,
        review_status=result.review_status,
        reviewed_by_user_id=result.reviewed_by_user_id,
        reviewed_at=result.reviewed_at,
        extracted_data=extracted_data,
        effective_data=effective_data,
        presentation=presentation,
        quality_issues=quality_issues,
        corrections=[
            CorrectionResponse(
                id=correction.id,
                target_id=path_targets.get(correction.field_path, correction.field_path),
                previous_value=(
                    normalize_extracted_text(correction.previous_value)
                    if isinstance(correction.previous_value, str)
                    else correction.previous_value
                ),
                corrected_value=(
                    normalize_extracted_text(correction.corrected_value)
                    if isinstance(correction.corrected_value, str)
                    else correction.corrected_value
                ),
                reason=correction.reason,
                corrected_by_user_id=correction.corrected_by_user_id,
                created_at=correction.created_at,
            )
            for correction in corrections
        ],
    )


@router.get("/runs/{run_id}/result", response_model=ResultResponse)
async def get_result(
    run_id: UUID,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ResultResponse:
    result_row = await _authorized_result_for_run(session, run_id, current_auth.user.id)
    if result_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    result, run, document = result_row
    corrections = await _corrections_for_result(session, result.id)
    response.headers["Cache-Control"] = "no-store"
    return _response(result, run, document, corrections)


@router.get("/results/{result_id}", response_model=ResultResponse)
async def get_result_by_id(
    result_id: UUID,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ResultResponse:
    result_row = await _authorized_result_by_id(session, result_id, current_auth.user.id)
    if result_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    result, run, document = result_row
    corrections = await _corrections_for_result(session, result.id)
    response.headers["Cache-Control"] = "no-store"
    return _response(result, run, document, corrections)


@router.patch("/results/{result_id}/fields", response_model=ResultResponse)
async def apply_result_corrections(
    result_id: UUID,
    payload: ApplyCorrectionsRequest,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ResultResponse:
    result_row = await _authorized_result_by_id(
        session,
        result_id,
        current_auth.user.id,
        lock=True,
    )
    if result_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    result, run, document = result_row
    if result.review_status == ReviewStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Return the result to review before saving corrections",
        )
    if result.version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Result changed; reload before saving corrections",
        )

    existing_corrections = await _corrections_for_result(session, result.id)
    effective_data = apply_corrections(result.canonical_data, existing_corrections)
    _, target_paths = coerce_stored_extraction(effective_data, result.document_type)
    pending_corrections: list[Correction] = []
    correction_time = datetime.now(UTC)
    try:
        for index, change in enumerate(payload.changes):
            field_path = target_paths.get(change.target_id)
            if field_path is None:
                raise InvalidCorrectionPath("Correction target does not exist")
            previous_value = replace_pointer(effective_data, field_path, change.value)
            correction = Correction(
                extraction_result_id=result.id,
                corrected_by_user_id=current_auth.user.id,
                field_path=field_path,
                previous_value=previous_value,
                corrected_value=change.value,
                reason=change.reason,
                created_at=correction_time + timedelta(microseconds=index),
            )
            session.add(correction)
            pending_corrections.append(correction)
        effective_document, _ = coerce_stored_extraction(
            effective_data,
            result.document_type,
        )
    except InvalidCorrectionPath as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    changed_at = datetime.now(UTC)
    deterministic_codes = {"duplicate_observation", "possible_gibberish"}
    corrected_targets = {change.target_id for change in payload.changes}
    retained_issues = [
        issue
        for issue in result.validation_issues
        if isinstance(issue, dict)
        and issue.get("code") not in deterministic_codes
        and issue.get("target_id") not in corrected_targets
    ]
    recomputed_issues = deterministic_quality_issues(effective_document)
    result.validation_issues = [
        *retained_issues,
        *(issue.model_dump(mode="json") for issue in recomputed_issues),
    ][:200]
    result.review_status = ReviewStatus.IN_REVIEW
    result.reviewed_by_user_id = None
    result.reviewed_at = None
    result.version += 1
    result.updated_at = changed_at
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="result.corrected",
            entity_type="extraction_result",
            entity_id=result.id,
            metadata_={
                "correction_count": len(pending_corrections),
                "result_version": result.version,
            },
        )
    )
    await session.flush()
    await session.commit()
    return _response(
        result,
        run,
        document,
        [*existing_corrections, *pending_corrections],
    )


@router.post("/results/{result_id}/review", response_model=ResultResponse)
async def review_result(
    result_id: UUID,
    payload: ReviewRequest,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ResultResponse:
    result_row = await _authorized_result_by_id(
        session,
        result_id,
        current_auth.user.id,
        lock=True,
    )
    if result_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    result, run, document = result_row
    if result.version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Result changed; reload before updating review status",
        )

    requested_status = ReviewStatus(payload.status)
    corrections = await _corrections_for_result(session, result.id)
    if result.review_status == requested_status:
        return _response(result, run, document, corrections)

    changed_at = datetime.now(UTC)
    result.review_status = requested_status
    result.reviewed_by_user_id = (
        current_auth.user.id if requested_status == ReviewStatus.APPROVED else None
    )
    result.reviewed_at = changed_at if requested_status == ReviewStatus.APPROVED else None
    result.version += 1
    result.updated_at = changed_at
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="result.review_status_changed",
            entity_type="extraction_result",
            entity_id=result.id,
            metadata_={
                "review_status": requested_status.value,
                "result_version": result.version,
            },
        )
    )
    await session.commit()
    return _response(result, run, document, corrections)
