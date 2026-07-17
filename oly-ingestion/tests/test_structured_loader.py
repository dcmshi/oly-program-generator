# tests/test_structured_loader.py
"""
Integration tests for StructuredLoader against the live database.

Requires: Docker Postgres running (docker compose up -d).
No API keys needed.

Run: uv run python tests/test_structured_loader.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Settings
from loaders.structured_loader import StructuredLoader

# Use a unique prefix so test data doesn't collide with seed data
TEST_PREFIX = "__test__"


def make_loader() -> StructuredLoader:
    return StructuredLoader(Settings())


def cleanup(loader: StructuredLoader, source_id: int | None = None):
    """Remove test data inserted during this run."""
    cur = loader.conn.cursor()
    if source_id:
        # Cascades handle ingestion_chunk_log; clean up everything else manually
        cur.execute("DELETE FROM programming_principles WHERE source_id = %s", (source_id,))
        cur.execute("DELETE FROM program_templates WHERE source_id = %s", (source_id,))
        cur.execute("DELETE FROM percentage_schemes WHERE source_id = %s", (source_id,))
        cur.execute("DELETE FROM exercises WHERE name LIKE %s", (f"{TEST_PREFIX}%",))
        cur.execute("DELETE FROM ingestion_runs WHERE source_id = %s", (source_id,))
        cur.execute("DELETE FROM sources WHERE id = %s", (source_id,))
    loader.conn.commit()
    cur.close()


# ── upsert_source ────────────────────────────────────────────

def test_upsert_source_creates_new():
    loader = make_loader()
    sid = loader.upsert_source(
        title=f"{TEST_PREFIX}Test Book",
        author="Test Author",
        source_type="book",
    )
    assert isinstance(sid, int), "Expected an integer source ID"

    # Second call with same title+author returns same ID (idempotent)
    sid2 = loader.upsert_source(
        title=f"{TEST_PREFIX}Test Book",
        author="Test Author",
        source_type="book",
    )
    assert sid == sid2, "Expected same ID on duplicate upsert"

    print(f"  upsert_source: created source_id={sid}, idempotent ✓")
    cleanup(loader, sid)
    loader.close()


# ── load_exercise ─────────────────────────────────────────────

def test_load_exercise():
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Exercise Book", "Author", "book")

    ex_id = loader.load_exercise({
        "name": f"{TEST_PREFIX}Power Snatch Variation",
        "category": "competition_variant",
        "movement_family": "snatch",
        "primary_purpose": "Test exercise for unit test",
        "faults_addressed": ["slow_turnover", "bar_crashing"],
        "source_id": sid,
    })
    assert isinstance(ex_id, int), "Expected exercise ID"

    # Verify in DB
    cur = loader.conn.cursor()
    cur.execute(
        "SELECT name, category FROM exercises WHERE id = %s", (ex_id,)
    )
    row = cur.fetchone()
    cur.close()
    assert row is not None
    assert row[0] == f"{TEST_PREFIX}Power Snatch Variation"
    assert row[1] == "competition_variant"

    print(f"  load_exercise: exercise_id={ex_id} ✓")
    cleanup(loader, sid)
    loader.close()


# ── load_percentage_schemes ───────────────────────────────────

def test_load_percentage_schemes():
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Scheme Book", "Author", "book")

    rows = [
        {
            "scheme_name": f"{TEST_PREFIX}Test Scheme",
            "phase": "accumulation",
            "week_number": 1,
            "day_number": 1,
            "exercise_order": 1,
            "sets": 5,
            "reps": 3,
            "intensity_pct": 72.0,
            "intensity_reference": "snatch",
        },
        {
            "scheme_name": f"{TEST_PREFIX}Test Scheme",
            "phase": "accumulation",
            "week_number": 1,
            "day_number": 1,
            "exercise_order": 2,
            "sets": 4,
            "reps": 5,
            "intensity_pct": 75.0,
            "intensity_reference": "back_squat",
        },
    ]
    count = loader.load_percentage_schemes(rows, sid)
    assert count == 2

    cur = loader.conn.cursor()
    cur.execute(
        "SELECT count(*) FROM percentage_schemes WHERE scheme_name = %s",
        (f"{TEST_PREFIX}Test Scheme",),
    )
    assert cur.fetchone()[0] == 2
    cur.close()

    print(f"  load_percentage_schemes: {count} rows ✓")
    cleanup(loader, sid)
    loader.close()


# ── load_program ──────────────────────────────────────────────

def test_load_program():
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Program Book", "Author", "book")

    program = {
        "name": f"{TEST_PREFIX}8-Week Accumulation",
        "source_id": sid,
        "athlete_level": "intermediate",
        "goal": "general_strength",
        "duration_weeks": 8,
        "sessions_per_week": 4,
        "program_structure": {
            "weeks": [{"week_number": 1, "phase": "accumulation", "sessions": []}]
        },
    }
    prog_id = loader.load_program(program)
    assert isinstance(prog_id, int)

    cur = loader.conn.cursor()
    cur.execute("SELECT name, duration_weeks FROM program_templates WHERE id = %s", (prog_id,))
    row = cur.fetchone()
    cur.close()
    assert row[0] == f"{TEST_PREFIX}8-Week Accumulation"
    assert row[1] == 8

    print(f"  load_program: program_id={prog_id} ✓")
    cleanup(loader, sid)
    loader.close()


def test_load_program_dedup():
    """ING-M6: re-loading the same template (source_id + name) must not
    duplicate it — re-running a source's pipeline is documented as safe."""
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Program Dedup Book", "Author", "book")

    program = {
        "name": f"{TEST_PREFIX}Dedup Template",
        "source_id": sid,
        "athlete_level": "any",
        "goal": "general_strength",
        "duration_weeks": 4,
        "sessions_per_week": 3,
        "program_structure": {"weeks": []},
    }
    id1 = loader.load_program(program)
    assert isinstance(id1, int)
    id2 = loader.load_program(program)
    assert id2 is None, f"duplicate load should be skipped, got id {id2}"

    cur = loader.conn.cursor()
    cur.execute("SELECT count(*) FROM program_templates WHERE source_id = %s", (sid,))
    total = cur.fetchone()[0]
    cur.close()
    assert total == 1, f"expected 1 stored template, got {total}"

    print("  load_program: dedup on (source_id, name) ✓")
    cleanup(loader, sid)
    loader.close()


def test_load_program_normalizes_legacy_goal():
    """ING-M5: the old parse-prompt vocabulary (technique/accumulation/
    intensification) violates the goal CHECK — load_program must normalize the
    label instead of rolling back and silently dropping the template."""
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Goal Vocab Book", "Author", "book")

    program = {
        "name": f"{TEST_PREFIX}Legacy Goal Template",
        "source_id": sid,
        "athlete_level": "any",
        "goal": "accumulation",  # not in the DB CHECK list
        "duration_weeks": 4,
        "sessions_per_week": 3,
        "program_structure": {"weeks": []},
    }
    pid = loader.load_program(program)
    assert isinstance(pid, int), "template with a legacy goal label must still load"

    cur = loader.conn.cursor()
    cur.execute("SELECT goal FROM program_templates WHERE id = %s", (pid,))
    stored = cur.fetchone()[0]
    cur.close()
    allowed = {"general_strength", "competition_prep", "technique_focus",
               "hypertrophy", "work_capacity", "peaking", "return_to_sport"}
    assert stored in allowed, f"stored goal {stored!r} not in the CHECK list"

    print(f"  load_program: legacy goal normalized to {stored!r} ✓")
    cleanup(loader, sid)
    loader.close()


# ── ingestion run tracking ────────────────────────────────────

def test_create_and_complete_run():
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Run Book", "Author", "book")

    run_id = loader.create_run(
        source_id=sid,
        file_path="/test/fake.pdf",
        file_hash="abc123",
        config_snapshot={"embedding_model": "text-embedding-3-small"},
    )
    assert isinstance(run_id, int)

    # Check status is 'started'
    cur = loader.conn.cursor()
    cur.execute("SELECT status FROM ingestion_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == "started"

    # Update progress
    loader.update_run_progress(run_id, pages_processed=10, last_processed_page=10)
    cur.execute("SELECT status, pages_processed FROM ingestion_runs WHERE id = %s", (run_id,))
    row = cur.fetchone()
    assert row[0] == "processing"
    assert row[1] == 10

    # Complete
    stats = {"prose_chunks_valid": 25, "prose_chunks": 26, "prose_chunks_quarantined": 0,
             "principles": 3, "programs": 1, "exercises": 0, "tables_parsed": 2}
    loader.complete_run(run_id, stats)
    cur.execute("SELECT status, chunks_created, principles_extracted FROM ingestion_runs WHERE id = %s", (run_id,))
    row = cur.fetchone()
    assert row[0] == "completed"
    assert row[1] == 25
    assert row[2] == 3
    cur.close()

    print(f"  create_run/complete_run: run_id={run_id} ✓")
    cleanup(loader, sid)
    loader.close()


def test_fail_run():
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Fail Book", "Author", "book")

    run_id = loader.create_run(sid, "/test/fail.pdf", "failhash123")
    loader.fail_run(run_id, "Simulated failure", {"detail": "test error"})

    cur = loader.conn.cursor()
    cur.execute("SELECT status, error_message FROM ingestion_runs WHERE id = %s", (run_id,))
    row = cur.fetchone()
    cur.close()
    assert row[0] == "failed"
    assert row[1] == "Simulated failure"

    print(f"  fail_run: run_id={run_id} ✓")
    cleanup(loader, sid)
    loader.close()


def test_find_resumable_run():
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Resume Book", "Author", "book")

    run_id = loader.create_run(sid, "/test/resume.pdf", "resumehash999")
    loader.update_run_progress(run_id, pages_processed=20, last_processed_page=20)
    loader.fail_run(run_id, "Failed at section 42")

    found = loader.find_resumable_run("resumehash999")
    # Now returns (run_id, sections_already_processed) so the pipeline can skip
    # completed work instead of restarting from section 0 (I-M1).
    assert found == (run_id, 20), f"Expected ({run_id}, 20), got {found}"

    # A completed run should not be resumable
    run_id2 = loader.create_run(sid, "/test/resume.pdf", "donehash000")
    loader.complete_run(run_id2, {})
    assert loader.find_resumable_run("donehash000") is None

    print(f"  find_resumable_run: found run #{found[0]} at section {found[1]} ✓")
    cleanup(loader, sid)
    loader.close()


# ── load_principles ───────────────────────────────────────────

def test_load_principles_dedup():
    """I-H3/I-L2: re-loading the same principle inserts nothing (UNIQUE +
    ON CONFLICT) and is not counted (rowcount-based tally)."""
    from types import SimpleNamespace
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Principle Book", "Author", "book")
    p = SimpleNamespace(
        principle_name=f"{TEST_PREFIX}p1", category="volume", rule_type="guideline",
        condition={"lift": "snatch"}, recommendation={"sets": 5}, rationale="because", priority=1,
    )
    n1 = loader.load_principles([p], sid)
    assert n1 == 1, f"first load should insert 1, got {n1}"
    n2 = loader.load_principles([p], sid)  # identical → skipped
    assert n2 == 0, f"duplicate load should insert 0, got {n2}"

    cur = loader.conn.cursor()
    cur.execute("SELECT count(*) FROM programming_principles WHERE source_id = %s", (sid,))
    total = cur.fetchone()[0]
    cur.close()
    assert total == 1, f"expected 1 stored principle, got {total}"

    print("  load_principles: dedup on (source_id, principle_name) ✓")
    cleanup(loader, sid)
    loader.close()


# ── Runner ────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_upsert_source_creates_new,
        test_load_exercise,
        test_load_percentage_schemes,
        test_load_program,
        test_load_program_dedup,
        test_load_program_normalizes_legacy_goal,
        test_create_and_complete_run,
        test_fail_run,
        test_find_resumable_run,
        test_load_principles_dedup,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            import traceback

            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
