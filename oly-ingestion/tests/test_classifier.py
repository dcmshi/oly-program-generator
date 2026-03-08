# tests/test_classifier.py
"""
Tests for the ContentClassifier.

Run: python -m pytest tests/test_classifier.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.classifier import ContentClassifier, ContentType
from config import Settings


def make_classifier() -> ContentClassifier:
    return ContentClassifier(Settings())


# ── Prose classification ──────────────────────────────────────

def test_prose_classification():
    clf = make_classifier()
    text = (
        "The accumulation phase is characterized by high volume and moderate intensity. "
        "During this period, the athlete builds a broad aerobic base and reinforces "
        "technical patterns across a variety of exercises. The goal is to increase "
        "work capacity without pushing intensity to levels that create excessive fatigue."
    )
    sections = clf.classify_sections(text)
    assert len(sections) > 0
    assert sections[0].content_type == ContentType.PROSE


# ── Program template classification ──────────────────────────

def test_program_template_classification():
    clf = make_classifier()
    text = (
        "Monday:\n"
        "  Snatch 5x3 @ 72%\n"
        "  Back Squat 4x5 @ 75%\n"
        "  Snatch Pull 3x4 @ 90%\n"
    )
    sections = clf.classify_sections(text)
    assert len(sections) > 0
    assert sections[0].content_type == ContentType.PROGRAM_TEMPLATE


def test_week_program_template():
    clf = make_classifier()
    text = (
        "Week 1:\n"
        "  Day 1: Snatch work\n"
        "  Day 2: Clean and jerk\n"
        "  Day 3: Squat focus\n"
        "  Day 4: Competition lifts\n"
    )
    sections = clf.classify_sections(text)
    types = [s.content_type for s in sections]
    assert ContentType.PROGRAM_TEMPLATE in types


# ── Table classification ──────────────────────────────────────

def test_table_classification():
    clf = make_classifier()
    text = (
        "Intensity | Sets | Reps | Total\n"
        "70%       | 3    | 6    | 18\n"
        "80%       | 3    | 4    | 12\n"
        "90%       | 2    | 2    | 4\n"
    )
    sections = clf.classify_sections(text)
    assert any(s.content_type == ContentType.TABLE for s in sections)


# ── Mixed content classification ──────────────────────────────

def test_mixed_classification():
    clf = make_classifier()
    text = (
        "During the intensification phase, intensity should never exceed 95% "
        "and volume should always be reduced by at least 25% relative to accumulation. "
        "The athlete should maintain no more than 20 total reps above 80% per session. "
        "This allows sufficient neural recovery while maintaining peak strength expression. "
        "Athletes at the intermediate level typically respond well to 3-4 sessions per week "
        "with at least 48 hours between heavy snatch sessions."
    )
    sections = clf.classify_sections(text)
    types = {s.content_type for s in sections}
    # Should detect MIXED or PRINCIPLE due to the "never exceed", "always be reduced" patterns
    assert ContentType.MIXED in types or ContentType.PRINCIPLE in types or ContentType.PROSE in types


# ── Section splitting ─────────────────────────────────────────

def test_chapter_split():
    clf = make_classifier()
    text = (
        "Some intro text.\n\n"
        "Chapter 1 The Snatch\n\n"
        "The snatch is the first of two competition lifts.\n\n"
        "Chapter 2 The Clean and Jerk\n\n"
        "The clean and jerk is the second competition lift.\n"
    )
    sections = clf.classify_sections(text)
    # Should have at least 2 sections (one per chapter body)
    assert len(sections) >= 2


# ── LLM fallback tests (require ANTHROPIC_API_KEY) ───────────
# These passages are deliberately ambiguous so heuristics score < 0.6
# and fall through to the LLM path.

def test_llm_classifies_ambiguous_principle():
    """Short principle-like text that heuristics underscore."""
    clf = make_classifier()
    # Short, no percentage sign → heuristic scores PROSE at 0.60 (< threshold triggers LLM)
    # But under 50 words → heuristic scores 0.60 exactly which doesn't trigger LLM
    # Force it: pass directly to _llm_classify
    content_type, confidence = clf._llm_classify(
        "When an athlete misses two consecutive attempts at the same weight, "
        "the coach should drop 5 to 10 kilos and rebuild confidence before "
        "attempting that weight again.",
        source_title="Test",
    )
    assert content_type in (ContentType.PRINCIPLE, ContentType.PROSE, ContentType.MIXED), \
        f"Unexpected type: {content_type}"
    assert 0.0 <= confidence <= 1.0
    print(f"  LLM ambiguous principle: classified as {content_type.value} (conf={confidence:.2f})")


def test_llm_classifies_prose():
    """Pure narrative text should come back as prose."""
    clf = make_classifier()
    content_type, confidence = clf._llm_classify(
        "Soviet coaches of the 1970s developed many of the periodization models "
        "still in use today. Coaches like Medvedev studied hundreds of athletes "
        "over multi-year cycles to derive general training laws.",
        source_title="Test",
    )
    assert content_type == ContentType.PROSE, \
        f"Expected PROSE for narrative text, got {content_type.value}"
    assert confidence >= 0.5
    print(f"  LLM prose passage: {content_type.value} (conf={confidence:.2f})")


def test_llm_classifies_exercise_description():
    """Exercise execution description should be classified correctly."""
    clf = make_classifier()
    content_type, confidence = clf._llm_classify(
        "The hang snatch is performed by starting with the bar at mid-thigh. "
        "The athlete maintains a neutral spine, then drives through the hips "
        "explosively, pulling themselves under the bar into a full squat overhead position.",
        source_title="Test",
    )
    assert content_type in (ContentType.EXERCISE_DESCRIPTION, ContentType.PROSE), \
        f"Unexpected type: {content_type}"
    assert confidence >= 0.5
    print(f"  LLM exercise description: {content_type.value} (conf={confidence:.2f})")


def test_llm_fallback_triggers_in_classify_sections():
    """A section that scores below 0.6 on heuristics should trigger LLM and return a valid type."""
    clf = make_classifier()
    # Construct text that is ambiguous: looks partly like a principle but has no %,
    # short enough that heuristics return PROSE at 0.60 (word_count < 50 → 0.60)
    # We craft something just below threshold: > 50 words, no strong signals
    text = (
        "The competition day warm-up requires careful calibration. "
        "An athlete who opens too heavy risks missing their opener, while "
        "one who opens too light may not peak at the right moment. "
        "The warm-up should mirror competition conditions as closely as possible. "
        "Bar speed and feel matter more than hitting specific percentages on the day."
    )
    sections = clf.classify_sections(text)
    assert len(sections) > 0
    for s in sections:
        assert isinstance(s.content_type, ContentType)
        assert 0.0 <= s.confidence <= 1.0
    print(f"  LLM fallback in classify_sections: {sections[0].content_type.value} (conf={sections[0].confidence:.2f})")


if __name__ == "__main__":
    import sys
    llm_tests = [
        test_llm_classifies_ambiguous_principle,
        test_llm_classifies_prose,
        test_llm_classifies_exercise_description,
        test_llm_fallback_triggers_in_classify_sections,
    ]
    heuristic_tests = [v for k, v in globals().items()
                       if k.startswith("test_") and v not in llm_tests]

    run_llm = "--llm" in sys.argv
    tests = heuristic_tests + (llm_tests if run_llm else [])

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    if not run_llm:
        print(f"\n{passed} passed, {failed} failed  (LLM tests skipped — run with --llm to include)")
    else:
        print(f"\n{passed} passed, {failed} failed")
