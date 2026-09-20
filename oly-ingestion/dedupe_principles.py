#!/usr/bin/env python3
"""
Mark duplicate programming principles (JEV-1b / PRIN-DEDUPE).

Four books each restate Prilepin, warm-up ramps and deload rules, and the
joined-section extractor keeps them all — 2,234 principles on the dev copy
where `MAX_PRINCIPLES_IN_PROMPT` should be choosing among distinct rules.

Two stages: (1) embed every principle (name + rationale) with the corpus
embedder and pair same-category near neighbours by cosine similarity —
the prefilter a name-similarity scan missed (44 pairs vs the real number);
(2) ask Jev (TypeSafe AI) one calibrated yes/no per pair — "do these state
the same rule?" — and, for pairs at or above the threshold, point the later
row at the earlier via `programming_principles.duplicate_of` (migration
0016; union-find keeps chains on one canonical root). Nothing is deleted;
`plan._load_principles` skips rows with `duplicate_of` set.

Usage (from oly-ingestion/):
    PYTHONUTF8=1 uv run python dedupe_principles.py --dry-run
    PYTHONUTF8=1 uv run python dedupe_principles.py [--min-cosine 0.80] [--threshold 0.7]
    PYTHONUTF8=1 uv run python dedupe_principles.py --release      # clear every duplicate_of

Cost: one embedding batch (~250k tokens, cents) + one Jev call per candidate
pair (~$0.04 per 1,000 pairs). Needs OPENAI_API_KEY + TYPESAFE_API_KEY.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root for shared.*
from config import Settings
from loaders.vector_loader import VectorLoader

from shared.constants import PRINCIPLE_DUPLICATE_MIN_COSINE, PRINCIPLE_DUPLICATE_THRESHOLD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SAME_RULE = ("Do principle_A and principle_B state the same programming rule — the same trigger (condition) and "
             "the same prescription (recommendation) — such that keeping both would be a duplicate?")


def principle_text(row: dict) -> str:
    return f"{row['principle_name']} — {(row['rationale'] or '')[:400]}"


def candidate_pairs(rows: list[dict], vectors, min_cosine: float) -> list[tuple[int, int, float]]:
    """(i, j, cosine) over same-category pairs with cosine >= min_cosine; i < j by list index.
    `vectors` is an (n, d) array of unit-normalised embeddings."""
    import numpy as np

    sims = vectors @ vectors.T
    out = []
    n = len(rows)
    for i in range(n):
        js = np.nonzero(sims[i, i + 1:] >= min_cosine)[0] + i + 1
        for j in js:
            if rows[i]["category"] == rows[j]["category"]:
                out.append((i, int(j), float(sims[i, j])))
    return out


async def judge_pairs_async(pairs: list[dict], concurrency: int = 8) -> dict[int, float]:
    """{pair index: P(same rule)}; failed calls are left out."""
    from typesafe_sdk import AsyncTypeSafeClient, Noul

    q = Noul(instructions=SAME_RULE)
    out: dict[int, float] = {}
    sem = asyncio.Semaphore(concurrency)
    async with AsyncTypeSafeClient() as client:
        async def one(k: int, state: dict) -> None:
            async with sem:
                try:
                    r = await client.system_one(state=state, questions={"same": q})
                except Exception as e:
                    logger.warning(f"Jev failed for pair {k}: {type(e).__name__}: {e}")
                    return
            out[k] = float(r.nouls["same"].noul)
        await asyncio.gather(*(one(k, s) for k, s in enumerate(pairs)))
    return out


def judge_pairs(pairs: list[dict], **kwargs) -> dict[int, float]:
    return asyncio.run(judge_pairs_async(pairs, **kwargs))


def canonical_map(ids: list[int], duplicate_pairs: list[tuple[int, int]]) -> dict[int, int]:
    """Union-find over duplicate pairs → {id: canonical id} for non-canonical ids.
    The canonical member of a group is its lowest id (the first extracted)."""
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in duplicate_pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))
            parent[hi] = lo
    return {i: find(i) for i in ids if find(i) != i}


def pair_state(a: dict, b: dict) -> dict:
    def view(r):
        return {"name": r["principle_name"], "condition": r["condition"], "recommendation": r["recommendation"],
                "rationale": (r["rationale"] or "")[:400]}
    return {"principle_A": view(a), "principle_B": view(b)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mark duplicate principles with an embedding prefilter + Jev (PRIN-DEDUPE)")
    ap.add_argument("--min-cosine", type=float, default=PRINCIPLE_DUPLICATE_MIN_COSINE)
    ap.add_argument("--threshold", type=float, default=PRINCIPLE_DUPLICATE_THRESHOLD)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--release", action="store_true", help="clear duplicate_of on every principle")
    args = ap.parse_args(argv)

    settings = Settings()
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    if args.release:
        cur.execute("UPDATE programming_principles SET duplicate_of = NULL, duplicate_probability = NULL")
        conn.commit()
        print(f"released {cur.rowcount} principle(s)")
        return 0

    cur.execute("SELECT id, principle_name, category::text, source_id, condition, recommendation, rationale "
                "FROM programming_principles ORDER BY id")
    cols = ("id", "principle_name", "category", "source_id", "condition", "recommendation", "rationale")
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    logger.info(f"{len(rows)} principles")

    import numpy as np

    loader = VectorLoader(settings)
    vectors = np.array(loader._embed_batch([principle_text(r) for r in rows]), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    loader.close()
    pairs = candidate_pairs(rows, vectors, args.min_cosine)
    logger.info(f"{len(pairs)} same-category pairs at cosine >= {args.min_cosine}")

    states = [pair_state(rows[i], rows[j]) for i, j, _c in pairs]
    probs = judge_pairs(states)
    dup_pairs = [(rows[i]["id"], rows[j]["id"]) for k, (i, j, _c) in enumerate(pairs) if probs.get(k, 0.0) >= args.threshold]
    prob_by_id: dict[int, float] = {}
    for k, (i, j, _c) in enumerate(pairs):
        if probs.get(k, 0.0) >= args.threshold:
            hi = max(rows[i]["id"], rows[j]["id"])
            prob_by_id[hi] = max(prob_by_id.get(hi, 0.0), probs[k])
    mapping = canonical_map([r["id"] for r in rows], dup_pairs)
    cross = sum(1 for a, b in dup_pairs if next(r for r in rows if r["id"] == a)["source_id"] != next(r for r in rows if r["id"] == b)["source_id"])
    print(f"{'DRY RUN — ' if args.dry_run else ''}{len(dup_pairs)} duplicate pairs at P >= {args.threshold} "
          f"({cross} cross-source) → {len(mapping)} principles marked as duplicates of {len(set(mapping.values()))} canonical rows; "
          f"{len(rows) - len(mapping)} distinct principles remain")
    by_id = {r["id"]: r for r in rows}
    for dup, canon in sorted(mapping.items(), key=lambda kv: -prob_by_id.get(kv[0], 0))[:8]:
        print(f"  {prob_by_id.get(dup, 0):.2f}  #{dup} {by_id[dup]['principle_name'][:60]!r}  →  #{canon} {by_id[canon]['principle_name'][:60]!r}")
    if args.dry_run:
        return 0
    cur.execute("UPDATE programming_principles SET duplicate_of = NULL, duplicate_probability = NULL")
    cur.executemany("UPDATE programming_principles SET duplicate_of = %s, duplicate_probability = %s WHERE id = %s",
                    [(canon, prob_by_id.get(dup), dup) for dup, canon in mapping.items()])
    conn.commit()
    (Path(__file__).parent / "sources" / "principle_duplicates_last_run.json").write_text(
        json.dumps({"pairs": dup_pairs, "mapping": mapping}, indent=1), encoding="utf-8")
    print(f"written: {len(mapping)} duplicate_of links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
