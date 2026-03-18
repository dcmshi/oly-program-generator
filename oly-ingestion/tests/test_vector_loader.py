# tests/test_vector_loader.py
"""
Integration tests for VectorLoader against the live database.

Requires:
  - Docker Postgres running (docker compose up -d)
  - OPENAI_API_KEY set in .env

Run: PYTHONUTF8=1 uv run python tests/test_vector_loader.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Settings
from loaders.vector_loader import VectorLoader
from loaders.structured_loader import StructuredLoader
from processors.chunker import Chunk

TEST_PREFIX = "__test__"


def make_chunk(raw: str, chapter: str = "Test Chapter", topics: list[str] | None = None) -> Chunk:
    """Build a minimal Chunk with preamble prepended to content."""
    preamble = f"Source: Test Book | Chapter: {chapter}\n\n"
    return Chunk(
        content=preamble + raw,
        raw_content=raw,
        metadata={
            "chapter": chapter,
            "section_title": "Test Section",
            "chunk_type": "concept",
            "page_number": 1,
            "athlete_level_relevance": "intermediate",
        },
        token_count=len(raw.split()),
        topics=topics or ["snatch", "technique"],
        contains_specific_numbers=True,
        information_density="high",
    )


def make_loaders() -> tuple[VectorLoader, StructuredLoader]:
    s = Settings()
    return VectorLoader(s), StructuredLoader(s)


def cleanup(vloader: VectorLoader, sloader: StructuredLoader, source_id: int | None = None):
    cur = vloader.conn.cursor()
    if source_id:
        cur.execute("DELETE FROM knowledge_chunks WHERE source_id = %s", (source_id,))
        cur.execute("DELETE FROM ingestion_runs WHERE source_id = %s", (source_id,))
        cur.execute("DELETE FROM sources WHERE id = %s", (source_id,))
    vloader.conn.commit()
    cur.close()


# ── embed_and_load ─────────────────────────────────────────────

def test_load_single_chunk():
    """Embed one chunk and verify it lands in knowledge_chunks."""
    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Vector Book", "Test Author", "book")

    chunk = make_chunk(
        "The snatch requires precise timing at the extension. "
        "Athletes should achieve full hip and knee extension before initiating the pull under. "
        "The bar should be close to the body throughout the lift.",
    )
    loaded = vl.load_chunks([chunk], source_id=sid)
    assert loaded == 1, f"Expected 1 chunk loaded, got {loaded}"

    cur = vl.conn.cursor()
    cur.execute(
        "SELECT content, chunk_type, topics, information_density FROM knowledge_chunks WHERE source_id = %s",
        (sid,),
    )
    row = cur.fetchone()
    cur.close()
    assert row is not None, "Chunk not found in DB"
    assert "snatch" in row[0].lower()
    assert row[1] == "concept"
    assert "snatch" in row[2]
    assert row[3] == "high"

    print(f"  load_single_chunk: chunk stored with embedding, source_id={sid} OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


def test_dedup_skips_identical_chunk():
    """Re-inserting the same raw_content should be skipped (content_hash dedup)."""
    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Dedup Book", "Test Author", "book")

    chunk = make_chunk(
        "Clean and jerk demands a strong overhead position. "
        "The jerk split requires lat engagement to stabilize the bar overhead.",
    )
    first = vl.load_chunks([chunk], source_id=sid)
    assert first == 1

    second = vl.load_chunks([chunk], source_id=sid)
    assert second == 0, f"Expected 0 (dedup), got {second}"

    cur = vl.conn.cursor()
    cur.execute("SELECT count(*) FROM knowledge_chunks WHERE source_id = %s", (sid,))
    count = cur.fetchone()[0]
    cur.close()
    assert count == 1, f"Expected 1 row in DB (no duplicate), found {count}"

    print(f"  dedup: first={first}, second={second} (skipped) OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


def test_embedding_is_nonzero():
    """Verify the embedding vector is non-trivial (not all zeros)."""
    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Embedding Check", "Test Author", "book")

    chunk = make_chunk(
        "Periodization structures training into phases of accumulation, "
        "intensification, and realization to peak for competition.",
        topics=["periodization", "programming"],
    )
    vl.load_chunks([chunk], source_id=sid)

    cur = vl.conn.cursor()
    cur.execute(
        "SELECT embedding FROM knowledge_chunks WHERE source_id = %s",
        (sid,),
    )
    row = cur.fetchone()
    cur.close()
    assert row is not None
    embedding = row[0]
    assert len(embedding) == 1536, f"Expected 1536 dims, got {len(embedding)}"
    assert any(v != 0.0 for v in embedding), "Embedding is all zeros"
    nonzero = sum(1 for v in embedding if v != 0.0)
    print(f"  embedding: 1536 dims, {nonzero} non-zero values OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


def test_similarity_search_returns_relevant_chunk():
    """Insert a chunk and verify similarity search retrieves it for a related query."""
    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Search Book", "Test Author", "book")

    chunk = make_chunk(
        "Prilepin's chart prescribes optimal rep ranges for each intensity zone. "
        "At 70-75% of 1RM, athletes should perform 18-24 total reps per session. "
        "Exceeding this volume leads to accumulated fatigue without additional strength gains.",
        topics=["prilepin", "volume", "programming"],
    )
    vl.load_chunks([chunk], source_id=sid)

    results = vl.similarity_search(
        "How many reps should I do at 70 percent of max?",
        top_k=5,
    )
    assert len(results) > 0, "Similarity search returned no results"

    # The inserted chunk should be the top result (it's the only one with this content)
    top = results[0]
    assert "prilepin" in top["content"].lower() or "prilepin" in top["raw_content"].lower(), (
        f"Expected Prilepin chunk to rank first, got: {top['raw_content'][:80]}"
    )
    assert top["similarity"] > 0.5, f"Expected similarity > 0.5, got {top['similarity']:.4f}"

    print(f"  similarity_search: top result similarity={top['similarity']:.4f} OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


def test_similarity_search_with_filters():
    """Filtered search: chunk_type filter should narrow results correctly."""
    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Filter Book", "Test Author", "book")

    chunk = make_chunk(
        "Back squat training builds the posterior chain strength necessary "
        "for a strong clean recovery. Sets of 3-5 at 80% are common.",
        topics=["squat", "strength"],
    )
    # Use a valid enum value that differs from the default 'concept'
    chunk.metadata["chunk_type"] = "fault_correction"

    vl.load_chunks([chunk], source_id=sid)

    # Filter for 'methodology' — our chunk is 'fault_correction', should be excluded
    results_other = vl.similarity_search(
        "back squat strength training",
        top_k=5,
        chunk_types=["methodology"],
    )
    source_ids_in_results = [r["source_id"] for r in results_other]
    assert sid not in source_ids_in_results, (
        "chunk_type='methodology' filter should have excluded our 'fault_correction' chunk"
    )

    # Filter for fault_correction — should include our chunk
    results_match = vl.similarity_search(
        "back squat strength training",
        top_k=5,
        chunk_types=["fault_correction"],
    )
    source_ids = [r["source_id"] for r in results_match]
    assert sid in source_ids, "fault_correction filter should return our chunk"

    print(f"  chunk_type filter: methodology excluded, fault_correction included OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


def test_load_with_run_logging():
    """load_chunks with run_id should write rows to ingestion_chunk_log."""
    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Run Log Book", "Test Author", "book")
    run_id = sl.create_run(sid, "/test/run_log.pdf", "runloghash456")

    chunk = make_chunk(
        "The turnover in the snatch must be aggressive. "
        "Athletes who are slow in the pull under often miss forward.",
        topics=["snatch", "technique"],
    )
    loaded = vl.load_chunks([chunk], source_id=sid, run_id=run_id, structured_loader=sl)
    assert loaded == 1

    cur = vl.conn.cursor()
    cur.execute(
        "SELECT count(*) FROM ingestion_chunk_log WHERE ingestion_run_id = %s",
        (run_id,),
    )
    count = cur.fetchone()[0]
    cur.close()
    assert count == 1, f"Expected 1 log entry, got {count}"

    print(f"  run logging: 1 chunk logged to ingestion_chunk_log OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


def test_empty_content_chunks_skipped_before_embed():
    """Chunks with empty content are filtered out before the embedding API is called.

    Requires: live DB only — no OPENAI_API_KEY needed because the filter
    runs before _embed_batch and returns 0 without making any API call.
    """
    from unittest.mock import patch

    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Empty Chunk Book", "Test Author", "book")

    empty_chunk = Chunk(
        content="   ",   # whitespace only
        raw_content="",
        metadata={"chapter": "Ch1", "section_title": "", "chunk_type": "concept", "page_number": 1},
        token_count=0,
        topics=[],
        contains_specific_numbers=False,
        information_density="low",
    )

    embed_calls = []
    original_embed = vl._embed_batch
    def tracking_embed(texts):
        embed_calls.append(texts)
        return original_embed(texts)

    with patch.object(vl, "_embed_batch", side_effect=tracking_embed):
        loaded = vl.load_chunks([empty_chunk], source_id=sid)

    assert loaded == 0, f"Expected 0 chunks loaded for empty content, got {loaded}"
    assert len(embed_calls) == 0, (
        f"_embed_batch should not be called for empty chunks, was called {len(embed_calls)} time(s)"
    )

    cur = vl.conn.cursor()
    cur.execute("SELECT count(*) FROM knowledge_chunks WHERE source_id = %s", (sid,))
    count = cur.fetchone()[0]
    cur.close()
    assert count == 0, f"Expected no chunks stored for empty content, found {count}"

    print(f"  empty_chunk_filter: 0 loaded, embed not called, DB clean OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


def test_mixed_empty_and_valid_chunks_only_valid_embedded():
    """When a batch has both empty and valid chunks, only valid ones are embedded and stored."""
    from unittest.mock import patch

    vl, sl = make_loaders()
    sid = sl.upsert_source(f"{TEST_PREFIX}Mixed Chunk Book", "Test Author", "book")

    valid_chunk = make_chunk(
        "The snatch pull develops explosive hip extension critical for the full snatch.",
        topics=["snatch", "pull_programming"],
    )
    empty_chunk = Chunk(
        content="",
        raw_content="",
        metadata={"chapter": "Ch1", "section_title": "", "chunk_type": "concept", "page_number": 1},
        token_count=0,
        topics=[],
        contains_specific_numbers=False,
        information_density="low",
    )

    loaded = vl.load_chunks([empty_chunk, valid_chunk], source_id=sid)

    assert loaded == 1, f"Expected 1 chunk loaded (valid only), got {loaded}"

    cur = vl.conn.cursor()
    cur.execute("SELECT count(*) FROM knowledge_chunks WHERE source_id = %s", (sid,))
    count = cur.fetchone()[0]
    cur.close()
    assert count == 1, f"Expected 1 chunk in DB, found {count}"

    print(f"  mixed_chunks: {loaded} loaded (empty skipped, valid stored) OK")
    cleanup(vl, sl, sid)
    vl.close()
    sl.close()


# ── Runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_load_single_chunk,
        test_dedup_skips_identical_chunk,
        test_embedding_is_nonzero,
        test_similarity_search_returns_relevant_chunk,
        test_similarity_search_with_filters,
        test_load_with_run_logging,
        test_empty_content_chunks_skipped_before_embed,
        test_mixed_empty_and_valid_chunks_only_valid_embedded,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
