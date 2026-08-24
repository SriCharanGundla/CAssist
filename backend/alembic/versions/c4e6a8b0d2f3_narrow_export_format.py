"""narrow export format to the implemented Tally handoff

Revision ID: c4e6a8b0d2f3
Revises: b2d4f6a8c0e1
Create Date: 2026-08-13 20:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e6a8b0d2f3"
down_revision: str | None = "b2d4f6a8c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    has_historical_exports = bool(
        op.get_bind().scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM export_events "
                "WHERE format::text IN ('json', 'csv', 'xlsx')"
                ")"
            )
        )
    )
    if has_historical_exports:
        # Preserve previously valid history. The API enum still restricts all new writes.
        return
    op.execute("ALTER TABLE export_events ALTER COLUMN format TYPE text USING format::text")
    op.execute("DROP TYPE export_format")
    op.execute("CREATE TYPE export_format AS ENUM ('tally_json')")
    op.execute(
        "ALTER TABLE export_events ALTER COLUMN format TYPE export_format "
        "USING format::export_format"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE export_events ALTER COLUMN format TYPE text USING format::text")
    op.execute("DROP TYPE export_format")
    op.execute("CREATE TYPE export_format AS ENUM ('json', 'csv', 'xlsx', 'tally_json')")
    op.execute(
        "ALTER TABLE export_events ALTER COLUMN format TYPE export_format "
        "USING format::export_format"
    )
