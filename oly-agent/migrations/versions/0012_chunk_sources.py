"""chunk_sources: every source a chunk's text appeared in (RAG-L5).

Chunk dedup is global by SHA-256 of raw_content, so text shared by two sources
(Everett's two books, a Catalyst article reprinting a chapter) is embedded once
and knowledge_chunks.source_id credits only whichever source was ingested
first. This table records every (chunk, source) pair — the loader writes it for
new chunks and for duplicates it skips — so provenance and per-source counts
survive dedup. knowledge_chunks.source_id keeps meaning "first seen in".

Revision ID: 0012_chunk_sources
Revises: 0011_drop_dead_level_index
Create Date: 2026-09-15
"""

from alembic import op

revision = "0012_chunk_sources"
down_revision = "0011_drop_dead_level_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_sources (
            chunk_id    INT NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
            source_id   INT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            first_seen  TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chunk_id, source_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_sources_source ON chunk_sources (source_id)")
    # Backfill: every existing chunk belongs at least to the source that first ingested it.
    op.execute(
        """
        INSERT INTO chunk_sources (chunk_id, source_id, first_seen)
        SELECT id, source_id, coalesce(created_at, NOW())
        FROM knowledge_chunks
        WHERE source_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunk_sources")
