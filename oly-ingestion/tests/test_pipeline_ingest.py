# tests/test_pipeline_ingest.py
"""
No-DB, no-key tests for IngestionPipeline.ingest() — the orchestration from
source upsert to completed run (test_pipeline.py covers the same path against
a live DB and is integration-only). The loaders, classifier and section
processor are mocks; the document is a real .txt / .json file.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import IngestionPipeline, SourceDocument
from processors.chunker import SourceProfile
from processors.classifier import ClassifiedSection, ContentType


def _pipeline(sections, *, resumable=None, principle_audit=True, quarantine=True):
    p = object.__new__(IngestionPipeline)
    p.settings = SimpleNamespace(embedding_model="e", llm_model="m", template_model="", batch_size=50,
                                 validate_chunks=True)
    p.max_pages, p.batch, p.classifier_name = 0, False, "heuristic"
    p.principle_audit, p.quarantine = principle_audit, quarantine
    p.structured_loader = MagicMock()
    p.structured_loader.upsert_source.return_value = 810
    p.structured_loader.create_run.return_value = 77
    p.structured_loader.find_resumable_run.return_value = resumable
    p.vector_loader = MagicMock()
    p.classifier = MagicMock()
    p.classifier.classify_sections.return_value = sections
    p.sections = MagicMock()

    def process(section, target, stats, **_kw):
        stats["chunks_loaded"] += 1
        if section.content_type is ContentType.MIXED:
            stats["principles"] += 2
        return stats
    p.sections.process.side_effect = process
    return p


def _sections(n, ctype=ContentType.PROSE):
    return [ClassifiedSection(content=f"Section {i} about deload weeks.", content_type=ctype,
                              metadata={"title": f"S{i}"}) for i in range(n)]


def _doc(tmp_path, text="Deloading practices in strength sports.", title="Deloading Practices in Strength and Physique Sports"):
    f = tmp_path / "paper.txt"
    f.write_text(text, encoding="utf-8")
    return SourceDocument(path=f, title=title, author="David Rogerson", doc_type="article")


def test_ingest_routes_every_section_and_completes_the_run(tmp_path):
    p = _pipeline(_sections(3) + _sections(1, ContentType.MIXED))
    with patch("principle_audit.run_audit_pass", return_value={"claims": 2}) as audit, \
         patch.object(p, "_quarantine_source", return_value=1) as quarantine:
        stats = p.ingest(_doc(tmp_path))
    assert stats["source_id"] == 810 and stats["chunks_loaded"] == 4 and stats["principles"] == 2
    # the mapped title got its profile even though the doc type is article
    target = p.sections.process.call_args.args[1]
    assert target.source_id == 810 and target.run_id == 77
    assert target.chunker.source_profile is SourceProfile.RESEARCH
    p.structured_loader.update_run_progress.assert_any_call(77, pages_processed=1, last_processed_page=1)
    assert audit.call_args.args[0] == 810 and "Deloading practices" in audit.call_args.args[2]
    quarantine.assert_called_once_with(810)
    assert stats["principle_audit"] == {"claims": 2} and stats["chunks_quarantined_jev"] == 1
    p.structured_loader.complete_run.assert_called_once()
    config = p.structured_loader.create_run.call_args.kwargs["config_snapshot"]
    assert config["template_model"] == "m" and config["batch"] is False


def test_ingest_skips_the_post_passes_when_disabled_or_empty(tmp_path):
    p = _pipeline(_sections(2), principle_audit=False, quarantine=False)
    with patch("principle_audit.run_audit_pass") as audit, patch.object(p, "_quarantine_source") as q:
        p.ingest(_doc(tmp_path))
    audit.assert_not_called()
    q.assert_not_called()
    p = _pipeline([])                                              # nothing loaded, no principles
    with patch("principle_audit.run_audit_pass") as audit, patch.object(p, "_quarantine_source") as q:
        p.ingest(_doc(tmp_path))
    audit.assert_not_called()
    q.assert_not_called()


def test_ingest_resumes_a_failed_run_past_the_done_sections(tmp_path):
    p = _pipeline(_sections(5), resumable=(66, 3))
    with patch.object(p, "_quarantine_source", return_value=0):
        p.ingest(_doc(tmp_path))
    p.structured_loader.create_run.assert_not_called()
    p.structured_loader.update_run_status.assert_called_once_with(66, "started")
    processed = [c.args[0].content for c in p.sections.process.call_args_list]
    assert processed == ["Section 3 about deload weeks.", "Section 4 about deload weeks."]
    assert p.structured_loader.complete_run.call_args.args[0] == 66


def test_a_failing_section_is_rolled_back_and_the_run_continues(tmp_path):
    p = _pipeline(_sections(3))
    calls = {"n": 0}

    def flaky(section, target, stats, **_kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("bad section")
        stats["chunks_loaded"] += 1
        return stats
    p.sections.process.side_effect = flaky
    with patch.object(p, "_rollback_connections") as rb, patch.object(p, "_quarantine_source", return_value=0):
        stats = p.ingest(_doc(tmp_path))
    rb.assert_called_once()
    assert stats["chunks_loaded"] == 2


def test_ingest_failure_marks_the_run_failed_and_reraises(tmp_path):
    p = _pipeline(_sections(1))
    p.classifier.classify_sections.side_effect = RuntimeError("classifier down")
    with pytest.raises(RuntimeError, match="classifier down"):
        p.ingest(_doc(tmp_path))
    run_id, kwargs = p.structured_loader.fail_run.call_args.args[0], p.structured_loader.fail_run.call_args.kwargs
    assert run_id == 77 and kwargs["error_message"] == "classifier down" and "Traceback" in kwargs["error_details"]["traceback"]
    p.structured_loader.complete_run.assert_not_called()


def test_json_seed_files_go_straight_to_the_structured_loader(tmp_path):
    f = tmp_path / "seed.json"
    f.write_text('{"target_table": "exercises", "records": []}', encoding="utf-8")
    p = _pipeline([])
    p.structured_loader.load_json.return_value = 12
    stats = p.ingest(SourceDocument(path=f, title="Exercise Taxonomy", author="Manual", doc_type="structured"))
    assert stats["tables_parsed"] == 12
    p.classifier.classify_sections.assert_not_called()
    p.structured_loader.complete_run.assert_called_once()
