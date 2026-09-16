"""Drop the index behind the never-populated athlete_level_relevance filter (RAG-M7).

`knowledge_chunks.athlete_level_relevance` is NULL on every row — nothing in the
ingestion path ever set it — so the `athlete_level` filter in
VectorLoader.similarity_search was a tautology and `idx_chunks_level` indexed
nothing but NULLs. The filter and the insert column are removed from the code;
the column stays (nullable, unused) so no data-shaped migration is needed.

Revision ID: 0011_drop_dead_level_index
Revises: 0010_genlog_retrieval_set
Create Date: 2026-09-15
"""

from alembic import op

revision = "0011_drop_dead_level_index"
down_revision = "0010_genlog_retrieval_set"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_level")


def downgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_level ON knowledge_chunks (athlete_level_relevance)")
