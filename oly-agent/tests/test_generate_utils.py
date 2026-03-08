# oly-agent/tests/test_generate_utils.py
"""
Tests for the pure utility functions in generate.py:
  - parse_llm_response()
  - validate_exercise_names()

No DB or API keys needed.

Run: python tests/test_generate_utils.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from generate import parse_llm_response, validate_exercise_names


VALID_EXERCISE = {
    "exercise_name": "Snatch",
    "exercise_order": 1,
    "sets": 4,
    "reps": 3,
    "intensity_pct": 75,
    "intensity_reference": "snatch",
    "rest_seconds": 180,
    "rpe_target": 7.5,
    "selection_rationale": "Primary competition lift for the session.",
    "source_principle_ids": [],
}

AVAILABLE = ["Snatch", "Clean & Jerk", "Back Squat", "Front Squat", "Snatch Pull"]


# ── parse_llm_response ────────────────────────────────────────

def test_parse_plain_json_array():
    """Plain JSON array is parsed directly."""
    raw = json.dumps([VALID_EXERCISE])
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["exercise_name"] == "Snatch"
    return True, ""


def test_parse_json_with_markdown_fences():
    """JSON wrapped in ```json ... ``` fences is stripped."""
    raw = f"```json\n{json.dumps([VALID_EXERCISE])}\n```"
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    return True, ""


def test_parse_json_with_plain_fences():
    """JSON wrapped in ``` ... ``` (no language tag) is stripped."""
    raw = f"```\n{json.dumps([VALID_EXERCISE])}\n```"
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    return True, ""


def test_parse_json_array_embedded_in_text():
    """JSON array embedded after preamble text is extracted."""
    raw = f"Here is the session:\n{json.dumps([VALID_EXERCISE])}\nEnd."
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    return True, ""


def test_parse_single_object_wrapped_in_list():
    """A single JSON object (not array) is wrapped in a list."""
    raw = json.dumps(VALID_EXERCISE)
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["exercise_name"] == "Snatch"
    return True, ""


def test_parse_multiple_exercises():
    """Multiple exercises in an array are all returned."""
    exercises = [dict(VALID_EXERCISE, exercise_order=i, exercise_name=f"Exercise {i}")
                 for i in range(1, 6)]
    raw = json.dumps(exercises)
    result = parse_llm_response(raw)
    assert len(result) == 5
    return True, ""


def test_parse_invalid_json_raises():
    """Completely invalid JSON raises ValueError."""
    try:
        parse_llm_response("This is not JSON at all.")
        return False, "Expected ValueError, got none"
    except ValueError:
        return True, ""


def test_parse_empty_array():
    """Empty JSON array returns empty list."""
    result = parse_llm_response("[]")
    assert result == []
    return True, ""


def test_parse_preserves_all_fields():
    """All fields in the exercise dict are preserved."""
    raw = json.dumps([VALID_EXERCISE])
    result = parse_llm_response(raw)
    for key in VALID_EXERCISE:
        assert key in result[0], f"Missing field: {key}"
    return True, ""


# ── validate_exercise_names ───────────────────────────────────

def test_validate_all_valid_names():
    """All valid names → no errors."""
    exercises = [{"exercise_name": name} for name in AVAILABLE]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert errors == [], errors
    return True, ""


def test_validate_unknown_name_returns_error():
    """Unknown exercise name returns an error."""
    exercises = [{"exercise_name": "Log Press"}]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert len(errors) == 1
    assert "Log Press" in errors[0]
    return True, ""


def test_validate_close_match_suggests_alternative():
    """Name that partially matches an available exercise includes a suggestion."""
    exercises = [{"exercise_name": "Power Snatch"}]  # 'Snatch' is a substring
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert len(errors) == 1
    assert "Snatch" in errors[0], errors[0]  # suggestion included
    return True, ""


def test_validate_case_insensitive():
    """Exercise name matching is case-insensitive — 'snatch' matches 'Snatch'."""
    exercises = [{"exercise_name": "snatch"}]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert errors == [], f"Expected no errors (case-insensitive match), got: {errors}"
    return True, ""


def test_validate_multiple_invalid():
    """Multiple invalid names all produce errors."""
    exercises = [
        {"exercise_name": "Log Press"},
        {"exercise_name": "Snatch"},  # valid
        {"exercise_name": "Deadlift"},
    ]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert len(errors) == 2, errors
    return True, ""


def test_validate_empty_list():
    """Empty exercise list → no errors."""
    errors = validate_exercise_names([], AVAILABLE)
    assert errors == []
    return True, ""


# ── Runner ────────────────────────────────────────────────────

TESTS = [
    # parse_llm_response
    ("parse: plain JSON array", test_parse_plain_json_array),
    ("parse: JSON with ```json fences", test_parse_json_with_markdown_fences),
    ("parse: JSON with plain ``` fences", test_parse_json_with_plain_fences),
    ("parse: JSON array embedded in text", test_parse_json_array_embedded_in_text),
    ("parse: single object → wrapped in list", test_parse_single_object_wrapped_in_list),
    ("parse: multiple exercises returned", test_parse_multiple_exercises),
    ("parse: invalid JSON → ValueError", test_parse_invalid_json_raises),
    ("parse: empty array → empty list", test_parse_empty_array),
    ("parse: all fields preserved", test_parse_preserves_all_fields),
    # validate_exercise_names
    ("validate names: all valid → no errors", test_validate_all_valid_names),
    ("validate names: unknown → error", test_validate_unknown_name_returns_error),
    ("validate names: close match → suggestion", test_validate_close_match_suggests_alternative),
    ("validate names: case insensitive match", test_validate_case_insensitive),
    ("validate names: multiple invalid", test_validate_multiple_invalid),
    ("validate names: empty list → no errors", test_validate_empty_list),
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
    print("GENERATE UTILS — Test Results")
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
