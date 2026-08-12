from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
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
from app.schemas.extraction import CanonicalInvoice
from app.schemas.review import (
    ApplyCorrectionsRequest,
    CorrectionResponse,
    ResultResponse,
    ReviewRequest,
)
from app.services.auth import CurrentAuth
from app.services.corrections import InvalidCorrectionPath, apply_corrections, replace_pointer
from app.services.invoice_validation import validate_invoice

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
    corrections: list[Correction],
) -> ResultResponse:
    effective_data = apply_corrections(result.canonical_data, corrections)
    invoice = CanonicalInvoice.model_validate(effective_data)
    return ResultResponse(
        result_id=result.id,
        run_id=run.id,
        document_type=result.document_type,
        version=result.version,
        review_status=result.review_status,
        reviewed_by_user_id=result.reviewed_by_user_id,
        reviewed_at=result.reviewed_at,
        canonical_data=result.canonical_data,
        effective_data=invoice.model_dump(mode="json"),
        validation_issues=validate_invoice(invoice),
        corrections=[
            CorrectionResponse(
                id=correction.id,
                field_path=correction.field_path,
                previous_value=correction.previous_value,
                corrected_value=correction.corrected_value,
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
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ResultResponse:
    result_row = await _authorized_result_for_run(session, run_id, current_auth.user.id)
    if result_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    result, run, _ = result_row
    corrections = await _corrections_for_result(session, result.id)
    return _response(result, run, corrections)


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
    if result.version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Result changed; reload before saving corrections",
        )

    existing_corrections = await _corrections_for_result(session, result.id)
    effective_data = apply_corrections(result.canonical_data, existing_corrections)
    pending_corrections: list[Correction] = []
    correction_time = datetime.now(UTC)
    try:
        for index, change in enumerate(payload.changes):
            previous_value = replace_pointer(effective_data, change.field_path, change.value)
            correction = Correction(
                extraction_result_id=result.id,
                corrected_by_user_id=current_auth.user.id,
                field_path=change.field_path,
                previous_value=previous_value,
                corrected_value=change.value,
                reason=change.reason,
                created_at=correction_time + timedelta(microseconds=index),
            )
            session.add(correction)
            pending_corrections.append(correction)
        invoice = CanonicalInvoice.model_validate(effective_data)
    except InvalidCorrectionPath as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Corrected data does not match the canonical invoice schema",
        ) from exc

    changed_at = datetime.now(UTC)
    result.validation_issues = [
        issue.model_dump(mode="json") for issue in validate_invoice(invoice)
    ]
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
    return _response(result, run, [*existing_corrections, *pending_corrections])


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
        return _response(result, run, corrections)

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
    return _response(result, run, corrections)
