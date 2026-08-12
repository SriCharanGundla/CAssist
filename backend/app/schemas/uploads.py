from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

AllowedUploadMimeType = Literal[
    "application/pdf",
    "image/jpeg",
    "image/png",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]


class CreateUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: AllowedUploadMimeType
    byte_size: int = Field(gt=0)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("filename contains unsupported characters")
        if "/" in value or "\\" in value:
            raise ValueError("filename must not contain a path")
        return value


class UploadTargetResponse(BaseModel):
    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]
    expires_at: datetime


class CreateUploadResponse(BaseModel):
    document_id: UUID
    upload: UploadTargetResponse


class CompleteUploadResponse(BaseModel):
    document_id: UUID
    status: Literal["uploaded", "processing", "ready", "failed"]
    deduplicated: bool = False
