from sqlalchemy import CHAR, Numeric
from sqlalchemy.dialects.postgresql import ENUM, JSONB

from app.models import Base

EXPECTED_COLUMNS = {
    "users": {
        "id",
        "external_auth_id",
        "email",
        "display_name",
        "created_at",
        "last_seen_at",
    },
    "workspaces": {"id", "name", "created_by_user_id", "created_at"},
    "workspace_members": {"workspace_id", "user_id", "role", "created_at"},
    "auth_sessions": {
        "id",
        "user_id",
        "token_hash",
        "csrf_token_hash",
        "created_at",
        "last_seen_at",
        "idle_expires_at",
        "absolute_expires_at",
        "revoked_at",
    },
    "documents": {
        "id",
        "workspace_id",
        "uploaded_by_user_id",
        "original_filename",
        "mime_type",
        "byte_size",
        "page_count",
        "sha256",
        "r2_object_key",
        "status",
        "upload_expires_at",
        "original_deleted_at",
        "original_deleted_by",
        "created_at",
        "updated_at",
    },
    "processing_runs": {
        "id",
        "document_id",
        "requested_by_user_id",
        "provider",
        "model_id",
        "prompt_version",
        "schema_version",
        "preprocessing_version",
        "status",
        "attempt_count",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
        "error_code",
        "error_message_safe",
        "queued_at",
        "started_at",
        "completed_at",
        "created_at",
    },
    "extraction_results": {
        "id",
        "processing_run_id",
        "document_type",
        "raw_provider_output",
        "canonical_data",
        "validation_issues",
        "review_status",
        "reviewed_by_user_id",
        "reviewed_at",
        "created_at",
        "updated_at",
    },
    "corrections": {
        "id",
        "extraction_result_id",
        "corrected_by_user_id",
        "field_path",
        "previous_value",
        "corrected_value",
        "reason",
        "created_at",
    },
    "export_events": {
        "id",
        "extraction_result_id",
        "exported_by_user_id",
        "format",
        "exporter_version",
        "options",
        "created_at",
    },
    "audit_events": {
        "id",
        "workspace_id",
        "actor_user_id",
        "action",
        "entity_type",
        "entity_id",
        "metadata",
        "created_at",
    },
}

EXPECTED_ENUMS = {
    "member_role": ["owner", "admin", "member"],
    "document_status": ["upload_pending", "uploaded", "processing", "ready", "failed"],
    "run_status": [
        "queued",
        "preprocessing",
        "extracting",
        "validating",
        "succeeded",
        "failed",
        "cancelled",
    ],
    "review_status": ["unreviewed", "in_review", "approved"],
    "model_provider": ["openai", "gemini"],
    "export_format": ["json", "csv", "xlsx", "tally_json"],
}

EXPECTED_INDEXES = {
    "auth_sessions_user_active_idx",
    "users_email_lower_idx",
    "documents_workspace_sha256_idx",
    "documents_workspace_created_idx",
    "processing_runs_success_cache_idx",
    "processing_runs_queue_idx",
    "extraction_results_document_type_idx",
    "extraction_results_canonical_gin_idx",
    "corrections_result_created_idx",
    "export_events_result_created_idx",
    "audit_events_workspace_created_idx",
}


def test_metadata_matches_locked_tables_and_columns() -> None:
    assert set(Base.metadata.tables) == set(EXPECTED_COLUMNS)
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        assert set(Base.metadata.tables[table_name].columns.keys()) == expected_columns


def test_metadata_matches_locked_enum_values() -> None:
    enum_types = {
        column.type.name: column.type.enums
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, ENUM)
    }
    assert enum_types == EXPECTED_ENUMS


def test_metadata_contains_required_indexes() -> None:
    index_names = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    assert index_names == EXPECTED_INDEXES


def test_hash_cost_and_provider_payload_types_are_exact() -> None:
    documents = Base.metadata.tables["documents"]
    runs = Base.metadata.tables["processing_runs"]
    results = Base.metadata.tables["extraction_results"]

    assert isinstance(documents.c.sha256.type, CHAR)
    assert documents.c.sha256.type.length == 64
    assert isinstance(runs.c.estimated_cost_usd.type, Numeric)
    assert runs.c.estimated_cost_usd.type.precision == 12
    assert runs.c.estimated_cost_usd.type.scale == 6
    assert isinstance(results.c.raw_provider_output.type, JSONB)
    assert isinstance(results.c.canonical_data.type, JSONB)
