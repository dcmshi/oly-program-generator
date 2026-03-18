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

import json
import re
from dataclasses import dataclass, field
from enum import Enum


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


class SourceProfile(Enum):
    """Chunk sizing profiles keyed to source type."""
    THEORY_HEAVY = "theory_heavy"           # Zatsiorsky, Verkhoshansky
    PROGRAMMING_FOCUSED = "programming"     # Everett, Takano, Pendlay
    DATA_HEAVY_SOVIET = "soviet"            # Medvedev, Laputin & Oleshko
    WEB_ARTICLE = "web_article"             # Catalyst website, SBS, JTS


# Chunk size and overlap per source profile
CHUNK_PROFILES = {
    SourceProfile.THEORY_HEAVY: {
        "chunk_size": 1100,
        "chunk_overlap": 250,
        "description": "Dense academic prose, multi-paragraph arguments",
    },
    SourceProfile.PROGRAMMING_FOCUSED: {
        "chunk_size": 900,
        "chunk_overlap": 200,
        "description": "Mixed rationale and program descriptions",
    },
    SourceProfile.DATA_HEAVY_SOVIET: {
        "chunk_size": 700,
        "chunk_overlap": 150,
        "description": "Terse prose between data tables",
    },
    SourceProfile.WEB_ARTICLE: {
        "chunk_size": 700,     # default; adjusted dynamically by article length
        "chunk_overlap": 150,
        "description": "Variable — adjusted by article length",
    },
}

# Map known sources to profiles (extend as you ingest new material)
SOURCE_PROFILE_MAP = {
    "Olympic Weightlifting: A Complete Guide": SourceProfile.PROGRAMMING_FOCUSED,
    "Weightlifting Programming": SourceProfile.PROGRAMMING_FOCUSED,
    "Scientific Principles of Hypertrophy Training": SourceProfile.PROGRAMMING_FOCUSED,
    "Intervention": SourceProfile.PROGRAMMING_FOCUSED,
    "Managing the Training of Weightlifters": SourceProfile.DATA_HEAVY_SOVIET,
    "A System of Multi-Year Training in Weightlifting": SourceProfile.DATA_HEAVY_SOVIET,
    "Science and Practice of Strength Training": SourceProfile.THEORY_HEAVY,
    "Supertraining": SourceProfile.THEORY_HEAVY,
    "Weightlifting Encyclopedia": SourceProfile.THEORY_HEAVY,
    "Becoming a Supple Leopard": SourceProfile.THEORY_HEAVY,
}


# ──────────────────────────────────────────────────────────────
# Keep-together patterns
# ──────────────────────────────────────────────────────────────

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
# Topic tagging
# ──────────────────────────────────────────────────────────────

KEYWORD_TO_TOPIC: dict[str, list[str]] = {
    # Periodization
    "accumulation": ["accumulation_phase"],
    "intensification": ["intensification_phase"],
    "realization": ["realization_phase", "competition_peaking"],
    "peaking": ["competition_peaking"],
    "taper": ["competition_peaking", "volume_management"],
    "deload": ["deload_strategy", "recovery_protocols"],
    "overtraining": ["overtraining_detection", "fatigue_management"],
    "supercompensation": ["adaptation_theory"],
    "mesocycle": ["periodization_models", "annual_planning"],
    "macrocycle": ["periodization_models", "annual_planning"],
    "microcycle": ["periodization_models"],
    "block periodization": ["periodization_models"],
    "linear periodization": ["periodization_models"],
    "undulating": ["periodization_models"],

    # Lifts
    "snatch": ["snatch_technique", "snatch_programming"],
    "clean": ["clean_technique", "clean_programming"],
    "jerk": ["jerk_technique", "jerk_programming"],
    "front squat": ["squat_programming"],
    "back squat": ["squat_programming"],
    "snatch pull": ["pull_programming", "snatch_programming"],
    "clean pull": ["pull_programming", "clean_programming"],

    # Programming concepts
    "prilepin": ["volume_management", "intensity_prescription"],
    "volume": ["volume_management"],
    "intensity": ["intensity_prescription"],
    "frequency": ["periodization_models"],
    "1rm": ["intensity_prescription", "load_progression"],
    "rpe": ["intensity_prescription", "fatigue_management"],

    # Athlete management
    "beginner": ["beginner_development"],
    "novice": ["beginner_development"],
    "intermediate": ["intermediate_development"],
    "advanced": ["advanced_development"],
    "training age": ["beginner_development", "load_progression"],
    "fault": ["fault_correction"],
    "correction": ["fault_correction"],
    "error": ["fault_correction"],
    "miss": ["fault_correction"],

    # Competition
    "competition": ["competition_strategy"],
    "attempt selection": ["competition_strategy"],
    "attempt": ["competition_strategy"],
    "opener": ["competition_strategy"],
    "warm-up": ["competition_strategy"],
    "weight class": ["weight_class_management"],
    "make weight": ["weight_class_management"],

    # Peaking / competition prep
    "weeks out": ["competition_peaking"],
    "pre-competition": ["competition_peaking"],
    "competition preparation": ["competition_peaking"],
    "competition prep": ["competition_peaking"],
    "peak": ["competition_peaking"],
    "reduce volume": ["volume_management", "competition_peaking"],
    "volume reduction": ["volume_management", "competition_peaking"],

    # Programming / periodization
    "periodization": ["periodization_models"],
    "program design": ["periodization_models"],
    "programming": ["periodization_models"],
    "training block": ["periodization_models"],
    "training cycle": ["periodization_models", "annual_planning"],
    "annual plan": ["annual_planning"],
    "max effort": ["intensity_prescription"],
    "maximal": ["intensity_prescription"],
    "load progression": ["load_progression"],
    "progression": ["load_progression"],

    # Lifts (additional)
    "overhead squat": ["snatch_technique", "squat_programming"],
    "hang snatch": ["snatch_programming"],
    "hang clean": ["clean_programming"],
    "pull": ["pull_programming"],
    "push press": ["jerk_programming"],

    # Recovery
    "recovery": ["recovery_protocols"],
    "adaptation": ["adaptation_theory"],
    "fatigue": ["fatigue_management"],
    "sleep": ["recovery_protocols"],
    "nutrition": ["nutrition_bodyweight"],

    # Soviet / Eastern-bloc terminology (Laputin, Medvedev, Verkhoshansky)
    "sporting form": ["competition_peaking", "periodization_models"],
    "sports form": ["competition_peaking", "periodization_models"],
    "trainability": ["adaptation_theory", "load_progression"],
    "training load": ["volume_management", "fatigue_management"],
    "educational-training": ["periodization_models"],
    "training preparation": ["periodization_models"],
    "special preparatory": ["periodization_models"],
    "general preparatory": ["periodization_models"],
    "preparatory period": ["periodization_models", "accumulation_phase"],
    "competitive period": ["competition_peaking", "periodization_models"],
    "transitional period": ["deload_strategy", "recovery_protocols"],
    "recuperation": ["recovery_protocols"],
    "rehabilitation": ["recovery_protocols"],
    "pharmacological": ["recovery_protocols"],
    "food supplement": ["nutrition_bodyweight", "recovery_protocols"],
    "bioenergetics": ["adaptation_theory"],
    "triathlon": ["competition_strategy"],   # old Soviet 3-lift format (press + snatch + C&J)
    "biathlon": ["competition_strategy"],    # modern 2-lift format (snatch + C&J)
    "tonnage": ["volume_management"],
    "number of lifts": ["volume_management"],
    "lifting total": ["competition_strategy"],
    "total result": ["competition_strategy"],
    "world record": ["competition_strategy"],
    "national team": ["competition_strategy"],
    "master of sport": ["advanced_development"],
    "candidate master": ["intermediate_development"],

    # Medvedev exercise abbreviations (A Program of Multi-Year Training)
    # These appear in workout listings as abbreviated notation
    "p. sn": ["snatch_programming"],          # Power Snatch
    "cl. sn": ["snatch_programming"],          # Classic Snatch
    "sn. pu": ["pull_programming", "snatch_programming"],  # Snatch Pull
    "p. cl": ["clean_programming"],            # Power Clean
    "sq. cl": ["clean_programming"],           # Squat Clean
    "cl. pu": ["pull_programming", "clean_programming"],   # Clean Pull
    "pu. j": ["jerk_programming"],             # Push Jerk
    "c + j": ["clean_programming", "jerk_programming"],    # Clean + Jerk
    "fr. sq": ["squat_programming"],           # Front Squat
    "b. sq": ["squat_programming"],            # Back Squat
    "ov. sq": ["snatch_technique", "squat_programming"],   # Overhead Squat
    "be. pr": ["jerk_programming"],            # Behind Press
    "hyper": ["squat_programming"],            # Hyperextension
    "multi-year": ["beginner_development", "periodization_models"],
    "year of training": ["beginner_development", "periodization_models", "load_progression"],
    "class iii": ["beginner_development"],
    "class ii": ["intermediate_development"],
    "class i": ["intermediate_development"],
    "candidate master of sport": ["advanced_development"],
    "training block": ["periodization_models"],
    "loading scheme": ["volume_management", "intensity_prescription"],
    "exercise classification": ["periodization_models"],
    "coordination structure": ["snatch_technique", "clean_technique"],
    # RPE / auto-regulation
    "rate of perceived exertion": ["intensity_prescription", "fatigue_management"],
    "rpe scale": ["intensity_prescription", "fatigue_management"],
    "rpe-based": ["intensity_prescription", "fatigue_management"],
    "autoregulation": ["intensity_prescription", "fatigue_management"],
    "auto-regulation": ["intensity_prescription", "fatigue_management"],
    # Rest and recovery timing
    "rest between sets": ["recovery_protocols", "periodization_models"],
    "inter-set rest": ["recovery_protocols"],
    "rest interval": ["recovery_protocols"],
    "between sets": ["recovery_protocols"],
    # Exercise complexity and motor learning
    "technical difficulty": ["exercise_selection_rationale"],
    "motor learning": ["beginner_development", "exercise_selection_rationale"],
    "movement pattern": ["exercise_selection_rationale"],
    "skill acquisition": ["beginner_development", "exercise_selection_rationale"],
    "learning curve": ["beginner_development", "exercise_selection_rationale"],
    "technical proficiency": ["exercise_selection_rationale"],
    # Accessory / supplemental exercise selection
    "accessory": ["exercise_selection_rationale"],
    "supplemental": ["exercise_selection_rationale"],
    "auxiliary": ["exercise_selection_rationale"],
    "good morning": ["exercise_selection_rationale", "pull_programming"],
    "nordic": ["exercise_selection_rationale"],
    "glute-ham": ["exercise_selection_rationale"],

    # Israetel / hypertrophy volume landmarks (Scientific Principles of Hypertrophy Training)
    "hypertrophy": ["exercise_selection_rationale", "volume_management"],
    "muscle growth": ["exercise_selection_rationale", "volume_management"],
    "muscle gain": ["exercise_selection_rationale", "volume_management"],
    "mev": ["exercise_selection_rationale", "volume_management"],
    "mav": ["exercise_selection_rationale", "volume_management"],
    "mrv": ["exercise_selection_rationale", "volume_management"],
    "minimum effective volume": ["exercise_selection_rationale", "volume_management"],
    "maximum adaptive volume": ["exercise_selection_rationale", "volume_management"],
    "maximum recoverable volume": ["exercise_selection_rationale", "volume_management"],
    "hard sets": ["exercise_selection_rationale", "volume_management"],
    "reps in reserve": ["intensity_prescription", "fatigue_management"],
    "rir": ["intensity_prescription", "fatigue_management"],
    "sets per week": ["exercise_selection_rationale", "volume_management"],
    "weekly sets": ["exercise_selection_rationale", "volume_management"],
    "muscle group": ["exercise_selection_rationale"],
    "upper back": ["exercise_selection_rationale"],
    "hypertrophy block": ["exercise_selection_rationale", "periodization_models"],
    "overload": ["load_progression", "adaptation_theory"],
    "progressive overload": ["load_progression", "adaptation_theory"],

    # Starrett / mobility (Becoming a Supple Leopard)
    "mobility": ["fault_correction", "exercise_selection_rationale"],
    "range of motion": ["fault_correction", "exercise_selection_rationale"],
    "soft tissue": ["fault_correction", "recovery_protocols"],
    "joint restriction": ["fault_correction"],
    "hip flexor": ["fault_correction", "clean_technique"],
    "hip mobility": ["fault_correction", "clean_technique", "squat_programming"],
    "ankle mobility": ["fault_correction", "squat_programming"],
    "thoracic": ["fault_correction", "snatch_technique"],
    "thoracic mobility": ["fault_correction", "snatch_technique"],
    "overhead position": ["snatch_technique", "fault_correction"],
    "bracing": ["biomechanics", "exercise_selection_rationale"],
    "spinal mechanics": ["biomechanics"],
    "prehab": ["recovery_protocols", "exercise_selection_rationale"],
    "t-spine": ["fault_correction", "snatch_technique"],
    "hip capsule": ["fault_correction", "clean_technique"],
    "dorsiflexion": ["fault_correction", "squat_programming"],
    "internal rotation": ["fault_correction"],
    "external rotation": ["fault_correction"],
    "tissue quality": ["recovery_protocols", "fault_correction"],
    "lacrosse ball": ["recovery_protocols", "fault_correction"],
    "foam roll": ["recovery_protocols", "fault_correction"],
    "position": ["biomechanics", "exercise_selection_rationale"],

    # Dan John / GPP and loaded carries (Intervention / Can You Go)
    "loaded carry": ["exercise_selection_rationale"],
    "farmer": ["exercise_selection_rationale"],
    "farmer carry": ["exercise_selection_rationale"],
    "sled": ["exercise_selection_rationale"],
    "goblet squat": ["exercise_selection_rationale", "squat_programming"],
    "kettlebell": ["exercise_selection_rationale"],
    "swing": ["exercise_selection_rationale"],
    "hinge": ["exercise_selection_rationale", "pull_programming"],
    "hip hinge": ["exercise_selection_rationale", "pull_programming"],
    "fundamental movement": ["exercise_selection_rationale"],
    "big rocks": ["exercise_selection_rationale", "periodization_models"],
    "general physical preparation": ["exercise_selection_rationale", "periodization_models"],
    "gpp": ["exercise_selection_rationale", "periodization_models"],
    "off-season": ["periodization_models", "exercise_selection_rationale"],
    "conditioning": ["exercise_selection_rationale"],
    "carry": ["exercise_selection_rationale"],
    "push-up": ["exercise_selection_rationale"],
    "get-up": ["exercise_selection_rationale"],
    "turkish get": ["exercise_selection_rationale"],
    "park bench": ["periodization_models"],
    "bus bench": ["periodization_models"],
}


# Ensures chunk_type-derived topics are always present regardless of keyword coverage.
# When pipeline.py sets chunk_type via metadata, these baseline topics are guaranteed
# even if keyword_tag() misses them (sparse content, unusual phrasing, etc.).
CHUNK_TYPE_DEFAULT_TOPICS: dict[str, list[str]] = {
    "fault_correction":      ["fault_correction"],
    "biomechanics":          ["biomechanics"],
    "competition_strategy":  ["competition_strategy"],
    "recovery_adaptation":   ["recovery_protocols", "adaptation_theory"],
    "nutrition_bodyweight":  ["nutrition_bodyweight"],
    "periodization":         ["periodization_models"],
    "programming_rationale": ["periodization_models"],
}


def keyword_tag(content: str) -> set[str]:
    """Fast keyword-based topic tagging (Pass 1)."""
    content_lower = content.lower()
    topics: set[str] = set()
    for keyword, topic_list in KEYWORD_TO_TOPIC.items():
        if keyword in content_lower:
            topics.update(topic_list)
    return topics


# ──────────────────────────────────────────────────────────────
# Chunk validation
# ──────────────────────────────────────────────────────────────

@dataclass
class ChunkValidationResult:
    chunk_index: int
    is_valid: bool
    issues: list[str]
    severity: str = "info"     # info | warning | error


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
        severity = "warning"

    # Too long — embedding signal dilution
    if chunk.token_count > 1500:
        issues.append(
            f"Chunk too long ({chunk.token_count} tokens). "
            "Consider splitting further."
        )
        severity = "warning"

    # No topics — invisible to filtered search
    if not chunk.topics:
        issues.append(
            "No topics assigned. Chunk will only appear in unfiltered similarity search."
        )
        severity = "warning"

    # Contains structured data that should have been routed to tables
    rep_scheme_count = len(
        KEEP_TOGETHER_PATTERNS["rep_scheme"].findall(chunk.raw_content)
    )
    if rep_scheme_count >= 3:
        issues.append(
            f"Contains {rep_scheme_count} rep schemes. "
            "Consider also routing to percentage_schemes table."
        )
        severity = "info"  # dual-storage is fine, just flag it

    # Contains a table
    lines = chunk.raw_content.split("\n")
    table_like = [l for l in lines if l.count("|") >= 2 or l.count("\t") >= 2]
    if len(table_like) >= 3:
        issues.append(
            "Contains what looks like a table. Consider parsing as structured data."
        )
        severity = "warning"

    # Orphaned context — chunk starts mid-sentence
    stripped = chunk.raw_content.strip()
    if stripped and stripped[0].islower():
        issues.append(
            "Starts with lowercase — likely a mid-sentence split. "
            "Check chunk boundary alignment."
        )
        severity = "warning"

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

    SECTION_BREAK_PATTERNS = [
        r"^#{1,3}\s+",                              # Markdown headers
        r"^Chapter\s+\d+",                           # Chapter markers
        r"^PART\s+[IVX]+",                           # Part markers
        r"^(?:Week|Phase|Block|Cycle)\s+\d+",        # Program phase markers
        r"^\d+\.\d+\s+[A-Z]",                        # Numbered sections
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

    @classmethod
    def for_source(cls, source_title: str) -> "SemanticChunker":
        """Factory: select the right chunking profile for a known source."""
        # Try prefix matching for sources where the title in the map
        # is a prefix of the actual title.
        for key, profile in SOURCE_PROFILE_MAP.items():
            if key.lower() in source_title.lower():
                return cls(source_profile=profile)
        return cls(source_profile=SourceProfile.PROGRAMMING_FOCUSED)

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

        return sections if sections else [{"title": "", "text": text}]

    # ── Paragraph-aware chunking with keep-together ───────────

    def _chunk_section(self, text: str) -> list[str]:
        """Chunk within a section using paragraph-aware sliding window.

        Respects keep-together patterns: if a chunk boundary would split
        a matched pattern, the chunk is extended to include the full match.
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        chunks: list[str] = []
        current_chunk = ""
        current_tokens = 0

        for para in paragraphs:
            para_tokens = self._estimate_tokens(para)

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
        for pattern in KEEP_TOGETHER_PATTERNS.values():
            match = pattern.search(boundary_zone)
            if match:
                # Check if the match spans the boundary point
                boundary_idx = len(chunk_end[-200:])
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
        """Rough token estimate. ~1.3 tokens per word for English.
        For production, replace with tiktoken or your model's tokenizer."""
        return int(len(text.split()) * 1.3)
