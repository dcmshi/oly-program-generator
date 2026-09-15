# processors/sectioning.py
"""
Section post-processing shared by the classifier and the chunker (RAG-H1).

Both split documents on heading regexes. Two failure modes follow:

* **Fragments.** The weaker patterns (``\\d+.\\d+ Title``, ``Week N …``) over-fire
  on tabular text — ``1.5 Snatch 3x3`` or ``Week 2 Monday`` — carving one table
  into dozens of one-line "sections" whose heading text is really data (Medvedev:
  617 chunks averaging 269 chars). ``merge_small_sections`` folds a fragment into
  its neighbour and puts the heading line back into the body so nothing is lost.
  Reliable headings (``Chapter N``, ``PART``, markdown ``#``) are never merged.

* **Blobs.** Once PDF pages are joined into one document, a heading-delimited
  section can run to 60k chars, and a single program table inside it would route
  the whole chapter to the template parser. ``split_oversized_sections`` caps
  sections at paragraph boundaries; each part keeps the section's title/chapter
  so the chunk preamble still carries it.

A section is ``(title, text, meta)``; ``meta`` is passed through untouched (copied
with a ``part`` index when a section is split).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from shared.constants import CLASSIFY_SECTION_MAX_CHARS, MIN_SECTION_CHARS

Section = tuple[str, str, dict]

# Headings that are also plausible table/program lines. Only sections carrying
# one of these (or no title at all) are candidates for merging.
WEAK_HEADING_RE = re.compile(r"^(?:\d+\.\d+\s|(?:Week|Phase|Block|Cycle)\s+\d+)", re.IGNORECASE)


def _is_weak(title: str) -> bool:
    return not title or bool(WEAK_HEADING_RE.match(title.strip()))


def merge_small_sections(sections: list[Section], min_chars: int = MIN_SECTION_CHARS) -> list[Section]:
    """Fold sections shorter than ``min_chars`` with a weak/empty heading into the
    previous section (or the next one when there is no previous), restoring the
    fragment's heading line as the first line of its text."""
    merged: list[Section] = []
    carry: list[str] = []  # small leading fragments waiting for a section to join

    for title, text, meta in sections:
        body = text.strip()
        small = len(body) < min_chars and _is_weak(title)
        restored = f"{title.strip()}\n{body}".strip() if small and title.strip() else body

        if small and merged:
            p_title, p_text, p_meta = merged[-1]
            merged[-1] = (p_title, f"{p_text.rstrip()}\n\n{restored}", p_meta)
        elif small:
            carry.append(restored)
        else:
            if carry:
                body = "\n\n".join([*carry, body])
                carry = []
            merged.append((title, body, meta))

    if carry:  # document consisted only of fragments — keep them as one section
        merged.append(("", "\n\n".join(carry), {}))
    return merged


def split_oversized_sections(sections: list[Section], max_chars: int = CLASSIFY_SECTION_MAX_CHARS) -> list[Section]:
    """Split any section longer than ``max_chars`` at paragraph (``\\n\\n``) boundaries.

    A single paragraph longer than the cap is kept whole (the chunker deals with
    it). Parts inherit the title and a copy of ``meta`` with ``part`` = 1, 2, …
    """
    out: list[Section] = []
    for title, text, meta in sections:
        if len(text) <= max_chars:
            out.append((title, text, meta))
            continue
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        parts: list[str] = []
        buf: list[str] = []
        size = 0
        for para in paragraphs:
            if buf and size + len(para) + 2 > max_chars:
                parts.append("\n\n".join(buf))
                buf, size = [], 0
            buf.append(para)
            size += len(para) + 2
        if buf:
            parts.append("\n\n".join(buf))
        for i, part in enumerate(parts, 1):
            out.append((title, part, {**meta, "part": i}))
    return out
