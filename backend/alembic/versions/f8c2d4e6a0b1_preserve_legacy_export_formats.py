"""preserve legacy export formats in the database enum

Revision ID: f8c2d4e6a0b1
Revises: c7a1e5d9b3f2
Create Date: 2026-08-23 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f8c2d4e6a0b1"
down_revision: str | None = "c7a1e5d9b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extra database values preserve old export-event history. The application enum
    # continues to allow only tally_json for newly-created exports.
    op.execute("ALTER TYPE export_format ADD VALUE IF NOT EXISTS 'json'")
    op.execute("ALTER TYPE export_format ADD VALUE IF NOT EXISTS 'csv'")
    op.execute("ALTER TYPE export_format ADD VALUE IF NOT EXISTS 'xlsx'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values without recreating the type. Keeping the
    # values is data-safe and remains compatible with the earlier application schema.
    pass
