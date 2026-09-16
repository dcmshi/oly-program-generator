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
    vl, _ = _loader(model="text-embedding-3-large", dim=1536)
    vl.embed_client = MagicMock()
    vl.embed_client.embeddings.create.return_value = MagicMock(data=[MagicMock(embedding=[0.1])])
    vl._embed("q")
    assert vl.embed_client.embeddings.create.call_args.kwargs["dimensions"] == 1536

    vl_old, _ = _loader(model="text-embedding-ada-002", dim=1536)
    vl_old.embed_client = MagicMock()
    vl_old.embed_client.embeddings.create.return_value = MagicMock(data=[MagicMock(embedding=[0.1])])
    vl_old._embed("q")
    assert "dimensions" not in vl_old.embed_client.embeddings.create.call_args.kwargs


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
