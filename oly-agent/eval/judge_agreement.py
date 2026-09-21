# oly-agent/eval/judge_agreement.py
"""
JUDGE-1: can the golden-set judge move off Claude Haiku?

Re-grades the *existing* golden pool (same queries, same candidate ids, same
prompt) with a candidate judge and reports agreement with the stored grades:
exact agreement, Cohen's κ on the 0/1/2 scale (linear weights), and the
binary agreement that the retrieval metrics actually depend on (relevant =
grade ≥ 1). Nothing is written unless --write is passed, which saves the
candidate's grades beside golden.json for a re-freeze.

    PYTHONUTF8=1 uv run python -m eval.judge_agreement --model deepseek/deepseek-v4.1-flash
    PYTHONUTF8=1 uv run python -m eval.judge_agreement --model moonshotai/kimi-k3 --limit 10
"""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from eval.build_golden import grade_candidates  # noqa: E402

from shared.config import Settings  # noqa: E402
from shared.db import fetch_all, get_connection  # noqa: E402
from shared.llm import create_llm_client  # noqa: E402

logger = logging.getLogger(__name__)
GOLDEN = Path(__file__).parent / "golden.json"


def weighted_kappa(a: list[int], b: list[int], levels: int = 3) -> float:
    """Cohen's κ with linear weights on an ordinal scale 0..levels-1."""
    n = len(a)
    if n == 0:
        return float("nan")
    obs = [[0] * levels for _ in range(levels)]
    for x, y in zip(a, b, strict=True):
        obs[x][y] += 1
    ra = [sum(row) for row in obs]
    cb = [sum(obs[i][j] for i in range(levels)) for j in range(levels)]
    num = den = 0.0
    for i in range(levels):
        for j in range(levels):
            w = abs(i - j) / (levels - 1)
            num += w * obs[i][j]
            den += w * ra[i] * cb[j] / n
    return 1.0 - num / den if den else float("nan")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="candidate judge (OpenRouter id)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N queries")
    ap.add_argument("--write", action="store_true", help="save the candidate grades to eval/golden_<model>.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = golden["queries"][: args.limit or None]
    ids = sorted({int(cid) for q in queries for cid in q["grades"]})
    conn = get_connection(Settings().database_url)
    rows = fetch_all(conn, "SELECT id, raw_content FROM knowledge_chunks WHERE id = ANY(%s)", (ids,))
    text = {r["id"]: r["raw_content"] for r in rows}
    client = create_llm_client(Settings())

    pairs: list[tuple[int, int]] = []
    per_query = []
    out = {"model": args.model, "queries": []}
    for q in queries:
        cands = [{"id": int(cid), "raw_content": text.get(int(cid), "")} for cid in q["grades"] if int(cid) in text]
        got = grade_candidates(client, args.model, q["query"], cands)
        both = [(int(q["grades"][str(cid)]), int(g)) for cid, g in got.items() if str(cid) in q["grades"]]
        pairs.extend(both)
        exact = sum(1 for a, b in both if a == b) / max(1, len(both))
        per_query.append((q["id"], len(both), exact))
        out["queries"].append({"id": q["id"], "grades": {str(k): v for k, v in got.items()}})
        logger.info(f"{q['id']:<42} n={len(both):3d} exact={exact:.2f}")

    a = [x for x, _ in pairs]
    b = [y for _, y in pairs]
    exact = sum(1 for x, y in pairs if x == y) / max(1, len(pairs))
    binary = sum(1 for x, y in pairs if (x >= 1) == (y >= 1)) / max(1, len(pairs))
    kappa = weighted_kappa(a, b)
    ka = weighted_kappa([int(x >= 1) for x in a], [int(y >= 1) for y in b], levels=2)
    dist_h, dist_c = Counter(a), Counter(b)
    print(f"\n{args.model} vs stored Haiku grades on {len(pairs)} (query, chunk) pairs")
    print(f"  exact agreement       {exact:.3f}")
    print(f"  weighted κ (0/1/2)    {kappa:.3f}")
    print(f"  relevant/not agreement {binary:.3f}   binary κ {ka:.3f}")
    print(f"  grade distribution    haiku {dict(sorted(dist_h.items()))}   candidate {dict(sorted(dist_c.items()))}")
    print(f"  verdict: {'ACCEPT (κ ≥ 0.7)' if kappa >= 0.7 else 'REJECT (κ < 0.7)'}")
    if args.write:
        dest = GOLDEN.with_name(f"golden_{args.model.replace('/', '_')}.json")
        dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"  candidate grades written to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
