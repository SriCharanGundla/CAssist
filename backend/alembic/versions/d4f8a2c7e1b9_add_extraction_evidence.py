"""add extraction evidence

Revision ID: d4f8a2c7e1b9
Revises: c1d53e6a8b2f
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4f8a2c7e1b9"
down_revision: str | None = "c1d53e6a8b2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extraction_results",
        sa.Column(
            "evidence_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("extraction_results", "evidence_data")
