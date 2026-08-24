from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ReviewStatus
from app.schemas.extraction import DocumentPresentation, GenericDocumentExtraction, QualityIssue


class CorrectionChange(BaseModel):
    target_id: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=20_000)
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ApplyCorrectionsRequest(BaseModel):
    expected_version: int = Field(ge=1)
    changes: list[CorrectionChange] = Field(min_length=1, max_length=50)


class ReviewRequest(BaseModel):
    expected_version: int = Field(ge=1)
    status: Literal["in_review", "approved"]


class UpdateSelectionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    excluded_target_ids: list[str] = Field(default_factory=list, max_length=2_600)

    @field_validator("excluded_target_ids")
    @classmethod
    def validate_excluded_target_ids(cls, target_ids: list[str]) -> list[str]:
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("Excluded target IDs must be unique")
        return target_ids


class CorrectionResponse(BaseModel):
    id: UUID
    target_id: str
    previous_value: Any | None
    corrected_value: Any | None
    reason: str | None
    corrected_by_user_id: UUID
    created_at: datetime


class ResultResponse(BaseModel):
    result_id: UUID
    run_id: UUID
    document_id: UUID
    original_filename: str
    original_mime_type: str
    original_available: bool
    document_type: str
    version: int
    review_status: ReviewStatus
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    extracted_data: GenericDocumentExtraction
    effective_data: GenericDocumentExtraction
    presentation: DocumentPresentation
    quality_issues: list[QualityIssue]
    corrections: list[CorrectionResponse]
