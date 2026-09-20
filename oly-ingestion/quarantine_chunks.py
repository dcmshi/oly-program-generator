#!/usr/bin/env python3
"""
Quarantine non-content chunks (JEV-1a).

Asks Jev (TypeSafe AI) one calibrated yes/no per chunk — "is this passage NOT
usable coaching content?" — and sets `knowledge_chunks.quarantined` for those
at or above the threshold. Quarantined rows stay in the table (hashes, dedup,
provenance) but `VectorLoader.similarity_search` excludes them from both
retrieval legs. `junk_probability` is stored on every scored row, so changing
the threshold later is `--rescore-from-db`, not another Jev pass.

On the dev copy (2026-09-20): 282 of 4,607 chunks at >= 0.7 — book indexes,
reference / URL lists, tables of contents, title pages; a Sonnet 5 spot-check
agreed on 39 of 40 flagged and found 2 of 40 low-probability chunks junk.

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python quarantine_chunks.py --dry-run           # score + report, write nothing
    PYTHONUTF8=1 uv run python quarantine_chunks.py                     # score + quarantine at 0.7
    PYTHONUTF8=1 uv run python quarantine_chunks.py --threshold 0.8 --rescore-from-db
    PYTHONUTF8=1 uv run python quarantine_chunks.py --source-id 501 --release   # un-quarantine

Cost: ~1k tokens per chunk at $0.042/MTok — about $0.19 for the corpus.
Needs TYPESAFE_API_KEY. Re-run the retrieval eval afterwards (the golden pool
may hold quarantined ids as graded-relevant; they count as misses).
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root for shared.*
from config import Settings

from shared.constants import JUNK_QUARANTINE_THRESHOLD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PASSAGE_CHARS = 1200
REASON = "jev_junk"
JUNK_INSTRUCTIONS = (
    "Is this passage NOT usable coaching content — e.g. a table of contents, index or reference list, "
    "a title/copyright page, a bare list of URLs or figure captions, navigation boilerplate, or otherwise "
    "fragments with no instructional meaning?"
)


async def score_chunks_async(passages: dict[int, str], concurrency: int = 8) -> dict[int, float]:
    """{id: passage} → {id: P(junk)}; a failed call is logged and left out."""
    from typesafe_sdk import AsyncTypeSafeClient, Noul

    question = Noul(instructions=JUNK_INSTRUCTIONS)
    out: dict[int, float] = {}
    sem = asyncio.Semaphore(concurrency)
    async with AsyncTypeSafeClient() as client:
        async def one(cid: int, text: str) -> None:
            async with sem:
                try:
                    r = await client.system_one(state={"passage": text}, questions={"junk": question})
                except Exception as e:
                    logger.warning(f"Jev junk score failed for chunk {cid}: {type(e).__name__}: {e}")
                    return
            out[cid] = float(r.nouls["junk"].noul)
        await asyncio.gather(*(one(c, t) for c, t in passages.items()))
    return out


def score_chunks(passages: dict[int, str], **kwargs) -> dict[int, float]:
    return asyncio.run(score_chunks_async(passages, **kwargs))


def plan(probabilities: dict[int, float], current: dict[int, bool], threshold: float) -> tuple[list[int], list[int]]:
    """(ids to quarantine, ids to release) so the table matches `probability >= threshold`."""
    quarantine = [cid for cid, p in probabilities.items() if p >= threshold and not current.get(cid, False)]
    release = [cid for cid, p in probabilities.items() if p < threshold and current.get(cid, False)]
    return quarantine, release


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Quarantine non-content chunks with Jev (JEV-1a)")
    ap.add_argument("--threshold", type=float, default=JUNK_QUARANTINE_THRESHOLD)
    ap.add_argument("--source-id", type=int, default=None, help="only this source")
    ap.add_argument("--limit", type=int, default=0, help="only the first N chunks (smoke test)")
    ap.add_argument("--dry-run", action="store_true", help="score and report; write nothing")
    ap.add_argument("--rescore-from-db", action="store_true",
                    help="reuse stored junk_probability instead of calling Jev (threshold change)")
    ap.add_argument("--release", action="store_true", help="clear quarantined + junk_probability for the selection")
    args = ap.parse_args(argv)

    settings = Settings()
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    where, params = [], []
    if args.source_id:
        where.append("source_id = %s")
        params.append(args.source_id)
    sql = "SELECT id, left(raw_content, %s), quarantined, junk_probability FROM knowledge_chunks"
    params.insert(0, PASSAGE_CHARS)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    if args.limit:
        sql += " LIMIT %s"
        params.append(args.limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    logger.info(f"{len(rows)} chunks selected")

    if args.release:
        ids = [r[0] for r in rows]
        if not args.dry_run:
            cur.execute("UPDATE knowledge_chunks SET quarantined = FALSE, junk_probability = NULL, quarantine_reason = NULL "
                        "WHERE id = ANY(%s)", (ids,))
            conn.commit()
        print(f"released {len(ids)} chunk(s){' (dry run)' if args.dry_run else ''}")
        return 0

    current = {r[0]: bool(r[2]) for r in rows}
    if args.rescore_from_db:
        probabilities = {r[0]: float(r[3]) for r in rows if r[3] is not None}
        logger.info(f"{len(probabilities)} stored probabilities")
    else:
        probabilities = score_chunks({r[0]: r[1] for r in rows})
        logger.info(f"Jev scored {len(probabilities)} of {len(rows)} chunks")

    to_q, to_r = plan(probabilities, current, args.threshold)
    n_hi = sum(1 for p in probabilities.values() if p >= args.threshold)
    print(f"{'DRY RUN — ' if args.dry_run else ''}threshold {args.threshold}: {n_hi} chunks at/above it "
          f"({sum(1 for p in probabilities.values() if p >= 0.9)} at >= 0.9); quarantine {len(to_q)}, release {len(to_r)}")
    for cid in sorted(to_q, key=lambda c: -probabilities[c])[:10]:
        text = next(r[1] for r in rows if r[0] == cid)
        print(f"  {probabilities[cid]:.2f} #{cid}: {text[:90]!r}")
    if args.dry_run:
        return 0

    if not args.rescore_from_db:
        cur.executemany("UPDATE knowledge_chunks SET junk_probability = %s WHERE id = %s",
                        [(p, cid) for cid, p in probabilities.items()])
    if to_q:
        cur.execute("UPDATE knowledge_chunks SET quarantined = TRUE, quarantine_reason = %s WHERE id = ANY(%s)", (REASON, to_q))
    if to_r:
        cur.execute("UPDATE knowledge_chunks SET quarantined = FALSE, quarantine_reason = NULL WHERE id = ANY(%s)", (to_r,))
    conn.commit()
    cur.execute("SELECT count(*) FILTER (WHERE quarantined), count(*) FROM knowledge_chunks")
    q, total = cur.fetchone()
    print(f"quarantined {q} of {total} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
