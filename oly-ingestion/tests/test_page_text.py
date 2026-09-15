# tests/test_page_text.py
"""
No-key tests for extractors/page_text.py (RAG-H1): de-hyphenation, running-head /
folio stripping, page joining, and the end-to-end guarantee that a chunk can span
a PDF page break.

Run: PYTHONUTF8=1 uv run pytest tests/test_page_text.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from extractors.page_text import dehyphenate, join_pages, strip_running_heads
from processors.chunker import SemanticChunker

# ── dehyphenate ───────────────────────────────────────────────────────────────

def test_dehyphenate_joins_lowercase_continuation():
    assert dehyphenate("hy-\npertrophy and ac-\ncumulation") == "hypertrophy and accumulation"


def test_dehyphenate_keeps_capitalised_continuation():
    assert dehyphenate("the Snatch-\nBalance") == "the Snatch-\nBalance"


# ── strip_running_heads ───────────────────────────────────────────────────────

def test_strip_running_heads_removes_recurring_head_and_folios():
    pages = [f"Science and Practice\n{i + 1}\nBody paragraph {i} text.\n{100 + i}" for i in range(10)]
    cleaned = strip_running_heads(pages)
    assert cleaned == [f"Body paragraph {i} text." for i in range(10)]


def test_strip_running_heads_handles_alternating_heads():
    """Zatsiorsky alternates the book title and the chapter title page by page."""
    pages = [
        ("Science and Practice of Strength Training" if i % 2 == 0 else "Timing in Strength Training")
        + f"\nBody {i}."
        for i in range(10)
    ]
    assert strip_running_heads(pages) == [f"Body {i}." for i in range(10)]


def test_strip_running_heads_head_with_folio_on_same_line():
    """A head that recurs on its own is also stripped where it is joined to a folio
    (``83 Timing …``). The joined form alone never counts as recurring, so page-top
    labels like ``Week 3`` / ``Week 4`` are not collapsed into one key and removed."""
    pages = [
        ("Timing in Strength Training" if i % 2 == 0 else f"{80 + i} Timing in Strength Training") + f"\nBody {i}."
        for i in range(10)
    ]
    assert strip_running_heads(pages) == [f"Body {i}." for i in range(10)]


def test_strip_running_heads_keeps_numbered_labels_at_page_top():
    pages = [f"Week {i + 1}\nBody {i}." for i in range(10)]
    assert strip_running_heads(pages) == pages


def test_strip_running_heads_keeps_unique_first_lines():
    pages = [f"Unique heading {i}\nBody {i}." for i in range(10)]
    assert strip_running_heads(pages) == pages


def test_strip_running_heads_short_doc_strips_only_folios():
    assert strip_running_heads(["Intro\n1", "Same\n2"]) == ["Intro", "Same"]


def test_strip_running_heads_ignores_long_recurring_lines():
    long_line = "This sentence is far too long to be a running head and it happens to open every page " * 2
    pages = [f"{long_line}\nBody {i}." for i in range(5)]
    assert strip_running_heads(pages) == pages


# ── join_pages ────────────────────────────────────────────────────────────────

def test_join_pages_rejoins_sentence_across_break():
    out = join_pages(["The lifter must keep the bar", "close to the body.\n\nNext paragraph."])
    assert out == "The lifter must keep the bar close to the body.\n\nNext paragraph."


def test_join_pages_rejoins_hyphenated_word_across_break():
    out = join_pages(["volume rises during accumu-", "lation and falls later."])
    assert out == "volume rises during accumulation and falls later."


def test_join_pages_paragraph_break_when_sentence_ended():
    assert join_pages(["First page ends here.", "Second page starts."]) == "First page ends here.\n\nSecond page starts."


def test_join_pages_comma_continuation_before_capitalised_word():
    out = join_pages(["as shown by Medvedev,", "Roman and Vorobyev."])
    assert out == "as shown by Medvedev, Roman and Vorobyev."


def test_join_pages_skips_blank_pages():
    assert join_pages(["A.", "   ", "B."]) == "A.\n\nB."


# ── end to end: a chunk may span a page break ─────────────────────────────────

def test_chunk_spans_page_break_after_strip_and_join():
    """The RAG-H1 guarantee: with pages joined, a sentence split by a page break
    lands whole inside one chunk, and no chunk is exactly one page."""
    filler = "Training volume is the primary driver of adaptation in this block. "
    pages = [
        "Intervention\n" + filler * 3 + "The lifter must keep the bar\n41",
        "Intervention\nclose to the body throughout the pull. " + filler * 3 + "\n42",
        "Intervention\n" + filler * 3 + "\n43",
    ]
    doc = join_pages(strip_running_heads(pages))
    chunks = SemanticChunker().chunk(doc, source_title="T", author="A")

    assert chunks, "expected at least one chunk"
    assert any("keep the bar close to the body throughout the pull" in c.raw_content for c in chunks)
    assert all("Intervention" not in c.raw_content for c in chunks)
    stripped_pages = {p.strip() for p in strip_running_heads(pages)}
    assert all(c.raw_content.strip() not in stripped_pages for c in chunks), "a chunk is still exactly one page"
