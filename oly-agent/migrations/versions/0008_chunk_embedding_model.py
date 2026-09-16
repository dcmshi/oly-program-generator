"""Record which embedding model produced each knowledge_chunks row (RAG-M8).

Only ingestion_runs.config_snapshot knew the model. A re-embed — RAG-M3
contextual prefixes, roadmap #22 (text-embedding-3-large truncated to 1536) —
would otherwise leave vectors from two embedding spaces indistinguishable inside
one HNSW index, and a cosine distance across spaces is meaningless.
VectorLoader now writes the column on insert and filters on it in
similarity_search; oly-ingestion/reembed.py rewrites rows model by model.

Every existing row was embedded with text-embedding-3-small, hence the default.

Revision ID: 0008_chunk_embedding_model
Revises: 0007_training_log_unique_session
Create Date: 2026-09-15
"""

from alembic import op

revision = "0008_chunk_embedding_model"
down_revision = "0007_training_log_unique_session"
branch_labels = None
depends_on = None

_INDEX = "idx_chunks_embedding_model"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_chunks "
        "ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small'"
    )
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMP")
    op.execute("UPDATE knowledge_chunks SET embedded_at = created_at WHERE embedded_at IS NULL")
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN embedded_at SET DEFAULT NOW()")
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON knowledge_chunks (embedding_model)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedded_at")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding_model")
