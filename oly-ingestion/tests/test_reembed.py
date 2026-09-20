# tests/test_reembed.py
"""
No-key tests for embedding-model versioning (RAG-M8): the insert records the
model, similarity_search filters on it, `dimensions` is sent only for models
that support it, and reembed.py selects/batches rows correctly.

Run: PYTHONUTF8=1 uv run pytest tests/test_reembed.py -q
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from loaders.vector_loader import VectorLoader
from processors.chunker import Chunk
from reembed import DEFAULT_BATCH_SIZE, batches, select_sql


def _loader(model="text-embedding-3-small", dim=1536):
    vl = VectorLoader.__new__(VectorLoader)
    vl.settings = MagicMock(embedding_model=model, embedding_dim=dim, batch_size=50)
    vl.batch_size = 50
    vl._hnsw_settings_supported = False
    vl.last_skipped_count = 0
    vl.conn = MagicMock()
    cur = MagicMock()
    cur.description = [("id",)]
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (1,)
    vl.conn.cursor.return_value = cur
    return vl, cur


def test_insert_records_embedding_model():
    vl, cur = _loader(model="text-embedding-3-large")
    vl._embed_batch = lambda texts: [[0.0] * 3 for _ in texts]
    chunk = Chunk(content="[Source: x]\n\nbody text", raw_content="body text")
    vl.load_chunks([chunk], source_id=1)
    insert = next(c for c in cur.execute.call_args_list if "INSERT INTO knowledge_chunks" in c.args[0])
    assert "embedding_model" in insert.args[0]
    assert insert.args[1][-2] == "text-embedding-3-large"  # context_prefix (RAG-M3) is the last param


def test_similarity_search_filters_on_embedding_model():
    vl, cur = _loader(model="text-embedding-3-small")
    vl._embed = lambda _q: [0.0, 0.0]
    vl.similarity_search("q", top_k=3)
    select = next(c for c in cur.execute.call_args_list if "FROM knowledge_chunks" in c.args[0])
    assert "embedding_model = %s" in select.args[0]
    assert select.args[1][1] == "text-embedding-3-small"  # first WHERE param, right after the similarity vector


def test_dimensions_sent_only_for_text_embedding_3_models():
    """The OpenAI provider passes Matryoshka `dimensions` for text-embedding-3-*
    only; every provider then fits the vector to the column width."""
    from loaders.embedders import OpenAIEmbedder

    def _fake(embedder):
        embedder.client = MagicMock()
        embedder.client.embeddings.create.return_value = MagicMock(data=[MagicMock(embedding=[3.0, 4.0])])
        return embedder.client.embeddings.create

    e = OpenAIEmbedder("text-embedding-3-large", 4, api_key="k")
    create = _fake(e)
    vec = e.embed_query("q")
    assert create.call_args.kwargs["dimensions"] == 4
    assert [round(x, 4) for x in vec] == [0.6, 0.8, 0.0, 0.0]   # normalised, zero-padded to dim

    e_old = OpenAIEmbedder("text-embedding-ada-002", 4, api_key="k")
    create = _fake(e_old)
    e_old.embed_query("q")
    assert "dimensions" not in create.call_args.kwargs


def test_embedder_factory_and_compat_provider():
    """make_embedder picks the provider from settings; the OpenAI-compatible
    provider points the OpenAI client at EMBEDDING_BASE_URL and never sends
    `dimensions`; fit_dimension pads, truncates and normalises."""
    from types import SimpleNamespace

    from loaders.embedders import OpenAICompatEmbedder, OpenAIEmbedder, fit_dimension, make_embedder

    assert [round(x, 4) for x in fit_dimension([[3.0, 4.0]], 3)[0]] == [0.6, 0.8, 0.0]      # float32 arithmetic
    assert [round(x, 4) for x in fit_dimension([[1.0, 1.0, 1.0, 1.0]], 2)[0]] == [0.7071, 0.7071]   # truncate + renormalise
    assert fit_dimension([[0.0, 0.0]], 2) == [[0.0, 0.0]]

    s = SimpleNamespace(embedding_provider="openai", embedding_model="text-embedding-3-small", embedding_dim=1536, openai_api_key="k")
    assert isinstance(make_embedder(s), OpenAIEmbedder)
    s = SimpleNamespace(embedding_provider="openai_compat", embedding_model="BAAI/bge-m3", embedding_dim=1536,
                        embedding_base_url="http://localhost:11434/v1", embedding_api_key="")
    e = make_embedder(s)
    assert isinstance(e, OpenAICompatEmbedder) and e.provider == "openai_compat" and not e.send_dimensions
    assert str(e.client.base_url).startswith("http://localhost:11434/v1")
    try:
        make_embedder(SimpleNamespace(embedding_provider="bogus", embedding_model="m", embedding_dim=8))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_select_sql_skips_rows_already_on_target_model():
    sql, params = select_sql("text-embedding-3-large")
    assert "embedding_model <> %s" in sql and params == ["text-embedding-3-large"]
    sql_all, params_all = select_sql("text-embedding-3-large", include_current=True)
    assert "WHERE" not in sql_all and params_all == []


def test_select_sql_source_and_limit():
    sql, params = select_sql("m", source_id=51, limit=200)
    assert "source_id = %s" in sql and sql.rstrip().endswith("LIMIT %s")
    assert params == ["m", 51, 200]


def test_batches_split_evenly():
    rows = list(range(250))
    chunks = list(batches(rows, DEFAULT_BATCH_SIZE))
    assert [len(c) for c in chunks] == [100, 100, 50]
    assert sum(chunks, []) == rows
