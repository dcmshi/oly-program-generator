#!/usr/bin/env python3
"""
Rewrite Roman's fraction notation in stored chunks and re-embed them (DOG-2r).

New ingests are normalised in `SectionProcessor.process`
(`processors/rep_notation.py`); this script brings the rows already in
`knowledge_chunks` to the same text. For each chunk whose `raw_content`
changes it replaces the body of `content` (the preamble and any
`context_prefix` stay), recomputes `content_hash` (SHA-256 of the new
`raw_content`, as the loader writes it) and re-embeds `content` with
`settings.embedding_model`. The old row values go to
`logs/rep_notation_backup.jsonl` first. Without `--apply` it only prints what
would change.

    cd oly-ingestion
    PYTHONUTF8=1 uv run python normalize_rep_notation.py              # dry run
    PYTHONUTF8=1 uv run python normalize_rep_notation.py --apply

Run `eval/run_eval` afterwards: the rewritten chunks are re-embedded, so their
ranking can move (docs/RETRIEVAL_EVAL.md).
"""

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import Settings
from processors.rep_notation import normalize_fraction_notation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BACKUP = Path(__file__).parent / "logs" / "rep_notation_backup.jsonl"
# a cheap prefilter: every layout the normaliser converts has a "W (P)" pair
CANDIDATE_SQL = r"SELECT id, content, raw_content FROM knowledge_chunks WHERE raw_content ~ '\d \(\d+(\.\d+)?\)' ORDER BY id"


def rewrite(content: str, raw: str) -> tuple[str, str] | None:
    """(new_content, new_raw), or None when the notation normaliser leaves `raw` alone."""
    new_raw = normalize_fraction_notation(raw)
    if new_raw == raw:
        return None
    if content.endswith(raw):
        return content[: len(content) - len(raw)] + new_raw, new_raw
    if raw in content:
        return content.replace(raw, new_raw, 1), new_raw
    raise ValueError("raw_content is not inside content — unknown layout, not rewritten")


def run(apply: bool) -> int:
    from loaders.vector_loader import VectorLoader

    settings = Settings()
    loader = VectorLoader(settings)
    cur = loader.conn.cursor()
    cur.execute(CANDIDATE_SQL)
    changes = []
    for chunk_id, content, raw in cur.fetchall():
        try:
            out = rewrite(content, raw)
        except ValueError as e:
            logger.warning(f"chunk {chunk_id}: {e}")
            continue
        if out:
            changes.append((chunk_id, content, raw, *out))
    for chunk_id, _c, raw, _nc, new_raw in changes:
        logger.info(f"chunk {chunk_id}: raw {len(raw)} → {len(new_raw)} chars")
    if not apply or not changes:
        logger.info(f"{len(changes)} chunk(s) to rewrite{'' if apply else ' — dry run, pass --apply'}")
        cur.close()
        loader.close()
        return 0

    new_hashes = [hashlib.sha256(new_raw.encode()).hexdigest() for *_x, new_raw in changes]
    cur.execute("SELECT content_hash, id FROM knowledge_chunks WHERE content_hash = ANY(%s)", (new_hashes,))
    clash = cur.fetchall()
    if clash:
        raise SystemExit(f"new text already stored as another chunk: {clash} — not rewriting")

    vectors = loader._embed_batch([new_content for *_x, new_content, _r in changes])
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    with BACKUP.open("a", encoding="utf-8") as f:
        for chunk_id, content, raw, *_ in changes:
            cur.execute("SELECT content_hash, embedding_model FROM knowledge_chunks WHERE id = %s", (chunk_id,))
            old_hash, old_model = cur.fetchone()
            f.write(json.dumps({"id": chunk_id, "content": content, "raw_content": raw,
                                "content_hash": old_hash, "embedding_model": old_model}, ensure_ascii=False) + "\n")
    for (chunk_id, _c, _r, new_content, new_raw), new_hash, vec in zip(changes, new_hashes, vectors, strict=True):
        cur.execute(
            "UPDATE knowledge_chunks SET content = %s, raw_content = %s, content_hash = %s, embedding = %s, "
            "embedding_model = %s, embedded_at = NOW() WHERE id = %s",
            (new_content, new_raw, new_hash, vec, settings.embedding_model, chunk_id),
        )
    loader.conn.commit()
    logger.info(f"{len(changes)} chunk(s) rewritten and re-embedded; old values in {BACKUP}")
    cur.close()
    loader.close()
    return len(changes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rewrite fraction notation in stored chunks (DOG-2r)")
    parser.add_argument("--apply", action="store_true", help="Write the rewrite (default: dry run)")
    run(parser.parse_args().apply)
