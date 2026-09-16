#!/usr/bin/env python3
"""
Re-embed knowledge_chunks with the configured embedding model (RAG-M8).

Each row records the model that produced its vector (`embedding_model`, migration
0008) and `similarity_search` only ranks rows in the query's space, so a re-embed
can run incrementally: rows already on the target model are skipped, rows still
on the old model stay searchable under the old model until they are rewritten.

Switching models: set `embedding_model` (and, for text-embedding-3-large,
`embedding_dim = 1536` so Matryoshka truncation keeps the vector(1536) column)
in settings, then run this script. Changing the vector width itself needs a
schema change first — the script refuses if the API returns a different width.

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python reembed.py --dry-run             # how many rows would move
    PYTHONUTF8=1 uv run python reembed.py --source-id 51        # one source
    PYTHONUTF8=1 uv run python reembed.py --limit 200           # smoke test
    PYTHONUTF8=1 uv run python reembed.py --all                 # include rows already on the target model

Cost: ~3.4k chunks ≈ 3.4M input tokens ≈ well under a dollar on -small, ~$0.45 on -large.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100  # matches VectorLoader.EMBED_BATCH_SIZE


def select_sql(target_model: str, source_id: int | None = None, include_current: bool = False,
               limit: int = 0) -> tuple[str, list]:
    """The rows to (re-)embed: not yet on the target model unless include_current."""
    sql = "SELECT id, content FROM knowledge_chunks"
    where, params = [], []
    if not include_current:
        where.append("embedding_model <> %s")
        params.append(target_model)
    if source_id:
        where.append("source_id = %s")
        params.append(source_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    return sql, params


def batches(rows: list, size: int):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def reembed(source_id: int | None, dry_run: bool, include_current: bool = False,
            limit: int = 0, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Rewrite embedding + embedding_model + embedded_at for the selected rows. Returns rows written."""
    from loaders.vector_loader import VectorLoader

    settings = Settings()
    loader = VectorLoader(settings)  # opens the DB connection + OpenAI client
    target = settings.embedding_model
    cur = loader.conn.cursor()
    sql, params = select_sql(target, source_id, include_current, limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    logger.info(f"{len(rows)} chunk(s) to embed with {target}"
                f"{f' (source_id={source_id})' if source_id else ''}{' — DRY RUN' if dry_run else ''}")
    if dry_run or not rows:
        cur.close()
        loader.close()
        return 0

    written = 0
    for batch in batches(rows, batch_size):
        vectors = loader._embed_batch([content for _id, content in batch])
        width = len(vectors[0]) if vectors else 0
        if width != settings.embedding_dim:
            raise SystemExit(
                f"API returned {width}-d vectors but settings.embedding_dim={settings.embedding_dim}; "
                "the vector column width must change before re-embedding with this model"
            )
        for (chunk_id, _content), vec in zip(batch, vectors, strict=True):
            cur.execute(
                "UPDATE knowledge_chunks SET embedding = %s, embedding_model = %s, embedded_at = NOW() WHERE id = %s",
                (vec, target, chunk_id),
            )
            written += 1
        loader.conn.commit()
        logger.info(f"  {written}/{len(rows)} rows re-embedded")

    cur.close()
    loader.close()
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-embed knowledge_chunks with settings.embedding_model (RAG-M8)")
    parser.add_argument("--source-id", type=int, help="Limit to one source")
    parser.add_argument("--dry-run", action="store_true", help="Count rows only")
    parser.add_argument("--all", action="store_true", help="Also re-embed rows already on the target model")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N rows (smoke test)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()
    reembed(args.source_id, args.dry_run, include_current=args.all, limit=args.limit, batch_size=args.batch_size)
