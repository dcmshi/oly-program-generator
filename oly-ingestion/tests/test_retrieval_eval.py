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
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))

MIN_SIMILARITY = 0.45  # mirrors VECTOR_SEARCH_MIN_SIMILARITY in shared/constants.py

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
        "expected_chunk_type": ["periodization", "programming_rationale", "concept"],
        "should_contain_numbers": True,
        "note": "Explanatory peaking content is correctly classified as 'concept'; eval accepts concept type.",
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
    # Jerk faults
    {
        "query": "What causes an athlete to press out on the jerk and how do I fix it?",
        "expected_topics": ["fault_correction", "jerk_technique"],
        "expected_chunk_type": ["fault_correction", "methodology"],
    },
    # Clean technique
    {
        "query": "How should I coach the catch position in the clean for an athlete with poor thoracic mobility?",
        "expected_topics": ["clean_technique", "fault_correction"],
        "expected_chunk_type": ["fault_correction", "methodology", "concept"],
    },
    # Recovery / fatigue management
    {
        "query": "How much recovery time is needed between heavy sessions and how do I manage fatigue across a training week?",
        "expected_topics": ["fatigue_management", "recovery_protocols"],
        "expected_chunk_type": ["periodization", "concept", "recovery_adaptation"],
        "note": "Fatigue/recovery content is correctly classified as 'concept' or 'recovery_adaptation'; eval accepts both.",
    },
    # Competition attempt selection
    {
        "query": "How should an athlete select opening attempts and second attempts at a competition?",
        "expected_topics": ["competition_strategy"],
        "expected_chunk_type": ["methodology", "concept"],
    },
    # Long-term athlete development (Medvedev's specialty)
    {
        "query": "How should multi-year training be structured from beginner to elite weightlifter?",
        "expected_topics": ["beginner_development", "periodization_models", "annual_planning"],
        "expected_chunk_type": ["periodization", "methodology", "concept"],
        "note": "Long-term development content is predominantly classified 'concept'; eval accepts it.",
    },
    # Training frequency / session structure
    {
        "query": "How many training sessions per week should an intermediate weightlifter do?",
        "expected_topics": ["periodization_models", "intermediate_development"],
        "expected_chunk_type": ["periodization", "methodology", "concept"],
    },
    # Prilepin / intensity zones
    {
        "query": "What does Prilepin's chart say about optimal sets and reps at 80 to 90 percent intensity?",
        "expected_topics": ["intensity_prescription", "volume_management"],
        "expected_chunk_type": ["periodization", "concept"],
        "should_contain_numbers": True,
    },
    # Accessory / hypertrophy (Israetel)
    {
        "query": "How much weekly volume should I do for upper back hypertrophy as a weightlifter?",
        "expected_topics": ["exercise_selection_rationale"],
        "expected_chunk_type": ["concept", "methodology"],
        "note": (
            "Israetel covers MEV/MAV/MRV as a framework but has no per-muscle-group volume tables. "
            "Expect framework-level content (concept type); do not require numbers."
        ),
    },
    {
        "query": "How do I periodise accessory hypertrophy work alongside competition lift training?",
        "expected_topics": ["exercise_selection_rationale", "periodization_models"],
        "expected_chunk_type": ["periodization", "concept", "methodology"],
        "note": "Should surface Israetel on integrating hypertrophy blocks with strength phases.",
    },
    # Mobility (Starrett)
    {
        "query": "How do I improve hip mobility and squat depth for the receiving position in the clean?",
        "expected_topics": ["clean_technique"],
        "expected_chunk_type": ["fault_correction", "methodology", "concept"],
        "note": "Should surface Starrett content on hip mobility and squat mechanics.",
    },
    {
        "query": "What mobility work helps an athlete achieve a better overhead position in the snatch?",
        "expected_topics": ["snatch_technique"],
        "expected_chunk_type": ["fault_correction", "methodology", "concept"],
        "note": "Should surface Starrett shoulder/thoracic mobility content.",
    },
    {
        "query": "How should I address limited ankle mobility that causes an athlete to fold forward in the squat?",
        "expected_topics": ["squat_programming"],
        "expected_chunk_type": ["fault_correction", "methodology", "concept"],
        "note": "Should surface Starrett ankle mobility content. Explanatory chunks correctly classified as 'concept'.",
    },
    # GPP / conditioning (Dan John)
    {
        "query": "What general physical preparation work should a weightlifter do in the off-season?",
        "expected_topics": ["exercise_selection_rationale", "periodization_models"],
        "expected_chunk_type": ["methodology", "concept"],
        "note": "Should surface Dan John GPP and big rocks content.",
    },
    {
        "query": "How should I use loaded carries and conditioning work alongside weightlifting training?",
        "expected_topics": ["exercise_selection_rationale"],
        "expected_chunk_type": ["methodology", "concept"],
        "note": "Should surface Dan John carry and conditioning content.",
    },
    # RPE / autoregulation (cross-source)
    {
        "query": "How do I use RPE to autoregulate training intensity from session to session?",
        "expected_topics": ["load_progression", "intensity_prescription"],
        "expected_chunk_type": ["methodology", "concept"],
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


def run_eval(top_k: int = 5, min_similarity: float = MIN_SIMILARITY):
    """Run all evaluation queries and print a report."""
    from loaders.vector_loader import VectorLoader
    from config import Settings
    import psycopg2

    settings = Settings()
    loader = VectorLoader(settings)
    db_conn = psycopg2.connect(settings.database_url)

    print(f"\n{'='*60}")
    print(f"RETRIEVAL EVAL — top_k={top_k}, min_similarity={min_similarity}")
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
            min_similarity=min_similarity,
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
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-similarity", type=float, default=MIN_SIMILARITY)
    args = parser.parse_args()
    run_eval(top_k=args.top_k, min_similarity=args.min_similarity)
