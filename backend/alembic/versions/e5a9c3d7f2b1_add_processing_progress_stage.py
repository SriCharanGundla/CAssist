"""add processing progress stage

Revision ID: e5a9c3d7f2b1
Revises: d4f8a2c7e1b9
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5a9c3d7f2b1"
down_revision: str | None = "d4f8a2c7e1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_runs",
        sa.Column("progress_stage", sa.Text(), server_default="queued", nullable=False),
    )
    op.create_check_constraint(
        "processing_runs_progress_stage_valid",
        "processing_runs",
        "progress_stage IN ('queued', 'preparing', 'classifying', 'extracting', "
        "'quality_check', 'saving', 'complete', 'failed')",
    )
    op.execute(
        """
        UPDATE processing_runs
        SET progress_stage = CASE status::text
            WHEN 'preprocessing' THEN 'preparing'
            WHEN 'extracting' THEN 'extracting'
            WHEN 'validating' THEN 'saving'
            WHEN 'succeeded' THEN 'complete'
            WHEN 'failed' THEN 'failed'
            WHEN 'cancelled' THEN 'failed'
            ELSE 'queued'
        END
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "processing_runs_progress_stage_valid",
        "processing_runs",
        type_="check",
    )
    op.drop_column("processing_runs", "progress_stage")
