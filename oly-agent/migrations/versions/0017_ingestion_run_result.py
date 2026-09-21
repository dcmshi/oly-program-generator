"""ingestion_runs.result — the run's full stats as JSONB (OCR-QA).

The OCR quality gate produces per-page verdicts (suspect pages, pages left
unresolved after the second and third view) that only lived in the log and in
sources/.ocr_cache/<sha>.report.json. complete_run now stores the whole stats
dict here, so `SELECT result->'ocr_pages_unresolved' FROM ingestion_runs`
answers "which pages of which book still need a look".

Revision ID: 0017_ingestion_run_result
Revises: 0016_principle_duplicates
Create Date: 2026-09-21
"""
from alembic import op

revision = "0017_ingestion_run_result"
down_revision = "0016_principle_duplicates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS result JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE ingestion_runs DROP COLUMN IF EXISTS result")
