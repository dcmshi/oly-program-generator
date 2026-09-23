# shared/excerpt.py
"""
Query-focused excerpts of long knowledge chunks (AUD-2).

Most live chunks are longer than the prompt's per-chunk budget
(`SNIPPET_MAX_CHARS`; median chunk ≈ 3.8k chars), and a head cut showed the
model the first ~40 % of each — often not the part the query matched.
`focused_excerpt` splits a chunk into sentence / line pieces, scores each by
overlap with the query's content terms, and keeps the best pieces (plus their
neighbours, and the chunk's heading line) in their original order.

Pure and deterministic: no DB, no model, no randomness. Shared so the eval's
golden-set grader can show the model-facing excerpt at its next rebuild.
"""

import math
import re

from shared.constants import (
    EXCERPT_GAP_MARKER,
    EXCERPT_HEADING_MAX_CHARS,
    EXCERPT_MAX_PIECE_CHARS,
    EXCERPT_MIN_TERM_CHARS,
)

_TOKEN_RE = re.compile(r"[a-z][a-z0-9]*")   # hyphens split: snatch-grip → snatch, grip
# A piece boundary: whitespace after sentence-final punctuation, or any line break.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])[ \t]+|[ \t]*\n\s*")

# English function words plus the boilerplate every generated query carries
# ("exercise selection for a … session with … support", "… athlete",
# "addressing faults: …") — those words match everywhere and would drown the
# lift / phase / fault terms that actually discriminate.
STOP_WORDS: frozenset[str] = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its itself just
me more most my no nor not now of off on once only or other our out over own same she should
so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your
yours one two use used using may might must shall also well get got like make made
exercise exercises selection session sessions support supporting work athlete athletes
addressing address focus lift lifts phase intensity week weeks weightlifting weightlifter
strength development correcting correct correction limiter limiters fault faults
""".split())


def _stem(word: str) -> str:
    """Crude suffix stripping, applied to query and chunk alike (so it only has
    to be consistent, not linguistically right): `snatches` → `snatch`,
    `pulling` → `pull`, `intensities` → `intensity`."""
    w = word
    for suffix, repl in (("ies", "y"), ("ing", ""), ("es", ""), ("ed", ""), ("s", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            if suffix == "s" and w.endswith("ss"):
                break
            return w[: len(w) - len(suffix)] + repl
    return w


def content_terms(text: str) -> set[str]:
    """Lowercased, stop-worded, stemmed content terms of ``text``."""
    terms = set()
    for t in _TOKEN_RE.findall(text.lower()):
        if len(t) < EXCERPT_MIN_TERM_CHARS or t in STOP_WORDS:
            continue
        stem = _stem(t)
        if stem not in STOP_WORDS:
            terms.add(stem)
    return terms


def _pieces(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of the sentence / line pieces of ``text``; a piece
    longer than EXCERPT_MAX_PIECE_CHARS (a table row, a run-on OCR line) is cut
    at whitespace so one piece can't take the whole budget."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _BOUNDARY_RE.finditer(text):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))

    out: list[tuple[int, int]] = []
    for start, end in spans:
        while end - start > EXCERPT_MAX_PIECE_CHARS:
            cut = text.rfind(" ", start + 1, start + EXCERPT_MAX_PIECE_CHARS)
            if cut <= start:
                cut = start + EXCERPT_MAX_PIECE_CHARS
            out.append((start, cut))
            start = cut
            while start < end and text[start].isspace():
                start += 1
        if end > start and text[start:end].strip():
            out.append((start, end))
    return out


def _assemble(text: str, spans: list[tuple[int, int]], chosen: set[int]) -> str:
    """Join the chosen pieces in order: adjacent pieces keep the original text
    between them, a skipped stretch becomes EXCERPT_GAP_MARKER, and an omitted
    head or tail is marked too."""
    idx = sorted(chosen)
    if not idx:
        return ""
    parts: list[str] = []
    if idx[0] > 0:
        parts.append(EXCERPT_GAP_MARKER.lstrip())
    prev = None
    for i in idx:
        start, end = spans[i]
        if prev is not None:
            parts.append(text[spans[prev][1]:start] if i == prev + 1 else EXCERPT_GAP_MARKER)
        parts.append(text[start:end])
        prev = i
    if idx[-1] < len(spans) - 1:
        parts.append(EXCERPT_GAP_MARKER.rstrip())
    return "".join(parts)


def _head(text: str, max_chars: int) -> str:
    """The head of ``text`` cut at a word boundary, marked as truncated."""
    tail = EXCERPT_GAP_MARKER.rstrip()
    room = max(max_chars - len(tail), 0)
    cut = text.rfind(" ", 0, room + 1)
    if cut <= room // 2:
        cut = room
    return text[:cut].rstrip() + tail


def _is_heading(text: str) -> bool:
    line = text.strip()
    if not line or len(line) > EXCERPT_HEADING_MAX_CHARS:
        return False
    return line.startswith("#") or line[-1] not in ".!?,;"


def focused_excerpt(text: str, query: str, max_chars: int) -> str:
    """The part of ``text`` most relevant to ``query``, at most ``max_chars``.

    Text within budget comes back unchanged. Otherwise pieces (sentences /
    lines) are scored by the query content terms they contain, each term
    weighted by its rarity inside the chunk; the best pieces are taken greedily
    (score, then position), then the budget left over is filled with the
    neighbours of chosen pieces so the excerpt reads as passages rather than
    stray sentences. The chunk's first line is kept when it is a heading.
    Non-adjacent pieces are joined with " … ". When no piece matches the query
    the head of the chunk is returned, as before.
    """
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text

    spans = _pieces(text)
    terms = content_terms(query or "")
    piece_terms = [content_terms(text[s:e]) & terms for s, e in spans]
    if not terms or not any(piece_terms):
        return _head(text, max_chars)

    n = len(spans)
    df: dict[str, int] = {}
    for pt in piece_terms:
        for t in pt:
            df[t] = df.get(t, 0) + 1
    weight = {t: 1.0 + math.log(n / c) for t, c in df.items()}
    scores = [sum(weight[t] for t in pt) for pt in piece_terms]

    chosen: set[int] = set()

    def _try(i: int) -> bool:
        if i in chosen:
            return False
        if len(_assemble(text, spans, chosen | {i})) > max_chars:
            return False
        chosen.add(i)
        return True

    if _is_heading(text[spans[0][0]:spans[0][1]]):
        _try(0)
    for i in sorted((i for i in range(n) if scores[i] > 0), key=lambda i: (-scores[i], i)):
        _try(i)
    if not any(scores[i] > 0 for i in chosen):
        # no matching piece fits the budget (only possible when max_chars is
        # below EXCERPT_MAX_PIECE_CHARS): fall back to the head
        return _head(text, max_chars)

    # Grow contiguous passages around the matches, one ring at a time (the
    # piece after, then before, each match — best match first), so the budget
    # left over goes to the nearest context rather than to the chunk's head.
    matched = sorted((i for i in chosen if scores[i] > 0), key=lambda i: (-scores[i], i))
    for d in range(1, n):
        grew = False
        for i in matched:
            for j, inner in ((i + d, i + d - 1), (i - d, i - d + 1)):
                if 0 <= j < n and inner in chosen and _try(j):
                    grew = True
        if not grew:
            break
    return _assemble(text, spans, chosen)
