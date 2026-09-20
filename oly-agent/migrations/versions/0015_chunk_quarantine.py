"""Quarantine flag on knowledge_chunks (JEV-1a).

A Jev (TypeSafe) `Noul` over the corpus finds ~6% of chunks are non-content:
book indexes, reference / URL lists, tables of contents, title pages. They are
kept (provenance, hashes, dedup) but excluded from retrieval:
`VectorLoader.similarity_search` adds `NOT quarantined` to both legs.
`junk_probability` records the score the decision was made on so a threshold
change is an UPDATE, not a re-score.

Revision ID: 0015_chunk_quarantine
Revises: 0014_seed_exercise_variants
Create Date: 2026-09-20
"""
from alembic import op

revision = "0015_chunk_quarantine"
down_revision = "0014_seed_exercise_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS quarantined BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS junk_probability REAL")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS quarantine_reason VARCHAR(100)")
    # partial index: the search filters on NOT quarantined, and quarantined rows are few
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_quarantined ON knowledge_chunks (id) WHERE quarantined")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_quarantined")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS quarantine_reason")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS junk_probability")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS quarantined")
