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


def test_upsert_source_distinct_urls_distinct_sources():
    """audit2-M3: two different web pages with the same extracted title under
    the constant curator author must NOT merge into one source row."""
    loader = make_loader()
    sid1 = loader.upsert_source(
        f"{TEST_PREFIX}Same Essay Title", "Andrew Charniga", "website",
        url="http://sportivnypress.com/2016/essay-a/",
    )
    sid2 = loader.upsert_source(
        f"{TEST_PREFIX}Same Essay Title", "Andrew Charniga", "website",
        url="http://sportivnypress.com/2017/essay-b/",
    )
    assert sid1 != sid2, "distinct URLs must produce distinct sources (audit2-M3)"

    # same URL again → same source (idempotent by url)
    sid3 = loader.upsert_source(
        f"{TEST_PREFIX}Same Essay Title", "Andrew Charniga", "website",
        url="http://sportivnypress.com/2016/essay-a/",
    )
    assert sid3 == sid1

    print(f"  upsert_source: url-disambiguated sources {sid1} != {sid2} ✓")
    cur = loader.conn.cursor()
    cur.execute("DELETE FROM sources WHERE id IN (%s, %s)", (sid1, sid2))
    loader.conn.commit()
    cur.close()
    loader.close()


def test_upsert_source_repeated_slug_collision_no_crash():
    """audit3-M1 (ingestion): three same-title pages whose URLs share a final
    slug used to raise UniqueViolation on the third insert and abort the whole
    ingest run. Every page must get its own source id, no exception."""
    loader = make_loader()
    title = f"{TEST_PREFIX}Repeated Slug Title"
    ids = []
    for year in (2016, 2017, 2018):
        sid = loader.upsert_source(
            title, "Andrew Charniga", "website",
            url=f"http://sportivnypress.com/{year}/foo/",
        )
        assert isinstance(sid, int), f"insert for {year} failed"
        ids.append(sid)
    assert len(set(ids)) == 3, f"expected 3 distinct sources, got {ids}"

    print(f"  upsert_source: repeated-slug collision → {len(set(ids))} distinct sources ✓")
    cur = loader.conn.cursor()
    cur.execute("DELETE FROM sources WHERE id = ANY(%s)", (ids,))
    loader.conn.commit()
    cur.close()
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


# ── load_exercise — audit5-H1/M1/M3 ───────────────────────────

def test_load_exercise_preserves_curated_faults():
    """audit5-H1: a book EXERCISE_DESCRIPTION always emits faults_addressed=[]
    and a heuristic purpose; the ON CONFLICT DO UPDATE must NOT wipe a curated
    seed row's faults_addressed."""
    loader = make_loader()
    # 'Snatch Balance' is a seed exercise with curated faults_addressed
    cur = loader.conn.cursor()
    cur.execute("SELECT faults_addressed FROM exercises WHERE name = 'Snatch Balance'")
    row = cur.fetchone()
    cur.close()
    if not row or not row[0]:
        print("  (skipped: 'Snatch Balance' seed row absent)")
        loader.close()
        return
    curated = row[0]

    loader.load_exercise({
        "name": "Snatch Balance", "category": "competition_variant",
        "movement_family": "snatch", "primary_purpose": "heuristic first sentence",
        "faults_addressed": [], "source_id": None,
    })
    cur = loader.conn.cursor()
    cur.execute("SELECT faults_addressed FROM exercises WHERE name = 'Snatch Balance'")
    after = cur.fetchone()[0]
    cur.close()
    assert after == curated, f"curated faults wiped: {curated} → {after}"
    print("  load_exercise: curated faults_addressed preserved on conflict ✓")
    loader.close()


def test_load_exercise_variation_category_maps_to_valid_enum():
    """audit5-M1: '_parse_exercise' emits category='variation', which is not a
    valid exercise_category enum label → INSERT fails and the variant is lost.
    load_exercise must normalize it."""
    loader = make_loader()
    ex_id = loader.load_exercise({
        "name": f"{TEST_PREFIX}Power Clean Variant", "category": "variation",
        "movement_family": "clean", "primary_purpose": "test",
        "faults_addressed": [], "source_id": None,
    })
    assert isinstance(ex_id, int), "a 'variation' category must not drop the exercise (audit5-M1)"
    cur = loader.conn.cursor()
    cur.execute("SELECT category::text FROM exercises WHERE id = %s", (ex_id,))
    cat = cur.fetchone()[0]
    cur.execute("DELETE FROM exercises WHERE id = %s", (ex_id,))
    loader.conn.commit()
    cur.close()
    assert cat == "competition_variant", cat
    print(f"  load_exercise: 'variation' normalized to {cat!r} ✓")
    loader.close()


def test_load_principles_savepoint_keeps_valid_rows():
    """audit5-M3: a mid-batch bad row (priority 0 violates CHECK) rolled back
    ALL earlier uncommitted inserts while still counting them — valid rows lost."""
    from types import SimpleNamespace
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Savepoint Book", "Author", "book")

    def _p(name, priority):
        return SimpleNamespace(
            principle_name=f"{TEST_PREFIX}{name}", category="volume",
            rule_type="guideline", condition={}, recommendation={"x": 1},
            rationale="r", priority=priority,
        )
    principles = [_p("good1", 5), _p("good2", 5), _p("bad", 0), _p("good3", 5)]
    n = loader.load_principles(principles, sid)

    cur = loader.conn.cursor()
    cur.execute("SELECT count(*) FROM programming_principles WHERE source_id = %s", (sid,))
    stored = cur.fetchone()[0]
    cur.close()
    assert stored == 3, f"3 valid principles must survive the 1 bad row, got {stored}"
    assert n == stored, f"count must match stored rows, got {n} vs {stored}"
    print("  load_principles: bad row isolated, valid rows kept ✓")
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


def test_load_percentage_schemes_dedup_counts_rowcount():
    """ING-L3: ON CONFLICT DO NOTHING skips must not count as loaded."""
    import uuid
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Scheme Dedup Book", "Author", "book")
    # UNIQUE(scheme_name, week, day, order) has no source_id — a unique name
    # keeps this test independent of leftovers from earlier failed runs
    rows = [{
        "scheme_name": f"{TEST_PREFIX}Dedup {uuid.uuid4().hex[:8]}", "phase": "accumulation",
        "week_number": 1, "day_number": 1, "exercise_order": 1,
        "sets": 5, "reps": 3, "intensity_pct": 72.0, "intensity_reference": "snatch",
    }]
    n1 = loader.load_percentage_schemes(rows, sid)
    assert n1 == 1, f"first load should count 1, got {n1}"
    n2 = loader.load_percentage_schemes(rows, sid)
    assert n2 == 0, f"conflict-skipped row must not count (ING-L3), got {n2}"

    print("  load_percentage_schemes: rowcount-based dedup count ✓")
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


def test_load_program_same_name_distinct_structure_both_load():
    """Audit2-H1: template names are auto-generated ("Program from {title}") so
    EVERY template of a source shares a name — dedup keyed on (source_id, name)
    alone silently discards distinct programs (Takano: 16 → 1). Identity must
    include the structure."""
    loader = make_loader()
    sid = loader.upsert_source(f"{TEST_PREFIX}Same Name Book", "Author", "book")

    base = {
        "name": f"{TEST_PREFIX}Program from Same Name Book",
        "source_id": sid,
        "athlete_level": "any",
        "goal": "general_strength",
        "duration_weeks": 4,
        "sessions_per_week": 3,
    }
    id1 = loader.load_program({**base, "program_structure": {"weeks": [{"week_number": 1}]}})
    id2 = loader.load_program({**base, "program_structure": {"weeks": [{"week_number": 2}]}})
    assert isinstance(id1, int)
    assert isinstance(id2, int), "distinct structure under the same auto-name must NOT be deduped"

    cur = loader.conn.cursor()
    cur.execute("SELECT count(*) FROM program_templates WHERE source_id = %s", (sid,))
    total = cur.fetchone()[0]
    cur.close()
    assert total == 2, f"expected both templates stored, got {total}"

    print("  load_program: same-name distinct-structure templates both kept ✓")
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
        test_upsert_source_distinct_urls_distinct_sources,
        test_upsert_source_repeated_slug_collision_no_crash,
        test_load_exercise,
        test_load_exercise_preserves_curated_faults,
        test_load_exercise_variation_category_maps_to_valid_enum,
        test_load_principles_savepoint_keeps_valid_rows,
        test_load_percentage_schemes,
        test_load_percentage_schemes_dedup_counts_rowcount,
        test_load_program,
        test_load_program_dedup,
        test_load_program_same_name_distinct_structure_both_load,
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
