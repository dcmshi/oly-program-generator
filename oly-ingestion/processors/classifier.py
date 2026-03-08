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
        header_pattern = re.compile(
            r"^(#{1,3}\s+.+|Chapter\s+\d+.*|PART\s+[IVX]+.*|\d+\.\d+\s+[A-Z].+)$",
            re.MULTILINE,
        )

        parts = header_pattern.split(text)
        sections = []
        current_chapter = ""
        current_title = ""

        for part in parts:
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

    def _get_client(self):
        """Lazy-init Anthropic client."""
        if not hasattr(self, "_client"):
            import anthropic
            if not self.settings.anthropic_api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY is required for LLM classification. "
                    "Set it in .env or pass via environment variable."
                )
            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
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

    def _llm_classify(self, text: str, source_title: str) -> tuple[ContentType, float]:
        """LLM-assisted classification for ambiguous sections."""
        import json

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
            message = client.messages.create(
                model=self.settings.llm_model,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            parsed = json.loads(raw)
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
