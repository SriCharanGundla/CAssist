from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import DocumentStatus, ModelProvider, ReviewStatus, RunStatus


class RunSummaryResponse(BaseModel):
    id: UUID
    status: RunStatus
    provider: ModelProvider
    model_id: str
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_id: UUID | None
    review_status: ReviewStatus | None


class DocumentDetailResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    original_filename: str
    mime_type: str
    byte_size: int
    page_count: int | None
    status: DocumentStatus
    original_available: bool
    original_deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    latest_run: RunSummaryResponse | None


class RunProgressResponse(BaseModel):
    stage: RunStatus
    completed_pages: int | None
    total_pages: int | None


class RunErrorResponse(BaseModel):
    code: str
    message: str


class RunDetailResponse(RunSummaryResponse):
    document_id: UUID
    attempt_count: int
    progress: RunProgressResponse
    error: RunErrorResponse | None


class ViewOriginalResponse(BaseModel):
    url: str
    expires_at: datetime
