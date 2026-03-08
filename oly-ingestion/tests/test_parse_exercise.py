# tests/test_parse_exercise.py
"""
Tests for pipeline._parse_exercise() heuristic extraction.

No DB or API keys needed — tests the pure heuristic logic only.
Run: python tests/test_parse_exercise.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import IngestionPipeline
from processors.classifier import ClassifiedSection, ContentType


# ---------------------------------------------------------------------------
# Minimal stub so we can instantiate IngestionPipeline without real settings
# or DB connections.
# ---------------------------------------------------------------------------

class _FakeSettings:
    anthropic_api_key = ""
    embedding_model = "text-embedding-3-small"
    llm_model = "claude-sonnet-4-6"
    batch_size = 10
    validate_chunks = False
    quarantine_invalid_chunks = False
    database_url = ""

class _FakeLoader:
    conn = None

class _FakePrincipleExtractor:
    pass

def _make_pipeline():
    """Build a bare IngestionPipeline with all heavy deps stubbed out."""
    p = object.__new__(IngestionPipeline)
    p.settings = _FakeSettings()
    p.max_pages = 0
    p.pdf_extractor = None
    p.classifier = None
    p.principle_extractor = _FakePrincipleExtractor()
    p.vector_loader = _FakeLoader()
    p.structured_loader = _FakeLoader()
    return p


def _section(title: str, content: str) -> ClassifiedSection:
    return ClassifiedSection(
        content=content,
        content_type=ContentType.EXERCISE_DESCRIPTION,
        metadata={"title": title, "chapter": ""},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

RESULTS = []

def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, str(e)))


pipeline = _make_pipeline()


def test_name_from_title():
    section = _section("Power Snatch", "The power snatch is performed with a high pull. The bar is caught above parallel.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["name"] == "Power Snatch", result
    assert result["movement_family"] == "snatch", result
    assert result["category"] == "variation", result

def test_name_from_content_when_no_title():
    section = _section("", "Hang Clean\n\nThe hang clean develops rate of force development from the hang position.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["name"] == "Hang Clean", result
    assert result["movement_family"] == "clean", result
    assert result["category"] == "variation", result

def test_competition_lift_category():
    section = _section("Snatch", "The snatch is the first competition lift.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["category"] == "competition_variant", result
    assert result["movement_family"] == "snatch", result

def test_clean_and_jerk():
    section = _section("Clean & Jerk", "The clean and jerk is the second competition lift.")
    result = pipeline._parse_exercise(section, source_id=1)
    # clean takes priority over jerk in name scan
    assert result["movement_family"] == "clean", result

def test_jerk_only():
    section = _section("Split Jerk", "The split jerk is caught with a split stance.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["movement_family"] == "jerk", result
    assert result["category"] == "competition_variant", result

def test_squat_family():
    section = _section("Front Squat", "The front squat builds the receiving position for the clean.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["movement_family"] == "squat", result
    assert result["category"] == "strength", result

def test_pull_family():
    # "Snatch Pull" is snatch-family (snatch checked before pull in name scan)
    section = _section("Snatch Pull", "The snatch pull develops pulling strength.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["movement_family"] == "snatch", result
    # Pure pull (no competition lift prefix) → pull family + strength category
    section2 = _section("Romanian Deadlift", "The RDL builds posterior chain strength.")
    result2 = pipeline._parse_exercise(section2, source_id=1)
    assert result2["movement_family"] == "pull", result2
    assert result2["category"] == "strength", result2

def test_press_family():
    section = _section("Push Press", "The push press is used to develop overhead strength.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["movement_family"] == "press", result
    assert result["category"] == "strength", result

def test_primary_purpose_extracted():
    section = _section("Block Snatch", "The block snatch develops positional strength from a specific height. It is used in accumulation phases.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert "block snatch" in result["primary_purpose"].lower(), result

def test_source_id_passed_through():
    section = _section("Muscle Snatch", "The muscle snatch builds upper body pulling strength.")
    result = pipeline._parse_exercise(section, source_id=99)
    assert result["source_id"] == 99, result

def test_empty_when_no_name():
    section = _section("", "This section has no recognisable exercise name in it at all.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result == {}, f"Expected empty dict, got {result}"

def test_the_prefix_stripped():
    section = _section("The Snatch Balance", "The snatch balance is a classic overhead stability exercise.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["name"] == "Snatch Balance", result

def test_faults_addressed_default_empty():
    section = _section("Deficit Snatch", "A snatch pulled from a deficit plate.")
    result = pipeline._parse_exercise(section, source_id=1)
    assert result["faults_addressed"] == [], result


if __name__ == "__main__":
    for fn_name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(fn_name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        status = r[0]
        name = r[1]
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {status}  {name}{detail}")
    print(f"\n{passed} passed, {failed} failed")
