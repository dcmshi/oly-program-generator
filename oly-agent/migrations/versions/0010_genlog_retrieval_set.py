"""Record what each generation call retrieved (RAG-M5).

generation_log stored the prompt text but not which knowledge chunks were in
it, with what similarity, from which query — so retrieval quality could not be
audited after the fact, and the per-exercise source_chunk_ids were a heuristic
(the same top-3 ids on every exercise). `retrieval_set` holds the labelled
context list the prompt showed:

    [{"label": "C1", "id": 812, "chunk_type": "periodization", "source_id": 51,
      "similarity": 0.61, "score": 0.66, "session_query": "exercise selection for …"}, …]

Revision ID: 0010_genlog_retrieval_set
Revises: 0009_chunk_tsv_context_prefix
Create Date: 2026-09-15
"""

from alembic import op

revision = "0010_genlog_retrieval_set"
down_revision = "0009_chunk_tsv_context_prefix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE generation_log ADD COLUMN IF NOT EXISTS retrieval_set JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE generation_log DROP COLUMN IF EXISTS retrieval_set")
