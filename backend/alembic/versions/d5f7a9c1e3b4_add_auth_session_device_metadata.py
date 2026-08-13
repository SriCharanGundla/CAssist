"""Add auth session device metadata.

Revision ID: d5f7a9c1e3b4
Revises: c4e6a8b0d2f3
"""

import sqlalchemy as sa

from alembic import op

revision: str = "d5f7a9c1e3b4"
down_revision: str | None = "c4e6a8b0d2f3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("auth_sessions", sa.Column("user_agent", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_sessions", "user_agent")
