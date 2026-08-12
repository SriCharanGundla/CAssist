"""enforce processing cancellation terminal states

Revision ID: b2d4f6a8c0e1
Revises: a7c9e2f4b6d8
Create Date: 2026-08-13 19:15:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2d4f6a8c0e1"
down_revision: str | None = "a7c9e2f4b6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Repair any terminal result written by a worker after cancellation won the race.
    op.execute(
        """
        DELETE FROM extraction_results
        WHERE processing_run_id IN (
            SELECT id
            FROM processing_runs
            WHERE cancellation_requested_at IS NOT NULL
              AND status IN ('succeeded', 'failed', 'queued')
        )
        """
    )
    op.execute(
        """
        UPDATE processing_runs
        SET status = 'cancelled',
            progress_stage = 'cancelled',
            worker_id = NULL,
            lease_expires_at = NULL,
            error_code = NULL,
            error_message_safe = NULL,
            completed_at = COALESCE(completed_at, cancellation_requested_at)
        WHERE cancellation_requested_at IS NOT NULL
          AND status IN ('succeeded', 'failed', 'queued')
        """
    )
    op.execute(
        """
        UPDATE documents AS document
        SET status = CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM processing_runs AS successful_run
                    WHERE successful_run.document_id = document.id
                      AND successful_run.status = 'succeeded'
                ) THEN 'ready'::document_status
                ELSE 'failed'::document_status
            END,
            updated_at = now()
        WHERE EXISTS (
            SELECT 1
            FROM processing_runs AS cancelled_run
            WHERE cancelled_run.document_id = document.id
              AND cancelled_run.status = 'cancelled'
              AND cancelled_run.cancellation_requested_at IS NOT NULL
        )
          AND NOT EXISTS (
            SELECT 1
            FROM processing_runs AS active_run
            WHERE active_run.document_id = document.id
              AND active_run.status IN ('queued', 'preprocessing', 'extracting', 'validating')
        )
        """
    )
    op.create_check_constraint(
        "processing_runs_cancellation_terminal_state",
        "processing_runs",
        "cancellation_requested_at IS NULL OR "
        "status IN ('preprocessing', 'extracting', 'validating', 'cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "processing_runs_cancellation_terminal_state",
        "processing_runs",
        type_="check",
    )
