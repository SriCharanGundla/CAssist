"""add processing run cancellation requests

Revision ID: a7c9e2f4b6d8
Revises: f6b0d4e8a3c2
Create Date: 2026-08-12 23:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c9e2f4b6d8"
down_revision: str | None = "f6b0d4e8a3c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_runs",
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "processing_runs_progress_stage_valid",
        "processing_runs",
        type_="check",
    )
    op.create_check_constraint(
        "processing_runs_progress_stage_valid",
        "processing_runs",
        "progress_stage IN ('queued', 'preparing', 'classifying', 'extracting', "
        "'organizing', 'quality_check', 'saving', 'stopping', 'complete', "
        "'cancelled', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "processing_runs_progress_stage_valid",
        "processing_runs",
        type_="check",
    )
    op.create_check_constraint(
        "processing_runs_progress_stage_valid",
        "processing_runs",
        "progress_stage IN ('queued', 'preparing', 'classifying', 'extracting', "
        "'organizing', 'quality_check', 'saving', 'complete', 'failed')",
    )
    op.drop_column("processing_runs", "cancellation_requested_at")
