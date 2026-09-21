# processors/ocr_quality.py
"""
No-reference quality checks for vision-OCR output (OCR-QA).

There is no ground truth for a scanned book, so a page is judged by signals
that need none — the ones the OCR-quality literature finds usable without a
reference (OCR-Quality dataset, arXiv 2510.21774; risk-controlled VLM OCR,
arXiv 2603.19790): length against its neighbours, repetition of the previous
page (a model echoing), the share of tokens that are not words, the share of
non-ASCII characters, and — the strongest single signal in that work —
**agreement between independent views of the same page**. Asking a VLM to
grade its own transcription is the weakest option (F1 ≈ 40 on intermediate
quality), so this module never does that.

`assess_pages` scores every page and returns the suspects with reasons;
`PDFExtractor` re-OCRs suspects on their own at a different zoom (a second
"view") and keeps the transcription the two views agree on, or the longer one
when they roughly agree, and reports pages where they don't.

`ocr_audit.py` runs the same checks over `sources/.ocr_cache/*.json` for
books already ingested.
"""

import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from shared.constants import (  # noqa: E402
    OCR_ECHO_JACCARD,
    OCR_GARBLED_RATIO_MAX,
    OCR_MIN_PAGE_CHARS,
    OCR_NON_ASCII_RATIO_MAX,
    OCR_SHORT_VS_NEIGHBOURS,
    OCR_VIEW_AGREEMENT_MIN,
)

_WORD_RE = re.compile(r"[A-Za-z]+")
_TOKEN_RE = re.compile(r"\S+")
_VOWELS = set("aeiouyAEIOUY")
# A short page made of captions is a figure page, not a lost one.
_CAPTION_RE = re.compile(r"^\s*(diag(ram)?|fig(ure)?|table|photo|plate|chart)\b", re.I | re.M)


@dataclass
class PageQuality:
    index: int                       # 0-based
    chars: int
    reasons: list[str] = field(default_factory=list)

    @property
    def suspect(self) -> bool:
        return bool(self.reasons)


def garbled_ratio(text: str) -> float:
    """Share of alphabetic tokens that don't look like words: no vowel, or a
    run of 4+ identical letters, or 3+ consonant clusters of 5 — the shapes
    OCR noise takes ('vvvvv', 'tbxrqk', '|||')."""
    tokens = [t for t in _TOKEN_RE.findall(text) if len(t) >= 3]
    if not tokens:
        return 0.0
    bad = 0
    for t in tokens:
        letters = "".join(_WORD_RE.findall(t))
        if not letters:
            continue
        if len(letters) >= 4 and not (set(letters) & _VOWELS):
            bad += 1
        elif re.search(r"(.)\1{3,}", letters):
            bad += 1
        elif re.search(r"[^aeiouyAEIOUY\W\d]{6,}", letters):
            bad += 1
    return bad / len(tokens)


def non_ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if ord(c) > 127) / len(text)


def _shingles(text: str, k: int = 5) -> set[str]:
    words = text.lower().split()
    return {" ".join(words[i:i + k]) for i in range(max(0, len(words) - k + 1))}


def jaccard(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def view_agreement(a: str, b: str) -> float:
    """Similarity of two transcriptions of the same page (word-level Jaccard
    on 3-shingles — tolerant of line-break and punctuation differences)."""
    sa, sb = _shingles(a, 3), _shingles(b, 3)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def assess_pages(pages: list[str], has_ink: list[bool] | None = None) -> list[PageQuality]:
    """Score every page; `has_ink[i]` (optional) says whether the rendered page
    has dark pixels, so a blank transcription of a blank page is not a suspect."""
    n = len(pages)
    out: list[PageQuality] = []
    lengths = [len(p.strip()) for p in pages]
    for i, text in enumerate(pages):
        q = PageQuality(index=i, chars=lengths[i])
        ink = has_ink[i] if has_ink is not None else True
        if lengths[i] < OCR_MIN_PAGE_CHARS:
            if ink and not (text.strip() and _CAPTION_RE.search(text)):
                q.reasons.append("blank")
            out.append(q)
            continue
        neighbours = [lengths[j] for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < n and lengths[j] >= OCR_MIN_PAGE_CHARS]
        if (len(neighbours) >= 2 and lengths[i] < OCR_SHORT_VS_NEIGHBOURS * statistics.median(neighbours)
                and ink and not _CAPTION_RE.search(text)):
            q.reasons.append(f"short ({lengths[i]} vs neighbours ~{int(statistics.median(neighbours))})")
        if i > 0 and lengths[i - 1] >= OCR_MIN_PAGE_CHARS:
            j = jaccard(text, pages[i - 1])
            if j >= OCR_ECHO_JACCARD:
                q.reasons.append(f"echoes previous page (jaccard {j:.2f})")
        g = garbled_ratio(text)
        if g > OCR_GARBLED_RATIO_MAX:
            q.reasons.append(f"garbled tokens {g:.0%}")
        na = non_ascii_ratio(text)
        if na > OCR_NON_ASCII_RATIO_MAX:
            q.reasons.append(f"non-ascii {na:.0%}")
        out.append(q)
    return out


def choose_view(first: str, second: str) -> tuple[str, float, str]:
    """Pick between two transcriptions of one page. Returns (text, agreement,
    verdict): 'agree' (kept the longer of two consistent views), 'disagree'
    (kept the longer but the page stays flagged), or 'recovered' (first was
    blank/short and the second view produced text)."""
    a = view_agreement(first, second)
    if len(first.strip()) < OCR_MIN_PAGE_CHARS and len(second.strip()) >= OCR_MIN_PAGE_CHARS:
        return second, a, "recovered"
    longer = first if len(first) >= len(second) else second
    return longer, a, ("agree" if a >= OCR_VIEW_AGREEMENT_MIN else "disagree")


def summarize(qualities: list[PageQuality]) -> dict:
    suspects = [q for q in qualities if q.suspect]
    return {
        "pages": len(qualities),
        "pages_suspect": len(suspects),
        "suspect_pages": [q.index + 1 for q in suspects],
        "reasons": {q.index + 1: q.reasons for q in suspects},
    }
