"""add idempotency records and object deletion outbox

Revision ID: c7a1e5d9b3f2
Revises: e6a8c0d2f4b5
Create Date: 2026-08-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c7a1e5d9b3f2"
down_revision: str | None = "e6a8c0d2f4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("request_method", sa.Text(), nullable=False),
        sa.Column("request_path", sa.Text(), nullable=False),
        sa.Column("idempotency_key_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("request_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_token_hash",
            "request_method",
            "request_path",
            "idempotency_key_hash",
            name="idempotency_records_request_key",
        ),
    )
    op.create_index("idempotency_records_expires_idx", "idempotency_records", ["expires_at"])
    op.create_table(
        "pending_object_deletions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "pending_object_deletions_created_idx",
        "pending_object_deletions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("pending_object_deletions_created_idx", table_name="pending_object_deletions")
    op.drop_table("pending_object_deletions")
    op.drop_index("idempotency_records_expires_idx", table_name="idempotency_records")
    op.drop_table("idempotency_records")
