"""add result versions

Revision ID: c1d53e6a8b2f
Revises: b9e42d5f7a1c
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1d53e6a8b2f"
down_revision: str | Sequence[str] | None = "b9e42d5f7a1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_results",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("extraction_results", "version")
