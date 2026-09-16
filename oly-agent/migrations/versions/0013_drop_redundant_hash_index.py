"""Drop idx_chunks_hash — the UNIQUE(content_hash) constraint already indexes it (RAG-L7).

knowledge_chunks.content_hash is declared UNIQUE, which creates
knowledge_chunks_content_hash_key; idx_chunks_hash was a second btree on the
same column, costing a write per insert and buying nothing.

Revision ID: 0013_drop_redundant_hash_index
Revises: 0012_chunk_sources
Create Date: 2026-09-15
"""

from alembic import op

revision = "0013_drop_redundant_hash_index"
down_revision = "0012_chunk_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_hash")


def downgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_hash ON knowledge_chunks (content_hash)")
