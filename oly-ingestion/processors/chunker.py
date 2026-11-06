# processors/chunker.py
"""
Semantic chunker optimized for weightlifting programming content.

Key design decisions:
- Chunk sizes vary by source type (see CHUNK_PROFILES below)
- Contextual preambles prepended to every chunk for embedding disambiguation
- Keep-together patterns prevent splitting inline prescriptions and program blocks
- Tail overlap + preamble provides both content continuity and structural context
- Two-pass topic tagging: keyword matching (fast) + LLM fallback (accurate)
"""

import re
from dataclasses import dataclass, field

from processors.sectioning import MARKDOWN_HEADING_RE_SRC
from processors.source_profiles import CHUNK_PROFILES, SOURCE_PROFILE_MAP, SourceProfile  # noqa: F401 — re-exported
from processors.topic_tags import (  # noqa: F401 — re-exported: chunker was their home
    _KEYWORD_MATCHERS,
    CHUNK_TYPE_DEFAULT_TOPICS,
    KEYWORD_TO_TOPIC,
    keyword_tag,
)
from shared.constants import WEEK_BOUNDARY_FLUSH_FRACTION

# ──────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    content: str                                    # the actual text (with preamble prepended)
    raw_content: str                                # text without preamble (for display / debugging)
    metadata: dict = field(default_factory=dict)
    token_count: int = 0
    topics: list[str] = field(default_factory=list)
    contains_specific_numbers: bool = False
    information_density: str = "medium"             # low | medium | high


# ──────────────────────────────────────────────────────────────
# Keep-together patterns
# ──────────────────────────────────────────────────────────────

# A line that opens a week of a training log: "Week 9", "Week # 12", "Week #10 -".
# The chunker starts a paragraph there and prefers to close the chunk before it
# (WEEK_BOUNDARY_FLUSH_FRACTION), so day-by-day logs chunk per week.
WEEK_BOUNDARY_RE = re.compile(r"^Week\s*#?\s*\d+\b", re.IGNORECASE)

KEEP_TOGETHER_PATTERNS = {
    # Rep schemes: "5x3 @ 75%", "3×2 at 85%"
    "rep_scheme": re.compile(
        r"\d+\s*[xX×]\s*\d+\s*(?:@|at)?\s*\d+%"
    ),
    # Percentage references: "85% of 1RM", "work up to 90%"
    "percentage_ref": re.compile(
        r"\d+%\s*(?:of\s+)?(?:1RM|max|PR|one[- ]rep[- ]max)"
    ),
    # Prescription labels: "Sets: 5", "Reps: 3", "Rest: 2 min"
    "prescription_label": re.compile(
        r"(?:Sets?|Reps?|Rest|Tempo|RPE|Intensity)\s*:\s*[\d\w]+"
    ),
    # Exercise complexes: "Clean + Front Squat + Jerk"
    "exercise_complex": re.compile(
        r"(?:[A-Z][a-z]+\s*(?:Snatch|Clean|Jerk|Squat|Pull|Press))"
        r"(?:\s*\+\s*(?:[A-Z][a-z]+\s*)*"
        r"(?:Snatch|Clean|Jerk|Squat|Pull|Press|Balance|Deadlift))+"
    ),
    # Daily program blocks:
    #   Monday:
    #     Snatch 5x3 @ 72%
    #     Back Squat 4x5 @ 75%
    "daily_program_block": re.compile(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
        r"Day\s+\d+)\s*:?\s*\n(?:\s+.+\n?){1,8}",
        re.MULTILINE,
    ),
    # Numbered exercise lists
    "numbered_exercise_list": re.compile(
        r"(?:\d+\.\s+.+\n?){2,8}"
    ),
    # Percentage ranges: "70-80%", "85%-93%"
    "percentage_range": re.compile(
        r"\d+\s*-\s*\d+\s*%"
    ),
    # Volume prescriptions: "15-20 total reps", "NL = 24"
    "volume_prescription": re.compile(
        r"(?:\d+\s*-\s*\d+\s+(?:total\s+)?reps|NL\s*=\s*\d+)"
    ),
    # Soviet notation: 70%/3x3  75%/3x2  80%/2x2
    "soviet_notation": re.compile(
        r"(?:\d+%\s*/\s*\d+\s*[xX]\s*\d+\s*){2,}"
    ),
}


# ──────────────────────────────────────────────────────────────
# Chunk validation
# ──────────────────────────────────────────────────────────────

@dataclass
class ChunkValidationResult:
    chunk_index: int
    is_valid: bool
    issues: list[str]
    severity: str = "info"     # info | warning | error


_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}


def _worse(current: str, new: str) -> str:
    """Aggregate check severities by rank (ING-L2): a later 'info' check must not
    downgrade an earlier 'warning'."""
    return new if _SEVERITY_RANK[new] > _SEVERITY_RANK[current] else current


def validate_chunk(chunk: Chunk) -> ChunkValidationResult:
    """Validate a chunk before loading into the vector store."""
    issues = []
    severity = "info"

    # Too short — probably a context-stripped fragment
    if chunk.token_count < 50:
        issues.append(
            f"Chunk too short ({chunk.token_count} tokens). "
            "Likely a fragment — consider merging with adjacent chunk."
        )
        severity = _worse(severity, "warning")

    # Too long — embedding signal dilution
    if chunk.token_count > 1500:
        issues.append(
            f"Chunk too long ({chunk.token_count} tokens). "
            "Consider splitting further."
        )
        severity = _worse(severity, "warning")

    # No topics — invisible to filtered search
    if not chunk.topics:
        issues.append(
            "No topics assigned. Chunk will only appear in unfiltered similarity search."
        )
        severity = _worse(severity, "warning")

    # Contains structured data that should have been routed to tables
    rep_scheme_count = len(
        KEEP_TOGETHER_PATTERNS["rep_scheme"].findall(chunk.raw_content)
    )
    if rep_scheme_count >= 3:
        issues.append(
            f"Contains {rep_scheme_count} rep schemes. "
            "Consider also routing to percentage_schemes table."
        )
        severity = _worse(severity, "info")  # dual-storage is fine, just flag it

    # Contains a table
    lines = chunk.raw_content.split("\n")
    table_like = [ln for ln in lines if ln.count("|") >= 2 or ln.count("\t") >= 2]
    if len(table_like) >= 3:
        issues.append(
            "Contains what looks like a table. Consider parsing as structured data."
        )
        severity = _worse(severity, "warning")

    # Orphaned context — chunk starts mid-sentence
    stripped = chunk.raw_content.strip()
    if stripped and stripped[0].islower():
        issues.append(
            "Starts with lowercase — likely a mid-sentence split. "
            "Check chunk boundary alignment."
        )
        severity = _worse(severity, "warning")

    return ChunkValidationResult(
        chunk_index=chunk.metadata.get("chunk_index", -1),
        is_valid=len(issues) == 0,
        issues=issues,
        severity=severity,
    )


# ──────────────────────────────────────────────────────────────
# Main chunker
# ──────────────────────────────────────────────────────────────

class SemanticChunker:
    """Weightlifting-aware semantic chunker with contextual preambles,
    keep-together rules, variable sizing, and topic tagging."""

    # Capture the FULL heading line (…\.*$), not just the marker, so the section
    # title is the real heading (e.g. "# Snatch Technique") rather than the bare
    # "#", and the heading text isn't duplicated into the body / preamble (I-L7).
    # Mirrors classifier._split_into_sections: `# 4 - March (Monday)` (vision
    # OCR's rendering of Medvedev's session labels) is not a heading — this
    # copy made chunk = session even after the classifier was fixed (MEDVEDEV).
    # `Week N` is deliberately absent: a week label is handled by the chunker's
    # week-boundary flush, which keeps the label in the chunk text instead of
    # lifting it into a title.
    SECTION_BREAK_PATTERNS = [
        r"^" + MARKDOWN_HEADING_RE_SRC + r"$",        # Markdown headers (minus OCR session labels)
        r"^Chapter\s+\d+.*$",                        # Chapter markers
        r"^PART\s+[IVX]+.*$",                        # Part markers
        r"^(?:Phase|Block|Cycle)\s+\d+.*$",          # Program phase markers
        r"^\d+\.\d+\s+[A-Z].*$",                     # Numbered sections
    ]

    def __init__(
        self,
        source_profile: SourceProfile = SourceProfile.PROGRAMMING_FOCUSED,
        chunk_size_override: int | None = None,
        chunk_overlap_override: int | None = None,
    ):
        profile = CHUNK_PROFILES[source_profile]
        self.chunk_size = chunk_size_override or profile["chunk_size"]
        self.chunk_overlap = chunk_overlap_override or profile["chunk_overlap"]
        self.source_profile = source_profile

    @staticmethod
    def mapped_profile(source_title: str) -> "SourceProfile | None":
        """The SOURCE_PROFILE_MAP profile for a title (substring match), or None."""
        for key, profile in SOURCE_PROFILE_MAP.items():
            if key.lower() in (source_title or "").lower():
                return profile
        return None

    @classmethod
    def for_source(cls, source_title: str) -> "SemanticChunker":
        """Factory: select the right chunking profile for a known source."""
        # Try prefix matching for sources where the title in the map
        # is a prefix of the actual title.
        return cls(source_profile=cls.mapped_profile(source_title) or SourceProfile.PROGRAMMING_FOCUSED)

    @classmethod
    def for_web_article(cls, article_word_count: int) -> "SemanticChunker":
        """Factory: dynamically size chunks for web articles based on length."""
        if article_word_count > 3000:
            return cls(source_profile=SourceProfile.THEORY_HEAVY)
        elif article_word_count > 1000:
            return cls(source_profile=SourceProfile.PROGRAMMING_FOCUSED)
        else:
            return cls(
                source_profile=SourceProfile.WEB_ARTICLE,
                chunk_size_override=500,
                chunk_overlap_override=100,
            )

    # ── Public API ────────────────────────────────────────────

    def chunk(
        self,
        text: str,
        metadata: dict | None = None,
        source_title: str = "",
        author: str = "",
    ) -> list[Chunk]:
        """Split text into semantically meaningful, contextually enriched chunks."""
        metadata = metadata or {}

        # Step 1: Split on hard section boundaries
        sections = self._split_on_sections(text)

        chunks: list[Chunk] = []
        for section in sections:
            # Step 2: Build preamble from document structure
            preamble = self._build_preamble(
                source_title=source_title,
                author=author,
                chapter=metadata.get("chapter", ""),
                section_title=section.get("title", ""),
            )

            # Step 3: Chunk within the section
            raw_chunks = self._chunk_section(section["text"])

            for i, raw_text in enumerate(raw_chunks):
                if not raw_text.strip():
                    continue

                # Prepend preamble to the content that gets embedded
                full_content = preamble + raw_text.strip()

                # Step 4: Detect numbers and estimate density
                has_numbers = bool(
                    KEEP_TOGETHER_PATTERNS["rep_scheme"].search(raw_text)
                    or KEEP_TOGETHER_PATTERNS["percentage_ref"].search(raw_text)
                    or KEEP_TOGETHER_PATTERNS["soviet_notation"].search(raw_text)
                )
                density = self._estimate_density(raw_text, has_numbers)

                # Step 5: Topic tagging (keyword pass)
                topics = sorted(keyword_tag(raw_text))

                # Guarantee chunk_type's baseline topics are always present
                chunk_type = metadata.get("chunk_type", "")
                if chunk_type in CHUNK_TYPE_DEFAULT_TOPICS:
                    topics = sorted(set(topics) | set(CHUNK_TYPE_DEFAULT_TOPICS[chunk_type]))

                chunk = Chunk(
                    content=full_content,
                    raw_content=raw_text.strip(),
                    metadata={
                        **metadata,
                        "section_title": section.get("title", ""),
                        "chunk_index": i,
                        "source_profile": self.source_profile.value,
                        "chunk_size_target": self.chunk_size,
                    },
                    token_count=self._estimate_tokens(full_content),
                    topics=topics,
                    contains_specific_numbers=has_numbers,
                    information_density=density,
                )
                chunks.append(chunk)

        return chunks

    # ── Preamble ──────────────────────────────────────────────

    @staticmethod
    def _build_preamble(source_title: str, author: str,
                        chapter: str, section_title: str) -> str:
        """Build a contextual preamble to prepend to every chunk.

        Kept short (40-60 tokens) so it doesn't eat into the content budget.
        Provides structural context to the embedding model for disambiguation.
        """
        lines = []
        if source_title or author:
            source_parts = []
            if source_title:
                source_parts.append(f"Source: {source_title}")
            if author:
                source_parts.append(f"Author: {author}")
            lines.append(f"[{' | '.join(source_parts)}]")

        context_parts = []
        if chapter:
            context_parts.append(f"Chapter: {chapter}")
        if section_title:
            context_parts.append(f"Section: {section_title}")
        if context_parts:
            lines.append(f"[{' | '.join(context_parts)}]")

        return "\n".join(lines) + "\n\n" if lines else ""

    # ── Section splitting ─────────────────────────────────────

    def _split_on_sections(self, text: str) -> list[dict]:
        """Split on chapter/section boundaries (hard breaks)."""
        combined = "|".join(self.SECTION_BREAK_PATTERNS)
        parts = re.split(f"({combined})", text, flags=re.MULTILINE)

        sections: list[dict] = []
        current_title = ""
        current_text = ""

        for part in parts:
            if re.match(combined, part, re.MULTILINE):
                if current_text.strip():
                    sections.append({"title": current_title, "text": current_text})
                current_title = part.strip()
                current_text = ""
            else:
                current_text += part

        if current_text.strip():
            sections.append({"title": current_title, "text": current_text})

        if not sections:
            return [{"title": "", "text": text}]

        # RAG-H1: "Week 2 …" / "1.5 Snatch" lines inside program listings match
        # the break patterns and would each become a one-line chunk; fold such
        # fragments back into their neighbour with the line restored.
        from processors.sectioning import merge_small_sections
        merged = merge_small_sections([(s["title"], s["text"], {}) for s in sections])
        return [{"title": title, "text": body} for title, body, _meta in merged]

    # ── Paragraph-aware chunking with keep-together ───────────

    def _chunk_section(self, text: str) -> list[str]:
        """Chunk within a section using paragraph-aware sliding window.

        Respects keep-together patterns: if a chunk boundary would split
        a matched pattern, the chunk is extended to include the full match.
        """
        # A week label glued to the previous session's totals ("F. L. = 67\n
        # Totals for week 8 …\nWeek # 9\n# 3 - February") gets its own
        # paragraph, so the week can start a chunk (MEDVEDEV).
        text = re.sub(r"\n(?=Week\s*#?\s*\d+\b)", "\n\n", text)
        paragraphs = [
            piece
            for p in text.split("\n\n") if p.strip()
            for piece in self._split_long_paragraph(p.strip())
        ]

        chunks: list[str] = []
        current_chunk = ""
        current_tokens = 0
        week_flush_at = self.chunk_size * WEEK_BOUNDARY_FLUSH_FRACTION

        for para in paragraphs:
            para_tokens = self._estimate_tokens(para)

            # Close the chunk at a week boundary once it is big enough to be
            # worth a chunk; no tail overlap — last week's totals are not
            # context for this week.
            if current_chunk and current_tokens >= week_flush_at and WEEK_BOUNDARY_RE.match(para):
                chunks.append(current_chunk)
                current_chunk = para
                current_tokens = para_tokens
                continue

            if current_tokens + para_tokens > self.chunk_size and current_chunk:
                # Check if splitting here would break a keep-together pattern
                if self._would_split_pattern(current_chunk, para):
                    # Extend the chunk to include this paragraph
                    current_chunk += "\n\n" + para
                    current_tokens = self._estimate_tokens(current_chunk)
                    continue

                chunks.append(current_chunk)
                # Tail overlap: keep last N tokens of previous chunk
                overlap_text = self._get_overlap(current_chunk)
                current_chunk = overlap_text + ("\n\n" if overlap_text else "") + para
                current_tokens = self._estimate_tokens(current_chunk)
            else:
                current_chunk += ("\n\n" if current_chunk else "") + para
                current_tokens += para_tokens

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _would_split_pattern(self, chunk_end: str, next_para: str) -> bool:
        """Check if splitting between chunk_end and next_para would break
        a keep-together pattern that spans the boundary."""
        # Take the last 200 chars of the current chunk + first 200 of next
        boundary_zone = chunk_end[-200:] + "\n\n" + next_para[:200]
        boundary_idx = len(chunk_end[-200:])
        for pattern in KEEP_TOGETHER_PATTERNS.values():
            # Check EVERY match, not just the first — an early non-spanning match
            # would otherwise mask a later boundary-spanning one (I-L6).
            for match in pattern.finditer(boundary_zone):
                if match.start() < boundary_idx < match.end():
                    return True
        return False

    def _get_overlap(self, text: str) -> str:
        """Get the last ~overlap tokens of text, respecting sentence boundaries."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        overlap = ""
        for sent in reversed(sentences):
            candidate = sent + " " + overlap if overlap else sent
            if self._estimate_tokens(candidate) > self.chunk_overlap:
                break
            overlap = candidate
        return overlap.strip()

    # ── Density estimation ────────────────────────────────────

    @staticmethod
    def _estimate_density(text: str, has_numbers: bool) -> str:
        """Estimate how information-dense a chunk is.

        High-density chunks contain concrete, actionable prescriptions.
        Low-density chunks contain general discussion or filler.
        """
        percentage_count = len(re.findall(r"\d+%", text))
        rep_scheme_count = len(re.findall(r"\d+\s*[xX×]\s*\d+", text))
        specific_exercise_refs = len(re.findall(
            r"(?:snatch|clean|jerk|squat|pull|press|deadlift)",
            text, re.IGNORECASE,
        ))

        score = percentage_count * 2 + rep_scheme_count * 3 + specific_exercise_refs
        if has_numbers:
            score += 5

        if score >= 10:
            return "high"
        elif score >= 4:
            return "medium"
        return "low"

    # ── Token estimation ──────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Token count under the embedding model's tokenizer (RAG-M2).

        Delegates to processors.tokens.count_tokens — tiktoken cl100k_base when
        available, words × 1.3 otherwise. The word estimate under-counted
        numeric notation by ~5×.
        """
        from processors.tokens import count_tokens
        return count_tokens(text)

    # ── Oversized paragraphs ──────────────────────────────────

    def _split_long_paragraph(self, para: str) -> list[str]:
        """Split a paragraph longer than chunk_size into sentence groups that fit.

        `_chunk_section` never split a paragraph, so one long paragraph became one
        oversized chunk that the embedding call then truncated (RAG-M2). Before
        RAG-H1 every PDF "paragraph" was a page, which masked it. Sentences are
        grouped up to chunk_size; a single sentence over the limit (a table with
        no punctuation) is split on whitespace by token budget.
        """
        if self._estimate_tokens(para) <= self.chunk_size:
            return [para]

        def _fit_words(sentence: str) -> list[str]:
            words, pieces, buf = sentence.split(), [], []
            for w in words:
                buf.append(w)
                if self._estimate_tokens(" ".join(buf)) > self.chunk_size and len(buf) > 1:
                    buf.pop()
                    pieces.append(" ".join(buf))
                    buf = [w]
            if buf:
                pieces.append(" ".join(buf))
            return pieces

        pieces: list[str] = []
        buf: list[str] = []
        for sent in re.split(r"(?<=[.!?])\s+", para):
            if not sent.strip():
                continue
            for unit in (_fit_words(sent) if self._estimate_tokens(sent) > self.chunk_size else [sent]):
                candidate = " ".join([*buf, unit])
                if buf and self._estimate_tokens(candidate) > self.chunk_size:
                    pieces.append(" ".join(buf))
                    buf = [unit]
                else:
                    buf.append(unit)
        if buf:
            pieces.append(" ".join(buf))
        return pieces
