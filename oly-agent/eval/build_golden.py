#!/usr/bin/env python3
"""
Build golden.json: a graded relevance set for the eval queries (RAG-M6).

For every query in eval/queries.py the candidate pool is the UNION of the dense
top-N and the hybrid top-N (so the labels don't favour either retriever), and an
LLM grades each candidate 0 / 1 / 2 for the query. Grades are frozen to
golden.json; run_eval.py then scores any retriever change against them.

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.build_golden --dry-run        # queries + pool sizes, no LLM
    PYTHONUTF8=1 uv run python -m eval.build_golden --limit 5        # smoke test
    PYTHONUTF8=1 uv run python -m eval.build_golden [--model claude-haiku-4-5-20251001]

Cost: ~70 queries × ~30 candidates × ~400 tokens ≈ 1M input tokens — about a
dollar on a Haiku-class model. Skim the output (grades are stored next to a
snippet) before accepting it as the baseline. Rebuild after any corpus change.
"""

import argparse
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT = _HERE.parent
_REPO = _AGENT.parent
for p in (str(_REPO), str(_AGENT), str(_REPO / "oly-ingestion")):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.constants import VECTOR_SEARCH_MIN_SIMILARITY
from shared.llm import (
    create_llm_client,
    create_message_with_retries,
    json_schema_kwargs,
    light_model_for,
    message_text,
    parse_llm_json,
    thinking_kwargs,
)

logger = logging.getLogger(__name__)

CANDIDATES_PER_RETRIEVER = 15
SNIPPET_CHARS = 1200
GRADE_BATCH = 10

GRADING_PROMPT = """\
You are grading search results for an Olympic weightlifting program generator.

The QUERY is what the generator searched for when building one training session (or when
looking for how to correct a technical fault / address a strength limiter). Grade each
PASSAGE for how useful it would be as reference material for that purpose:

2 = directly useful: it addresses the query's subject with concrete programming guidance
    (exercise choice, volume/intensity, progression, correction method) for that context
1 = partially useful: related background, or the right topic without actionable guidance
0 = not useful: different topic, generic filler, table fragments, or off-domain

QUERY: {query}

PASSAGES:
{passages}

Respond with JSON only: {{"grades": [{{"id": <passage id>, "grade": 0|1|2}}, ...]}} — one entry per passage."""

# Constrains the reply (STRUCT-1). One prose-wrapped reply killed the first
# golden build on 2026-09-16; with the schema the item-by-item salvage below
# is a fallback for replies produced without it.
GRADING_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "grades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "grade": {"type": "integer", "enum": [0, 1, 2]}},
                "required": ["id", "grade"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["grades"],
    "additionalProperties": False,
}


def candidate_pool(loader, query: dict, n: int = CANDIDATES_PER_RETRIEVER) -> list[dict]:
    """Dense ∪ hybrid top-n under the query's production preference, deduped by id."""
    pool: dict[int, dict] = {}
    for hybrid in (False, True):
        for r in loader.similarity_search(
            query=query["query"], top_k=n, min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
            preferred_chunk_types=query.get("preferred_chunk_types") or None, hybrid=hybrid,
        ):
            pool.setdefault(r["id"], r)
    return list(pool.values())


def build_grading_prompt(query: str, candidates: list[dict]) -> str:
    passages = "\n\n".join(f"[id {c['id']}] {str(c.get('raw_content', ''))[:SNIPPET_CHARS]}" for c in candidates)
    return GRADING_PROMPT.format(query=query, passages=passages)


_GRADE_ITEM_RE = re.compile(r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"grade"\s*:\s*(\d)\s*\}')


def parse_grades(raw_text: str, allowed_ids: set[int]) -> dict[int, int]:
    """``[{"id": 4684, "grade": 2}, …]`` → ``{4684: 2}`` for allowed ids and
    grades 0–2. A reply with prose around the array, two arrays, or "Extra
    data" after the JSON is salvaged item by item instead of raising — one
    such reply aborted a 45-query build on 2026-09-16."""
    try:
        items = parse_llm_json(raw_text)
    except (ValueError, TypeError):
        items = [{"id": int(i), "grade": int(g)} for i, g in _GRADE_ITEM_RE.findall(raw_text)]
    if isinstance(items, dict):
        items = items.get("grades", [items])   # schema wrapper, or a bare single object
    if not isinstance(items, list):
        items = []
    grades: dict[int, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            cid, g = int(item.get("id")), int(item.get("grade"))
        except (TypeError, ValueError):
            continue
        if cid in allowed_ids and g in (0, 1, 2):
            grades[cid] = g
    return grades


def grade_candidates(client, model: str, query: str, candidates: list[dict]) -> dict[int, int]:
    """Grade a query's candidate pool in batches. A batch whose reply can't be
    graded is retried once with a stricter instruction and then skipped with a
    warning — the query keeps the grades of its other batches."""
    grades: dict[int, int] = {}
    for i in range(0, len(candidates), GRADE_BATCH):
        batch = candidates[i:i + GRADE_BATCH]
        ids = {c["id"] for c in batch}
        prompt = build_grading_prompt(query, batch)
        for attempt in (1, 2):
            try:
                message = create_message_with_retries(
                    client, model=model, max_tokens=512,
                    messages=[{"role": "user", "content": prompt}],
                    **json_schema_kwargs(GRADING_SCHEMA, thinking_kwargs(model, "disabled")),
                )
                got = parse_grades(message_text(message), ids)
            except Exception as e:                       # API error, refusal, unparseable reply
                got = {}
                logger.warning(f"grading failed for {query[:50]!r} batch {i // GRADE_BATCH} (attempt {attempt}): {e}")
            if got:
                grades.update(got)
                break
            prompt = build_grading_prompt(query, batch) + "\n\nReply with ONLY the JSON array — no prose, no code fences."
        else:
            logger.warning(f"no grades for {query[:50]!r} batch {i // GRADE_BATCH} after 2 attempts — skipped")
    return grades


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the graded golden set for the retrieval eval (RAG-M6)")
    parser.add_argument("--out", type=Path, default=_HERE / "golden.json")
    parser.add_argument("--dry-run", action="store_true", help="print queries + pool sizes; no LLM calls")
    parser.add_argument("--limit", type=int, default=0, help="only the first N queries (smoke test)")
    parser.add_argument("--model", default=None, help="grading model (default: settings.light_model)")
    args = parser.parse_args(argv)

    from eval.queries import all_queries
    from loaders.vector_loader import VectorLoader

    from shared.config import Settings

    settings = Settings()
    queries = all_queries()
    if args.limit:
        queries = queries[:args.limit]
    loader = VectorLoader(settings)
    client = None
    if not args.dry_run:
        client = create_llm_client(settings)
    model = light_model_for(settings, args.model)

    graded = []
    try:
        for q in queries:
            pool = candidate_pool(loader, q)
            if args.dry_run:
                print(f"{q['id']:44s} pool={len(pool):3d}  {q['query'][:70]}")
                continue
            grades = grade_candidates(client, model, q["query"], pool)
            graded.append({
                **{k: q[k] for k in ("id", "kind", "query", "preferred_chunk_types")},
                "grades": {str(cid): g for cid, g in grades.items()},
                "snippets": {str(c["id"]): str(c.get("raw_content", ""))[:160] for c in pool},
            })
            n2 = sum(1 for g in grades.values() if g == 2)
            print(f"{q['id']:44s} pool={len(pool):3d} graded={len(grades):3d} grade2={n2:2d}")
    finally:
        loader.close()

    if args.dry_run:
        print(f"\n{len(queries)} queries (dry run)")
        return 0
    out = {
        "meta": {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "model": model,
                 "candidates_per_retriever": CANDIDATES_PER_RETRIEVER, "n_queries": len(graded)},
        "queries": graded,
    }
    args.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\ngolden set written: {args.out} ({len(graded)} queries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
