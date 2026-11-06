"""athletes.olympic_lifting_experience — the athlete has already learned the lifts.

A beginner by level can still have trained the snatch and clean & jerk (a
first coach-written block, a CrossFit background). Without this flag the
novice "foundation" principles — conditioned on beginner level or a short
training age, recommending the competition lifts be avoided (5073 Beginner
Foundation Phase, 5074 Post-Foundation Progression, 5079 Strength Before
Skill …) — matched such an athlete and the generator dropped every full
snatch, clean and jerk (program 53, 2026-11-03). `principle_matcher` skips
those rules when the flag is set.

Revision ID: 0022_olympic_lifting_experience
Revises: 0021_coach_athletes
Create Date: 2026-11-03
"""
from alembic import op

revision = "0022_olympic_lifting_experience"
down_revision = "0021_coach_athletes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE athletes ADD COLUMN olympic_lifting_experience BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade():
    op.execute("ALTER TABLE athletes DROP COLUMN IF EXISTS olympic_lifting_experience")
