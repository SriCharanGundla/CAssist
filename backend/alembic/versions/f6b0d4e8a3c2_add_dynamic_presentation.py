"""add dynamic presentation

Revision ID: f6b0d4e8a3c2
Revises: e5a9c3d7f2b1
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6b0d4e8a3c2"
down_revision: str | None = "e5a9c3d7f2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_results",
        sa.Column(
            "presentation_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{\"sections\": []}'::jsonb"),
            nullable=False,
        ),
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
        "'organizing', 'quality_check', 'saving', 'complete', 'failed')",
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
        "'quality_check', 'saving', 'complete', 'failed')",
    )
    op.drop_column("extraction_results", "presentation_data")
