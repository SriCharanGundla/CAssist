from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import (
    DocumentStatus,
    ModelProvider,
    ProcessingStage,
    ReviewStatus,
    RunStatus,
)


class RunSummaryResponse(BaseModel):
    id: UUID
    status: RunStatus
    provider: ModelProvider
    model_id: str
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancellation_requested_at: datetime | None
    result_id: UUID | None
    review_status: ReviewStatus | None
    classification_scope: Literal["supported", "unrelated", "uncertain"] | None
    classification_reason_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: str | None


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


class DocumentListItemResponse(DocumentDetailResponse):
    pass


class DocumentListResponse(BaseModel):
    items: list[DocumentListItemResponse]
    next_cursor: str | None


class RunProgressResponse(BaseModel):
    stage: ProcessingStage
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


class RetryDocumentResponse(BaseModel):
    document_id: UUID
    run_id: UUID
    status: Literal["uploaded"] = "uploaded"


class ConfirmDocumentResponse(BaseModel):
    document_id: UUID
    run_id: UUID
    status: Literal["uploaded"] = "uploaded"


class CreateRunRequest(BaseModel):
    provider: Literal["openai", "gemini"] | None = None
    model_id: str | None = None
    force: bool = False


class CreateRunResponse(BaseModel):
    run_id: UUID
    status: RunStatus
    cache_hit: bool


class CancelRunResponse(BaseModel):
    run_id: UUID
    status: Literal["stopping", "cancelled"]


class ComparisonRunResponse(BaseModel):
    provider: ModelProvider
    model_id: str
    run_id: UUID
    status: RunStatus
    cache_hit: bool
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: str | None
    quality_issue_count: int | None
    correction_count: int | None
    structural_failure: bool


class ComparisonDifferenceResponse(BaseModel):
    kind: Literal["field", "table_header", "table_cell", "text"]
    label: str | None
    value: str
    gemini_count: int
    openai_count: int


class ComparisonAgreementResponse(BaseModel):
    compared_observations: int
    matching_observations: int
    match_rate: float
    difference_count: int
    differences: list[ComparisonDifferenceResponse]


class ComparisonResponse(BaseModel):
    document_id: UUID
    runs: list[ComparisonRunResponse]
    agreement: ComparisonAgreementResponse | None
