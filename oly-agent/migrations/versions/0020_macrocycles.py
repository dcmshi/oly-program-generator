"""Macrocycles — a planned block sequence, generated one block at a time (PLAN-3e).

  * macrocycles: the plan — `blocks` is the ordered list of
    {"phase", "weeks", "note"} the athlete sees; it is re-flowed when a block
    completes (a held phase is repeated, a competition date re-fits the tail).
  * generated_programs.macrocycle_id / macrocycle_block_index: which block a
    program realises. Block status is derived from these rows (no program →
    planned, else the program's status), so deleting a program simply returns
    its block to "planned".

Revision ID: 0020_macrocycles
Revises: 0019_complexes_catalogue
Create Date: 2026-09-22
"""
from alembic import op

revision = "0020_macrocycles"
down_revision = "0019_complexes_catalogue"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE macrocycles (
            id               SERIAL PRIMARY KEY,
            athlete_id       INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            competition_date DATE,
            start_date       DATE NOT NULL DEFAULT CURRENT_DATE,
            blocks           JSONB NOT NULL,
            status           TEXT NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'completed', 'abandoned')),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_macrocycles_athlete ON macrocycles (athlete_id, status)")
    op.execute("""
        ALTER TABLE generated_programs
            ADD COLUMN macrocycle_id INTEGER REFERENCES macrocycles(id) ON DELETE SET NULL,
            ADD COLUMN macrocycle_block_index INTEGER
    """)
    op.execute("CREATE INDEX idx_programs_macrocycle ON generated_programs (macrocycle_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_programs_macrocycle")
    op.execute("""
        ALTER TABLE generated_programs
            DROP COLUMN IF EXISTS macrocycle_block_index,
            DROP COLUMN IF EXISTS macrocycle_id
    """)
    op.execute("DROP TABLE IF EXISTS macrocycles")
