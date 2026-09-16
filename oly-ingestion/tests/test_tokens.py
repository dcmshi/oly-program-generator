# tests/test_tokens.py
"""
Token accounting (RAG-M2): real tokenizer counts, the offline fallback, the
oversized-paragraph split in the chunker, and the token-based embedding cap.

Run: PYTHONUTF8=1 uv run pytest tests/test_tokens.py -q
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import processors.tokens as tokens
from loaders.vector_loader import VectorLoader
from processors.chunker import Chunk, SemanticChunker
from processors.tokens import count_tokens, truncate_to_tokens

from shared.constants import EMBED_MAX_TOKENS, TOKENS_PER_WORD_FALLBACK

NOTATION = "(85%/4)4 20:108:280 70%/3x3 75%/3x2 80%/2x2"


def _tiktoken_available() -> bool:
    return tokens._get_encoder() is not None


def test_notation_counts_far_more_tokens_than_words():
    """The words×1.3 estimate under-counted Soviet notation ~5× — a '900-token'
    Medvedev chunk could be 1,500+ real tokens."""
    if not _tiktoken_available():
        import pytest
        pytest.skip("tiktoken encoder unavailable offline")
    real = count_tokens(NOTATION)
    estimate = int(len(NOTATION.split()) * TOKENS_PER_WORD_FALLBACK)
    assert real >= 3 * estimate, (real, estimate)


def test_fallback_when_encoder_unavailable(monkeypatch):
    monkeypatch.setattr(tokens, "_encoder", None)
    monkeypatch.setattr(tokens, "_encoder_tried", True)
    assert count_tokens("one two three four") == int(4 * TOKENS_PER_WORD_FALLBACK)
    assert truncate_to_tokens("a b c d e f g h", 4).split() == ["a", "b", "c"]  # int(4/1.3) = 3 words


def test_truncate_to_tokens_respects_limit():
    text = "snatch pull " * 5000
    out = truncate_to_tokens(text, 100)
    assert count_tokens(out) <= 100
    assert text.startswith(out.rstrip())
    assert truncate_to_tokens("short", 100) == "short"


def test_chunker_splits_an_oversized_paragraph_on_sentences():
    """One paragraph with no blank lines used to become one oversized chunk; it
    is now split into sentence groups that respect the profile size."""
    chunker = SemanticChunker(chunk_size_override=120, chunk_overlap_override=20)
    sentence = "The lifter drives through the floor before the hips open fully. "
    para = (sentence * 60).strip()  # ~800 tokens, zero paragraph breaks
    chunks = chunker.chunk(para, source_title="T", author="A")
    assert len(chunks) >= 5, len(chunks)
    for c in chunks:
        body_tokens = chunker._estimate_tokens(c.raw_content)
        assert body_tokens <= 120 + 20 + 10, body_tokens  # piece + tail overlap + slack
        assert c.raw_content.startswith("The lifter"), c.raw_content[:40]  # sentence boundary


def test_chunker_splits_a_single_giant_sentence_by_words():
    chunker = SemanticChunker(chunk_size_override=50, chunk_overlap_override=10)
    giant = " ".join(f"row{i} 70%/3x3" for i in range(300))  # no sentence punctuation
    pieces = chunker._split_long_paragraph(giant)
    assert len(pieces) > 5
    assert all(chunker._estimate_tokens(p) <= 50 for p in pieces)
    assert " ".join(pieces) == giant  # nothing lost


def test_embedding_input_capped_by_tokens_not_chars():
    """A 6,000-row notation chunk is ~28k chars (under the old 30k-char cap) but
    far over 8,191 tokens; the embedding input must be cut by tokens."""
    vl = VectorLoader.__new__(VectorLoader)
    vl.settings = MagicMock(embedding_model="text-embedding-3-small", embedding_dim=1536)
    vl.batch_size = 50
    vl.last_skipped_count = 0
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (1,)
    vl.conn = MagicMock()
    vl.conn.cursor.return_value = cur
    sent_texts: list[str] = []

    def fake_embed_batch(texts):
        sent_texts.extend(texts)
        return [[0.0] * 2 for _ in texts]

    vl._embed_batch = fake_embed_batch

    huge = Chunk(content="70%/3x3 " * 3500, raw_content="x")  # ~28k chars
    assert len(huge.content) < 30000
    vl.load_chunks([huge], source_id=1)
    sent = sent_texts[0]
    assert count_tokens(sent) <= EMBED_MAX_TOKENS
    if tokens._get_encoder() is not None:
        assert len(sent) < len(huge.content), "over-limit notation chunk must have been truncated"
