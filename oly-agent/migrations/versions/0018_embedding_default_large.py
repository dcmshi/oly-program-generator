"""knowledge_chunks.embedding_model default → text-embedding-3-large (EMBED-1).

The corpus moved to text-embedding-3-large @ 1536 on 2026-09-22 and the code
default followed; VectorLoader always writes settings.embedding_model, so the
column default only matters for rows inserted outside the loader — but a stale
'text-embedding-3-small' default would tag such a row with the wrong space and
similarity_search would never rank it.

Revision ID: 0018_embedding_default_large
Revises: 0017_ingestion_run_result
Create Date: 2026-09-22
"""
from alembic import op

revision = "0018_embedding_default_large"
down_revision = "0017_ingestion_run_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-large'")


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN embedding_model SET DEFAULT 'text-embedding-3-small'")
