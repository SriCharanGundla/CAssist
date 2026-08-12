from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_database_session, require_csrf
from app.models import (
    AuditEvent,
    Correction,
    Document,
    ExportEvent,
    ExportFormat,
    ExtractionResult,
    ProcessingRun,
    ReviewStatus,
    WorkspaceMember,
)
from app.schemas.exports import CreateExportRequest
from app.schemas.extraction import QualityIssue
from app.services.auth import CurrentAuth
from app.services.corrections import apply_corrections
from app.services.generic_extraction import (
    coerce_stored_extraction,
    coerce_stored_presentation,
)
from app.services.tally_export import (
    EXPORTER_VERSION,
    build_tally_handoff,
    serialize_tally_handoff,
)

router = APIRouter()


@router.post("/results/{result_id}/exports")
async def export_result(
    result_id: UUID,
    payload: CreateExportRequest,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    result_row = (
        await session.execute(
            select(ExtractionResult, ProcessingRun, Document)
            .join(ProcessingRun, ProcessingRun.id == ExtractionResult.processing_run_id)
            .join(Document, Document.id == ProcessingRun.document_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
            .where(
                ExtractionResult.id == result_id,
                WorkspaceMember.user_id == current_auth.user.id,
            )
            .with_for_update(of=ExtractionResult)
        )
    ).one_or_none()
    if result_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    result, _, document = result_row
    if result.version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Result changed; reload before exporting",
        )
    if result.review_status != ReviewStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Result must be approved before export",
        )

    corrections = list(
        (
            await session.scalars(
                select(Correction)
                .where(Correction.extraction_result_id == result.id)
                .order_by(Correction.created_at, Correction.id)
            )
        ).all()
    )
    document_data = apply_corrections(result.canonical_data, corrections)
    try:
        extracted_document, _ = coerce_stored_extraction(
            document_data,
            result.document_type,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Effective result is invalid and cannot be exported",
        ) from exc
    presentation = coerce_stored_presentation(result.presentation_data, extracted_document)
    issues: list[QualityIssue] = []
    for issue_data in result.validation_issues:
        if isinstance(issue_data, dict) and "target_id" in issue_data:
            try:
                issues.append(QualityIssue.model_validate(issue_data))
            except ValueError:
                continue
    export_payload = build_tally_handoff(
        result_id=result.id,
        result_version=result.version,
        document=extracted_document,
        presentation=presentation,
        quality_issues=issues,
        include_quality_issues=payload.options.include_quality_issues,
    )
    export_bytes = serialize_tally_handoff(export_payload)

    event_options = {
        "include_quality_issues": payload.options.include_quality_issues,
        "result_version": result.version,
        "native_import_ready": False,
    }
    session.add(
        ExportEvent(
            extraction_result_id=result.id,
            exported_by_user_id=current_auth.user.id,
            format=ExportFormat.TALLY_JSON,
            exporter_version=EXPORTER_VERSION,
            options=event_options,
        )
    )
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="result.exported",
            entity_type="extraction_result",
            entity_id=result.id,
            metadata_={
                "format": ExportFormat.TALLY_JSON.value,
                "exporter_version": EXPORTER_VERSION,
                "result_version": result.version,
            },
        )
    )
    await session.commit()

    return Response(
        content=export_bytes,
        media_type="application/json",
        headers={
            "Content-Disposition": (f'attachment; filename="cassist-tally-{result.id}.json"'),
            "Cache-Control": "no-store",
        },
    )
