# Olympic Weightlifting Programming — Code Reference

All implementation code for the ingestion pipeline, organized by module.
See `oly-programming-pipeline.md` for architecture decisions, schema design,
chunking strategy, and implementation order.

See `schema.sql` for the consolidated database DDL and seed data.

---

## Project Structure

```
oly-ingestion/
├── docker-compose.yml
├── schema.sql                   # Consolidated DDL + seed data (ready to run)
├── requirements.txt
├── .env                         # API keys (not committed to git)
├── .gitignore
├── config.py
├── pipeline.py                  # Main orchestrator
├── extractors/
│   ├── __init__.py
│   ├── pdf_extractor.py         # PDF → page texts (PyMuPDF + OCR fallback)
│   ├── html_extractor.py        # HTML → clean text (uses BeautifulSoup)
│   └── epub_extractor.py        # EPUB → chapter texts (uses ebooklib)
├── processors/
│   ├── __init__.py
│   ├── chunker.py               # Semantic chunking with profiles, preambles, tagging
│   ├── classifier.py            # Content type classification and routing
│   ├── principle_extractor.py   # LLM-assisted rule extraction
│   └── ocr_corrections.py       # OCR correction dictionary for Soviet-era sources
├── loaders/
│   ├── __init__.py
│   ├── vector_loader.py         # Chunks → pgvector (with dedup via content_hash)
│   └── structured_loader.py     # Structured data → Postgres tables
├── sources/                     # Source PDFs, articles, etc. (not committed to git)
├── logs/
└── tests/
    ├── test_chunker.py
    ├── test_classifier.py
    └── test_retrieval_eval.py   # Retrieval quality evaluation queries
```

---

## config.py

```python
# config.py
"""
Pipeline configuration. Load from environment variables or .env file.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    # ── Database ──────────────────────────────────────────────
    database_url: str = ""

    # ── Embedding model ───────────────────────────────────────
    # Using OpenAI text-embedding-3-small (1536 dims).
    # At ~$0.02/1M tokens, the total cost for all source books is under $1
    # even with multiple re-embedding passes during iteration.
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # ── LLM (for principle extraction, content classification, topic tagging) ──
    llm_model: str = "claude-sonnet-4-20250514"
    llm_max_tokens: int = 4096

    # ── API keys (loaded from env vars) ───────────────────────
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Paths ─────────────────────────────────────────────────
    sources_dir: Path = Path("./sources")
    logs_dir: Path = Path("./logs")

    # ── Ingestion behavior ────────────────────────────────────
    batch_size: int = 50               # chunks per DB commit
    skip_existing_sources: bool = True  # skip if source title+author already in DB
    validate_chunks: bool = True        # run chunk validation pass before loading
    quarantine_invalid_chunks: bool = False  # if True, skip invalid chunks; if False, load with warnings

    def __post_init__(self):
        """Load sensitive values from environment variables."""
        self.database_url = self.database_url or os.getenv(
            "DATABASE_URL", "postgresql://oly:oly@localhost:5432/oly_programming"
        )
        self.openai_api_key = self.openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_api_key = self.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
```

---

## pipeline.py

```python
# pipeline.py
"""
Main ingestion pipeline orchestrator.

Usage:
    python pipeline.py --source ./sources/catalyst_athletics.pdf --title "Olympic Weightlifting" --author "Greg Everett" --type book
    python pipeline.py --source ./sources/prilepin_data.json --type structured
    python pipeline.py --source ./sources/article.html --title "Peaking for Competition" --author "Greg Everett" --type article
"""

import argparse
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from extractors.pdf_extractor import PDFExtractor
from processors.chunker import SemanticChunker, validate_chunk
from processors.classifier import ContentClassifier, ContentType
from processors.principle_extractor import PrincipleExtractor
from loaders.vector_loader import VectorLoader
from loaders.structured_loader import StructuredLoader
from config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class SourceDocument:
    path: Path
    title: str
    author: str
    doc_type: str  # 'book', 'article', 'program', 'structured'


class IngestionPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pdf_extractor = PDFExtractor()
        self.classifier = ContentClassifier(settings)
        self.principle_extractor = PrincipleExtractor(settings)
        self.vector_loader = VectorLoader(settings)
        self.structured_loader = StructuredLoader(settings)

    def ingest(self, source: SourceDocument) -> dict:
        """Full ingestion pipeline for a single source document.

        Steps:
            1. Upsert source record in DB, get source_id
            2. Extract raw text from the document
            3. Classify content sections (prose vs table vs program vs principle)
            4. Route each section to the appropriate processor
            5. Load processed data into DB
            6. Run validation and report stats
        """
        logger.info(f"Starting ingestion: {source.title} by {source.author}")

        # ── Step 1: Upsert source record ──────────────────────
        # All downstream FK references need this ID.
        source_id = self.structured_loader.upsert_source(
            title=source.title,
            author=source.author,
            source_type=source.doc_type,
        )
        if source_id is None and self.settings.skip_existing_sources:
            logger.info(f"Source already exists, skipping: {source.title}")
            return {"skipped": True}

        logger.info(f"Source record ID: {source_id}")

        stats = {
            "source_id": source_id,
            "prose_chunks": 0,
            "prose_chunks_valid": 0,
            "prose_chunks_quarantined": 0,
            "principles": 0,
            "programs": 0,
            "exercises": 0,
            "tables_parsed": 0,
        }

        # ── Step 2: Extract raw text ──────────────────────────
        if source.path.suffix == ".json":
            # Pre-structured data (e.g., manually curated Prilepin's data,
            # exercise taxonomy JSON). Load directly, skip classification.
            count = self.structured_loader.load_json(source.path, source_id)
            stats["tables_parsed"] = count
            logger.info(f"Loaded {count} structured records from JSON")
            return stats

        if source.path.suffix == ".pdf":
            pages = self.pdf_extractor.extract(source.path)
        elif source.path.suffix in (".html", ".htm"):
            # For web articles: strip HTML tags, extract body text
            from extractors.html_extractor import extract_text_from_html
            pages = [extract_text_from_html(source.path)]
        elif source.path.suffix == ".epub":
            from extractors.epub_extractor import extract_text_from_epub
            pages = extract_text_from_epub(source.path)
        else:
            # Plain text, markdown, etc.
            pages = [source.path.read_text(encoding="utf-8")]

        full_text = "\n\n".join(pages)
        logger.info(f"Extracted {len(full_text):,} characters from {source.path.name}")

        # ── Step 3: Select chunker profile for this source ────
        if source.doc_type == "article":
            word_count = len(full_text.split())
            chunker = SemanticChunker.for_web_article(word_count)
        else:
            chunker = SemanticChunker.for_source(source.title)
        logger.info(f"Using chunk profile: {chunker.source_profile.value} "
                     f"(size={chunker.chunk_size}, overlap={chunker.chunk_overlap})")

        # ── Step 4: Classify content sections ─────────────────
        sections = self.classifier.classify_sections(full_text, source.title)

        for section in sections:
            try:
                match section.content_type:

                    case ContentType.PROSE:
                        stats = self._process_prose(
                            section, chunker, source, source_id, stats
                        )

                    case ContentType.MIXED:
                        # Dual-storage: chunk into vector store AND extract principles.
                        # The prose version preserves reasoning context; the structured
                        # version makes concrete rules queryable.
                        stats = self._process_prose(
                            section, chunker, source, source_id, stats
                        )
                        principles = self.principle_extractor.extract(
                            text=section.content,
                            source_title=source.title,
                            source_id=source_id,
                        )
                        self.structured_loader.load_principles(principles, source_id)
                        stats["principles"] += len(principles)

                    case ContentType.PRINCIPLE:
                        principles = self.principle_extractor.extract(
                            text=section.content,
                            source_title=source.title,
                            source_id=source_id,
                        )
                        self.structured_loader.load_principles(principles, source_id)
                        stats["principles"] += len(principles)

                    case ContentType.PROGRAM_TEMPLATE:
                        program = self._parse_program_template(section, source, source_id)
                        self.structured_loader.load_program(program)
                        stats["programs"] += 1

                    case ContentType.TABLE:
                        rows = self._parse_table(section, source_id)
                        stats["tables_parsed"] += rows

                    case ContentType.EXERCISE_DESCRIPTION:
                        exercise = self._parse_exercise(section, source_id)
                        self.structured_loader.load_exercise(exercise)
                        stats["exercises"] += 1

            except Exception as e:
                logger.error(f"Error processing section '{section.metadata.get('title', 'unknown')}': {e}")
                continue  # don't let one bad section kill the whole ingestion

        logger.info(f"Ingestion complete: {stats}")
        return stats

    def _process_prose(self, section, chunker, source, source_id, stats):
        """Chunk prose content, validate, tag, and load into vector store."""
        chunks = chunker.chunk(
            text=section.content,
            metadata={
                "chapter": section.metadata.get("chapter", ""),
                "chunk_type": section.metadata.get("topic_category", "concept"),
            },
            source_title=source.title,
            author=source.author,
        )

        # Validate if enabled
        valid_chunks = []
        for chunk in chunks:
            if self.settings.validate_chunks:
                result = validate_chunk(chunk)
                if not result.is_valid:
                    logger.warning(
                        f"Chunk validation issues (index={result.chunk_index}): "
                        f"{'; '.join(result.issues)}"
                    )
                    if self.settings.quarantine_invalid_chunks and result.severity == "error":
                        stats["prose_chunks_quarantined"] += 1
                        continue
            valid_chunks.append(chunk)

        self.vector_loader.load_chunks(valid_chunks, source_id)
        stats["prose_chunks"] += len(chunks)
        stats["prose_chunks_valid"] += len(valid_chunks)
        return stats

    def _parse_program_template(self, section, source, source_id) -> dict:
        """Convert a detected program section into structured JSONB format.

        Uses an LLM call to convert natural language program descriptions
        (e.g., "Monday: Snatch 5x3 @ 75%") into the program_structure JSONB
        schema defined in the program_templates table.
        """
        # TODO: Implement LLM-assisted program parsing.
        # The prompt should include:
        #   1. The raw program text
        #   2. The target program_structure JSONB schema (from section 6 of this doc)
        #   3. Instructions to extract exercise_name, sets, reps, intensity_pct,
        #      intensity_reference, rest_seconds, and notes for each exercise
        #   4. Instructions to identify the phase, week structure, and progression
        return {
            "name": section.metadata.get("program_name", "Unknown Program"),
            "source_id": source_id,
            "athlete_level": section.metadata.get("athlete_level", "any"),
            "goal": section.metadata.get("goal", "general_strength"),
            "duration_weeks": section.metadata.get("duration_weeks", 0),
            "sessions_per_week": section.metadata.get("sessions_per_week", 0),
            "program_structure": section.structured_data or {},
        }

    def _parse_table(self, section, source_id) -> int:
        """Route parsed table data to the appropriate structured table.

        Tables might contain percentage schemes, volume distributions,
        or other structured data. The classifier tags each table with
        a target_table hint in metadata.
        """
        target = section.metadata.get("target_table", "percentage_schemes")
        rows = section.structured_data or []
        if not rows:
            return 0

        if target == "percentage_schemes":
            self.structured_loader.load_percentage_schemes(rows, source_id)
        elif target == "prilepin_chart":
            self.structured_loader.load_prilepin_rows(rows)
        else:
            logger.warning(f"Unknown target table for parsed table: {target}")
            return 0

        return len(rows)

    def _parse_exercise(self, section, source_id) -> dict:
        """Parse exercise description into taxonomy entry."""
        data = section.structured_data or {}
        data["source_id"] = source_id
        return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest weightlifting programming source material")
    parser.add_argument("--source", required=True, help="Path to source file (PDF, JSON, HTML, EPUB, TXT)")
    parser.add_argument("--title", required=True, help="Document title")
    parser.add_argument("--author", default="Unknown", help="Author name")
    parser.add_argument("--type", default="book",
                        choices=["book", "article", "program", "structured", "website"],
                        help="Source document type")
    args = parser.parse_args()

    settings = Settings()
    pipeline = IngestionPipeline(settings)

    doc = SourceDocument(
        path=Path(args.source),
        title=args.title,
        author=args.author,
        doc_type=args.type,
    )
    result = pipeline.ingest(doc)
    print(f"\nIngestion result: {result}")
```

---

## processors/chunker.py

```python
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
    "Managing the Training of Weightlifters": SourceProfile.DATA_HEAVY_SOVIET,
    "A System of Multi-Year Training in Weightlifting": SourceProfile.DATA_HEAVY_SOVIET,
    "Science and Practice of Strength Training": SourceProfile.THEORY_HEAVY,
    "Supertraining": SourceProfile.THEORY_HEAVY,
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
    "fault": ["fault_correction"],
    "correction": ["fault_correction"],
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
        profile = SOURCE_PROFILE_MAP.get(source_title, SourceProfile.PROGRAMMING_FOCUSED)
        return cls(source_profile=profile)

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
                candidate_boundary = current_chunk + "\n\n" + para
                if self._would_split_pattern(current_chunk, para):
                    # Extend the chunk to include this paragraph
                    current_chunk = candidate_boundary
                    current_tokens = self._estimate_tokens(current_chunk)
                    continue

                chunks.append(current_chunk)
                # Tail overlap: keep last N tokens of previous chunk
                overlap_text = self._get_overlap(current_chunk)
                current_chunk = overlap_text + "\n\n" + para
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
        # Count specific indicators
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
```

---

## processors/classifier.py

```python
# processors/classifier.py
"""
Classifies sections of source text into content types for routing.

This is the critical routing layer. Misclassification means:
- A percentage table gets chunked as prose → loses tabular structure
- Rich periodization discussion gets extracted as a principle → loses nuanced reasoning
- A program template gets chunked → loses day/week/exercise structure

The classifier uses a two-pass approach:
1. Structural heuristics (fast, cheap) — catches obvious cases
2. LLM-assisted classification (slower) — handles ambiguous / mixed content
"""

import re
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ContentType(Enum):
    PROSE = "prose"                    # → vector store (chunker)
    TABLE = "table"                    # → structured tables (table parser)
    PROGRAM_TEMPLATE = "program"       # → program_templates table
    PRINCIPLE = "principle"            # → programming_principles table
    EXERCISE_DESCRIPTION = "exercise"  # → exercises table
    MIXED = "mixed"                    # → BOTH vector store AND principle extraction


@dataclass
class ClassifiedSection:
    """A section of text with its classified content type and metadata."""
    content: str
    content_type: ContentType
    metadata: dict = field(default_factory=dict)
    structured_data: dict | None = None  # pre-parsed data for TABLE/PROGRAM types
    confidence: float = 1.0              # 0.0-1.0, how confident the classification is


class ContentClassifier:
    """Routes document sections to the appropriate processing pipeline."""

    # ── Structural patterns for heuristic classification ──────

    # Tables: lines with consistent delimiters
    TABLE_PATTERNS = [
        re.compile(r"^.*\|.*\|.*$", re.MULTILINE),           # pipe-delimited
        re.compile(r"^\s*\d+%?\s*[\t|]\s*\d+", re.MULTILINE),  # percentage tables
    ]

    # Program templates: day/week structures with exercise prescriptions
    PROGRAM_PATTERNS = [
        # "Monday:" or "Day 1:" followed by exercise lines
        re.compile(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Day\s+\d+)\s*:?\s*\n"
            r"(?:\s+.*(?:\d+\s*[xX×]\s*\d+|sets?|reps?).*\n?){2,}",
            re.MULTILINE | re.IGNORECASE,
        ),
        # "Week 1" followed by structured content
        re.compile(
            r"Week\s+\d+\s*[:\-]?\s*\n(?:\s+.*\n?){3,}",
            re.MULTILINE | re.IGNORECASE,
        ),
    ]

    # Exercise descriptions: name followed by purpose/execution description
    EXERCISE_PATTERNS = [
        re.compile(
            r"^(?:The\s+)?(?:Power|Hang|Block|Muscle|Tall|Deficit|Pause|Tempo|No[- ]Feet)?\s*"
            r"(?:Snatch|Clean|Jerk|Squat|Pull|Press|Deadlift|RDL|Push Press|Snatch Balance)"
            r"\s*(?:\(.*?\))?\s*$",
            re.MULTILINE | re.IGNORECASE,
        ),
    ]

    # Principle indicators: if/then logic, concrete thresholds
    PRINCIPLE_INDICATORS = [
        r"should\s+(?:not\s+)?exceed",
        r"never\s+(?:go|exceed|perform|do)",
        r"always\s+(?:include|perform|start|use)",
        r"(?:reduce|increase|maintain)\s+.*(?:by|to|at)\s+\d+",
        r"(?:no more than|at least|a minimum of)\s+\d+",
        r"(?:rule of thumb|general guideline|as a rule)",
    ]

    def __init__(self, settings):
        self.settings = settings

    def classify_sections(
        self, text: str, source_title: str = ""
    ) -> list[ClassifiedSection]:
        """Split text into sections and classify each one.

        Args:
            text: Full extracted text from a source document.
            source_title: Used for source-specific classification hints.

        Returns:
            List of ClassifiedSection objects routed for processing.
        """
        # Split on major structural boundaries (chapters, parts, etc.)
        raw_sections = self._split_into_sections(text)
        classified = []

        for section_text, section_meta in raw_sections:
            content_type, confidence = self._classify_single(section_text)

            # If low confidence from heuristics, fall back to LLM classification
            if confidence < 0.6 and len(section_text) > 100:
                content_type, confidence = self._llm_classify(
                    section_text, source_title
                )

            classified.append(ClassifiedSection(
                content=section_text,
                content_type=content_type,
                metadata=section_meta,
                confidence=confidence,
            ))

        logger.info(
            f"Classified {len(classified)} sections: "
            f"{sum(1 for s in classified if s.content_type == ContentType.PROSE)} prose, "
            f"{sum(1 for s in classified if s.content_type == ContentType.TABLE)} tables, "
            f"{sum(1 for s in classified if s.content_type == ContentType.PROGRAM_TEMPLATE)} programs, "
            f"{sum(1 for s in classified if s.content_type == ContentType.PRINCIPLE)} principles, "
            f"{sum(1 for s in classified if s.content_type == ContentType.MIXED)} mixed"
        )
        return classified

    def _split_into_sections(self, text: str) -> list[tuple[str, dict]]:
        """Split document into major sections based on structural markers.

        Returns list of (section_text, metadata) tuples.
        """
        # Split on chapter/section headers
        header_pattern = re.compile(
            r"^(#{1,3}\s+.+|Chapter\s+\d+.*|PART\s+[IVX]+.*|\d+\.\d+\s+[A-Z].+)$",
            re.MULTILINE,
        )

        parts = header_pattern.split(text)
        sections = []
        current_chapter = ""
        current_title = ""

        for i, part in enumerate(parts):
            if header_pattern.match(part.strip()):
                if "chapter" in part.lower():
                    current_chapter = part.strip()
                current_title = part.strip()
            elif part.strip():
                sections.append((
                    part.strip(),
                    {"chapter": current_chapter, "title": current_title},
                ))

        return sections if sections else [(text, {"chapter": "", "title": ""})]

    def _classify_single(self, text: str) -> tuple[ContentType, float]:
        """Heuristic classification of a single section."""

        # Check for table patterns
        for pattern in self.TABLE_PATTERNS:
            matches = pattern.findall(text)
            if len(matches) >= 3:  # at least 3 table-like lines
                return ContentType.TABLE, 0.85

        # Check for program template patterns
        for pattern in self.PROGRAM_PATTERNS:
            if pattern.search(text):
                return ContentType.PROGRAM_TEMPLATE, 0.80

        # Check for exercise descriptions
        for pattern in self.EXERCISE_PATTERNS:
            if pattern.search(text) and len(text) < 2000:
                return ContentType.EXERCISE_DESCRIPTION, 0.70

        # Check for principle-heavy content
        principle_matches = sum(
            1 for pattern in self.PRINCIPLE_INDICATORS
            if re.search(pattern, text, re.IGNORECASE)
        )

        # Check if mixed: has both principle indicators AND substantial prose
        has_percentages = bool(re.search(r"\d+%", text))
        word_count = len(text.split())

        if principle_matches >= 2 and word_count > 100 and has_percentages:
            return ContentType.MIXED, 0.75
        elif principle_matches >= 3:
            return ContentType.PRINCIPLE, 0.70

        # Default: prose
        return ContentType.PROSE, 0.60 if word_count < 50 else 0.80

    def _llm_classify(self, text: str, source_title: str) -> tuple[ContentType, float]:
        """LLM-assisted classification for ambiguous sections.

        TODO: Implement with actual LLM call. The prompt should:
        1. Present the text (truncated to ~1000 tokens)
        2. Ask: "Classify this section as one of: prose, table,
           program_template, principle, exercise_description, mixed"
        3. Ask for confidence (0-1)
        4. Parse the response

        For now, falls back to PROSE with low confidence.
        """
        logger.debug(f"LLM classification needed for section from '{source_title}'")
        return ContentType.PROSE, 0.50
```

---

## processors/principle_extractor.py

```python
# processors/principle_extractor.py
"""
Uses an LLM to extract structured programming principles from prose text.

This is the most valuable part of the pipeline — converting sentences like
"During the final two weeks before competition, volume should be reduced
by 40-60% while maintaining intensity above 90%" into queryable rules.
"""

import json
from dataclasses import dataclass


@dataclass
class ExtractedPrinciple:
    principle_name: str
    category: str
    rule_type: str
    condition: dict
    recommendation: dict
    rationale: str
    priority: int


EXTRACTION_PROMPT = """You are an expert Olympic weightlifting coach analyzing programming literature.

Extract structured programming principles from the following text. Each principle should be a concrete, actionable rule that a program generator could follow.

For each principle found, provide:
- principle_name: Short descriptive name
- category: One of [volume, intensity, frequency, exercise_selection, periodization, peaking, recovery, technique, load_progression, deload]
- rule_type: One of [hard_constraint, guideline, heuristic]
  - hard_constraint: Violating this would be dangerous or clearly counterproductive
  - guideline: Strongly recommended but situationally flexible
  - heuristic: Rules of thumb, useful defaults
- condition: JSON object describing WHEN this applies. Use these fields (all optional):
    - "phase": training phase string or array, e.g. "intensification" or ["intensification", "realization"]
    - "weeks_out_from_competition": comparison object, e.g. {{"lte": 2}}
    - "athlete_level": array, e.g. ["intermediate", "advanced"]
    - "training_age_years": comparison object, e.g. {{"gte": 2}}
    - "week_of_block": comparison object, e.g. {{"gte": 3}}
    - "movement_family": string, e.g. "snatch"
    - "recent_make_rate": comparison object, e.g. {{"lt": 0.7}}
    - "rpe_average_last_week": comparison object, e.g. {{"gte": 9.0}}
    Comparison operators: lte, gte, lt, gt, eq, between
- recommendation: JSON object describing WHAT to do. Use these fields (all optional):
    - "volume_modifier": float (e.g. 0.6 means reduce to 60%)
    - "total_reps_max": int (hard cap on total reps)
    - "intensity_floor": int (minimum % of 1RM)
    - "intensity_ceiling": int (maximum % of 1RM)
    - "sessions_per_week_max": int
    - "competition_lift_frequency": int or "every_session"
    - "prefer_exercises": array of exercise names
    - "avoid_exercises": array of exercise names
    - "rest_between_sets_min": int (seconds)
    - "include_deload_week": boolean
    - "deload_frequency_weeks": int
    - "competition_lifts_first": boolean
- rationale: Brief explanation of WHY this rule exists
- priority: 1-10 (10 = most critical, use 10 only for safety constraints)

Respond with a JSON array of principles. If no clear principles are found, return [].

TEXT TO ANALYZE:
{text}

SOURCE: {source}

Respond ONLY with valid JSON array, no other text."""


class PrincipleExtractor:
    def __init__(self, settings):
        self.settings = settings
        # Initialize your LLM client here (OpenAI, Anthropic, local model)
        # self.client = ...

    def extract(self, text: str, source_title: str, source_id: int) -> list[ExtractedPrinciple]:
        """Extract structured principles from prose text using LLM."""
        prompt = EXTRACTION_PROMPT.format(text=text, source=source_title)

        # Replace with your actual LLM call
        # response = self.client.chat.completions.create(
        #     model="gpt-4o" or "claude-sonnet-4-20250514",
        #     messages=[{"role": "user", "content": prompt}],
        #     response_format={"type": "json_object"},
        # )
        # raw = json.loads(response.choices[0].message.content)

        # Placeholder — in production this comes from the LLM
        raw = []

        principles = []
        for item in raw:
            try:
                principles.append(ExtractedPrinciple(**item))
            except (TypeError, KeyError) as e:
                print(f"Skipping malformed principle: {e}")

        return principles
```

---

## loaders/vector_loader.py

```python
# loaders/vector_loader.py
"""
Loads processed chunks into pgvector with embeddings.

Handles:
- Embedding generation (pluggable: OpenAI, local, etc.)
- Batch inserts with configurable commit size
- Deduplication: skips chunks whose content hash already exists
- Similarity search with pre-filtering for the downstream agent
"""

import hashlib
import logging
from typing import Any

import psycopg2
from pgvector.psycopg2 import register_vector

from processors.chunker import Chunk

logger = logging.getLogger(__name__)


class VectorLoader:
    # OpenAI allows up to 2048 texts per embedding call.
    # We use a smaller batch to avoid context length issues with long chunks.
    EMBED_BATCH_SIZE = 100

    def __init__(self, settings):
        self.settings = settings
        self.conn = psycopg2.connect(settings.database_url)
        register_vector(self.conn)
        self.batch_size = settings.batch_size

        # OpenAI embedding client
        from openai import OpenAI
        if not settings.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required for embeddings. "
                "Set it in .env or pass via environment variable."
            )
        self.embed_client = OpenAI(api_key=settings.openai_api_key)

    def load_chunks(self, chunks: list[Chunk], source_id: int) -> int:
        """Embed and store chunks in pgvector.

        Uses batch embedding (multiple texts per API call) for efficiency.
        A 300-page book produces ~200-300 chunks, so 2-3 API calls total
        instead of 200-300 individual calls.

        Args:
            chunks: Processed Chunk objects from the SemanticChunker.
            source_id: FK to the sources table.

        Returns:
            Number of chunks loaded (excludes duplicates).
        """
        cursor = self.conn.cursor()
        loaded = 0
        skipped = 0

        # Step 1: Filter out duplicates before hitting the embedding API.
        # This avoids paying for embeddings we'll throw away.
        new_chunks: list[tuple[Chunk, str]] = []  # (chunk, content_hash)
        for chunk in chunks:
            content_hash = hashlib.sha256(chunk.raw_content.encode()).hexdigest()
            cursor.execute(
                "SELECT 1 FROM knowledge_chunks WHERE content_hash = %s",
                (content_hash,),
            )
            if cursor.fetchone():
                skipped += 1
            else:
                new_chunks.append((chunk, content_hash))

        if skipped:
            logger.info(f"  Skipped {skipped} duplicate chunks (pre-embedding filter)")

        if not new_chunks:
            logger.info("  No new chunks to embed")
            cursor.close()
            return 0

        # Step 2: Batch embed all new chunks.
        # Embed the FULL content (with preamble) for better retrieval.
        texts = [chunk.content for chunk, _ in new_chunks]
        all_embeddings = self._embed_batch(texts)

        # Step 3: Insert chunks with their embeddings.
        for (chunk, content_hash), embedding in zip(new_chunks, all_embeddings):
            cursor.execute(
                """
                INSERT INTO knowledge_chunks
                    (content, raw_content, content_hash, embedding,
                     source_id, chapter, section,
                     chunk_type, topics, athlete_level_relevance,
                     information_density, contains_specific_numbers)
                VALUES (%s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s)
                """,
                (
                    chunk.content,
                    chunk.raw_content,
                    content_hash,
                    embedding,
                    source_id,
                    chunk.metadata.get("chapter", ""),
                    chunk.metadata.get("section_title", ""),
                    chunk.metadata.get("chunk_type", "concept"),
                    chunk.topics or [],
                    chunk.metadata.get("athlete_level_relevance"),
                    chunk.information_density,
                    chunk.contains_specific_numbers,
                ),
            )
            loaded += 1

            if loaded % self.batch_size == 0:
                self.conn.commit()
                logger.info(f"  Committed batch: {loaded} chunks loaded so far")

        self.conn.commit()
        cursor.close()
        logger.info(f"  Loaded {loaded} chunks into knowledge_chunks")
        return loaded

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embed multiple texts in as few API calls as possible.

        OpenAI's embedding API accepts multiple texts per call (up to 2048).
        For 200 chunks, this means 2 API calls instead of 200.
        Includes basic retry logic for rate limits.
        """
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.EMBED_BATCH_SIZE):
            batch = texts[i : i + self.EMBED_BATCH_SIZE]
            logger.info(
                f"  Embedding batch {i // self.EMBED_BATCH_SIZE + 1} "
                f"({len(batch)} texts)"
            )

            # Retry with exponential backoff for rate limits
            for attempt in range(3):
                try:
                    response = self.embed_client.embeddings.create(
                        model=self.settings.embedding_model,
                        input=batch,
                    )
                    batch_embeddings = [item.embedding for item in response.data]
                    all_embeddings.extend(batch_embeddings)
                    break
                except Exception as e:
                    if attempt < 2 and "rate" in str(e).lower():
                        wait = 2 ** attempt
                        logger.warning(f"  Rate limited, retrying in {wait}s...")
                        import time
                        time.sleep(wait)
                    else:
                        raise

        return all_embeddings

    def _embed(self, text: str) -> list[float]:
        """Embed a single text. Used for query-time similarity search."""
        response = self.embed_client.embeddings.create(
            model=self.settings.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    def similarity_search(
        self,
        query: str,
        top_k: int = 5,
        chunk_types: list[str] | None = None,
        topics: list[str] | None = None,
        athlete_level: str | None = None,
        min_density: str | None = None,
        require_numbers: bool = False,
    ) -> list[dict[str, Any]]:
        """Retrieve similar chunks with optional pre-filtering.

        Used downstream by the programming agent. Supports filtered
        similarity search: filter by metadata first, then rank by
        vector similarity within the filtered set.
        """
        query_embedding = self._embed(query)
        cursor = self.conn.cursor()

        where_clauses = []
        params: list[Any] = []

        if chunk_types:
            where_clauses.append("chunk_type = ANY(%s)")
            params.append(chunk_types)

        if topics:
            where_clauses.append("topics && %s")  # array overlap operator
            params.append(topics)

        if athlete_level:
            where_clauses.append(
                "(athlete_level_relevance IS NULL "
                "OR athlete_level_relevance IN ('all', %s))"
            )
            params.append(athlete_level)

        if min_density:
            density_order = {"low": 0, "medium": 1, "high": 2}
            min_val = density_order.get(min_density, 0)
            allowed = [k for k, v in density_order.items() if v >= min_val]
            where_clauses.append("information_density = ANY(%s)")
            params.append(allowed)

        if require_numbers:
            where_clauses.append("contains_specific_numbers = TRUE")

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        cursor.execute(
            f"""
            SELECT content, raw_content, chapter, section,
                   chunk_type, topics, information_density,
                   source_id,
                   1 - (embedding <=> %s) AS similarity
            FROM knowledge_chunks
            WHERE {where_sql}
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            [query_embedding, *params, query_embedding, top_k],
        )

        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return results
```

---

## loaders/structured_loader.py

```python
# loaders/structured_loader.py
"""
Loads structured data into Postgres tables.

Handles: sources, exercises, percentage_schemes, programming_principles,
program_templates, exercise_substitutions, exercise_complexes.
"""

import json
import logging
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

logger = logging.getLogger(__name__)


class StructuredLoader:
    def __init__(self, settings):
        self.conn = psycopg2.connect(settings.database_url)

    # ── Sources ───────────────────────────────────────────────

    def upsert_source(self, title: str, author: str, source_type: str) -> int | None:
        """Insert or retrieve a source record. Returns the source ID.

        Returns None if the source already exists and skip_existing is implied
        by the caller. Otherwise returns the existing or new ID.
        """
        cursor = self.conn.cursor()

        # Check if source already exists
        cursor.execute(
            "SELECT id FROM sources WHERE title = %s AND author = %s",
            (title, author),
        )
        existing = cursor.fetchone()
        if existing:
            cursor.close()
            return existing[0]

        # Map doc_type string to source_type enum
        type_map = {
            "book": "book",
            "article": "article",
            "program": "manual",
            "structured": "manual",
            "website": "website",
        }
        db_type = type_map.get(source_type, "book")

        cursor.execute(
            """
            INSERT INTO sources (title, author, source_type)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (title, author, db_type),
        )
        source_id = cursor.fetchone()[0]
        self.conn.commit()
        cursor.close()
        return source_id

    # ── Principles ────────────────────────────────────────────

    def load_principles(self, principles: list, source_id: int) -> int:
        """Load extracted principles into programming_principles table."""
        cursor = self.conn.cursor()
        loaded = 0

        for p in principles:
            try:
                cursor.execute(
                    """
                    INSERT INTO programming_principles
                        (principle_name, source_id, category, rule_type,
                         condition, recommendation, rationale, priority)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        p.principle_name,
                        source_id,
                        p.category,
                        p.rule_type,
                        Json(p.condition),
                        Json(p.recommendation),
                        p.rationale,
                        p.priority,
                    ),
                )
                loaded += 1
            except Exception as e:
                logger.error(f"Failed to load principle '{p.principle_name}': {e}")
                self.conn.rollback()
                continue

        self.conn.commit()
        cursor.close()
        logger.info(f"  Loaded {loaded} principles")
        return loaded

    # ── Programs ──────────────────────────────────────────────

    def load_program(self, program: dict) -> int | None:
        """Load a program template into program_templates table."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO program_templates
                    (name, source_id, athlete_level, goal,
                     duration_weeks, sessions_per_week, program_structure)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    program["name"],
                    program["source_id"],
                    program.get("athlete_level", "any"),
                    program.get("goal", "general_strength"),
                    program.get("duration_weeks", 0),
                    program.get("sessions_per_week", 0),
                    Json(program["program_structure"]),
                ),
            )
            program_id = cursor.fetchone()[0]
            self.conn.commit()
            cursor.close()
            logger.info(f"  Loaded program template: {program['name']} (id={program_id})")
            return program_id
        except Exception as e:
            logger.error(f"Failed to load program '{program.get('name')}': {e}")
            self.conn.rollback()
            cursor.close()
            return None

    # ── Exercises ─────────────────────────────────────────────

    def load_exercise(self, exercise: dict) -> int | None:
        """Load an exercise into the exercises table."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO exercises
                    (name, category, movement_family, primary_purpose,
                     faults_addressed, source_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                    SET primary_purpose = EXCLUDED.primary_purpose,
                        faults_addressed = EXCLUDED.faults_addressed
                RETURNING id
                """,
                (
                    exercise["name"],
                    exercise.get("category", "competition_variant"),
                    exercise.get("movement_family", "snatch"),
                    exercise.get("primary_purpose", ""),
                    exercise.get("faults_addressed", []),
                    exercise.get("source_id"),
                ),
            )
            exercise_id = cursor.fetchone()[0]
            self.conn.commit()
            cursor.close()
            return exercise_id
        except Exception as e:
            logger.error(f"Failed to load exercise '{exercise.get('name')}': {e}")
            self.conn.rollback()
            cursor.close()
            return None

    # ── Percentage Schemes ────────────────────────────────────

    def load_percentage_schemes(self, rows: list[dict], source_id: int) -> int:
        """Load percentage scheme rows parsed from tables."""
        cursor = self.conn.cursor()
        loaded = 0
        for row in rows:
            try:
                cursor.execute(
                    """
                    INSERT INTO percentage_schemes
                        (scheme_name, source_id, phase, week_number, day_number,
                         exercise_order, sets, reps, intensity_pct, intensity_reference)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        row.get("scheme_name", "Unknown"),
                        source_id,
                        row.get("phase", "accumulation"),
                        row.get("week_number", 1),
                        row.get("day_number", 1),
                        row.get("exercise_order", 1),
                        row["sets"],
                        row["reps"],
                        row["intensity_pct"],
                        row.get("intensity_reference", "competition_lift"),
                    ),
                )
                loaded += 1
            except Exception as e:
                logger.error(f"Failed to load percentage scheme row: {e}")
                self.conn.rollback()
                continue
        self.conn.commit()
        cursor.close()
        return loaded

    # ── JSON import (for pre-structured seed data) ────────────

    def load_json(self, path: Path, source_id: int) -> int:
        """Load pre-structured JSON data.

        Expected JSON format:
        {
            "target_table": "exercises" | "percentage_schemes" | "prilepin_chart",
            "records": [ { ... }, { ... } ]
        }
        """
        with open(path) as f:
            data = json.load(f)

        target = data.get("target_table", "")
        records = data.get("records", [])

        if target == "exercises":
            for rec in records:
                rec["source_id"] = source_id
                self.load_exercise(rec)
        elif target == "percentage_schemes":
            self.load_percentage_schemes(records, source_id)
        else:
            logger.warning(f"Unknown target_table in JSON: {target}")
            return 0

        return len(records)

    def close(self):
        self.conn.close()
```

---

## extractors/pdf_extractor.py

```python
# extractors/pdf_extractor.py
"""
Extracts text from PDF files.

Uses PyMuPDF (fitz) as the primary extractor. Falls back to pdfplumber
for scanned PDFs that need layout-aware extraction. OCR via pytesseract
is available as a last resort for image-only PDFs.

Most weightlifting programming books are text-based PDFs, so the
PyMuPDF path handles the majority of cases.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PDFExtractor:
    """Extract text from PDFs with fallback chain."""

    def extract(self, path: Path) -> list[str]:
        """Extract text from a PDF, returning a list of page texts.

        Falls back through extraction methods if the primary method
        yields very little text (suggesting a scanned/image PDF).
        """
        pages = self._extract_with_pymupdf(path)

        # If PyMuPDF extracted very little text, try pdfplumber
        total_chars = sum(len(p) for p in pages)
        if total_chars < 100 and len(pages) > 0:
            logger.warning(
                f"PyMuPDF extracted only {total_chars} chars from {len(pages)} pages. "
                "Trying pdfplumber..."
            )
            pages = self._extract_with_pdfplumber(path)

        # If still very little text, this might be a scanned PDF
        total_chars = sum(len(p) for p in pages)
        if total_chars < 100 and len(pages) > 0:
            logger.warning(
                f"pdfplumber also extracted only {total_chars} chars. "
                "This may be a scanned PDF requiring OCR. "
                "Run with --ocr flag or pre-process with pytesseract."
            )

        logger.info(f"Extracted {len(pages)} pages, {total_chars:,} total characters")
        return pages

    @staticmethod
    def _extract_with_pymupdf(path: Path) -> list[str]:
        """Primary extraction using PyMuPDF (fast, handles most PDFs)."""
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
        doc.close()
        return pages

    @staticmethod
    def _extract_with_pdfplumber(path: Path) -> list[str]:
        """Fallback extraction using pdfplumber (better for complex layouts)."""
        import pdfplumber

        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text)
        return pages
```

---

## Infrastructure

### docker-compose.yml

```yaml
# docker-compose.yml
# Run: docker compose up -d
# Connect: postgresql://oly:oly@localhost:5432/oly_programming

services:
  db:
    image: pgvector/pgvector:pg16
    container_name: oly-postgres
    environment:
      POSTGRES_USER: oly
      POSTGRES_PASSWORD: oly
      POSTGRES_DB: oly_programming
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U oly -d oly_programming"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

### requirements.txt

```txt
# requirements.txt

# Database
psycopg2-binary>=2.9.9
pgvector>=0.2.4

# PDF extraction
PyMuPDF>=1.23.0
pdfplumber>=0.10.0

# Embeddings + LLM
openai>=1.12.0              # text-embedding-3-small (embeddings) + optional GPT fallback
anthropic>=0.25.0            # Claude for principle extraction & classification

# HTML extraction (for web articles)
beautifulsoup4>=4.12.0
lxml>=5.0.0

# EPUB extraction
# ebooklib>=0.18             # uncomment if ingesting EPUB sources

# Utilities
python-dotenv>=1.0.0         # for .env file loading
```

### .env

```bash
```bash
# .env — DO NOT COMMIT TO GIT
DATABASE_URL=postgresql://oly:oly@localhost:5432/oly_programming
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### .gitignore

```gitignore
# API keys and secrets
.env

# Source material (PDFs, EPUBs — not committed due to copyright and size)
sources/

# Pipeline outputs and logs
logs/
*.log

# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.venv/
venv/

# Docker volumes
pgdata/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db
```
