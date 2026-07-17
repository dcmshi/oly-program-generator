"""Add UNIQUE(source_id, name) to program_templates.

`load_program` was a plain INSERT with no dedup, so re-running the pipeline on
the same source — documented as safe — duplicated every template (e.g. Takano's
16 → 32), and the every-10-sections resume checkpoint re-inserted any template
in the redo window (ING-M6).

Revision ID: 0006_program_template_unique
Revises: 0005_athlete_timezone
Create Date: 2026-07-17
"""

from alembic import op

revision = "0006_program_template_unique"
down_revision = "0005_athlete_timezone"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_program_template_source_name"


def upgrade() -> None:
    # Remove pre-existing duplicates first — keep the lowest id of each
    # (source_id, name) group (earlier re-runs may have doubled templates).
    op.execute(
        """
        DELETE FROM program_templates a
        USING program_templates b
        WHERE a.id > b.id
          AND a.source_id = b.source_id
          AND a.name = b.name
        """
    )
    op.create_unique_constraint(
        _CONSTRAINT,
        "program_templates",
        ["source_id", "name"],
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "program_templates", type_="unique")
