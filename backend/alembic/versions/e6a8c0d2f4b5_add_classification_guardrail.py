"""Add classification guardrail states and decisions.

Revision ID: e6a8c0d2f4b5
Revises: d5f7a9c1e3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6a8c0d2f4b5"
down_revision: str | None = "d5f7a9c1e3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'needs_confirmation'")
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'unsupported'")
    op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'needs_confirmation'")
    op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'unsupported'")
    op.add_column("processing_runs", sa.Column("classification_scope", sa.Text()))
    op.add_column("processing_runs", sa.Column("classification_document_type", sa.Text()))
    op.add_column(
        "processing_runs",
        sa.Column("classification_confidence", sa.Numeric(4, 3)),
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
        "'cancelled', 'failed', 'needs_confirmation', 'unsupported')",
    )
    op.add_column("processing_runs", sa.Column("classification_reason_code", sa.Text()))
    op.add_column(
        "processing_runs",
        sa.Column(
            "classification_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "processing_runs_classification_scope_valid",
        "processing_runs",
        "classification_scope IS NULL OR "
        "classification_scope IN ('supported', 'unrelated', 'uncertain')",
    )
    op.create_check_constraint(
        "processing_runs_classification_confidence_valid",
        "processing_runs",
        "classification_confidence IS NULL OR "
        "(classification_confidence >= 0 AND classification_confidence <= 1)",
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
        "'organizing', 'quality_check', 'saving', 'stopping', 'complete', "
        "'cancelled', 'failed')",
    )
    op.drop_constraint(
        "processing_runs_classification_confidence_valid",
        "processing_runs",
        type_="check",
    )
    op.drop_constraint(
        "processing_runs_classification_scope_valid",
        "processing_runs",
        type_="check",
    )
    op.drop_column("processing_runs", "classification_override")
    op.drop_column("processing_runs", "classification_reason_code")
    op.drop_column("processing_runs", "classification_confidence")
    op.drop_column("processing_runs", "classification_document_type")
    op.drop_column("processing_runs", "classification_scope")
    # PostgreSQL enum values are intentionally retained on downgrade.
