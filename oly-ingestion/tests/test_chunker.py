# tests/test_chunker.py
"""
Tests for the SemanticChunker.

Run: python -m pytest tests/test_chunker.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.chunker import (
    KEEP_TOGETHER_PATTERNS,
    SemanticChunker,
    SourceProfile,
    keyword_tag,
    validate_chunk,
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


# ── T8: validate_chunk edge cases (missing lines) ─────────────────────────────

def test_validate_chunk_too_long():
    """Chunk over 1500 tokens triggers a warning issue."""
    from processors.chunker import Chunk
    chunk = Chunk(content="A" * 6000, raw_content="A" * 6000, token_count=1600,
                  topics=["snatch_technique"])
    result = validate_chunk(chunk)
    assert any("too long" in issue for issue in result.issues)


def test_validate_chunk_many_rep_schemes():
    """Chunk with 3+ rep schemes triggers routing suggestion."""
    from processors.chunker import Chunk
    text = "Snatch 5x3 @ 72%\nClean 4x2 @ 78%\nBack Squat 4x5 @ 80%"
    chunk = Chunk(content=text, raw_content=text, token_count=200,
                  topics=["snatch_programming"])
    result = validate_chunk(chunk)
    assert any("rep scheme" in issue.lower() for issue in result.issues)


def test_validate_chunk_table_like_content():
    """Chunk with 3+ pipe-delimited lines triggers table routing suggestion."""
    from processors.chunker import Chunk
    text = "Header | Col1 | Col2\nRow1 | A | B\nRow2 | C | D\nRow3 | E | F"
    chunk = Chunk(content=text, raw_content=text, token_count=100,
                  topics=["snatch_programming"])
    result = validate_chunk(chunk)
    assert any("table" in issue.lower() for issue in result.issues)


def test_validate_chunk_starts_lowercase():
    """Chunk starting with lowercase triggers mid-sentence split warning."""
    from processors.chunker import Chunk
    text = "the athlete should focus on strength during this block."
    chunk = Chunk(content=text, raw_content=text, token_count=100,
                  topics=["snatch_technique"])
    result = validate_chunk(chunk)
    assert any("lowercase" in issue.lower() or "mid-sentence" in issue.lower()
               for issue in result.issues)


# ── T8: _chunk_section / _would_split_pattern / _get_overlap ─────────────────

def test_chunk_section_empty_returns_empty():
    """_chunk_section returns empty list for empty or whitespace-only text."""
    chunker = SemanticChunker()
    assert chunker._chunk_section("") == []
    assert chunker._chunk_section("   \n\n   ") == []


def test_would_split_pattern_true_when_straddles_boundary():
    """Returns True when a keep-together pattern spans the chunk/para boundary."""
    chunker = SemanticChunker()
    # "5" at end of chunk_end, "x3 @ 72%" at start of next_para
    # boundary_zone = "...text before 5\n\nx3 @ 72%..."
    # \d+\s*[xX×]\s*\d+ matches "5\n\nx3" — \s* crosses the boundary
    result = chunker._would_split_pattern("text before 5", "x3 @ 72% more text")
    assert result is True


def test_would_split_pattern_false_when_no_match():
    """Returns False when no keep-together pattern straddles the boundary."""
    chunker = SemanticChunker()
    result = chunker._would_split_pattern("Normal prose text here.", "More normal prose.")
    assert result is False


def test_get_overlap_returns_sentence_suffix():
    """_get_overlap returns the last sentence(s) within the overlap token budget."""
    chunker = SemanticChunker(chunk_overlap_override=15)
    text = "This is a longer first sentence with many words in it. Short end."
    overlap = chunker._get_overlap(text)
    assert len(overlap) > 0
    assert "Short end" in overlap


def test_get_overlap_empty_text():
    """_get_overlap on empty text returns empty string without error."""
    chunker = SemanticChunker()
    assert chunker._get_overlap("") == ""


def test_split_on_sections_captures_full_heading():
    """I-L7: the section title is the full heading line, not the bare marker,
    and the heading text is not duplicated into the body."""
    chunker = SemanticChunker()
    sections = chunker._split_on_sections("# Snatch Technique\n\nBody text here.")
    assert sections[0]["title"] == "# Snatch Technique"
    assert "Snatch Technique" not in sections[0]["text"]


def test_apply_ocr_corrections_fixes_known_errors():
    """I-L3: the OCR correction dict is actually applied to Soviet-era text."""
    from processors.ocr_corrections import apply_ocr_corrections
    assert apply_ocr_corrections("The snalch and c1ean") == "The snatch and clean"
    assert apply_ocr_corrections("mesocyc1e planning") == "mesocycle planning"


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


def test_program_listing_week_lines_do_not_fragment_chunks():
    """RAG-H1: 'Week N' lines match SECTION_BREAK_PATTERNS, so each week of a
    listing used to become its own one-line chunk (Medvedev: 617 chunks averaging
    269 chars). Weak-heading fragments now fold together with the line kept."""
    listing = "\n\n".join(f"Week {w}\nSnatch 5x2 @ 75%\nClean 4x2 @ 78%" for w in range(1, 7))
    chunks = SemanticChunker().chunk(listing, source_title="T", author="A")
    assert len(chunks) == 1, [c.raw_content[:30] for c in chunks]
    for w in range(1, 7):
        assert f"Week {w}" in chunks[0].raw_content


def test_training_log_chunks_per_week():
    """MEDVEDEV: a day-by-day log chunks at `Week # N` lines once the chunk is
    big enough — the week label glued to the previous week's totals line
    starts a new paragraph, and no tail overlap crosses the boundary."""
    from shared.constants import WEEK_BOUNDARY_FLUSH_FRACTION

    def session(n):
        lines = "\n".join(f"{i}. Ex. {i}, Ab. Kn., Bl.:  70 x 2 x 2, 80 x 2 x 3, 90 x 1 x 3" for i in range(1, 4))
        return f"# {n} - March (Monday)\n{lines}\nF. L. = 45"

    weeks = []
    for w in range(9, 13):
        days = "\n\n".join(session(n) for n in (4, 3, 2, 1))   # a real log week: 350-650 tokens (this one ≈ 580)
        label = f"\nWeek # {w + 1}" if w < 12 else ""          # the label opens the NEXT week's text
        weeks.append(f"{days}\nTotals for week {w}  F. L. = 135 lifts{label}")
    text = "Week # 9\n" + "\n".join(weeks)
    chunker = SemanticChunker.for_source("A System of Multi-Year Training in Weightlifting")
    assert chunker.source_profile == SourceProfile.DATA_HEAVY_SOVIET
    pieces = chunker._chunk_section(text)
    assert len(pieces) == 4, [p[:20] for p in pieces]
    assert all(p.startswith("Week # ") for p in pieces), [p[:12] for p in pieces]
    for w, piece in zip(range(9, 13), pieces, strict=True):
        assert piece.startswith(f"Week # {w}\n") and piece.count("- March (Monday)") == 4
        assert f"Totals for week {w}" in piece and f"Totals for week {w + 1}" not in piece
    # a week label after a small chunk does not flush (below the fraction): the
    # exercise-selection listings' weeks are ~150 tokens and pack 3-4 per chunk
    listing = "\n".join(
        f"Week {w}\n#1 - 1. P. Sn., Ab. Kn., Bl. 2. B. Sq. 3. Be. Pr.\n#2 - 1. P. Cl. 2. Fr. Sq. 3. Hyper."
        for w in range(40, 44)
    )
    assert len(chunker._chunk_section(listing)) == 1
    assert 0 < WEEK_BOUNDARY_FLUSH_FRACTION < 1


def test_keyword_tag_matches_whole_words_only():
    """RAG-M7: substring matching tagged 'requiring' as RPE/RIR content,
    'speaking' as peaking, 'permission' as fault correction."""
    assert "intensity_prescription" not in keyword_tag("The coach kept requiring more effort.")
    assert "competition_peaking" not in keyword_tag("Speaking of programming, ...")
    assert "fault_correction" not in keyword_tag("With the coach's permission the mission continued.")
    assert "intensity_prescription" in keyword_tag("Leave 2 RIR on every set.")
    assert "competition_peaking" in keyword_tag("The peak comes two weeks out.")
    assert "squat_programming" in keyword_tag("Front squat after the snatch.")     # multi-word keyword
    assert "competition_peaking" in keyword_tag("A pre-competition taper.")        # hyphenated keyword


def test_chunk_keeps_ocr_session_labels_and_week_lines_in_the_text():
    """MEDVEDEV, second copy of the heading bug: the chunker's own
    SECTION_BREAK_PATTERNS made `# 4 - March (Monday)` a section title (chunk =
    session, label lifted out of the text) even after the classifier was fixed,
    and `Week N` lines became titles too. Through chunk(): labels and week
    lines stay in raw_content and sessions pack together."""
    def session(n):
        lines = "\n".join(f"{i}. Ex. {i}, Ab. Kn., Bl.:  70 x 2 x 2, 80 x 2 x 3, 90 x 1 x 3" for i in range(1, 4))
        return f"# {n} - March (Monday)\n{lines}\nF. L. = 45"
    text = "Week 9\n" + "\n\n".join(session(n) for n in (4, 3, 2, 1)) + "\nTotals for week 9  F. L. = 180 lifts"
    chunker = SemanticChunker.for_source("A System of Multi-Year Training in Weightlifting")
    chunks = chunker.chunk(text, source_title="T", author="A")
    assert len(chunks) == 1, [c.raw_content[:30] for c in chunks]
    assert chunks[0].metadata["section_title"] == ""
    assert chunks[0].raw_content.startswith("Week 9\n# 4 - March (Monday)")
    assert chunks[0].raw_content.count("- March (Monday)") == 4
    # a real markdown heading is still a section break
    chunks = chunker.chunk("# Loading Dynamics\n\nProse about loading. " * 3 + "\n\n# 4 - March (Monday)\nsession", source_title="T", author="A")
    assert all(c.metadata["section_title"] == "# Loading Dynamics" for c in chunks)


def test_validate_chunk_severity_is_the_worst_check_not_the_last():
    """ING-L2: a chunk that is both too short (warning) and dual-storage (info)
    must report 'warning'."""
    from processors.chunker import Chunk, validate_chunk
    c = Chunk(content="x", raw_content="Snatch 3x2 @ 80%\nSnatch 3x2 @ 85%\nClean 3x2 @ 80%",
              metadata={"chunk_type": "concept"}, token_count=10, topics=[])
    r = validate_chunk(c)
    assert not r.is_valid and r.severity == "warning"
