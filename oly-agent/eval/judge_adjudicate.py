# oly-agent/eval/judge_adjudicate.py
"""
JUDGE-1, step 2: who is right when the judges disagree?

κ against the stored Haiku grades only measures similarity to Haiku. This
script (a) builds the inter-judge agreement matrix over every judge whose
grades are on disk (golden.json = Haiku, golden_<model>.json = candidates),
(b) sends every (query, chunk) pair on which the judges do not all agree to a
strong adjudicator — Opus 5 by default — with the same grading prompt and no
sight of the judges' grades, and (c) scores each judge against the
adjudication: exact accuracy, weighted κ, and the binary relevant/not
accuracy that recall@k and MRR depend on. Unanimous pairs are scored as
correct for everyone (the adjudicator is not asked about them).

    PYTHONUTF8=1 uv run python -m eval.judge_adjudicate
    PYTHONUTF8=1 uv run python -m eval.judge_adjudicate --adjudicator anthropic/claude-sonnet-5 --limit 100

Writes eval/judge_adjudication.json (adjudicator grades + the per-judge report).
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
from eval.judge_agreement import weighted_kappa  # noqa: E402

from shared.config import Settings  # noqa: E402
from shared.db import fetch_all, get_connection  # noqa: E402
from shared.llm import create_llm_client, estimate_cost  # noqa: E402

logger = logging.getLogger(__name__)
HERE = Path(__file__).parent
GOLDEN = HERE / "golden.json"
OUT = HERE / "judge_adjudication.json"


def load_judges() -> dict[str, dict[str, dict[str, int]]]:
    """{judge: {query_id: {chunk_id: grade}}} — Haiku from golden.json, the rest from golden_*.json."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    judges = {golden["meta"].get("model", "haiku"): {q["id"]: {str(k): int(v) for k, v in q["grades"].items()} for q in golden["queries"]}}
    for f in sorted(HERE.glob("golden_*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        judges[data["model"]] = {q["id"]: {str(k): int(v) for k, v in q["grades"].items()} for q in data["queries"]}
    return judges


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adjudicator", default="anthropic/claude-opus-5")
    ap.add_argument("--limit", type=int, default=0, help="only the first N disputed pairs (smoke)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    judges = load_judges()
    names = list(judges)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    query_text = {q["id"]: q["query"] for q in golden["queries"]}

    # ── (a) inter-judge agreement matrix ──────────────────────────────────
    print(f"judges: {names}\n\ninter-judge weighted κ (pairs graded by both):")
    print("  " + " " * 30 + "".join(f"{n[-14:]:>16}" for n in names))
    for a in names:
        row = []
        for b in names:
            pairs = [(judges[a][q][c], judges[b][q][c]) for q in judges[a] for c in judges[a][q] if c in judges.get(b, {}).get(q, {})]
            row.append(weighted_kappa([x for x, _ in pairs], [y for _, y in pairs]) if a != b else 1.0)
        print(f"  {a[-30:]:<32}" + "".join(f"{v:16.3f}" for v in row))

    # ── (b) disputed pairs → adjudicator ─────────────────────────────────
    disputed: list[tuple[str, str]] = []
    unanimous = 0
    for q in query_text:
        chunk_ids = set.intersection(*(set(judges[n].get(q, {})) for n in names))
        for c in sorted(chunk_ids, key=int):
            grades = {judges[n][q][c] for n in names}
            if len(grades) > 1:
                disputed.append((q, c))
            else:
                unanimous += 1
    if args.limit:
        disputed = disputed[: args.limit]
    print(f"\n{unanimous} unanimous pairs, {len(disputed)} disputed → adjudicating with {args.adjudicator}")

    conn = get_connection(Settings().database_url)
    ids = sorted({int(c) for _, c in disputed})
    text = {r["id"]: r["raw_content"] for r in fetch_all(conn, "SELECT id, raw_content FROM knowledge_chunks WHERE id = ANY(%s)", (ids,))}
    client = create_llm_client(Settings())
    adjudicated: dict[str, dict[str, int]] = {}
    by_query: dict[str, list[str]] = {}
    for q, c in disputed:
        by_query.setdefault(q, []).append(c)
    in_tok = out_tok = 0
    for q, chunks in by_query.items():
        cands = [{"id": int(c), "raw_content": text.get(int(c), "")} for c in chunks]
        got = grade_candidates(client, args.adjudicator, query_text[q], cands)
        adjudicated[q] = {str(k): int(v) for k, v in got.items()}
        in_tok += sum(len(c["raw_content"]) for c in cands) // 4 + 400 * ((len(cands) + 9) // 10)
        out_tok += 30 * len(cands)
        logger.info(f"{q:<42} adjudicated {len(got)}/{len(cands)}")
    print(f"  ≈ {estimate_cost(in_tok, out_tok, args.adjudicator):.2f} USD")

    # ── (c) score every judge against the adjudication ───────────────────
    report = {}
    print(f"\njudge vs {args.adjudicator} on {sum(len(v) for v in adjudicated.values())} disputed pairs (+{unanimous} unanimous scored as agree):")
    print(f"  {'judge':<32}{'exact':>8}{'κ':>8}{'binary':>8}{'over':>8}{'under':>8}")
    for n in names:
        a, b = [], []
        over = under = 0
        for q, grades in adjudicated.items():
            for c, truth in grades.items():
                mine = judges[n][q][c]
                a.append(truth)
                b.append(mine)
                over += mine > truth
                under += mine < truth
        exact = (sum(1 for x, y in zip(a, b, strict=True) if x == y) + unanimous) / max(1, len(a) + unanimous)
        binary = (sum(1 for x, y in zip(a, b, strict=True) if (x >= 1) == (y >= 1)) + unanimous) / max(1, len(a) + unanimous)
        kappa = weighted_kappa(a, b)
        report[n] = {"exact_incl_unanimous": round(exact, 3), "kappa_disputed": round(kappa, 3),
                     "binary_incl_unanimous": round(binary, 3), "over": over, "under": under}
        print(f"  {n[-30:]:<32}{exact:8.3f}{kappa:8.3f}{binary:8.3f}{over:8d}{under:8d}")
    dist = Counter(v for g in adjudicated.values() for v in g.values())
    print(f"  adjudicator grade distribution on disputed pairs: {dict(sorted(dist.items()))}")
    OUT.write_text(json.dumps({"adjudicator": args.adjudicator, "unanimous": unanimous, "adjudicated": adjudicated,
                               "report": report}, indent=1), encoding="utf-8")
    print(f"  written {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
