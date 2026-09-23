# tests/test_section_processor.py
"""
No-key unit tests for processors/section_processor.py — the per-section routing
shared by pipeline.py and ingest_web.py (I-L11).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.chunker import SemanticChunker
from processors.classifier import ClassifiedSection, ContentType
from processors.section_processor import (
    SectionProcessor,
    SectionTarget,
    infer_chunk_type,
    new_section_stats,
)

_BODY = ("Accumulation weeks keep the snatch between 70 and 80 percent because the "
         "athlete is building work capacity before the intensification block. ") * 10


def _processor(**settings_kw):
    settings = MagicMock(validate_chunks=False, quarantine_invalid_chunks=False)
    for k, v in settings_kw.items():
        setattr(settings, k, v)
    vl = MagicMock(last_skipped_count=0)
    vl.load_chunks.side_effect = lambda chunks, *a, **k: len(chunks)
    pe = MagicMock()
    pe.extract.return_value = ["p"]
    return SectionProcessor(settings, vl, MagicMock(), pe)


def _target():
    return SectionTarget(source_id=9, title="Book", author="A", chunker=SemanticChunker(), run_id=3)


def _section(ctype, content=_BODY):
    return ClassifiedSection(content=content, content_type=ctype, metadata={"chapter": "Ch 1", "title": ""})


def test_prose_is_chunked_and_loaded():
    proc, stats = _processor(), new_section_stats()
    proc.process(_section(ContentType.PROSE), _target(), stats)
    assert stats["chunks_loaded"] >= 1 and stats["principles"] == 0
    assert proc.vector_loader.load_chunks.call_args.kwargs["run_id"] == 3


def test_mixed_goes_to_both_paths():
    proc, stats = _processor(), new_section_stats()
    proc.process(_section(ContentType.MIXED), _target(), stats)
    assert stats["chunks_loaded"] >= 1 and stats["principles"] == 1
    proc.structured_loader.load_principles.assert_called_once_with(["p"], 9)


def test_principle_is_extracted_not_embedded():
    proc, stats = _processor(), new_section_stats()
    proc.process(_section(ContentType.PRINCIPLE), _target(), stats)
    proc.vector_loader.load_chunks.assert_not_called()
    assert stats["principles"] == 1


def test_principles_are_queued_in_batch_mode():
    proc, stats, queued = _processor(), new_section_stats(), []
    proc.process(_section(ContentType.MIXED), _target(), stats, queue_principles=queued.append)
    assert queued == [_BODY]
    proc.principle_extractor.extract.assert_not_called()
    assert stats["principles"] == 0


def test_structured_without_handler_falls_back_to_prose():
    """Web path: no structured loaders, so TABLE / PROGRAM / EXERCISE text is
    chunked rather than dropped (I-M2)."""
    for ctype in (ContentType.TABLE, ContentType.PROGRAM_TEMPLATE, ContentType.EXERCISE_DESCRIPTION):
        proc, stats = _processor(), new_section_stats()
        proc.process(_section(ctype), _target(), stats)
        assert stats["chunks_loaded"] >= 1, ctype


def test_structured_handler_decides_between_structured_and_prose():
    proc = _processor()
    handled = new_section_stats()
    proc.process(_section(ContentType.PROGRAM_TEMPLATE), _target(), handled, structured=lambda s, st: True)
    assert handled["chunks_loaded"] == 0

    declined = new_section_stats()        # e.g. a TABLE with no pre-parsed rows
    proc.process(_section(ContentType.TABLE), _target(), declined, structured=lambda s, st: False)
    assert declined["chunks_loaded"] >= 1


def test_invalid_chunks_quarantined_only_when_configured():
    from unittest.mock import patch

    bad = MagicMock(is_valid=False, severity="error", issues=["x"], chunk_index=0)
    for quarantine, expect_loaded in ((True, 0), (False, 1)):
        proc, stats = _processor(validate_chunks=True, quarantine_invalid_chunks=quarantine), new_section_stats()
        with patch("processors.section_processor.validate_chunk", return_value=bad):
            proc.process(_section(ContentType.PROSE), _target(), stats)
        assert (stats["prose_chunks_valid"] > 0) == bool(expect_loaded), quarantine
        assert (stats["prose_chunks_quarantined"] > 0) == quarantine


def test_rollback_resets_both_connections():
    proc = _processor()
    proc.rollback()
    proc.vector_loader.conn.rollback.assert_called_once()
    proc.structured_loader.conn.rollback.assert_called_once()


def test_rollback_failure_is_logged_and_the_other_loader_still_rolls_back(caplog):
    """A dead vector connection used to skip the structured rollback and log at
    DEBUG; each loader is now rolled back on its own and a failure is a WARNING."""
    import logging

    proc = _processor()
    proc.vector_loader.conn.rollback.side_effect = RuntimeError("connection already closed")
    with caplog.at_level(logging.WARNING, logger="processors.section_processor"):
        proc.rollback()
    proc.structured_loader.conn.rollback.assert_called_once()
    assert "connection already closed" in caplog.text


def test_infer_chunk_type_probes_only_the_head_of_the_content():
    """Keywords past CHUNK_TYPE_PROBE_CHARS don't label the section."""
    from shared.constants import CHUNK_TYPE_PROBE_CHARS

    pad = "x " * CHUNK_TYPE_PROBE_CHARS
    assert infer_chunk_type(_section(ContentType.PROSE, "A deload week. " + pad)) == "periodization"
    assert infer_chunk_type(_section(ContentType.PROSE, pad + " A deload week.")) != "periodization"


def test_pipeline_keeps_its_chunk_type_alias():
    from pipeline import CHUNK_TYPE_KEYWORDS, IngestionPipeline
    from processors import section_processor

    assert CHUNK_TYPE_KEYWORDS is section_processor.CHUNK_TYPE_KEYWORDS
    s = _section(ContentType.PROSE, "A deload week every fourth week.")
    assert IngestionPipeline._infer_chunk_type(s) == infer_chunk_type(s) == "periodization"
