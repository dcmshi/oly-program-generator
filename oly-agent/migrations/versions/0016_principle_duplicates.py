"""Duplicate marking on programming_principles (JEV-1b / PRIN-DEDUPE).

Joined sections reach the extractor whole, so four books each restate
Prilepin, warm-up ramps and deload rules — 161 -> 2,234 principles after the
2026-09-16 re-ingest. dedupe_principles.py embeds each principle, pairs
near-neighbours within a category, and asks Jev whether the pair states the
same rule; the later row points at the canonical one via duplicate_of and
plan._load_principles skips rows with it set. Nothing is deleted.

Revision ID: 0016_principle_duplicates
Revises: 0015_chunk_quarantine
Create Date: 2026-09-20
"""
from alembic import op

revision = "0016_principle_duplicates"
down_revision = "0015_chunk_quarantine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE programming_principles ADD COLUMN IF NOT EXISTS duplicate_of INTEGER "
               "REFERENCES programming_principles(id) ON DELETE SET NULL")
    op.execute("ALTER TABLE programming_principles ADD COLUMN IF NOT EXISTS duplicate_probability REAL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_principles_duplicate_of ON programming_principles (duplicate_of) "
               "WHERE duplicate_of IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_principles_duplicate_of")
    op.execute("ALTER TABLE programming_principles DROP COLUMN IF EXISTS duplicate_probability")
    op.execute("ALTER TABLE programming_principles DROP COLUMN IF EXISTS duplicate_of")
