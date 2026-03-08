# oly-agent/tests/test_weight_resolver.py
"""
Tests for weight_resolver.py — build_maxes_dict, resolve_weights,
resolve_exercise_ids, attach_source_chunk_ids.

No DB or API keys needed.

Run: python tests/test_weight_resolver.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from weight_resolver import (
    build_maxes_dict,
    resolve_exercise_ids,
    resolve_weights,
    attach_source_chunk_ids,
)


# ── Fixtures ───────────────────────────────────────────────────

SAMPLE_DB_MAXES = [
    {"name": "Snatch", "weight_kg": 100.0},
    {"name": "Clean & Jerk", "weight_kg": 125.0},
    {"name": "Back Squat", "weight_kg": 160.0},
    {"name": "Front Squat", "weight_kg": 135.0},
    {"name": "Snatch Pull", "weight_kg": 120.0},
]

SAMPLE_EXERCISE_LOOKUP = {
    "snatch": 1,
    "clean & jerk": 2,
    "back squat": 3,
    "front squat": 4,
    "snatch pull": 5,
    "hang snatch": 6,
}


# ── build_maxes_dict ──────────────────────────────────────────

def test_build_maxes_known_names():
    """Known exercise names map to correct intensity_reference keys."""
    maxes = build_maxes_dict(SAMPLE_DB_MAXES)
    assert maxes.get("snatch") == 100.0, maxes
    assert maxes.get("clean_and_jerk") == 125.0, maxes
    assert maxes.get("back_squat") == 160.0, maxes
    return True, ""


def test_build_maxes_unknown_name_snake_case():
    """Unknown exercise names fall back to snake_case."""
    rows = [{"name": "Romanian Deadlift", "weight_kg": 140.0}]
    maxes = build_maxes_dict(rows)
    assert "romanian_deadlift" in maxes, maxes
    assert maxes["romanian_deadlift"] == 140.0
    return True, ""


def test_build_maxes_float_conversion():
    """weight_kg is stored as float."""
    rows = [{"name": "Snatch", "weight_kg": "100"}]
    maxes = build_maxes_dict(rows)
    assert isinstance(maxes["snatch"], float)
    return True, ""


def test_build_maxes_empty():
    """Empty input returns empty dict."""
    assert build_maxes_dict([]) == {}
    return True, ""


# ── resolve_weights ───────────────────────────────────────────

def test_resolve_weights_basic():
    """100kg snatch × 75% = 75.0kg."""
    maxes = {"snatch": 100.0}
    exercises = [{"exercise_name": "Snatch", "intensity_pct": 75, "intensity_reference": "snatch"}]
    result = resolve_weights(exercises, maxes)
    assert result[0]["absolute_weight_kg"] == 75.0, result[0]
    return True, ""


def test_resolve_weights_rounds_to_half_kg():
    """Results round to nearest 0.5kg."""
    maxes = {"snatch": 100.0}
    # 100 × 73.3% = 73.3 → rounds to 73.5
    exercises = [{"exercise_name": "Snatch", "intensity_pct": 73.3, "intensity_reference": "snatch"}]
    result = resolve_weights(exercises, maxes)
    kg = result[0]["absolute_weight_kg"]
    assert kg % 0.5 == 0.0, f"Not a half-kg multiple: {kg}"
    return True, ""


def test_resolve_weights_125kg_cj_80pct():
    """125kg C&J × 80% = 100.0kg."""
    maxes = {"clean_and_jerk": 125.0}
    exercises = [{"exercise_name": "Clean & Jerk", "intensity_pct": 80, "intensity_reference": "clean_and_jerk"}]
    result = resolve_weights(exercises, maxes)
    assert result[0]["absolute_weight_kg"] == 100.0
    return True, ""


def test_resolve_weights_missing_ref_returns_none():
    """If intensity_reference is not in maxes, absolute_weight_kg is None."""
    maxes = {"snatch": 100.0}
    exercises = [{"exercise_name": "Box Jump", "intensity_pct": None, "intensity_reference": None}]
    result = resolve_weights(exercises, maxes)
    assert result[0]["absolute_weight_kg"] is None
    return True, ""


def test_resolve_weights_unknown_ref_returns_none():
    """Unknown intensity_reference (no max on file) returns None."""
    maxes = {"snatch": 100.0}
    exercises = [{"exercise_name": "Log Press", "intensity_pct": 70, "intensity_reference": "log_press"}]
    result = resolve_weights(exercises, maxes)
    assert result[0]["absolute_weight_kg"] is None
    return True, ""


def test_resolve_weights_multiple_exercises():
    """Multiple exercises are all resolved independently."""
    maxes = {"snatch": 100.0, "back_squat": 160.0}
    exercises = [
        {"exercise_name": "Snatch", "intensity_pct": 75, "intensity_reference": "snatch"},
        {"exercise_name": "Back Squat", "intensity_pct": 80, "intensity_reference": "back_squat"},
    ]
    result = resolve_weights(exercises, maxes)
    assert result[0]["absolute_weight_kg"] == 75.0
    assert result[1]["absolute_weight_kg"] == 128.0
    return True, ""


# ── resolve_exercise_ids ──────────────────────────────────────

def test_resolve_exercise_ids_found():
    """Known exercise names resolve to their DB id."""
    exercises = [{"exercise_name": "Snatch", "intensity_pct": 75, "intensity_reference": "snatch"}]
    result = resolve_exercise_ids(exercises, SAMPLE_EXERCISE_LOOKUP)
    assert result[0]["exercise_id"] == 1
    return True, ""


def test_resolve_exercise_ids_case_insensitive():
    """Lookup is case-insensitive (exercise_name.lower() is used)."""
    exercises = [{"exercise_name": "BACK SQUAT", "intensity_pct": 75, "intensity_reference": "back_squat"}]
    result = resolve_exercise_ids(exercises, SAMPLE_EXERCISE_LOOKUP)
    assert result[0]["exercise_id"] == 3
    return True, ""


def test_resolve_exercise_ids_not_found_returns_none():
    """Unknown exercise name sets exercise_id to None."""
    exercises = [{"exercise_name": "Log Press", "intensity_pct": 70, "intensity_reference": "log_press"}]
    result = resolve_exercise_ids(exercises, SAMPLE_EXERCISE_LOOKUP)
    assert result[0]["exercise_id"] is None
    return True, ""


def test_resolve_exercise_ids_empty_list():
    """Empty list returns empty list."""
    result = resolve_exercise_ids([], SAMPLE_EXERCISE_LOOKUP)
    assert result == []
    return True, ""


# ── attach_source_chunk_ids ───────────────────────────────────

def test_attach_chunk_ids_fault_rationale():
    """Exercises with fault-related rationale get fault chunk IDs."""
    exercises = [{"exercise_name": "Snatch", "selection_rationale": "Addresses slow turnover fault"}]
    context = {
        "programming_rationale": [{"id": 10}, {"id": 11}],
        "fault_correction_chunks": [{"id": 20}, {"id": 21}],
    }
    result = attach_source_chunk_ids(exercises, context)
    ids = set(result[0]["source_chunk_ids"])
    assert 20 in ids and 21 in ids, ids  # fault ids included
    assert 10 in ids and 11 in ids, ids  # rationale ids always included
    return True, ""


def test_attach_chunk_ids_no_fault_rationale():
    """Exercises without fault rationale only get programming_rationale IDs."""
    exercises = [{"exercise_name": "Back Squat", "selection_rationale": "Build leg strength"}]
    context = {
        "programming_rationale": [{"id": 10}],
        "fault_correction_chunks": [{"id": 20}],
    }
    result = attach_source_chunk_ids(exercises, context)
    ids = set(result[0]["source_chunk_ids"])
    assert 10 in ids
    assert 20 not in ids, "fault IDs should not be attached without fault rationale"
    return True, ""


def test_attach_chunk_ids_empty_context():
    """Empty retrieval context → empty source_chunk_ids."""
    exercises = [{"exercise_name": "Snatch", "selection_rationale": ""}]
    result = attach_source_chunk_ids(exercises, {"programming_rationale": [], "fault_correction_chunks": []})
    assert result[0]["source_chunk_ids"] == []
    return True, ""


def test_attach_chunk_ids_no_duplicates():
    """source_chunk_ids contains no duplicates."""
    exercises = [{"exercise_name": "Snatch", "selection_rationale": "correct fault"}]
    context = {
        "programming_rationale": [{"id": 10}, {"id": 10}],  # duplicate
        "fault_correction_chunks": [{"id": 10}],             # same ID
    }
    result = attach_source_chunk_ids(exercises, context)
    ids = result[0]["source_chunk_ids"]
    assert len(ids) == len(set(ids)), f"Duplicate IDs: {ids}"
    return True, ""


# ── Runner ────────────────────────────────────────────────────

TESTS = [
    # build_maxes_dict
    ("build_maxes_dict: known names → correct refs", test_build_maxes_known_names),
    ("build_maxes_dict: unknown name → snake_case fallback", test_build_maxes_unknown_name_snake_case),
    ("build_maxes_dict: weight stored as float", test_build_maxes_float_conversion),
    ("build_maxes_dict: empty input → empty dict", test_build_maxes_empty),
    # resolve_weights
    ("resolve_weights: 100kg × 75% = 75.0", test_resolve_weights_basic),
    ("resolve_weights: rounds to 0.5kg", test_resolve_weights_rounds_to_half_kg),
    ("resolve_weights: 125kg × 80% = 100.0", test_resolve_weights_125kg_cj_80pct),
    ("resolve_weights: no pct/ref → None", test_resolve_weights_missing_ref_returns_none),
    ("resolve_weights: unknown ref → None", test_resolve_weights_unknown_ref_returns_none),
    ("resolve_weights: multiple exercises resolved", test_resolve_weights_multiple_exercises),
    # resolve_exercise_ids
    ("resolve_exercise_ids: found → correct id", test_resolve_exercise_ids_found),
    ("resolve_exercise_ids: case insensitive", test_resolve_exercise_ids_case_insensitive),
    ("resolve_exercise_ids: not found → None", test_resolve_exercise_ids_not_found_returns_none),
    ("resolve_exercise_ids: empty list → empty", test_resolve_exercise_ids_empty_list),
    # attach_source_chunk_ids
    ("attach_chunk_ids: fault rationale → fault + rationale IDs", test_attach_chunk_ids_fault_rationale),
    ("attach_chunk_ids: no fault → only rationale IDs", test_attach_chunk_ids_no_fault_rationale),
    ("attach_chunk_ids: empty context → empty list", test_attach_chunk_ids_empty_context),
    ("attach_chunk_ids: no duplicate IDs", test_attach_chunk_ids_no_duplicates),
]


def main():
    failures = []
    results = []
    for name, fn in TESTS:
        try:
            ok, msg = fn()
            results.append((name, ok, msg))
            if not ok:
                failures.append(name)
        except Exception as e:
            results.append((name, False, str(e)))
            failures.append(name)

    print(f"\n{'='*50}")
    print("WEIGHT RESOLVER — Test Results")
    print(f"{'='*50}")
    for name, ok, msg in results:
        status = "✓" if ok else "✗"
        print(f"  {status} {name}" + (f"\n      {msg}" if not ok else ""))

    total = len(TESTS)
    passed = total - len(failures)
    print(f"\n{passed}/{total} passed")
    if failures:
        print(f"\nFailed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
