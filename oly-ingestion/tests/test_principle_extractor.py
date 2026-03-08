# tests/test_principle_extractor.py
"""
Integration tests for PrincipleExtractor against the live Anthropic API.

Requires:
  - ANTHROPIC_API_KEY set in .env
  - Docker Postgres running (for the load_into_db test)

Run: PYTHONUTF8=1 uv run python tests/test_principle_extractor.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Settings
from processors.principle_extractor import PrincipleExtractor, ExtractedPrinciple
from loaders.structured_loader import StructuredLoader

TEST_PREFIX = "__test__"

# ── Realistic test passages ────────────────────────────────────
# These are paraphrased programming principles from weightlifting literature.
# Each passage is chosen to reliably produce extractable principles.

PEAKING_PASSAGE = """
During the final two weeks before competition, training volume should be
reduced by 40 to 60 percent while intensity remains at or above 90 percent
of maximum. This allows the nervous system to recover from accumulated fatigue
while maintaining the neural patterns required for maximal expression of strength.
Performing more than three heavy singles in a session during this phase is
counterproductive and risks injury or peak suppression.
"""

PRILEPIN_PASSAGE = """
At intensities between 70 and 80 percent of the one-rep maximum, athletes
should aim for 18 to 24 total repetitions per session across all competition
lift variations. Sets of 3 to 5 repetitions work best in this zone.
Exceeding 30 total reps at this intensity without adequate recovery leads to
accumulated fatigue that depresses performance in subsequent sessions.
Beginner athletes may need to stay at the lower end of this range until
their work capacity has been established over at least one year of training.
"""

DELOAD_PASSAGE = """
A planned deload should be programmed every four weeks for intermediate and
advanced lifters. During the deload week, reduce volume by 50 percent and
keep intensity at or below 80 percent. Competition lifts should still be
performed to maintain skill, but supplemental work should be eliminated.
Failing to deload leads to cumulative fatigue that manifests as stalled
progress and elevated injury risk.
"""

NO_PRINCIPLES_PASSAGE = """
The history of Olympic weightlifting in the Soviet Union is a fascinating
subject. Coaches like Medvedev and Vorobyev dedicated their careers to
understanding the sport. The lifters of that era trained in conditions
very different from modern athletes, yet achieved remarkable results.
"""


def make_extractor() -> PrincipleExtractor:
    return PrincipleExtractor(Settings())


# ── Tests ──────────────────────────────────────────────────────

def test_extracts_principles_from_peaking_passage():
    """Should extract at least one principle with correct structure."""
    extractor = make_extractor()
    principles = extractor.extract(
        text=PEAKING_PASSAGE,
        source_title="Test Source",
        source_id=1,
    )
    assert len(principles) >= 1, f"Expected >= 1 principle, got {len(principles)}"

    for p in principles:
        assert isinstance(p, ExtractedPrinciple)
        assert p.principle_name, "principle_name must not be empty"
        assert p.category in [
            "volume", "intensity", "frequency", "exercise_selection",
            "periodization", "peaking", "recovery", "technique",
            "load_progression", "deload",
        ], f"Unexpected category: {p.category}"
        assert p.rule_type in ["hard_constraint", "guideline", "heuristic"], \
            f"Unexpected rule_type: {p.rule_type}"
        assert isinstance(p.condition, dict)
        assert isinstance(p.recommendation, dict)
        assert 1 <= p.priority <= 10, f"Priority out of range: {p.priority}"

    print(f"  peaking passage: {len(principles)} principle(s) extracted")
    for p in principles:
        print(f"    - [{p.category}] {p.principle_name} (priority={p.priority})")


def test_extracts_volume_intensity_rules():
    """Prilepin-style passage should yield volume/intensity principles."""
    extractor = make_extractor()
    principles = extractor.extract(
        text=PRILEPIN_PASSAGE,
        source_title="Test Source",
        source_id=1,
    )
    assert len(principles) >= 1, f"Expected >= 1 principle, got {len(principles)}"

    categories = {p.category for p in principles}
    assert categories & {"volume", "intensity", "load_progression"}, \
        f"Expected volume/intensity/load_progression category, got {categories}"

    print(f"  prilepin passage: {len(principles)} principle(s) — categories: {categories}")


def test_returns_empty_for_non_programming_text():
    """Narrative/historical text should return empty list, not crash."""
    extractor = make_extractor()
    principles = extractor.extract(
        text=NO_PRINCIPLES_PASSAGE,
        source_title="Test Source",
        source_id=1,
    )
    # We don't assert exactly 0 (LLM may occasionally find something),
    # but it should be very low and must not raise an exception.
    assert isinstance(principles, list)
    assert len(principles) <= 2, \
        f"Expected 0-2 principles from narrative text, got {len(principles)}"
    print(f"  narrative passage: {len(principles)} principle(s) (expected ~0)")


def test_extracts_multiple_principles_from_deload_passage():
    """Deload passage should yield at least 2 distinct principles."""
    extractor = make_extractor()
    principles = extractor.extract(
        text=DELOAD_PASSAGE,
        source_title="Test Source",
        source_id=1,
    )
    assert len(principles) >= 2, \
        f"Expected >= 2 principles from deload passage, got {len(principles)}"

    names = [p.principle_name for p in principles]
    print(f"  deload passage: {len(principles)} principle(s)")
    for p in principles:
        print(f"    - [{p.rule_type}] {p.principle_name}")


def test_recommendation_fields_are_valid():
    """Peaking passage should produce a recommendation with numeric fields."""
    extractor = make_extractor()
    principles = extractor.extract(
        text=PEAKING_PASSAGE,
        source_title="Test Source",
        source_id=1,
    )
    assert principles, "No principles extracted"

    # At least one principle should have a recommendation with numeric content
    has_numeric = False
    for p in principles:
        rec = p.recommendation
        if any(isinstance(v, (int, float)) for v in rec.values()):
            has_numeric = True
            break
    assert has_numeric, \
        f"Expected at least one numeric recommendation field. Got: {[p.recommendation for p in principles]}"

    print(f"  recommendation fields: numeric values present OK")


def test_load_extracted_principles_into_db():
    """End-to-end: extract principles and store them via StructuredLoader."""
    extractor = make_extractor()
    settings = Settings()
    loader = StructuredLoader(settings)

    sid = loader.upsert_source(f"{TEST_PREFIX}Principle Book", "Test Author", "book")

    principles = extractor.extract(
        text=PEAKING_PASSAGE,
        source_title=f"{TEST_PREFIX}Principle Book",
        source_id=sid,
    )
    assert principles, "No principles to store"

    count = loader.load_principles(principles, source_id=sid)
    assert count == len(principles), f"Expected {len(principles)} stored, got {count}"

    # Verify in DB
    cur = loader.conn.cursor()
    cur.execute(
        "SELECT count(*) FROM programming_principles WHERE source_id = %s",
        (sid,),
    )
    db_count = cur.fetchone()[0]
    cur.close()
    assert db_count == len(principles), f"DB count mismatch: {db_count}"

    print(f"  load into DB: {count} principle(s) stored for source_id={sid} OK")

    # Cleanup
    cur = loader.conn.cursor()
    cur.execute("DELETE FROM programming_principles WHERE source_id = %s", (sid,))
    cur.execute("DELETE FROM sources WHERE id = %s", (sid,))
    loader.conn.commit()
    cur.close()
    loader.close()


# ── Runner ─────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_extracts_principles_from_peaking_passage,
        test_extracts_volume_intensity_rules,
        test_returns_empty_for_non_programming_text,
        test_extracts_multiple_principles_from_deload_passage,
        test_recommendation_fields_are_valid,
        test_load_extracted_principles_into_db,
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
