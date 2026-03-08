# tests/test_pipeline.py
"""
End-to-end integration test for IngestionPipeline using a synthetic .txt source.

Exercises the full pipeline without a real PDF:
  extract → classify → chunk → embed → load (vector + structured)
  ingestion run tracking: create → progress → complete
  resume logic: failed run is picked up and restarted

Requires:
  - Docker Postgres running
  - OPENAI_API_KEY (embeddings)
  - ANTHROPIC_API_KEY (principle extraction + LLM classifier fallback)

Run: PYTHONUTF8=1 uv run python tests/test_pipeline.py
"""

import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Settings
from pipeline import IngestionPipeline, SourceDocument
from loaders.structured_loader import StructuredLoader

TEST_AUTHOR = "__test__ Author"


def unique_title(label: str = "") -> str:
    """Generate a unique source title so each test run gets a fresh source_id."""
    suffix = uuid.uuid4().hex[:8]
    return f"__test__ Pipeline {label} {suffix}".strip()

# A small but realistic document: prose + principle-heavy section
SYNTHETIC_DOCUMENT = """\
Chapter 1 The Competition Lifts

The snatch and the clean and jerk are the two competition lifts in Olympic
weightlifting. Both require a high degree of technical skill combined with
explosive power and positional strength. Coaches must develop both qualities
simultaneously across a training career.

The snatch demands precise timing at the extension. The athlete must achieve
full hip and knee extension before initiating the pull under the bar. A
premature pull under leads to the bar crashing on the arms overhead.

Chapter 2 Programming Principles

During the final two weeks before competition, volume should be reduced by
40 to 60 percent while intensity remains at or above 90 percent of maximum.
This allows the nervous system to recover from accumulated fatigue while
maintaining the neural patterns required for maximal expression of strength.

Athletes should never exceed three heavy singles above 95 percent in a single
session during the peaking phase. Rest periods between maximal attempts should
be no less than five minutes to allow complete recovery.

A deload should be programmed every four weeks for intermediate and advanced
lifters. During the deload week, reduce volume by 50 percent and keep
intensity at or below 80 percent of maximum.

Chapter 3 Exercise Selection

Back squat training builds the posterior chain strength necessary for a strong
clean recovery and an upright jerk. Front squats develop the positional
strength and flexibility needed for the receiving position in both competition
lifts. Both should be trained year-round with variation in intensity and volume.
"""


def make_pipeline() -> IngestionPipeline:
    return IngestionPipeline(Settings())


def cleanup(source_id: int):
    loader = StructuredLoader(Settings())
    cur = loader.conn.cursor()
    cur.execute("DELETE FROM knowledge_chunks WHERE source_id = %s", (source_id,))
    cur.execute("DELETE FROM programming_principles WHERE source_id = %s", (source_id,))
    cur.execute("DELETE FROM program_templates WHERE source_id = %s", (source_id,))
    cur.execute("DELETE FROM ingestion_runs WHERE source_id = %s", (source_id,))
    cur.execute("DELETE FROM sources WHERE id = %s", (source_id,))
    loader.conn.commit()
    cur.close()
    loader.close()


def write_temp_doc(content: str, suffix: str = ".txt") -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return Path(f.name)


def unique_doc() -> str:
    """Append a UUID so content hashes never collide across test runs."""
    return SYNTHETIC_DOCUMENT + f"\n\n[test-run-id: {uuid.uuid4().hex}]"


# ── Tests ──────────────────────────────────────────────────────

def test_full_pipeline_run():
    """Full pipeline on a synthetic .txt document: verifies run tracking and chunk loading."""
    tmp = write_temp_doc(unique_doc())
    pipeline = make_pipeline()
    source_id = None

    try:
        doc = SourceDocument(path=tmp, title=unique_title("Integration"), author=TEST_AUTHOR, doc_type="book")
        stats = pipeline.ingest(doc)
        source_id = stats["source_id"]

        assert isinstance(source_id, int), "source_id must be an int"
        assert stats["prose_chunks"] >= 1, "Expected at least 1 prose chunk"
        assert stats["prose_chunks_valid"] <= stats["prose_chunks"]
        print(f"  pipeline stats: {stats}")

        loader = StructuredLoader(Settings())
        cur = loader.conn.cursor()
        cur.execute(
            "SELECT status, chunks_created FROM ingestion_runs WHERE source_id = %s ORDER BY id DESC LIMIT 1",
            (source_id,),
        )
        run_row = cur.fetchone()
        assert run_row is not None, "No ingestion_run found"
        assert run_row[0] == "completed", f"Expected 'completed', got '{run_row[0]}'"
        assert run_row[1] >= 1, "chunks_created should be at least 1"

        cur.execute(
            "SELECT count(*) FROM knowledge_chunks WHERE source_id = %s", (source_id,)
        )
        chunk_count = cur.fetchone()[0]
        assert chunk_count >= 1, f"Expected >= 1 chunk in DB, got {chunk_count}"
        print(f"  ingestion_run: completed, chunks_created={run_row[1]}")
        print(f"  knowledge_chunks: {chunk_count} rows")
        cur.close()
        loader.close()
    finally:
        tmp.unlink(missing_ok=True)
        if source_id:
            cleanup(source_id)


def test_idempotent_source_upsert():
    """Running the pipeline twice on the same source re-uses the source_id."""
    tmp = write_temp_doc(unique_doc())
    pipeline = make_pipeline()
    source_id = None

    try:
        title = unique_title("Idempotent")
        doc = SourceDocument(path=tmp, title=title, author=TEST_AUTHOR, doc_type="book")
        stats1 = pipeline.ingest(doc)
        stats2 = pipeline.ingest(doc)
        source_id = stats1["source_id"]

        assert stats1["source_id"] == stats2["source_id"], \
            f"Expected same source_id, got {stats1['source_id']} vs {stats2['source_id']}"
        print(f"  idempotent: source_id={source_id}, second run prose_chunks={stats2['prose_chunks']}")

        loader = StructuredLoader(Settings())
        cur = loader.conn.cursor()
        cur.execute(
            "SELECT count(*) FROM ingestion_runs WHERE source_id = %s", (source_id,)
        )
        run_count = cur.fetchone()[0]
        assert run_count == 2, f"Expected 2 ingestion_run rows, got {run_count}"

        cur.execute(
            "SELECT chunks_created FROM ingestion_runs WHERE source_id = %s ORDER BY id DESC LIMIT 1",
            (source_id,),
        )
        second_run_chunks = cur.fetchone()[0]
        assert second_run_chunks == 0, \
            f"Second run should embed 0 chunks (all deduped), got {second_run_chunks}"
        print(f"  second run chunks_created={second_run_chunks} (all deduped, none re-embedded)")
        cur.close()
        loader.close()
    finally:
        tmp.unlink(missing_ok=True)
        if source_id:
            cleanup(source_id)


def test_resume_failed_run():
    """A previously failed run should be picked up and completed."""
    tmp = write_temp_doc(unique_doc())
    pipeline = make_pipeline()
    source_id = None

    try:
        loader = StructuredLoader(Settings())
        resume_title = unique_title("Resume")
        source_id = loader.upsert_source(
            title=resume_title, author=TEST_AUTHOR, source_type="book"
        )

        import hashlib
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        file_hash = h.hexdigest()

        failed_run_id = loader.create_run(source_id, str(tmp), file_hash)
        loader.fail_run(failed_run_id, "Simulated crash before completion")
        loader.close()

        # Re-use the same title so pipeline finds the existing source_id via upsert
        doc = SourceDocument(
            path=tmp, title=resume_title, author=TEST_AUTHOR, doc_type="book"
        )
        pipeline.ingest(doc)

        loader2 = StructuredLoader(Settings())
        cur = loader2.conn.cursor()
        cur.execute("SELECT status FROM ingestion_runs WHERE id = %s", (failed_run_id,))
        status = cur.fetchone()[0]
        assert status == "completed", f"Expected resumed run to complete, got '{status}'"
        cur.close()
        loader2.close()
        print(f"  resume: failed run #{failed_run_id} was resumed and completed")
    finally:
        tmp.unlink(missing_ok=True)
        if source_id:
            cleanup(source_id)


def test_pipeline_fail_run_on_bad_source():
    """An invalid source path should mark the run as failed, not leave it as started."""
    pipeline = make_pipeline()
    fail_title = unique_title("FailTest")  # generate once, reuse consistently
    loader = StructuredLoader(Settings())
    source_id = loader.upsert_source(fail_title, TEST_AUTHOR, "book")
    loader.close()

    try:
        doc = SourceDocument(
            path=Path("/nonexistent/path/fake.txt"),
            title=fail_title,
            author=TEST_AUTHOR,
            doc_type="book",
        )
        try:
            pipeline.ingest(doc)
            assert False, "Expected ingest to raise an exception"
        except Exception:
            pass  # expected

        loader2 = StructuredLoader(Settings())
        cur = loader2.conn.cursor()
        cur.execute(
            "SELECT status FROM ingestion_runs WHERE source_id = %s ORDER BY id DESC LIMIT 1",
            (source_id,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "failed", f"Expected 'failed', got '{row[0]}'"
        print(f"  fail_run: bad source correctly marked as failed")
        cur.close()
        loader2.close()
    finally:
        cleanup(source_id)


# ── Runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_full_pipeline_run,
        test_idempotent_source_upsert,
        test_resume_failed_run,
        test_pipeline_fail_run_on_bad_source,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
