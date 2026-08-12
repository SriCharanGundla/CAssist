from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.extraction import ValidationIssue


class CorrectionChange(BaseModel):
    field_path: str = Field(min_length=1, max_length=512)
    value: Any
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("field_path")
    @classmethod
    def require_json_pointer(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("field_path must be a JSON Pointer")
        return value

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


class CorrectionResponse(BaseModel):
    id: UUID
    field_path: str
    previous_value: Any | None
    corrected_value: Any | None
    reason: str | None
    corrected_by_user_id: UUID
    created_at: datetime


class ResultResponse(BaseModel):
    result_id: UUID
    run_id: UUID
    document_type: str
    version: int
    review_status: Literal["unreviewed", "in_review", "approved"]
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    canonical_data: dict[str, Any]
    effective_data: dict[str, Any]
    validation_issues: list[ValidationIssue]
    corrections: list[CorrectionResponse]
