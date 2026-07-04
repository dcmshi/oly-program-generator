"""Add timezone to athletes for tz-aware week math (W-L5).

Week computation and log-date defaults used server-local date.today(); storing
the athlete's IANA timezone lets "today" be computed in their local time.

Revision ID: 0005_athlete_timezone
Revises: 0004_principle_unique
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_athlete_timezone"
down_revision = "0004_principle_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "athletes",
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
    )


def downgrade() -> None:
    op.drop_column("athletes", "timezone")
