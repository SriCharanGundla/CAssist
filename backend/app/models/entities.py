from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.enums import (
    DocumentStatus,
    ExportFormat,
    MemberRole,
    ModelProvider,
    ReviewStatus,
    RunStatus,
)


def enum_values(enum_class: type) -> list[str]:
    return [member.value for member in enum_class]


member_role_type = ENUM(
    MemberRole,
    name="member_role",
    values_callable=enum_values,
)
document_status_type = ENUM(
    DocumentStatus,
    name="document_status",
    values_callable=enum_values,
)
run_status_type = ENUM(
    RunStatus,
    name="run_status",
    values_callable=enum_values,
)
review_status_type = ENUM(
    ReviewStatus,
    name="review_status",
    values_callable=enum_values,
)
model_provider_type = ENUM(
    ModelProvider,
    name="model_provider",
    values_callable=enum_values,
)
export_format_type = ENUM(
    ExportFormat,
    name="export_format",
    values_callable=enum_values,
)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("users_email_lower_idx", func.lower(text("email")), unique=True),)

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    external_auth_id: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[MemberRole] = mapped_column(
        member_role_type, server_default=MemberRole.MEMBER.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name="auth_sessions_expiry_ordered",
        ),
        Index(
            "auth_sessions_user_active_idx",
            "user_id",
            "absolute_expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="documents_byte_size_positive"),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="documents_page_count_positive",
        ),
        CheckConstraint(
            "original_deleted_at IS NULL OR "
            "(r2_object_key IS NULL AND original_deleted_by IS NOT NULL)",
            name="documents_original_deletion_consistent",
        ),
        Index(
            "documents_workspace_sha256_idx",
            "workspace_id",
            "sha256",
            unique=True,
            postgresql_where=text("sha256 IS NOT NULL"),
        ),
        Index("documents_workspace_created_idx", "workspace_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    uploaded_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    original_filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None]
    sha256: Mapped[str | None] = mapped_column(CHAR(64))
    r2_object_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DocumentStatus] = mapped_column(
        document_status_type, server_default=DocumentStatus.UPLOAD_PENDING.value
    )
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_deleted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="processing_runs_attempt_count_nonnegative"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="processing_runs_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="processing_runs_output_tokens_nonnegative",
        ),
        CheckConstraint(
            "estimated_cost_usd IS NULL OR estimated_cost_usd >= 0",
            name="processing_runs_estimated_cost_nonnegative",
        ),
        Index(
            "processing_runs_success_cache_idx",
            "document_id",
            "provider",
            "model_id",
            "prompt_version",
            "schema_version",
            "preprocessing_version",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
        Index(
            "processing_runs_queue_idx",
            "queued_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "processing_runs_reclaim_idx",
            "lease_expires_at",
            postgresql_where=text("status = 'preprocessing'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    requested_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[ModelProvider] = mapped_column(model_provider_type)
    model_id: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    preprocessing_version: Mapped[str] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(
        run_status_type, server_default=RunStatus.QUEUED.value
    )
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message_safe: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionResult(Base):
    __tablename__ = "extraction_results"
    __table_args__ = (
        UniqueConstraint("processing_run_id"),
        Index("extraction_results_document_type_idx", "document_type"),
        Index(
            "extraction_results_canonical_gin_idx",
            "canonical_data",
            postgresql_using="gin",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    processing_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="CASCADE")
    )
    document_type: Mapped[str] = mapped_column(Text)
    raw_provider_output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    canonical_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        review_status_type, server_default=ReviewStatus.UNREVIEWED.value
    )
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Correction(Base):
    __tablename__ = "corrections"
    __table_args__ = (
        Index("corrections_result_created_idx", "extraction_result_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    extraction_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("extraction_results.id", ondelete="CASCADE")
    )
    corrected_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    field_path: Mapped[str] = mapped_column(Text)
    previous_value: Mapped[Any | None] = mapped_column(JSONB)
    corrected_value: Mapped[Any | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportEvent(Base):
    __tablename__ = "export_events"
    __table_args__ = (
        Index("export_events_result_created_idx", "extraction_result_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    extraction_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("extraction_results.id", ondelete="CASCADE")
    )
    exported_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    format: Mapped[ExportFormat] = mapped_column(export_format_type)
    exporter_version: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("audit_events_workspace_created_idx", "workspace_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
