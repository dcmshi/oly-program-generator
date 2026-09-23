# oly-agent/eval/metrics.py
"""Retrieval metrics over ranked chunk ids (RAG-M6). Pure functions, no I/O.

Grades are 0 (not relevant), 1 (partially), 2 (directly useful); "relevant"
for recall/MRR means grade >= 1.

Plain recall@k divides by *every* relevant chunk, so with ~16 relevant chunks
per query and k = 5 it is capped near 0.3 whatever the ranking does (AUD-3).
`recall_at_k_normalized` divides by min(k, relevant) instead — 1.0 means the
top k is as full of relevant chunks as it can be. The gated variant counts
grade-2 ("directly useful") chunks only; the grade >= 1 variant is informational.
"""

import math
from collections import Counter


def relevant_ids(grades: dict[int, int], min_grade: int = 1) -> set[int]:
    return {cid for cid, g in grades.items() if g >= min_grade}


def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float | None:
    """Share of relevant ids found in the top k. None when nothing is relevant."""
    if not relevant:
        return None
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def recall_at_k_normalized(retrieved: list[int], relevant: set[int], k: int) -> float | None:
    """Relevant ids in the top k / min(k, |relevant|). None when nothing is relevant."""
    if not relevant or k <= 0:
        return None
    return len(set(retrieved[:k]) & relevant) / min(k, len(relevant))


def hit_at_k(retrieved: list[int], relevant: set[int], k: int) -> float | None:
    if not relevant:
        return None
    return 1.0 if set(retrieved[:k]) & relevant else 0.0


def mrr(retrieved: list[int], relevant: set[int]) -> float | None:
    """1 / rank of the first relevant id (0 when none is retrieved)."""
    if not relevant:
        return None
    for i, cid in enumerate(retrieved, 1):
        if cid in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: list[int], grades: dict[int, int], k: int) -> float | None:
    """Graded nDCG@k with gain = grade and log2 discount."""
    ideal = sorted((g for g in grades.values() if g > 0), reverse=True)[:k]
    if not ideal:
        return None
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    dcg = sum(grades.get(cid, 0) / math.log2(i + 1) for i, cid in enumerate(retrieved[:k], 1))
    return dcg / idcg if idcg else None


def max_source_share(results: list[dict]) -> float | None:
    """Largest fraction of the results coming from one source_id (diversity)."""
    if not results:
        return None
    counts = Counter(r.get("source_id") for r in results)
    return max(counts.values()) / len(results)


def score_query(results: list[dict], grades: dict[int, int], k: int) -> dict:
    retrieved = [r["id"] for r in results if "id" in r]
    rel = relevant_ids(grades)
    rel2 = relevant_ids(grades, min_grade=2)
    return {
        "recall_at_k": recall_at_k(retrieved, rel, k),
        "nrecall_at_k": recall_at_k_normalized(retrieved, rel2, k),
        "nrecall_g1_at_k": recall_at_k_normalized(retrieved, rel, k),
        "hit_at_k": hit_at_k(retrieved, rel, k),
        "mrr": mrr(retrieved, rel),
        "ndcg_at_k": ndcg_at_k(retrieved, grades, k),
        "max_source_share": max_source_share(results[:k]),
        "n_results": len(results),
        "n_relevant": len(rel),
        "n_relevant_g2": len(rel2),
    }


SUMMARY_METRICS = ("recall_at_k", "nrecall_at_k", "nrecall_g1_at_k", "hit_at_k", "mrr",
                   "ndcg_at_k", "max_source_share")


def summarize(per_query: list[dict]) -> dict:
    """Mean of each metric over the queries where it is defined."""
    out: dict = {"n_queries": len(per_query)}
    for key in SUMMARY_METRICS:
        vals = [q[key] for q in per_query if q.get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
        out[f"{key}_n"] = len(vals)
    return out
