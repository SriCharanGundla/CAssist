"""add processing run leases

Revision ID: a8f31c2d4e6b
Revises: 4d026d59a12e
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8f31c2d4e6b"
down_revision: str | None = "4d026d59a12e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processing_runs", sa.Column("worker_id", sa.Text(), nullable=True))
    op.add_column(
        "processing_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "processing_runs_reclaim_idx",
        "processing_runs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'preprocessing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "processing_runs_reclaim_idx",
        table_name="processing_runs",
        postgresql_where=sa.text("status = 'preprocessing'"),
    )
    op.drop_column("processing_runs", "lease_expires_at")
    op.drop_column("processing_runs", "worker_id")
