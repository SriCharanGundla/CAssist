from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_auth, get_database_session, require_csrf
from app.models import (
    AuditEvent,
    Document,
    DocumentStatus,
    ExtractionResult,
    ProcessingRun,
    ProcessingStage,
    RunStatus,
    WorkspaceMember,
)
from app.schemas.documents import CancelRunResponse, RunDetailResponse
from app.services.auth import CurrentAuth
from app.services.run_status import run_detail

router = APIRouter(prefix="/runs")


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: UUID,
    response: Response,
    current_auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RunDetailResponse:
    row = (
        await session.execute(
            select(ProcessingRun, Document, ExtractionResult)
            .join(Document, Document.id == ProcessingRun.document_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
            .outerjoin(
                ExtractionResult,
                ExtractionResult.processing_run_id == ProcessingRun.id,
            )
            .where(
                ProcessingRun.id == run_id,
                WorkspaceMember.user_id == current_auth.user.id,
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run, document, result = row
    response.headers["Cache-Control"] = "no-store"
    return run_detail(run, document, result)


@router.post("/{run_id}/cancel", response_model=CancelRunResponse, status_code=202)
async def cancel_run(
    run_id: UUID,
    current_auth: Annotated[CurrentAuth, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CancelRunResponse:
    row = (
        await session.execute(
            select(ProcessingRun, Document)
            .join(Document, Document.id == ProcessingRun.document_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
            .where(
                ProcessingRun.id == run_id,
                WorkspaceMember.user_id == current_auth.user.id,
            )
            .with_for_update(of=ProcessingRun)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run, document = row
    if run.status == RunStatus.CANCELLED:
        return CancelRunResponse(run_id=run.id)
    if run.status not in {
        RunStatus.QUEUED,
        RunStatus.PREPROCESSING,
        RunStatus.EXTRACTING,
        RunStatus.VALIDATING,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only queued or active processing can be cancelled",
        )

    cancelled_at = datetime.now(UTC)
    run.status = RunStatus.CANCELLED
    run.progress_stage = ProcessingStage.FAILED.value
    run.worker_id = None
    run.lease_expires_at = None
    run.completed_at = cancelled_at
    prior_success = await session.scalar(
        select(ProcessingRun.id)
        .where(
            ProcessingRun.document_id == document.id,
            ProcessingRun.id != run.id,
            ProcessingRun.status == RunStatus.SUCCEEDED,
        )
        .limit(1)
    )
    document.status = DocumentStatus.READY if prior_success else DocumentStatus.FAILED
    document.updated_at = cancelled_at
    session.add(
        AuditEvent(
            workspace_id=document.workspace_id,
            actor_user_id=current_auth.user.id,
            action="document.processing_cancelled",
            entity_type="processing_run",
            entity_id=run.id,
            metadata_={},
        )
    )
    await session.commit()
    return CancelRunResponse(run_id=run.id)
