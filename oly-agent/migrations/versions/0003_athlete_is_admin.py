"""Add is_admin flag to athletes table.

Revision ID: 0003_athlete_is_admin
Revises: 0002_athlete_cost_limit
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_athlete_is_admin"
down_revision = "0002_athlete_cost_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "athletes",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Seed: athlete id=1 (the initial dev account) gets admin rights
    op.execute("UPDATE athletes SET is_admin = true WHERE id = 1")


def downgrade() -> None:
    op.drop_column("athletes", "is_admin")
