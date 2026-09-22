# tests/test_contextualizer.py
"""
No-key tests for contextual retrieval (RAG-M3): prompt blocks, context insertion
into `content` (not `raw_content`), per-chunk failure isolation, pipeline wiring,
and the context_prefix column on insert.

Run: PYTHONUTF8=1 uv run pytest tests/test_contextualizer.py -q
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from loaders.vector_loader import VectorLoader
from processors.chunker import Chunk, SemanticChunker
from processors.contextualizer import (
    MAX_DOCUMENT_CHARS,
    apply_context,
    build_chunk_message,
    build_document_block,
    contextualize,
)


def _chunk():
    chunks = SemanticChunker().chunk(
        "The preparatory period builds volume before intensity rises.",
        source_title="Managing the Training", author="Laputin",
    )
    return chunks[0]


def _message(text):
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    return m


def test_prompt_blocks_carry_document_and_passage():
    doc = build_document_block("x" * (MAX_DOCUMENT_CHARS + 50), "Some Book")
    assert "Some Book" in doc and len(doc) < MAX_DOCUMENT_CHARS + 500
    msg = build_chunk_message("the passage text")
    assert "<passage>" in msg and "the passage text" in msg and "at most 60 words" in msg


def test_apply_context_sits_between_preamble_and_body_and_leaves_raw_alone():
    c = _chunk()
    raw_before, content_before = c.raw_content, c.content
    apply_context(c, "  From the chapter on periodization;\n prescribes weekly volume.  ")
    assert c.raw_content == raw_before
    assert c.metadata["context_prefix"] == "From the chapter on periodization; prescribes weekly volume."
    assert c.content.endswith(raw_before)
    assert c.content.startswith(content_before[: content_before.index(raw_before)])  # preamble intact
    assert "prescribes weekly volume.\n\n" + raw_before in c.content


def test_apply_context_ignores_blank():
    c = _chunk()
    before = c.content
    apply_context(c, "   ")
    assert c.content == before and "context_prefix" not in c.metadata


def test_contextualize_uses_cached_system_block_and_isolates_failures():
    chunks = [_chunk(), _chunk(), _chunk()]
    client = MagicMock()
    with patch("processors.contextualizer.create_message_with_retries") as call:
        call.side_effect = [_message("Context one."), RuntimeError("boom"), _message("Context three.")]
        out = contextualize(chunks, "the whole section text", "Book", client, "some-model")

    assert out is chunks
    assert chunks[0].metadata["context_prefix"] == "Context one."
    assert "context_prefix" not in chunks[1].metadata          # failure left it untouched
    assert chunks[2].metadata["context_prefix"] == "Context three."
    kwargs = call.call_args_list[0].kwargs
    assert kwargs["model"] == "some-model"
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "the whole section text" in kwargs["system"][0]["text"]
    assert chunks[0].raw_content in kwargs["messages"][0]["content"]


def test_section_processor_calls_contextualizer_only_when_enabled():
    from processors.section_processor import SectionProcessor, SectionTarget, new_section_stats

    section = MagicMock(content="Body text about accumulation volume.", metadata={"chapter": "", "title": ""})
    section.content_type = MagicMock(value="prose")
    target = SectionTarget(source_id=1, title="T", author="A", chunker=SemanticChunker())

    def run(enabled):
        settings = MagicMock(validate_chunks=False)
        vl = MagicMock(last_skipped_count=0)
        vl.load_chunks.return_value = 1
        proc = SectionProcessor(settings, vl, MagicMock(), MagicMock(), contextualize=enabled, context_model="m")
        with patch("processors.contextualizer.contextualize") as ctx:
            ctx.side_effect = lambda chunks, *a, **k: chunks
            proc.process_prose(section, target, new_section_stats())
            return ctx.call_count

    assert run(False) == 0
    assert run(True) == 1


def test_insert_writes_context_prefix_column():
    vl = VectorLoader.__new__(VectorLoader)
    vl.settings = MagicMock(embedding_model="text-embedding-3-small", embedding_dim=1536)
    vl.batch_size = 50
    vl.last_skipped_count = 0
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (1,)
    vl.conn = MagicMock()
    vl.conn.cursor.return_value = cur
    vl._embed_batch = lambda texts: [[0.0] * 2 for _ in texts]

    c = Chunk(content="[Source: x]\n\nctx.\n\nbody", raw_content="body", metadata={"context_prefix": "ctx."})
    vl.load_chunks([c], source_id=1)
    insert = next(k for k in cur.execute.call_args_list if "INSERT INTO knowledge_chunks" in k.args[0])
    assert "context_prefix" in insert.args[0]
    assert insert.args[1][-1] == "ctx."
