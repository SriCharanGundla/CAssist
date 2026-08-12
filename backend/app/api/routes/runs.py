from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_auth, get_database_session
from app.models import Document, ExtractionResult, ProcessingRun, WorkspaceMember
from app.schemas.documents import RunDetailResponse
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
