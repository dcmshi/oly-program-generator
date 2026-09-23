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

import logging
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root for shared.*
from processors.sectioning import MARKDOWN_HEADING_RE_SRC, merge_small_sections, split_oversized_sections
from shared.constants import CLASSIFIER_LLM_MAX_TOKENS, JEV_CLASSIFY_MIN_CONFIDENCE
from shared.llm import (
    create_llm_client,
    create_message_with_retries,
    json_schema_kwargs,
    light_model_for,
    message_text,
    parse_llm_json,
    thinking_kwargs,
)

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

    # `classifier="jev"` (pipeline --classifier jev, JEV-1c): one calibrated
    # Choice per section from Jev instead of heuristics + an LLM fallback. On
    # 119 real sections it agreed with the heuristic on 101 and a Sonnet 5
    # adjudication sided with Jev 13:4 on the rest. Below JEV_CLASSIFY_MIN_CONFIDENCE
    # the heuristic answer stands.
    def __init__(self, settings, classifier: str = "heuristic"):
        if classifier not in ("heuristic", "jev"):
            raise ValueError(f"classifier must be 'heuristic' or 'jev', got {classifier!r}")
        self.settings = settings
        self.classifier = classifier

    _JEV_TYPES = {
        "prose": "general explanation, rationale, narrative, theory — no actionable rules",
        "principle": "concrete if/then rules, thresholds or prescriptions (e.g. 'reduce volume by X% when …')",
        "mixed": "substantial prose AND concrete programming rules/thresholds together",
        "table": "percentage tables, rep/set schemes, structured numeric data",
        "program_template": "a day/week training schedule with exercises and sets/reps",
        "exercise_description": "how to perform a specific exercise",
    }
    _JEV_TO_TYPE = {
        "prose": ContentType.PROSE, "principle": ContentType.PRINCIPLE, "mixed": ContentType.MIXED,
        "table": ContentType.TABLE, "program_template": ContentType.PROGRAM_TEMPLATE,
        "exercise_description": ContentType.EXERCISE_DESCRIPTION,
    }

    def _jev_classify(self, sections: list[str]) -> dict[int, tuple[ContentType, float]]:
        """{index: (type, confidence)} for every section Jev answered."""
        import asyncio

        from typesafe_sdk import AsyncTypeSafeClient, Choice

        question = Choice(instructions="Classify this section of a weightlifting coaching book into exactly one content type.",
                          criteria=self._JEV_TYPES)

        async def run() -> dict[int, tuple[ContentType, float]]:
            out: dict[int, tuple[ContentType, float]] = {}
            sem = asyncio.Semaphore(8)
            async with AsyncTypeSafeClient() as client:
                async def one(i: int, text: str) -> None:
                    async with sem:
                        try:
                            r = await client.system_one(state={"section": text[:3000]}, questions={"type": question})
                        except Exception as e:
                            logger.warning(f"Jev classification failed for section {i}: {type(e).__name__}: {e}")
                            return
                    answer = r.choices["type"]
                    out[i] = (self._JEV_TO_TYPE[str(answer.choice)], float(answer.confidence))
                await asyncio.gather(*(one(i, t) for i, t in enumerate(sections)))
            return out

        return asyncio.run(run())

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
        raw_sections = self._split_into_sections(text)
        classified = []
        jev = self._jev_classify([t for t, _m in raw_sections]) if self.classifier == "jev" else {}

        for i, (section_text, section_meta) in enumerate(raw_sections):
            content_type, confidence = self._classify_single(section_text)

            if i in jev and jev[i][1] >= JEV_CLASSIFY_MIN_CONFIDENCE:
                content_type, confidence = jev[i]
            # If low confidence from heuristics, fall back to LLM classification
            elif confidence < 0.6 and len(section_text) > 100:
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
        # `#{1,3}\s+` is a markdown heading — except `# 4 - March (Monday)`,
        # which is how vision OCR renders Medvedev's "#4" session labels. Those
        # made every training session its own section (639 sections → 613
        # chunks of ~320 chars, MEDVEDEV); a heading never starts "<n> -".
        header_pattern = re.compile(
            rf"^({MARKDOWN_HEADING_RE_SRC}|Chapter\s+\d+.*|PART\s+[IVX]+.*|\d+\.\d+\s+[A-Z].+)$",
            re.MULTILINE,
        )

        parts = header_pattern.split(text)
        raw: list[tuple[str, str, dict]] = []
        current_chapter = ""
        current_title = ""

        for part in parts:
            if header_pattern.match(part.strip()):
                if "chapter" in part.lower():
                    current_chapter = part.strip()
                current_title = part.strip()
            elif part.strip():
                raw.append((
                    current_title,
                    part.strip(),
                    {"chapter": current_chapter, "title": current_title},
                ))

        if not raw:
            return [(text, {"chapter": "", "title": ""})]

        # RAG-H1: fold heading-regex fragments (table lines that matched
        # "\d+.\d+ Title") back into their neighbour, then cap section size at
        # paragraph boundaries so a page-joined chapter is classified in pieces
        # rather than routed wholesale by one embedded program table.
        sections = split_oversized_sections(merge_small_sections(raw))
        return [(sec_text, meta) for _title, sec_text, meta in sections]

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

        # Default: prose. Genuinely AMBIGUOUS sections — a weak/partial signal
        # that didn't clear any category (a lone principle indicator, or stray
        # percentages without principle language) — score below the LLM
        # threshold so _llm_classify can adjudicate. The old flat 0.80 made
        # `confidence < 0.6` unreachable, so the LLM fallback was dead code
        # (audit5-M2). Signal-FREE narrative stays confident 0.80 (no paid call
        # on plain prose — the common case in a book ingest).
        has_weak_signal = (
            principle_matches >= 1 or has_percentages
            or any(p.search(text) for p in self.EXERCISE_PATTERNS)
        )
        if word_count >= 50 and has_weak_signal:
            return ContentType.PROSE, 0.55
        return ContentType.PROSE, 0.60 if word_count < 50 else 0.80

    def _get_client(self):
        """Lazy-init the LLM client (the provider's key must be in .env)."""
        if not hasattr(self, "_client"):
            self._client = create_llm_client(self.settings)
        return self._client

    _CLASSIFY_PROMPT = """\
Classify this section of a weightlifting programming book into exactly one content type.

CONTENT TYPES:
- prose: general explanation, rationale, narrative, theory (no actionable rules)
- principle: contains concrete if/then rules, thresholds, or prescriptions (e.g. "reduce volume by X% when...")
- mixed: substantial prose AND concrete programming rules/thresholds together
- table: percentage tables, rep/set schemes, structured numeric data
- program_template: day/week training schedule with exercises and sets/reps
- exercise_description: description of how to perform a specific exercise

TEXT:
{text}

Respond with JSON only, no other text:
{{"content_type": "<one of the types above>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}}"""

    # Constrains the reply (STRUCT-1) — `content_type` can only be a label the
    # type_map below knows.
    _CLASSIFY_SCHEMA = {
        "type": "object",
        "properties": {
            "content_type": {"type": "string", "enum": ["prose", "principle", "mixed", "table",
                                                        "program_template", "exercise_description"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["content_type", "confidence"],
        "additionalProperties": False,
    }

    def _llm_classify(self, text: str, source_title: str) -> tuple[ContentType, float]:
        """LLM-assisted classification for ambiguous sections.

        Only a 3k-char sample is sent — that's ample to decide the section TYPE
        (prose vs table vs …); unlike principle extraction, the full text isn't
        needed here, so this truncation is intentional, not lossy.
        """
        prompt = self._CLASSIFY_PROMPT.format(text=text[:3000])

        type_map = {
            "prose": ContentType.PROSE,
            "principle": ContentType.PRINCIPLE,
            "mixed": ContentType.MIXED,
            "table": ContentType.TABLE,
            "program_template": ContentType.PROGRAM_TEMPLATE,
            "exercise_description": ContentType.EXERCISE_DESCRIPTION,
        }

        try:
            client = self._get_client()
            model = light_model_for(self.settings)  # a 128-token label pick — Haiku-class is enough
            message = create_message_with_retries(
                client,
                model=model,
                max_tokens=CLASSIFIER_LLM_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                **json_schema_kwargs(self._CLASSIFY_SCHEMA, thinking_kwargs(model, "disabled")),
            )
            parsed = parse_llm_json(message_text(message))
            content_type = type_map.get(parsed["content_type"], ContentType.PROSE)
            confidence = float(parsed.get("confidence", 0.65))
            logger.debug(
                f"LLM classified '{source_title}' section as {content_type.value} "
                f"(conf={confidence:.2f}): {parsed.get('reason', '')}"
            )
            return content_type, confidence
        except Exception as e:
            logger.warning(f"LLM classification failed for '{source_title}': {e}")
            return ContentType.PROSE, 0.50
