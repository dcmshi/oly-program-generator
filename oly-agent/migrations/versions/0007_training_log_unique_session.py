"""Partial UNIQUE index on training_logs.session_id.

The log-submit router's check-then-insert races on double-submit, producing
two log rows for one session — adherence >100%, duplicate dashboard cards, and
edits landing on an arbitrary row (WEB-L3). create_session_log now upserts via
ON CONFLICT against this index. Partial (WHERE session_id IS NOT NULL) because
delete_program deliberately NULLs session_id on preserved logs.

Revision ID: 0007_training_log_unique_session
Revises: 0006_program_template_unique
Create Date: 2026-07-17
"""

from alembic import op

revision = "0007_training_log_unique_session"
down_revision = "0006_program_template_unique"
branch_labels = None
depends_on = None

_INDEX = "uq_training_logs_session"


def upgrade() -> None:
    # Merge pre-existing duplicates: repoint exercise rows at the earliest log
    # of each session, then drop the later duplicates.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, FIRST_VALUE(id) OVER (
                       PARTITION BY session_id ORDER BY id) AS keeper
            FROM training_logs
            WHERE session_id IS NOT NULL
        )
        UPDATE training_log_exercises tle
        SET log_id = r.keeper
        FROM ranked r
        WHERE tle.log_id = r.id AND r.id <> r.keeper
        """
    )
    op.execute(
        """
        DELETE FROM training_logs t
        USING training_logs k
        WHERE t.session_id IS NOT NULL
          AND k.session_id = t.session_id
          AND t.id > k.id
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_INDEX}
        ON training_logs (session_id)
        WHERE session_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
