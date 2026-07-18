"""Content-aware unique identity for program_templates.

`load_program` was a plain INSERT with no dedup, so re-running the pipeline on
the same source duplicated templates (ING-M6). The first version of this
migration keyed identity on (source_id, name) alone — but template names are
AUTO-GENERATED as "Program from {source.title}" (pipeline._parse_program_template),
so every template of a source shares one name and that key would have deleted
15 of Takano's 16 distinct templates and permanently capped every source at
one template (audit2-H1). Identity therefore includes a hash of the parsed
program_structure: exact re-inserts dedup, distinct programs never collide.

NULLS NOT DISTINCT (PG15+) so NULL-source templates dedup too (audit2-L7).

Revision ID: 0006_program_template_unique
Revises: 0005_athlete_timezone
Create Date: 2026-07-17 (reworked same day before any corpus DB applied it)
"""

from alembic import op

revision = "0006_program_template_unique"
down_revision = "0005_athlete_timezone"
branch_labels = None
depends_on = None

_INDEX = "uq_program_template_identity"
_OLD_CONSTRAINT = "uq_program_template_source_name"  # first (destructive) version of this migration


def upgrade() -> None:
    # If a DB applied the first version of this migration, remove its
    # constraint so the reworked index below is the single identity.
    op.execute(
        f"ALTER TABLE program_templates DROP CONSTRAINT IF EXISTS {_OLD_CONSTRAINT}"
    )
    # Remove only TRUE duplicates: same source, same name, same parsed
    # structure — keep the lowest id. jsonb::text is canonical (sorted keys),
    # so md5 over it is a stable content fingerprint.
    op.execute(
        """
        DELETE FROM program_templates a
        USING program_templates b
        WHERE a.id > b.id
          AND a.source_id IS NOT DISTINCT FROM b.source_id
          AND a.name = b.name
          AND md5(a.program_structure::text) = md5(b.program_structure::text)
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_INDEX}
        ON program_templates (source_id, name, md5(program_structure::text))
        NULLS NOT DISTINCT
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute(
        f"ALTER TABLE program_templates DROP CONSTRAINT IF EXISTS {_OLD_CONSTRAINT}"
    )
