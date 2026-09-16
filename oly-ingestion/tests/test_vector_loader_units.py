# tests/test_vector_loader_units.py
"""
No-key unit tests for VectorLoader pure helpers.

Covers the dedup partition behind I-M9 (single existing-hash lookup + intra-batch
dedup) and I-M5 (accurate skipped count). No DB or OPENAI_API_KEY needed.

Run: python tests/test_vector_loader_units.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loaders.vector_loader import VectorLoader

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


# chunk objects are opaque to the partitioner — plain strings stand in.

def test_partition_all_new():
    new, skipped = VectorLoader._partition_new_chunks(["a", "b"], ["h1", "h2"], set())
    assert skipped == 0
    assert [h for _, h in new] == ["h1", "h2"]


def test_partition_skips_existing_db_hashes():
    new, skipped = VectorLoader._partition_new_chunks(["a", "b"], ["h1", "h2"], {"h1"})
    assert skipped == 1
    assert [h for _, h in new] == ["h2"]


def test_partition_dedups_within_batch():
    # 3rd chunk duplicates the 1st within the same call — must be skipped so it
    # doesn't collide on the UNIQUE(content_hash) insert after embedding (I-M9).
    new, skipped = VectorLoader._partition_new_chunks(["a", "b", "a"], ["h1", "h2", "h1"], set())
    assert skipped == 1
    assert [h for _, h in new] == ["h1", "h2"]


def test_partition_all_duplicates_counts_all_skipped():
    # I-M5: a fully-duplicate batch reports the real skipped count, not 0.
    new, skipped = VectorLoader._partition_new_chunks(["a", "a", "a"], ["h1", "h1", "h1"], set())
    assert skipped == 2
    assert len(new) == 1


def test_partition_empty():
    new, skipped = VectorLoader._partition_new_chunks([], [], set())
    assert new == [] and skipped == 0


# ── HNSW query settings (RAG-H5) — mocked cursor, no DB ─────────────────────

def _loader_with_mock_cursor(fail_on_set: bool = False):
    """A VectorLoader with no DB/API: only the attributes similarity_search touches."""
    from unittest.mock import MagicMock

    import psycopg2

    vl = VectorLoader.__new__(VectorLoader)
    vl.settings = MagicMock(embedding_model="text-embedding-3-small")
    vl._hnsw_settings_supported = None
    vl._embed = lambda _q: [0.0, 0.0, 0.0]

    cur = MagicMock()
    cur.description = [("id",), ("content",)]
    cur.fetchall.return_value = []
    if fail_on_set:
        def _execute(sql, *args, **kwargs):
            if "set_config('hnsw.iterative_scan'" in sql:
                raise psycopg2.Error("unrecognized configuration parameter")
        cur.execute.side_effect = _execute
    vl.conn = MagicMock()
    vl.conn.cursor.return_value = cur
    return vl, cur


def _executed_sql(cur):
    return [c.args[0] for c in cur.execute.call_args_list]


def test_similarity_search_sets_iterative_scan_and_ef_search_before_select():
    """RAG-H5: both HNSW GUCs are set (transaction-local) before the SELECT runs."""
    from shared.constants import HNSW_EF_SEARCH, HNSW_ITERATIVE_SCAN

    vl, cur = _loader_with_mock_cursor()
    vl.similarity_search("q", top_k=5, chunk_types=["fault_correction"], min_similarity=0.45)

    sql = _executed_sql(cur)
    i_scan = next(i for i, s in enumerate(sql) if "set_config('hnsw.iterative_scan'" in s)
    i_ef = next(i for i, s in enumerate(sql) if "set_config('hnsw.ef_search'" in s)
    i_select = next(i for i, s in enumerate(sql) if "FROM knowledge_chunks" in s)
    assert i_scan < i_select and i_ef < i_select, sql
    assert cur.execute.call_args_list[i_scan].args[1] == (HNSW_ITERATIVE_SCAN,)
    assert cur.execute.call_args_list[i_ef].args[1] == (str(HNSW_EF_SEARCH),)
    assert vl._hnsw_settings_supported is True


def test_preferred_chunk_types_reranks_a_candidate_pool_instead_of_filtering():
    """RAG-H2: a preference takes a pure-similarity pool (index-friendly), adds the
    boost to preferred types and re-ranks — no chunk_type predicate in WHERE."""
    from shared.constants import (
        CHUNK_TYPE_PREFERENCE_BOOST,
        VECTOR_SEARCH_CANDIDATE_MULTIPLIER,
        VECTOR_SEARCH_MIN_CANDIDATES,
    )

    vl, cur = _loader_with_mock_cursor()
    vl.similarity_search("q", top_k=5, preferred_chunk_types=["periodization"], min_similarity=0.45)

    select_call = next(c for c in cur.execute.call_args_list if "FROM knowledge_chunks" in c.args[0])
    sql, params = select_call.args
    assert "WITH candidates AS" in sql and "AS score" in sql and "ORDER BY score DESC" in sql
    assert "chunk_type::text = ANY(%s)" in sql.split("SELECT *,")[1], "boost must be in the re-rank, not the WHERE"
    assert "chunk_type::text = ANY(%s)" not in sql.split("WITH candidates AS")[1].split(")")[0]
    pool = max(5 * VECTOR_SEARCH_CANDIDATE_MULTIPLIER, VECTOR_SEARCH_MIN_CANDIDATES)
    assert params[-4:] == [pool, ["periodization"], CHUNK_TYPE_PREFERENCE_BOOST, 5], params[-4:]


def test_hard_chunk_types_filter_still_available():
    vl, cur = _loader_with_mock_cursor()
    vl.similarity_search("q", top_k=5, chunk_types=["fault_correction"])
    sql = next(c.args[0] for c in cur.execute.call_args_list if "FROM knowledge_chunks" in c.args[0])
    assert "WITH candidates" not in sql and "chunk_type::text = ANY(%s)" in sql


def test_similarity_search_survives_missing_hnsw_gucs():
    """RAG-H5: on pgvector < 0.8 the SET fails; the savepoint is rolled back, the
    SELECT still runs, and later calls skip the probe instead of re-failing."""
    vl, cur = _loader_with_mock_cursor(fail_on_set=True)
    vl.similarity_search("q", top_k=5)

    sql = _executed_sql(cur)
    assert any("ROLLBACK TO SAVEPOINT hnsw_cfg" in s for s in sql), sql
    assert any("FROM knowledge_chunks" in s for s in sql), sql
    assert vl._hnsw_settings_supported is False

    cur.execute.reset_mock()
    vl.similarity_search("q2", top_k=5)
    sql2 = _executed_sql(cur)
    assert not any("set_config" in s for s in sql2), "second call must not re-probe the GUC"
    assert any("FROM knowledge_chunks" in s for s in sql2)


# ── Hybrid lexical + vector search (RAG-M1) ──────────────────────────────────

def test_lexical_tsquery_ors_deduped_alphanumeric_terms():
    q = "exercise selection for a snatch session with squat support at 70-80% intensity, snatch technique"
    ts = VectorLoader._lexical_tsquery(q)
    assert ts is not None
    terms = ts.split(" | ")
    assert "snatch" in terms and terms.count("snatch") == 1
    assert "squat" in terms and "technique" in terms
    # domain boilerplate present in every production query is dropped (no IDF in ts_rank_cd)
    assert not {"exercise", "selection", "session", "intensity", "support"} & set(terms), terms
    assert all(t.isalnum() and len(t) >= 3 for t in terms), terms
    assert VectorLoader._lexical_tsquery("70-80% @ 5x3") is None
    assert VectorLoader._lexical_tsquery("exercise selection for the session") is None


def test_hybrid_search_fuses_two_legs_with_rrf():
    """RAG-M1: with hybrid=True the SQL has a vector leg, a tsvector leg joined by
    FULL OUTER JOIN, reciprocal-rank fusion, the similarity floor on the vector
    leg only, and the chunk_type boost on the RRF scale."""
    from shared.constants import CHUNK_TYPE_PREFERENCE_BOOST_RRF, HYBRID_CANDIDATES_PER_LEG, RRF_K

    vl, cur = _loader_with_mock_cursor()
    vl.similarity_search("snatch pull prilepin", top_k=5, min_similarity=0.45,
                         preferred_chunk_types=["periodization"], hybrid=True)
    call = next(c for c in cur.execute.call_args_list if "FROM knowledge_chunks" in c.args[0])
    sql, params = call.args
    assert "WITH vec AS" in sql and "lex AS" in sql and "FULL OUTER JOIN" in sql
    assert "to_tsquery('english', %s)" in sql and "tsv @@ q" in sql
    vec_leg = sql.split("lex AS")[0]
    lex_leg = sql.split("lex AS")[1].split("fused AS")[0]
    assert ">= %s" in vec_leg and ">= %s" not in lex_leg, "min_similarity must apply to the vector leg only"
    assert "embedding_model = %s" in lex_leg, "metadata filters apply to both legs"
    assert params.count(HYBRID_CANDIDATES_PER_LEG) == 2 and params.count(RRF_K) == 2
    assert "snatch | pull | prilepin" in params
    assert params[-3:] == [["periodization"], CHUNK_TYPE_PREFERENCE_BOOST_RRF, 5]


def test_hybrid_falls_back_to_dense_when_query_has_no_terms():
    vl, cur = _loader_with_mock_cursor()
    vl.similarity_search("70% 5x3", top_k=5, hybrid=True)
    sql = next(c.args[0] for c in cur.execute.call_args_list if "FROM knowledge_chunks" in c.args[0])
    assert "lex AS" not in sql


def test_hybrid_off_by_default_keeps_dense_sql():
    vl, cur = _loader_with_mock_cursor()
    vl.similarity_search("snatch", top_k=5, preferred_chunk_types=["periodization"])
    sql = next(c.args[0] for c in cur.execute.call_args_list if "FROM knowledge_chunks" in c.args[0])
    assert "tsv" not in sql and "WITH candidates AS" in sql

# ── Query-embedding LRU (RAG-L2) ─────────────────────────────────────────────

def _embedding_loader(model="text-embedding-3-small"):
    from unittest.mock import MagicMock

    vl = VectorLoader.__new__(VectorLoader)
    vl.settings = MagicMock(embedding_model=model, embedding_dim=1536)
    vl.embed_client = MagicMock()
    vl.embed_client.embeddings.create.return_value = MagicMock(data=[MagicMock(embedding=[0.1, 0.2])])
    return vl


def test_query_embedding_is_cached_per_text_and_model():
    vl = _embedding_loader()
    assert vl._embed("correcting early arm bend") == [0.1, 0.2]
    assert vl._embed("correcting early arm bend") == [0.1, 0.2]
    assert vl.embed_client.embeddings.create.call_count == 1, "second identical query must hit the cache"
    vl._embed("a different query")
    assert vl.embed_client.embeddings.create.call_count == 2
    vl.settings.embedding_model = "text-embedding-3-large"      # model change → different key space
    vl._embed("correcting early arm bend")
    assert vl.embed_client.embeddings.create.call_count == 3


def test_query_embedding_cache_is_bounded(monkeypatch):
    vl = _embedding_loader()
    monkeypatch.setattr(VectorLoader, "_QUERY_CACHE_MAX", 2)
    for q in ("q1", "q2", "q3"):
        vl._embed(q)
    assert len(vl._query_cache) == 2
    vl._embed("q1")   # evicted (oldest) → refetched
    assert vl.embed_client.embeddings.create.call_count == 4

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
