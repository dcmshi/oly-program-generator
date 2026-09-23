# processors/section_processor.py
"""
Per-section routing shared by both ingestion entry points (I-L11).

`pipeline.py` (books, papers, PDFs/EPUBs) and `ingest_web.py` (Catalyst,
Charniga, curated URL lists) used to carry their own copies of the loop body
that turns one classified section into chunks and principles. The copies
drifted: the web path never ran `validate_chunk` (which is how I-H1 went
unnoticed) and never got the end-of-ingest Jev quarantine pass. Both now go
through `SectionProcessor`:

    PROSE                         → chunk → validate → (contextualize) → embed
    MIXED                         → prose path + principle extraction
    PRINCIPLE                     → principle extraction
    PROGRAM_TEMPLATE / TABLE /
    EXERCISE_DESCRIPTION          → the caller's `structured` handler; when there
                                    is none (web) or it returns False (a TABLE
                                    with no parsed rows) → prose path, so the
                                    text is never dropped (I-M2)

Error policy stays with the caller: `process` raises, the caller logs, calls
`rollback()` and moves on to the next section.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from processors.chunker import SemanticChunker, validate_chunk
from processors.classifier import ClassifiedSection, ContentType
from processors.progress import Stage
from shared.constants import CHUNK_TYPE_PROBE_CHARS

logger = logging.getLogger(__name__)

CHUNK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "fault_correction": [
        "fault", "faults", "error", "errors", "correction", "corrections",
        "miss", "misses", "missed", "missing", "common mistake", "common mistakes",
    ],
    "biomechanics": [
        "biomechanics", "biomechanical", "anatomy", "physiology", "mechanics",
        "receiving position", "bar path", "muscle activation",
    ],
    "competition_strategy": [
        # Require specific competition-context phrases — "competition" alone appears
        # in almost every weightlifting chapter ("the competition lifts")
        "competition preparation", "competition day", "competition strategy",
        "meet preparation", "attempt selection", "opener", "openers", "warm-up room",
    ],
    "nutrition_bodyweight": [
        "nutrition", "weight class", "body weight", "bodyweight", "diet", "making weight",
        "hydration", "caloric",
    ],
    # Periodisation vocabulary is tested BEFORE recovery_adaptation: with the
    # old order 77 of 97 chunks mentioning "accumulation" and all 32 mentioning
    # "deload" were labelled recovery_adaptation because "adaptation"/"recovery"
    # appears in the same passages (RAG-H2).
    "periodization": [
        "periodization", "periodisation", "program design", "mesocycle", "macrocycle",
        "microcycle", "annual plan", "training block", "training cycle",
        "accumulation", "intensification", "realization", "realisation",
        "deload", "taper", "tapering", "peaking", "preparatory period", "competitive period",
    ],
    "recovery_adaptation": [
        "recovery", "adaptation", "sleep", "rest period", "restoration",
        "overtraining", "supercompensation",
    ],
    "programming_rationale": [
        "rationale", "reasoning", "because", "in order to",
    ],
}

# Word-boundary matchers built once from CHUNK_TYPE_KEYWORDS: substring tests
# fired `miss` on "permission"/"mission" and `error` on "terror" (RAG-H2).
_CHUNK_TYPE_MATCHERS: list[tuple[str, re.Pattern]] = [
    (chunk_type, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b", re.IGNORECASE))
    for chunk_type, kws in CHUNK_TYPE_KEYWORDS.items()
]

STRUCTURED_TYPES = frozenset({
    ContentType.PROGRAM_TEMPLATE, ContentType.TABLE, ContentType.EXERCISE_DESCRIPTION,
})


def infer_chunk_type(section) -> str:
    """Map classifier ContentType + section title/content to a chunk_type enum value.

    Scans the section title AND the first CHUNK_TYPE_PROBE_CHARS chars of content with
    word-boundary matchers; first match in CHUNK_TYPE_KEYWORDS order wins.
    (`title or content` used to skip the body whenever a title existed —
    after RAG-H1 almost every section has one — and substring tests fired
    on "permission"/"terror"; RAG-H2.) The label is a soft retrieval
    preference, not a filter, so a wrong guess costs rank, not recall.
    """
    title = section.metadata.get("title") or ""
    probe = f"{title}\n{section.content[:CHUNK_TYPE_PROBE_CHARS]}"

    for chunk_type, matcher in _CHUNK_TYPE_MATCHERS:
        if matcher.search(probe):
            return chunk_type

    # ContentType.MIXED means it has both prose and rules — label accordingly
    if section.content_type.value == "mixed":
        return "programming_rationale"

    return "concept"


def new_section_stats() -> dict:
    """The counters `SectionProcessor` maintains; both entry points store them in
    `ingestion_runs.result` via `complete_run`."""
    return {
        "prose_chunks": 0,
        "prose_chunks_valid": 0,
        "prose_chunks_quarantined": 0,
        "chunks_loaded": 0,
        "chunks_skipped_dedup": 0,
        "principles": 0,
    }


@dataclass
class SectionTarget:
    """Where one source's sections go: its row, byline, chunker and run."""
    source_id: int
    title: str
    author: str
    chunker: SemanticChunker
    run_id: int | None = None


# (section, stats) -> True when the section was stored as structured data;
# False sends it down the prose path instead.
StructuredHandler = Callable[[ClassifiedSection, dict], bool]


class SectionProcessor:
    def __init__(self, settings, vector_loader, structured_loader, principle_extractor,
                 *, contextualize: bool = False, context_model: str | None = None):
        self.settings = settings
        self.vector_loader = vector_loader
        self.structured_loader = structured_loader
        self.principle_extractor = principle_extractor
        # RAG-M3: LLM-written retrieval context per chunk (opt-in)
        self.contextualize = contextualize
        self.context_model = context_model

    def process(self, section: ClassifiedSection, target: SectionTarget, stats: dict, *,
                structured: StructuredHandler | None = None,
                queue_principles: Callable[[str], None] | None = None) -> dict:
        """Route one classified section. `queue_principles`, when given, receives
        the section text instead of extracting synchronously (COST-1 batch mode)."""
        ctype = section.content_type

        if ctype in STRUCTURED_TYPES:
            if structured is not None and structured(section, stats):
                return stats
            self.process_prose(section, target, stats)
            return stats

        if ctype in (ContentType.PROSE, ContentType.MIXED):
            self.process_prose(section, target, stats)

        if ctype in (ContentType.PRINCIPLE, ContentType.MIXED):
            if queue_principles is not None:
                queue_principles(section.content)
            else:
                stats["principles"] = stats.get("principles", 0) + self.extract_principles(section.content, target)
        return stats

    def process_prose(self, section: ClassifiedSection, target: SectionTarget, stats: dict) -> dict:
        """Chunk prose content, validate, tag, and load into the vector store."""
        chunks = target.chunker.chunk(
            text=section.content,
            metadata={
                "chapter": section.metadata.get("chapter", ""),
                "chunk_type": infer_chunk_type(section),
            },
            source_title=target.title,
            author=target.author,
        )

        if self.contextualize and chunks:
            from processors.contextualizer import contextualize
            chunks = contextualize(
                chunks, section.content, target.title,
                self.principle_extractor._get_client(), self.context_model,
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
                        stats["prose_chunks_quarantined"] = stats.get("prose_chunks_quarantined", 0) + 1
                        continue
            valid_chunks.append(chunk)

        loaded = self.vector_loader.load_chunks(
            valid_chunks, target.source_id,
            run_id=target.run_id,
            structured_loader=self.structured_loader,
        )
        stats["prose_chunks"] = stats.get("prose_chunks", 0) + len(chunks)
        stats["prose_chunks_valid"] = stats.get("prose_chunks_valid", 0) + len(valid_chunks)
        stats["chunks_loaded"] = stats.get("chunks_loaded", 0) + loaded
        stats["chunks_skipped_dedup"] = (
            stats.get("chunks_skipped_dedup", 0) + self.vector_loader.last_skipped_count
        )
        return stats

    def extract_principles(self, text: str, target: SectionTarget) -> int:
        """Synchronous principle extraction + load for one section; returns the count."""
        principles = self.principle_extractor.extract(
            text=text, source_title=target.title, source_id=target.source_id,
        )
        self.structured_loader.load_principles(principles, target.source_id)
        return len(principles)

    def rollback(self) -> None:
        """Roll back both loader connections after a section-level error, so the
        next section starts on a clean transaction ("transaction is aborted"
        otherwise). See `rollback_loaders`."""
        rollback_loaders(self.vector_loader, self.structured_loader)


def rollback_loaders(*loaders) -> None:
    """Roll back each loader's connection independently after an error, so the
    next unit of work starts on a clean transaction. A rollback that itself
    fails (connection already gone) is logged at WARNING and the remaining
    loaders are still rolled back — never raises. Shared by
    `SectionProcessor.rollback` and `ingest_web.main`'s unhandled-error path."""
    for loader in loaders:
        try:
            loader.conn.rollback()
        except Exception as rb_err:                  # noqa: BLE001 — best effort, logged
            logger.warning(f"Rollback of {type(loader).__name__} connection failed (non-fatal): {rb_err}")


def run_quarantine_pass(source_id: int, settings) -> int | None:
    """Run quarantine_chunks.py over one source (JEV-1a) at the end of an ingest
    so indexes / TOCs / reference lists never wait for a manual pass. Needs
    TYPESAFE_API_KEY; without it the step is skipped with a warning. Never
    raises — the ingest is already committed."""
    import os
    if not os.environ.get("TYPESAFE_API_KEY"):
        logger.warning("Quarantine pass skipped — TYPESAFE_API_KEY not set")
        return None
    try:
        from quarantine_chunks import quarantine_source
        with Stage("Quarantine pass", logger, f"source {source_id}, Jev junk detector"):
            return quarantine_source(source_id, settings)
    except Exception as e:                       # noqa: BLE001
        logger.error(f"Quarantine pass failed for source {source_id}: {e}")
        return None
