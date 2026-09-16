#!/usr/bin/env python3
"""
Score the live retriever against golden.json under production settings (RAG-M6).

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.run_eval                    # report + compare with baseline.json
    PYTHONUTF8=1 uv run python -m eval.run_eval --update-baseline  # accept the current numbers
    PYTHONUTF8=1 uv run python -m eval.run_eval --dense-only       # ablation: hybrid off

Needs the corpus DB and OPENAI_API_KEY (query embeddings). Exits 1 when a
summary metric drops more than REGRESSION_TOLERANCE below the baseline, so it
can gate a change: run it before and after, or in CI under INTEGRATION_TESTS=1.

golden.json is produced by eval/build_golden.py (LLM-graded, run once per corpus
change). Without it this script only prints the query set.
"""

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT = _HERE.parent
_REPO = _AGENT.parent
for p in (str(_REPO), str(_AGENT), str(_REPO / "oly-ingestion")):
    if p not in sys.path:
        sys.path.insert(0, p)

from eval.metrics import score_query, summarize

from shared.constants import HYBRID_SEARCH_ENABLED, VECTOR_SEARCH_DEFAULT_TOP_K, VECTOR_SEARCH_MIN_SIMILARITY

GOLDEN_PATH = _HERE / "golden.json"
BASELINE_PATH = _HERE / "baseline.json"
REGRESSION_TOLERANCE = 0.02          # absolute drop in a mean metric that fails the run
GATED_METRICS = ("recall_at_k", "mrr", "ndcg_at_k")


def load_golden(path: Path = GOLDEN_PATH) -> dict:
    """golden.json: {"meta": {...}, "queries": [{"id", "query", "kind",
    "preferred_chunk_types", "grades": {"<chunk_id>": 0|1|2}}]}"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise ValueError("golden.json must be {'meta': {...}, 'queries': [...]}")
    for q in data["queries"]:
        for key in ("id", "query", "grades"):
            if key not in q:
                raise ValueError(f"golden query missing {key!r}: {q}")
        q["grades"] = {int(k): int(v) for k, v in q["grades"].items()}
    return data


def compare_to_baseline(summary: dict, baseline: dict | None,
                        tolerance: float = REGRESSION_TOLERANCE) -> list[str]:
    """Names of gated metrics that regressed more than `tolerance`."""
    if not baseline:
        return []
    regressed = []
    for key in GATED_METRICS:
        cur, base = summary.get(key), baseline.get(key)
        if cur is not None and base is not None and cur < base - tolerance:
            regressed.append(f"{key}: {base:.3f} → {cur:.3f}")
    return regressed


def evaluate(loader, golden: dict, top_k: int = VECTOR_SEARCH_DEFAULT_TOP_K,
             hybrid: bool = HYBRID_SEARCH_ENABLED) -> tuple[list[dict], dict, dict]:
    """Run every golden query through the production search settings.

    Returns (per_query rows, overall summary, summary per kind)."""
    rows = []
    for q in golden["queries"]:
        results = loader.similarity_search(
            query=q["query"],
            top_k=top_k,
            preferred_chunk_types=q.get("preferred_chunk_types") or None,
            min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
            hybrid=hybrid,
        )
        scored = score_query(results, q["grades"], top_k)
        rows.append({"id": q["id"], "kind": q.get("kind", "?"), **scored})
    by_kind = {}
    for kind in sorted({r["kind"] for r in rows}):
        by_kind[kind] = summarize([r for r in rows if r["kind"] == kind])
    return rows, summarize(rows), by_kind


def _print_report(rows, summary, by_kind, baseline, regressed):
    print(f"\n{'id':44s} {'R@k':>6s} {'MRR':>6s} {'nDCG':>6s} {'src%':>5s} {'n':>3s}")
    for r in rows:
        def f(v, w=6):
            return f"{v:>{w}.3f}" if isinstance(v, float) else f"{'—':>{w}s}"
        print(f"{r['id'][:44]:44s} {f(r['recall_at_k'])} {f(r['mrr'])} {f(r['ndcg_at_k'])} {f(r['max_source_share'], 5)} {r['n_results']:>3d}")
    print("\nsummary:", json.dumps(summary))
    for kind, s in by_kind.items():
        print(f"  {kind:8s}", json.dumps({k: s[k] for k in ('n_queries', 'recall_at_k', 'mrr', 'ndcg_at_k', 'max_source_share')}))
    if baseline:
        print("baseline:", json.dumps({k: baseline.get(k) for k in GATED_METRICS}))
    if regressed:
        print("\nREGRESSION:", "; ".join(regressed))
    else:
        print("\nno regression against baseline" if baseline else "\n(no baseline.json — run with --update-baseline to create one)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval eval against golden.json (RAG-M6)")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--top-k", type=int, default=VECTOR_SEARCH_DEFAULT_TOP_K)
    parser.add_argument("--dense-only", action="store_true", help="ablation: disable the lexical leg")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)

    if not args.golden.exists():
        from eval.queries import all_queries
        qs = all_queries()
        print(f"No golden set at {args.golden}. The query set has {len(qs)} queries "
              f"({sum(q['kind'] != 'legacy' for q in qs)} production-shaped + "
              f"{sum(q['kind'] == 'legacy' for q in qs)} legacy). "
              "Build it with: uv run python -m eval.build_golden")
        return 2

    from loaders.vector_loader import VectorLoader

    from shared.config import Settings

    golden = load_golden(args.golden)
    loader = VectorLoader(Settings())
    try:
        rows, summary, by_kind = evaluate(loader, golden, top_k=args.top_k, hybrid=not args.dense_only)
    finally:
        loader.close()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    regressed = compare_to_baseline(summary, baseline)
    _print_report(rows, summary, by_kind, baseline, regressed)

    if args.update_baseline:
        args.baseline.write_text(json.dumps({**summary, "by_kind": by_kind, "golden_meta": golden.get("meta", {})}, indent=2), encoding="utf-8")
        print(f"baseline written to {args.baseline}")
        return 0
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
