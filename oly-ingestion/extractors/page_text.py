# extractors/page_text.py
"""
Page-level text cleanup for paginated sources (PDF text layer, vision OCR).

A PDF page is a rendering unit, not a logical one: sentences and paragraphs run
across page breaks, every page repeats a running head and a folio, and justified
text hyphenates words at line ends. Classifying and chunking page by page (the
pre-RAG-H1 behaviour) made chunk = page for every PyMuPDF source — 100% of their
chunks were single-paragraph, 33–72% of pages ended mid-sentence, and the running
heads were embedded as content.

Three pure helpers, applied by the pipeline in this order before classification:

    pages = strip_running_heads(pages)   # drop repeated heads/footers + page numbers
    doc   = join_pages(pages)            # one document; continuations re-joined
    # dehyphenate() is applied per block by the extractors, and again by join_pages
    # for words split across a page break.
"""

import math
import re
from collections import Counter

# Terminal punctuation: a page ending in one of these ends a sentence/paragraph.
_TERMINALS = (".", "!", "?", ":", ";", '"', "”", "’", ")", "]")

# A page number on its own line: arabic, roman, or "Page N".
_FOLIO_RE = re.compile(r"^\s*(?:\d{1,4}|[ivxlcdm]{1,8}|page\s+\d{1,4})\s*$", re.IGNORECASE)

# Running heads are short; never treat a long recurring sentence as one.
_MAX_HEAD_CHARS = 80


def dehyphenate(text: str) -> str:
    """Re-join words hyphenated at a line break: ``hy-\\npertrophy`` → ``hypertrophy``.

    Only fires when the continuation starts with a lowercase letter, so a
    line-final hyphen before a capitalised word (``the Snatch-\\nBalance``) is kept.
    Compound words split at their own hyphen (``clean-\\nand-jerk``) are also
    joined, which is the accepted cost of the heuristic.
    """
    return re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)


def _norm_edge(line: str) -> str:
    """Normalise an edge line for recurrence counting: collapse whitespace, lowercase."""
    return re.sub(r"\s+", " ", line.strip()).lower()


def _without_folio(line: str) -> str:
    """``83 Timing …`` / ``… Training 84`` → the head alone. Used only to match a
    combined head+folio line against a head that already recurs on its own —
    never for counting, or ``Week 3`` / ``Table 5`` edge lines would collapse to
    one recurring key and be stripped as running heads."""
    s = re.sub(r"^\d{1,4}\s+", "", line)
    return re.sub(r"\s+\d{1,4}$", "", s)


def strip_running_heads(pages: list[str], min_share: float = 0.3, min_pages: int = 3) -> list[str]:
    """Remove running heads/footers and page numbers from the edges of each page.

    A line is a running head when its normalised form is the first or last
    non-blank line on at least ``max(min_pages, ceil(min_share * n_pages))`` pages
    (Zatsiorsky alternates the book title and the chapter title at the top of
    every page; Dan John puts ``Intervention`` there). Folios (bare numerals /
    roman numerals / ``Page N``) are removed from page edges regardless of
    recurrence. Up to two lines are stripped from each edge (head + folio).
    Chapter titles that appear on only their own chapter's pages stay put.
    """
    if not pages:
        return pages

    counts: Counter[str] = Counter()
    for page in pages:
        lines = [ln for ln in page.splitlines() if ln.strip()]
        if not lines:
            continue
        for edge in {_norm_edge(lines[0]), _norm_edge(lines[-1])}:
            if edge and len(edge) <= _MAX_HEAD_CHARS:
                counts[edge] += 1

    threshold = max(min_pages, math.ceil(min_share * len(pages)))
    recurring = {line for line, n in counts.items() if n >= threshold}

    def _is_edge_noise(line: str) -> bool:
        if _FOLIO_RE.match(line):
            return True
        norm = _norm_edge(line)
        return norm in recurring or _without_folio(norm) in recurring

    cleaned: list[str] = []
    for page in pages:
        lines = page.splitlines()
        # leading edge
        for _ in range(2):
            idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
            if idx is None or not _is_edge_noise(lines[idx]):
                break
            del lines[idx]
        # trailing edge
        for _ in range(2):
            idx = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
            if idx is None or not _is_edge_noise(lines[idx]):
                break
            del lines[idx]
        cleaned.append("\n".join(lines).strip())
    return cleaned


def join_pages(pages: list[str]) -> str:
    """Join page texts into one document, re-attaching text that runs across a break.

    - previous page ends with a hyphen and the next starts lowercase → the word
      is re-joined (``accumu-`` | ``lation``);
    - previous page ends without terminal punctuation and the next starts
      lowercase, or the previous ends with a comma → the sentence continues,
      joined with a space;
    - otherwise the pages are separated by a paragraph break (``\\n\\n``).

    The result is what the classifier and chunker see for a PDF: paragraphs are
    real, chunk boundaries fall on paragraph edges, and no chunk is a page.
    """
    out: list[str] = []
    for page in pages:
        page = page.strip()
        if not page:
            continue
        if not out:
            out.append(page)
            continue
        prev = out[-1]
        starts_lower = page[:1].islower()
        if prev.endswith("-") and starts_lower:
            out[-1] = prev[:-1] + page
        elif not prev.endswith(_TERMINALS) and (starts_lower or prev.endswith(",")):
            out[-1] = prev + " " + page
        else:
            out.append(page)
    return "\n\n".join(out)
