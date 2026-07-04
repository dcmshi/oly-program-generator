"""Add UNIQUE(source_id, principle_name) to programming_principles.

Without this constraint the loader's `ON CONFLICT DO NOTHING` was inert, so any
reprocess/resume of a source inserted duplicate principles (I-H3).

Revision ID: 0004_principle_unique
Revises: 0003_athlete_is_admin
Create Date: 2026-07-03
"""

from alembic import op

revision = "0004_principle_unique"
down_revision = "0003_athlete_is_admin"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_principle_source_name"


def upgrade() -> None:
    # Remove any pre-existing duplicates first (the constraint never existed, so
    # earlier reprocessing may have inserted identical principles) — keep the
    # lowest id of each (source_id, principle_name) group.
    op.execute(
        """
        DELETE FROM programming_principles a
        USING programming_principles b
        WHERE a.id > b.id
          AND a.source_id = b.source_id
          AND a.principle_name = b.principle_name
        """
    )
    op.create_unique_constraint(
        _CONSTRAINT,
        "programming_principles",
        ["source_id", "principle_name"],
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "programming_principles", type_="unique")
