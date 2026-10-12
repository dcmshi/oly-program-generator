"""Coach ↔ athlete links (COACH-2): a coach account works on other athletes.

  * coach_athletes: one row per (coach, athlete). `origin` records how it was
    made — 'granted' (the athlete, logged in, gave a coach's username access)
    or 'created' (the coach added an athlete who has no login of their own).
    A coach acts as one linked athlete at a time (the session's
    acting_athlete_id); every athlete-scoped route then reads and writes that
    athlete. Deleting either account removes the link.

Revision ID: 0021_coach_athletes
Revises: 0020_macrocycles
Create Date: 2026-10-12
"""
from alembic import op

revision = "0021_coach_athletes"
down_revision = "0020_macrocycles"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE coach_athletes (
            coach_id    INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            athlete_id  INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            origin      TEXT NOT NULL CHECK (origin IN ('granted', 'created')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (coach_id, athlete_id),
            CHECK (coach_id <> athlete_id)
        )
    """)
    op.execute("CREATE INDEX idx_coach_athletes_athlete ON coach_athletes (athlete_id)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS coach_athletes")
