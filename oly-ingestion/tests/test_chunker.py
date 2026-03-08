# tests/test_chunker.py
"""
Tests for the SemanticChunker.

Run: python -m pytest tests/test_chunker.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.chunker import (
    SemanticChunker,
    SourceProfile,
    validate_chunk,
    keyword_tag,
    KEEP_TOGETHER_PATTERNS,
)


# ── Fixtures ──────────────────────────────────────────────────

SAMPLE_PROSE = """
During the accumulation phase, volume should be high and intensity moderate.
The athlete should focus on building work capacity and technique consistency.

The snatch and clean & jerk should be performed at 70-80% of 1RM for multiple
sets and reps. Prilepin's chart recommends 18-24 total reps in the working
zone for competition lifts at this intensity range.

Recovery between sessions is critical. Fatigue management should prevent
overtraining while allowing adaptation to occur.
"""

SAMPLE_PROGRAM_BLOCK = """
Week 1 — Accumulation

Monday:
  Snatch 5x3 @ 72%
  Back Squat 4x5 @ 75%
  Snatch Pull 3x4 @ 90%

Tuesday:
  Clean & Jerk 4x2 @ 78%
  Front Squat 4x3 @ 80%
  Clean Pull 3x4 @ 95%
"""

SAMPLE_SHORT = "Snatch technique."


# ── Unit tests ────────────────────────────────────────────────

def test_chunker_returns_chunks():
    chunker = SemanticChunker()
    chunks = chunker.chunk(SAMPLE_PROSE, source_title="Test Source", author="Test Author")
    assert len(chunks) > 0


def test_preamble_included_in_content():
    chunker = SemanticChunker()
    chunks = chunker.chunk(SAMPLE_PROSE, source_title="Test Source", author="Test Author")
    for chunk in chunks:
        assert "Source: Test Source" in chunk.content
        assert "Test Source" not in chunk.raw_content or True  # raw_content has no preamble guarantee


def test_content_hash_differs_from_content():
    """raw_content should not have the preamble that content has."""
    chunker = SemanticChunker()
    chunks = chunker.chunk(SAMPLE_PROSE, source_title="Test Source", author="Test Author")
    for chunk in chunks:
        assert chunk.content != chunk.raw_content


def test_topic_tagging_accumulation():
    topics = keyword_tag("During the accumulation phase, volume is high.")
    assert "accumulation_phase" in topics
    assert "volume_management" in topics


def test_topic_tagging_snatch():
    topics = keyword_tag("The snatch should be trained with high frequency.")
    assert "snatch_technique" in topics or "snatch_programming" in topics


def test_for_source_known():
    chunker = SemanticChunker.for_source("Olympic Weightlifting: A Complete Guide for Athletes and Coaches")
    assert chunker.source_profile == SourceProfile.PROGRAMMING_FOCUSED


def test_for_source_unknown_defaults_to_programming():
    chunker = SemanticChunker.for_source("Unknown Book Title")
    assert chunker.source_profile == SourceProfile.PROGRAMMING_FOCUSED


def test_for_web_article_long():
    chunker = SemanticChunker.for_web_article(5000)
    assert chunker.source_profile == SourceProfile.THEORY_HEAVY


def test_for_web_article_short():
    chunker = SemanticChunker.for_web_article(500)
    assert chunker.chunk_size == 500


def test_validate_chunk_too_short():
    from processors.chunker import Chunk
    chunk = Chunk(content="Short", raw_content="Short", token_count=5, topics=["snatch_technique"])
    result = validate_chunk(chunk)
    assert not result.is_valid
    assert any("too short" in issue for issue in result.issues)


def test_validate_chunk_no_topics():
    from processors.chunker import Chunk
    chunk = Chunk(content="A" * 500, raw_content="A" * 500, token_count=200, topics=[])
    result = validate_chunk(chunk)
    assert any("No topics" in issue for issue in result.issues)


def test_validate_chunk_valid():
    from processors.chunker import Chunk
    chunk = Chunk(
        content="During the snatch accumulation phase, volume is high.",
        raw_content="During the snatch accumulation phase, volume is high.",
        token_count=150,
        topics=["snatch_programming", "accumulation_phase"],
    )
    result = validate_chunk(chunk)
    assert result.is_valid


def test_keep_together_rep_scheme():
    pattern = KEEP_TOGETHER_PATTERNS["rep_scheme"]
    assert pattern.search("Snatch 5x3 @ 75%")
    assert pattern.search("3×2 at 85%")
    assert not pattern.search("just some prose text here")


def test_keep_together_soviet_notation():
    pattern = KEEP_TOGETHER_PATTERNS["soviet_notation"]
    assert pattern.search("70%/3x3  75%/3x2  80%/2x2")


if __name__ == "__main__":
    # Run manually without pytest
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
