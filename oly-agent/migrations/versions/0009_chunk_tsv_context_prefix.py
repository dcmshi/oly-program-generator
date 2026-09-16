"""Lexical index for hybrid retrieval + the contextual-prefix column (RAG-M1, RAG-M3).

Retrieval was dense-only, so exact terms — exercise names, "Prilepin",
"70%/3x3" notation, Soviet abbreviations — were matched only through the
embedding. `tsv` is a STORED generated tsvector over the chunk text so
`similarity_search(hybrid=True)` can fuse a BM25-style lexical leg with the
vector leg by reciprocal rank (no re-embed needed).

`context_prefix` (RAG-M3) is added here rather than in its own migration because
a generated column's expression cannot be altered later: the LLM-written chunk
context must be part of the lexical index from the start. It stays NULL until
the contextualizer runs.

Revision ID: 0009_chunk_tsv_context_prefix
Revises: 0008_chunk_embedding_model
Create Date: 2026-09-15
"""

from alembic import op

revision = "0009_chunk_tsv_context_prefix"
down_revision = "0008_chunk_embedding_model"
branch_labels = None
depends_on = None

_INDEX = "idx_chunks_tsv"


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS context_prefix TEXT")
    op.execute(
        """
        ALTER TABLE knowledge_chunks
        ADD COLUMN IF NOT EXISTS tsv tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english', coalesce(context_prefix, '') || ' ' || coalesce(raw_content, ''))
            ) STORED
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON knowledge_chunks USING GIN (tsv)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS tsv")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS context_prefix")
