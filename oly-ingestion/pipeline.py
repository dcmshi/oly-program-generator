# pipeline.py
"""
Main ingestion pipeline orchestrator.

Usage:
    python pipeline.py --source ./sources/catalyst_athletics.pdf --title "Olympic Weightlifting" --author "Greg Everett" --type book
    python pipeline.py --source ./sources/prilepin_data.json --type structured
    python pipeline.py --source ./sources/article.html --title "Peaking for Competition" --author "Greg Everett" --type article
"""

import argparse
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
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


# Maps chunk_type enum values to keyword signals used to infer them from section content.
# Checked in order — first match wins. Extend this dict when adding new sources or
# noticing systematic mis-classification in the retrieval eval.
# Mirrors the pattern of KEYWORD_TO_TOPIC in processors/chunker.py.
# Movement family inference for exercise descriptions.
# Ordered list of (trigger_keywords, family_name) — first match wins.
# "snatch" is checked before "pull" so "Snatch Pull" maps to the snatch family.
EXERCISE_FAMILY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("snatch",), "snatch"),
    (("clean",), "clean"),
    (("jerk",), "jerk"),
    (("squat",), "squat"),
    (("press", "push press"), "press"),
    (("pull", "deadlift", "rdl"), "pull"),
]
EXERCISE_FAMILY_DEFAULT = "accessory"

# Name modifiers that mark an exercise as a variation of a competition lift.
EXERCISE_VARIATION_MODIFIERS: frozenset[str] = frozenset({
    "power", "muscle", "tall", "hang", "block",
    "pause", "deficit", "tempo", "no-feet", "no feet",
})

# Families that map to category="strength" rather than "competition_variant".
EXERCISE_STRENGTH_FAMILIES: frozenset[str] = frozenset({"squat", "press", "pull"})

# Top-level keys returned by the LLM for _parse_program_template that are
# stored as dedicated columns rather than inside program_structure JSONB.
PROGRAM_TEMPLATE_COLUMN_KEYS: frozenset[str] = frozenset({
    "athlete_level", "goal", "duration_weeks", "sessions_per_week",
})

CHUNK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "fault_correction": [
        "fault", "error", "correction", "miss", "common mistake",
    ],
    "biomechanics": [
        "biomech", "anatomy", "physiology", "mechanics",
        "receiving position", "bar path", "muscle activation",
    ],
    "competition_strategy": [
        # Require specific competition-context phrases — "competition" alone appears
        # in almost every weightlifting chapter ("the competition lifts")
        "competition preparation", "competition day", "competition strategy",
        "meet preparation", "attempt selection", "opener", "warm-up room",
    ],
    "recovery_adaptation": [
        "recovery", "adaptation", "sleep", "rest period", "restoration",
        "overtraining", "supercompensation",
    ],
    "nutrition_bodyweight": [
        "nutrition", "weight class", "body weight", "diet", "making weight",
        "hydration", "caloric",
    ],
    "periodization": [
        "periodization", "program design", "mesocycle", "macrocycle",
        "annual plan", "training block", "training cycle",
    ],
    "programming_rationale": [
        "rationale", "reasoning", "because", "in order to",
    ],
}


_PROGRAM_PARSE_PROMPT = """\
You are parsing a weightlifting program template extracted from a coaching book.

Convert the following raw program text into structured JSON with this schema:
{{
  "duration_weeks": <int, 0 if unknown>,
  "sessions_per_week": <int, 0 if unknown>,
  "athlete_level": "<beginner|intermediate|advanced|elite|any>",
  "goal": "<general_strength|competition_prep|technique|accumulation|intensification>",
  "weeks": [
    {{
      "week_number": <int>,
      "sessions": [
        {{
          "day": "<Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Day N>",
          "exercises": [
            {{
              "name": "<exercise name>",
              "sets": <int>,
              "reps": <int or "1-3" range string>,
              "intensity_pct": <int, omit if not specified>,
              "notes": "<optional notes>"
            }}
          ]
        }}
      ]
    }}
  ]
}}

Rules:
- Only include weeks/sessions/exercises explicitly in the text.
- Use 0 for duration_weeks/sessions_per_week if not stated.
- If the text is not a parseable program, respond with {{}}.

TEXT:
{text}

Respond ONLY with valid JSON, no other text."""


@dataclass
class SourceDocument:
    path: Path
    title: str
    author: str
    doc_type: str  # 'book', 'article', 'program', 'structured'


class IngestionPipeline:
    def __init__(self, settings: Settings, use_vision: bool = False, max_pages: int = 0):
        self.settings = settings
        self.max_pages = max_pages
        # Build Anthropic client for vision OCR fallback (opt-in via --vision flag)
        _anthropic_client = None
        if use_vision and settings.anthropic_api_key:
            import anthropic
            _anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.pdf_extractor = PDFExtractor(anthropic_client=_anthropic_client)
        self.classifier = ContentClassifier(settings)
        self.principle_extractor = PrincipleExtractor(settings)
        self.vector_loader = VectorLoader(settings)
        self.structured_loader = StructuredLoader(settings)

    def ingest(self, source: SourceDocument) -> dict:
        """Full ingestion pipeline for a single source document.

        Steps:
            1. Upsert source record in DB, get source_id
            2. Hash the source file; check for a resumable failed run
            3. Create an ingestion_runs row (or resume the existing one)
            4. Extract raw text from the document
            5. Classify content sections and route to processors
            6. Load processed data into DB
            7. Mark run complete (or failed on exception)
        """
        logger.info(f"Starting ingestion: {source.title} by {source.author}")

        # ── Step 1: Upsert source record ──────────────────────
        source_id = self.structured_loader.upsert_source(
            title=source.title,
            author=source.author,
            source_type=source.doc_type,
        )
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

        # ── Step 2: Hash the file for change detection / resumability ──
        file_hash = self._hash_file(source.path)
        config_snapshot = {
            "embedding_model": self.settings.embedding_model,
            "llm_model": self.settings.llm_model,
            "batch_size": self.settings.batch_size,
            "validate_chunks": self.settings.validate_chunks,
        }

        # Check for a resumable failed run
        run_id = self.structured_loader.find_resumable_run(file_hash)
        if run_id:
            logger.info(f"Resuming failed run #{run_id}")
            self.structured_loader.update_run_status(run_id, "started")
        else:
            run_id = self.structured_loader.create_run(
                source_id=source_id,
                file_path=str(source.path),
                file_hash=file_hash,
                config_snapshot=config_snapshot,
            )

        try:
            # ── Step 3: Extract raw text ──────────────────────
            if source.path.suffix == ".json":
                count = self.structured_loader.load_json(source.path, source_id)
                stats["tables_parsed"] = count
                logger.info(f"Loaded {count} structured records from JSON")
                self.structured_loader.complete_run(run_id, stats)
                return stats

            if source.path.suffix == ".pdf":
                pages = self.pdf_extractor.extract(source.path, max_pages=self.max_pages)
            elif source.path.suffix in (".html", ".htm"):
                from extractors.html_extractor import extract_text_from_html
                pages = [extract_text_from_html(source.path)]
            elif source.path.suffix == ".epub":
                from extractors.epub_extractor import extract_text_from_epub
                pages = extract_text_from_epub(source.path)
            else:
                pages = [source.path.read_text(encoding="utf-8")]

            total_chars = sum(len(p) for p in pages)
            logger.info(
                f"Extracted {len(pages)} page(s) / {total_chars:,} characters "
                f"from {source.path.name}"
            )

            # Update run with page count
            self.structured_loader.update_run_progress(
                run_id, pages_processed=0, last_processed_page=0
            )

            # ── Step 4: Select chunker profile ────────────────
            if source.doc_type == "article":
                word_count = sum(len(p.split()) for p in pages)
                chunker = SemanticChunker.for_web_article(word_count)
            else:
                chunker = SemanticChunker.for_source(source.title)
            logger.info(
                f"Using chunk profile: {chunker.source_profile.value} "
                f"(size={chunker.chunk_size}, overlap={chunker.chunk_overlap})"
            )

            # ── Step 5: Classify and route sections ───────────
            # Process each page/chapter individually to avoid creating oversized
            # text blobs that defeat chunking (critical for EPUB/multi-chapter sources).
            all_sections = []
            for page_text in pages:
                page_sections = self.classifier.classify_sections(page_text, source.title)
                all_sections.extend(page_sections)

            logger.info(f"Classified {len(all_sections)} sections across {len(pages)} page(s)")

            for i, section in enumerate(all_sections):
                try:
                    match section.content_type:

                        case ContentType.PROSE:
                            stats = self._process_prose(
                                section, chunker, source, source_id, stats, run_id
                            )

                        case ContentType.MIXED:
                            stats = self._process_prose(
                                section, chunker, source, source_id, stats, run_id
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

                    # Checkpoint progress every 10 sections
                    if i % 10 == 0:
                        self.structured_loader.update_run_progress(
                            run_id, pages_processed=i, last_processed_page=i
                        )

                except Exception as e:
                    logger.error(
                        f"Error processing section '{section.metadata.get('title', 'unknown')}': {e}"
                    )
                    self._rollback_connections()
                    continue

            # ── Step 6: Mark complete ──────────────────────────
            self.structured_loader.complete_run(run_id, stats)
            logger.info(f"Ingestion complete: {stats}")
            return stats

        except Exception as e:
            import traceback
            self.structured_loader.fail_run(
                run_id,
                error_message=str(e),
                error_details={"traceback": traceback.format_exc()},
            )
            raise

    @staticmethod
    def _hash_file(path: Path) -> str | None:
        """SHA-256 hash of a file for change detection. Returns None if file unreadable."""
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None

    @staticmethod
    def _infer_chunk_type(section) -> str:
        """Map classifier ContentType + section title/content to a chunk_type enum value.

        Checks section title first, then falls back to a content keyword scan using
        CHUNK_TYPE_KEYWORDS. EPUB chapters often have empty titles, so content-based
        inference is the common path. First match in CHUNK_TYPE_KEYWORDS wins.
        """
        title = (section.metadata.get("title") or "").lower()
        # Use first 800 chars of content when title is empty
        probe = title or section.content[:800].lower()

        for chunk_type, keywords in CHUNK_TYPE_KEYWORDS.items():
            if any(kw in probe for kw in keywords):
                return chunk_type

        # ContentType.MIXED means it has both prose and rules — label accordingly
        if section.content_type.value == "mixed":
            return "programming_rationale"

        return "concept"

    def _rollback_connections(self) -> None:
        """Roll back both loader connections after a section-level error.

        Keeps the pipeline alive so the next section can proceed on a clean
        transaction state. Logs at DEBUG level if rollback itself fails.
        """
        try:
            self.vector_loader.conn.rollback()
            self.structured_loader.conn.rollback()
        except Exception as rb_err:
            logger.debug(f"Rollback failed (non-fatal): {rb_err}")

    def _process_prose(self, section, chunker, source, source_id, stats, run_id=None):
        """Chunk prose content, validate, tag, and load into vector store."""
        chunks = chunker.chunk(
            text=section.content,
            metadata={
                "chapter": section.metadata.get("chapter", ""),
                "chunk_type": self._infer_chunk_type(section),
            },
            source_title=source.title,
            author=source.author,
        )

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

        loaded = self.vector_loader.load_chunks(
            valid_chunks, source_id,
            run_id=run_id,
            structured_loader=self.structured_loader,
        )
        stats["prose_chunks"] += len(chunks)
        stats["prose_chunks_valid"] += len(valid_chunks)
        stats["chunks_loaded"] = stats.get("chunks_loaded", 0) + loaded
        return stats

    def _parse_program_template(self, section, source, source_id) -> dict:
        """Convert a detected program section into structured JSONB format via LLM."""
        prompt = _PROGRAM_PARSE_PROMPT.format(text=section.content[:6000])
        parsed = {}
        try:
            client = self.principle_extractor._get_client()
            message = client.messages.create(
                model=self.settings.llm_model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
        except Exception as e:
            logger.warning(f"Program template parsing failed for '{source.title}': {e}")

        program_structure = {k: v for k, v in parsed.items() if k not in PROGRAM_TEMPLATE_COLUMN_KEYS}

        return {
            "name": section.metadata.get("program_name", f"Program from {source.title}"),
            "source_id": source_id,
            "athlete_level": parsed.get("athlete_level", section.metadata.get("athlete_level", "any")),
            "goal": parsed.get("goal", section.metadata.get("goal", "general_strength")),
            "duration_weeks": parsed.get("duration_weeks", section.metadata.get("duration_weeks", 0)),
            "sessions_per_week": parsed.get("sessions_per_week", section.metadata.get("sessions_per_week", 0)),
            "program_structure": program_structure or {},
        }

    def _parse_table(self, section, source_id) -> int:
        """Route parsed table data to the appropriate structured table."""
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
        """Parse exercise description into taxonomy entry using heuristics.

        Extracts the exercise name from the section title or a standalone name
        line in the content, then infers movement_family and category from
        standard weightlifting naming conventions.
        """
        # Try title first (set by section splitter for markdown / numbered headings)
        name = re.sub(r"^The\s+", "", section.metadata.get("title", ""), flags=re.IGNORECASE).strip()

        if not name:
            # Fall back: find the exercise name as a standalone line in content
            match = re.search(
                r"^(?:The\s+)?(?:Power|Hang|Block|Muscle|Tall|Deficit|Pause|Tempo|No[- ]Feet)?\s*"
                r"(?:Snatch|Clean|Jerk|Squat|Pull|Press|Deadlift|RDL|Push Press|Snatch Balance)"
                r"\s*(?:\(.*?\))?\s*$",
                section.content,
                re.MULTILINE | re.IGNORECASE,
            )
            name = match.group(0).strip() if match else ""

        if not name:
            return {}

        name_lower = name.lower()

        # Infer movement family — first match in EXERCISE_FAMILY_KEYWORDS wins
        family = EXERCISE_FAMILY_DEFAULT
        for keywords, fam in EXERCISE_FAMILY_KEYWORDS:
            if any(kw in name_lower for kw in keywords):
                family = fam
                break

        # Infer category from name modifiers and family
        if any(kw in name_lower for kw in EXERCISE_VARIATION_MODIFIERS):
            category = "variation"
        elif family in EXERCISE_STRENGTH_FAMILIES:
            category = "strength"
        else:
            category = "competition_variant"

        # First sentence of the description as primary_purpose
        content = section.content.strip()
        first_sentence = content.split(".")[0][:400].strip() if content else ""

        return {
            "name": name,
            "category": category,
            "movement_family": family,
            "primary_purpose": first_sentence,
            "faults_addressed": [],
            "source_id": source_id,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest weightlifting programming source material")
    parser.add_argument("--source", required=True, help="Path to source file (PDF, JSON, HTML, EPUB, TXT)")
    parser.add_argument("--title", required=True, help="Document title")
    parser.add_argument("--author", default="Unknown", help="Author name")
    parser.add_argument("--type", default="book",
                        choices=["book", "article", "program", "structured", "website"],
                        help="Source document type")
    parser.add_argument("--vision", action="store_true",
                        help="Enable Claude vision API as OCR fallback for image-only PDFs")
    parser.add_argument("--max-pages", type=int, default=0, metavar="N",
                        help="Only process the first N pages (useful for test runs)")
    args = parser.parse_args()

    settings = Settings()
    pipeline = IngestionPipeline(settings, use_vision=args.vision, max_pages=args.max_pages)

    doc = SourceDocument(
        path=Path(args.source),
        title=args.title,
        author=args.author,
        doc_type=args.type,
    )
    result = pipeline.ingest(doc)
    print(f"\nIngestion result: {result}")
