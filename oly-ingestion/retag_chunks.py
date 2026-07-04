#!/usr/bin/env python3
"""
Re-run keyword_tag() on existing knowledge_chunks and update topics in DB.

Useful after expanding KEYWORD_TO_TOPIC in chunker.py without re-ingesting.

Usage:
    uv run python retag_chunks.py                  # all chunks
    uv run python retag_chunks.py --source-id 499  # single source
    uv run python retag_chunks.py --dry-run        # preview only
"""
import argparse
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
from config import Settings
from processors.chunker import CHUNK_TYPE_DEFAULT_TOPICS, keyword_tag


def compute_topics(raw_content: str, chunk_type: str | None) -> list[str]:
    """Topics for a chunk: keyword tags UNION the chunk_type baseline topics.

    Mirrors chunker.chunk() exactly. Recomputing from keyword_tag alone stripped
    any topic that came only from the chunk_type default (e.g. fault_correction
    on a fault chunk with no matching keyword), silently degrading topic-filtered
    retrieval (I-M3).
    """
    topics = set(keyword_tag(raw_content))
    topics |= set(CHUNK_TYPE_DEFAULT_TOPICS.get(chunk_type or "", []))
    return sorted(topics)


def retag(source_id: int | None, dry_run: bool) -> None:
    settings = Settings()
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    if source_id:
        cur.execute(
            "SELECT id, raw_content, topics, chunk_type::text FROM knowledge_chunks WHERE source_id = %s",
            (source_id,),
        )
    else:
        cur.execute("SELECT id, raw_content, topics, chunk_type::text FROM knowledge_chunks")

    rows = cur.fetchall()
    print(f"Loaded {len(rows)} chunks{f' for source_id={source_id}' if source_id else ''}.")

    updated = 0
    newly_tagged = 0

    for chunk_id, raw_content, old_topics, chunk_type in rows:
        new_topics = compute_topics(raw_content, chunk_type)
        old_set = set(old_topics or [])
        new_set = set(new_topics)

        if new_set == old_set:
            continue

        added = new_set - old_set
        removed = old_set - new_set

        if dry_run:
            print(f"  [chunk {chunk_id}] +{sorted(added)} -{sorted(removed)}")
        else:
            cur.execute(
                "UPDATE knowledge_chunks SET topics = %s WHERE id = %s",
                (new_topics, chunk_id),
            )
        updated += 1
        if not old_set and new_set:
            newly_tagged += 1

    if dry_run:
        print(f"\nDry run: {updated} chunks would be updated ({newly_tagged} newly tagged).")
    else:
        conn.commit()
        print(f"Updated {updated} chunks ({newly_tagged} newly tagged).")

    cur.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-tag knowledge_chunks with updated KEYWORD_TO_TOPIC")
    parser.add_argument("--source-id", type=int, help="Limit to a single source")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()
    retag(args.source_id, args.dry_run)
