from enum import StrEnum


class MemberRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class DocumentStatus(StrEnum):
    UPLOAD_PENDING = "upload_pending"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingStage(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    CLASSIFYING = "classifying"
    EXTRACTING = "extracting"
    ORGANIZING = "organizing"
    QUALITY_CHECK = "quality_check"
    SAVING = "saving"
    COMPLETE = "complete"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"


class ModelProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"
    TALLY_JSON = "tally_json"
