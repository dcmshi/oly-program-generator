"""Add per-athlete cost_limit_usd column.

Revision ID: 0002_athlete_cost_limit
Revises: 0001_baseline
Create Date: 2026-03-16

NULL means "use the global cost_limit_per_program from Settings/env".
A non-null value overrides the global limit for that athlete, allowing
different cost caps for different users (e.g. stricter limits for free-tier).
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0002_athlete_cost_limit"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE athletes
        ADD COLUMN IF NOT EXISTS cost_limit_usd NUMERIC(6,2) DEFAULT NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE athletes DROP COLUMN IF EXISTS cost_limit_usd")
