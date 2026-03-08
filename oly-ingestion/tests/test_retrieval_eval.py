# tests/test_retrieval_eval.py
"""
Retrieval quality evaluation queries.

Requires: live DB with ingested content + OPENAI_API_KEY.
Run after Phase 3 (first ingestion) to validate chunk quality.

Run: python tests/test_retrieval_eval.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Evaluation query set ──────────────────────────────────────
# From the design doc — used to measure retrieval quality after ingestion.

RETRIEVAL_EVAL_QUERIES = [
    {
        "query": "How should I structure volume during a 4-week accumulation block?",
        "expected_topics": ["accumulation_phase", "volume_management"],
        "expected_chunk_type": ["periodization", "programming_rationale"],
        "should_contain_numbers": True,
    },
    {
        "query": "What exercises help fix an athlete who consistently misses snatches forward?",
        "expected_topics": ["fault_correction", "snatch_technique", "exercise_selection_rationale"],
        "expected_chunk_type": ["fault_correction", "methodology"],
    },
    {
        "query": "How many weeks out should I start reducing volume before a competition?",
        "expected_topics": ["competition_peaking", "volume_management"],
        "expected_chunk_type": ["periodization", "programming_rationale"],
        "should_contain_numbers": True,
    },
    {
        "query": "When should a beginner transition from learning technique to structured programming?",
        "expected_topics": ["beginner_development", "periodization_models"],
        "expected_chunk_type": ["methodology", "concept"],
    },
    {
        "query": "What is the relationship between squat strength and clean & jerk performance?",
        "expected_topics": ["squat_programming", "clean_programming"],
        "expected_chunk_type": ["concept", "biomechanics"],
    },
    # Negative / edge cases
    {
        "query": "How to do a barbell curl",
        "expected_topics": [],
        "note": "Should return few or no results — curls are not in the knowledge base.",
    },
    {
        "query": "What percentage should I use for back squats?",
        "note": "Ambiguous query — results should span multiple contexts.",
    },
]


def _search_principles(conn, keywords: list[str], limit: int = 3) -> list[dict]:
    """Simple keyword search against programming_principles for eval display."""
    if not keywords:
        return []
    cur = conn.cursor()
    # Search principle_name and rationale for any keyword
    like_clauses = " OR ".join(
        ["lower(principle_name) LIKE %s OR lower(rationale) LIKE %s"] * len(keywords)
    )
    params = []
    for kw in keywords:
        params.extend([f"%{kw.lower()}%", f"%{kw.lower()}%"])
    params.append(limit)
    cur.execute(
        f"SELECT principle_name, category, priority, recommendation "
        f"FROM programming_principles WHERE {like_clauses} "
        f"ORDER BY priority DESC LIMIT %s",
        params,
    )
    rows = cur.fetchall()
    cur.close()
    return [{"name": r[0], "category": r[1], "priority": r[2], "recommendation": r[3]} for r in rows]


def run_eval(top_k: int = 5):
    """Run all evaluation queries and print a report."""
    from loaders.vector_loader import VectorLoader
    from config import Settings
    import psycopg2

    settings = Settings()
    loader = VectorLoader(settings)
    db_conn = psycopg2.connect(settings.database_url)

    print(f"\n{'='*60}")
    print(f"RETRIEVAL EVAL — top_k={top_k}")
    print(f"{'='*60}\n")

    for i, eval_case in enumerate(RETRIEVAL_EVAL_QUERIES, 1):
        query = eval_case["query"]
        expected_topics = eval_case.get("expected_topics", [])
        expected_types = eval_case.get("expected_chunk_type", [])
        require_numbers = eval_case.get("should_contain_numbers", False)
        note = eval_case.get("note", "")

        print(f"[{i}] {query}")
        if note:
            print(f"     NOTE: {note}")

        results = loader.similarity_search(
            query=query,
            top_k=top_k,
            require_numbers=require_numbers if require_numbers else None,
        )

        if not results:
            print("     → No results returned")
            print()
            continue

        # Evaluate topic hit rate
        returned_topics: set[str] = set()
        returned_types: set[str] = set()
        sources_seen: set[int] = set()

        for r in results:
            returned_topics.update(r.get("topics") or [])
            returned_types.add(r.get("chunk_type", ""))
            sources_seen.add(r.get("source_id"))

        topic_hits = [t for t in expected_topics if t in returned_topics]
        type_hits = [t for t in expected_types if t in returned_types]

        print(f"     Results: {len(results)}, Sources: {len(sources_seen)}")
        print(f"     Similarity range: {results[-1]['similarity']:.3f} – {results[0]['similarity']:.3f}")
        if expected_topics:
            print(f"     Topic hits: {topic_hits} / {expected_topics}")
        if expected_types:
            print(f"     Type hits:  {type_hits} / {expected_types}")

        # Show top result snippet
        top = results[0]
        snippet = top["raw_content"][:120].replace("\n", " ")
        print(f"     Top result (sim={top['similarity']:.3f}): \"{snippet}...\"")

        # Show matching principles for queries that expect structured rules
        if expected_topics:
            # Use expected topics as search keywords against principles table
            search_keywords = [t.replace("_", " ") for t in expected_topics[:2]]
            matching_principles = _search_principles(db_conn, search_keywords, limit=2)
            if matching_principles:
                print(f"     Matching principles ({len(matching_principles)}):")
                for p in matching_principles:
                    print(f"       [{p['category']}] {p['name']} (priority={p['priority']})")
        print()

    loader.close()
    db_conn.close()


if __name__ == "__main__":
    run_eval(top_k=5)
