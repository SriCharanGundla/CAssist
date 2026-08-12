"""expand active run reclaim index

Revision ID: b9e42d5f7a1c
Revises: a8f31c2d4e6b
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9e42d5f7a1c"
down_revision: str | None = "a8f31c2d4e6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "processing_runs_reclaim_idx",
        table_name="processing_runs",
        postgresql_where=sa.text("status = 'preprocessing'"),
    )
    op.create_index(
        "processing_runs_reclaim_idx",
        "processing_runs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('preprocessing', 'extracting', 'validating')"),
    )


def downgrade() -> None:
    op.drop_index(
        "processing_runs_reclaim_idx",
        table_name="processing_runs",
        postgresql_where=sa.text("status IN ('preprocessing', 'extracting', 'validating')"),
    )
    op.create_index(
        "processing_runs_reclaim_idx",
        "processing_runs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'preprocessing'"),
    )
