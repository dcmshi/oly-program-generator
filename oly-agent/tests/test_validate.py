# oly-agent/tests/test_validate.py
"""
Tests for the VALIDATE step (validate.py).

All checks are pure logic — no DB or API keys needed.

Run: python -m pytest tests/test_validate.py -v
  or: python tests/test_validate.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from validate import validate_session

# ── Fixtures ───────────────────────────────────────────────────

WEEK_TARGET = {
    "week_number": 1,
    "intensity_floor": 70,
    "intensity_ceiling": 80,
    "volume_modifier": 1.0,
    "reps_per_set_range": [3, 5],
    "is_deload": False,
    "total_competition_lift_reps": 18,
}

ATHLETE = {
    "name": "David",
    "session_duration_minutes": 90,
    "exercise_preferences": {"avoid": []},
}

PRINCIPLES = []


def _ex(name, sets, reps, pct, ref="snatch", order=1):
    return {
        "exercise_name": name,
        "exercise_order": order,
        "sets": sets,
        "reps": reps,
        "intensity_pct": pct,
        "intensity_reference": ref,
        "rest_seconds": 120,
        "rpe_target": 7.5,
    }


def run(label, passed, failed=None):
    if failed is None:
        failed = []
    total = len(passed) + len(failed)
    print(f"\n{'─'*50}")
    print(f"{label}")
    for name, ok, msg in passed:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f": {msg}" if not ok else ""))
    for name, ok, msg in failed:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f": {msg}" if not ok else ""))
    failures = [n for n, ok, _ in passed + failed if not ok]
    print(f"  {total - len(failures)}/{total} passed")
    return failures


# ── Check 1: Prilepin session volume ──────────────────────────

def test_prilepin_within_range():
    # 70-80% zone: range 12-24, optimal 18
    # 4 sets × 4 reps = 16 reps → within range, no errors or warnings
    exercises = [_ex("Snatch", 4, 4, 75)]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.is_valid, result.errors
    assert not result.warnings, result.warnings
    return True, ""


def test_prilepin_warning_above_range():
    # 5 × 5 = 25 reps → above range_high (24) but below hard cap (36)
    exercises = [_ex("Snatch", 5, 5, 75)]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.is_valid, "Should still be valid (warning only)"
    assert any("Prilepin" in w for w in result.warnings), result.warnings
    return True, ""


def test_prilepin_error_above_hard_cap():
    # 8 × 5 = 40 reps → exceeds hard cap of 36 (range_high 24 × 1.5)
    exercises = [_ex("Snatch", 8, 5, 75)]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert not result.is_valid, "Should be invalid"
    assert any("excessive" in e.lower() for e in result.errors), result.errors
    return True, ""


def test_prilepin_non_comp_lift_not_counted():
    # Romanian Deadlift is not a comp lift — should not count toward Prilepin volume
    exercises = [_ex("Romanian Deadlift", 8, 5, 75, ref="back_squat")]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.is_valid, result.errors
    assert not any("Prilepin" in e for e in result.errors), result.errors
    return True, ""


def test_prilepin_session_comp_reps_accumulated():
    exercises = [_ex("Snatch", 3, 3, 75)]  # 9 reps
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.session_comp_reps.get("70-80") == 9, result.session_comp_reps
    return True, ""


# ── Check 2: Intensity envelope ───────────────────────────────

def test_intensity_above_ceiling_error():
    exercises = [_ex("Snatch", 3, 2, 85)]  # 85% > ceiling 80%
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert not result.is_valid, "Should be invalid"
    assert any("ceiling" in e.lower() for e in result.errors), result.errors
    return True, ""


def test_intensity_at_ceiling_ok():
    exercises = [_ex("Snatch", 3, 2, 80)]  # exactly at ceiling
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert not any("ceiling" in e.lower() for e in result.errors), result.errors
    return True, ""


def test_intensity_below_floor_comp_lift_warning():
    exercises = [_ex("Snatch", 3, 3, 65)]  # 65% < floor 70%, comp lift
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.is_valid, "Should still be valid (warning only)"
    assert any("below week floor" in w for w in result.warnings), result.warnings
    return True, ""


def test_intensity_below_floor_non_comp_lift_no_warning():
    # Romanian Deadlift at 65% — not a comp lift, no floor warning
    exercises = [_ex("Romanian Deadlift", 3, 5, 65, ref="back_squat")]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert not any("floor" in w for w in result.warnings), result.warnings
    return True, ""


def test_no_intensity_pct_skipped():
    # Exercises with no intensity_pct (bodyweight, etc.) should be skipped
    exercises = [{"exercise_name": "Box Jump", "exercise_order": 1,
                  "sets": 3, "reps": 5, "intensity_pct": None,
                  "intensity_reference": None, "rest_seconds": 90}]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.is_valid, result.errors
    return True, ""


# ── Check 3: Reps per set ─────────────────────────────────────

def test_reps_above_90_limit_error():
    # Use a higher ceiling so we don't get a ceiling error masking the reps/set error
    wt = dict(WEEK_TARGET, intensity_ceiling=95)
    exercises = [_ex("Snatch", 3, 3, 92)]  # 3 reps at 92% — Prilepin allows max 2
    result = validate_session(exercises, wt, PRINCIPLES, ATHLETE)
    assert not result.is_valid, "Should be invalid"
    assert any("max 2 reps" in e for e in result.errors), result.errors
    return True, ""


def test_reps_at_90_limit_ok():
    wt = dict(WEEK_TARGET, intensity_ceiling=95)
    exercises = [_ex("Snatch", 4, 2, 92)]  # 2 reps at 92% — OK
    result = validate_session(exercises, wt, PRINCIPLES, ATHLETE)
    assert not any("max 2 reps" in e for e in result.errors), result.errors
    return True, ""


def test_reps_above_80_limit_warning():
    exercises = [_ex("Snatch", 3, 5, 83)]  # 5 reps at 83% — Prilepin suggests max 4
    wt = dict(WEEK_TARGET, intensity_ceiling=85)
    result = validate_session(exercises, wt, PRINCIPLES, ATHLETE)
    assert any("max 4 reps" in w for w in result.warnings), result.warnings
    return True, ""


def test_reps_at_80_limit_ok():
    exercises = [_ex("Snatch", 3, 4, 83)]  # 4 reps at 83% — OK
    wt = dict(WEEK_TARGET, intensity_ceiling=85)
    result = validate_session(exercises, wt, PRINCIPLES, ATHLETE)
    assert not any("max 4 reps" in w for w in result.warnings), result.warnings
    return True, ""


# ── Check 4: Avoid list ───────────────────────────────────────

def test_avoid_list_blocks_exercise():
    athlete = dict(ATHLETE, exercise_preferences={"avoid": ["Back Squat", "Good Morning"]})
    exercises = [_ex("Back Squat", 4, 4, 75, ref="back_squat")]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, athlete)
    assert not result.is_valid
    assert any("avoid" in e.lower() for e in result.errors), result.errors
    return True, ""


def test_avoid_list_case_insensitive():
    athlete = dict(ATHLETE, exercise_preferences={"avoid": ["back squat"]})
    exercises = [_ex("Back Squat", 4, 4, 75, ref="back_squat")]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, athlete)
    assert not result.is_valid
    return True, ""


def test_avoid_list_empty_no_errors():
    exercises = [_ex("Snatch", 4, 3, 75)]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert not any("avoid" in e.lower() for e in result.errors)
    return True, ""


# ── Check 5: Principles ───────────────────────────────────────

def test_principle_max_exercises_warning():
    principles = [{
        "id": 1,
        "principle_name": "Keep it simple",
        "recommendation": {"max_exercises_per_session": 4},
    }]
    exercises = [_ex(f"Exercise {i}", 3, 3, 75, order=i) for i in range(1, 7)]
    result = validate_session(exercises, WEEK_TARGET, principles, ATHLETE)
    assert any("max 4" in w for w in result.warnings), result.warnings
    return True, ""


def test_principle_comp_lift_first_warning():
    principles = [{
        "id": 1,
        "principle_name": "Comp lifts first",
        "recommendation": {"competition_lifts_first": True},
    }]
    exercises = [
        _ex("Back Squat", 4, 4, 75, ref="back_squat", order=1),
        _ex("Snatch", 4, 3, 75, order=2),
    ]
    result = validate_session(exercises, WEEK_TARGET, principles, ATHLETE)
    assert any("competition lifts first" in w.lower() for w in result.warnings), result.warnings
    return True, ""


def test_principle_comp_lift_first_ok():
    principles = [{
        "id": 1,
        "principle_name": "Comp lifts first",
        "recommendation": {"competition_lifts_first": True},
    }]
    exercises = [
        _ex("Snatch", 4, 3, 75, order=1),
        _ex("Back Squat", 4, 4, 75, ref="back_squat", order=2),
    ]
    result = validate_session(exercises, WEEK_TARGET, principles, ATHLETE)
    assert not any("competition lifts first" in w.lower() for w in result.warnings), result.warnings
    return True, ""


def test_principle_text_recommendation_skipped():
    # String recommendations (not dicts) should not raise
    principles = [{"id": 1, "principle_name": "General", "recommendation": "Always warm up."}]
    exercises = [_ex("Snatch", 4, 3, 75)]
    result = validate_session(exercises, WEEK_TARGET, principles, ATHLETE)
    assert result.is_valid
    return True, ""


# ── Check 6: Duration ─────────────────────────────────────────

def test_duration_warning_when_too_long():
    # 8 exercises × 5 sets × (30s + 90s rest) / 60 = 80 min; 80 > 90*1.2=108 min? No.
    # Need more: 15 exercises × 5 sets × 120s / 60 = 150 min > 90*1.2=108 min
    exercises = [_ex(f"Snatch {i}", 5, 3, 75, order=i) for i in range(1, 16)]
    athlete = dict(ATHLETE, session_duration_minutes=60)
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, athlete)
    assert any("duration" in w.lower() or "min" in w.lower() for w in result.warnings), result.warnings
    return True, ""


def test_duration_no_warning_within_limit():
    # 4 exercises × 4 sets × 120s / 60 = 32 min; well within 90 min
    exercises = [_ex(f"Snatch {i}", 4, 3, 75, order=i) for i in range(1, 5)]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert not any("duration" in w.lower() for w in result.warnings), result.warnings
    return True, ""


# ── Integration: clean session ────────────────────────────────

def test_valid_session_no_errors():
    exercises = [
        _ex("Snatch", 3, 3, 72, order=1),          # comp lift, 9 reps, within zone
        _ex("Back Squat", 4, 4, 78, ref="back_squat", order=2),
        _ex("Snatch Pull", 3, 3, 80, ref="snatch", order=3),
    ]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.is_valid, result.errors
    assert not result.errors
    return True, ""


def test_valid_session_with_warmup_sets():
    # Warmup sets at 50% should not trigger Prilepin zone (below 55%)
    exercises = [
        _ex("Snatch", 2, 3, 50, order=1),   # warmup — below 55%, no zone
        _ex("Snatch", 2, 3, 60, order=2),   # warmup — 55-65% zone
        _ex("Snatch", 4, 3, 75, order=3),   # working sets — 70-80% zone
        _ex("Back Squat", 4, 4, 78, ref="back_squat", order=4),
    ]
    result = validate_session(exercises, WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert result.is_valid, result.errors
    return True, ""


# ── Empty session guard ───────────────────────────────────────

def test_empty_session_is_invalid():
    result = validate_session([], WEEK_TARGET, PRINCIPLES, ATHLETE)
    assert not result.is_valid, "Empty session should be invalid"
    assert result.errors, "Empty session should have at least one error"
    assert "no exercises" in result.errors[0].lower(), result.errors
    return True, ""


# ── Runner ────────────────────────────────────────────────────

TESTS = [
    ("Prilepin: within range → no errors/warnings", test_prilepin_within_range),
    ("Prilepin: above range_high → warning", test_prilepin_warning_above_range),
    ("Prilepin: above hard cap → error", test_prilepin_error_above_hard_cap),
    ("Prilepin: non-comp lift not counted", test_prilepin_non_comp_lift_not_counted),
    ("Prilepin: session_comp_reps accumulated", test_prilepin_session_comp_reps_accumulated),
    ("Intensity: above ceiling → error", test_intensity_above_ceiling_error),
    ("Intensity: at ceiling → ok", test_intensity_at_ceiling_ok),
    ("Intensity: below floor comp lift → warning", test_intensity_below_floor_comp_lift_warning),
    ("Intensity: below floor non-comp → no warning", test_intensity_below_floor_non_comp_lift_no_warning),
    ("Intensity: no pct → skipped", test_no_intensity_pct_skipped),
    ("Reps/set: >2 at ≥90% → error", test_reps_above_90_limit_error),
    ("Reps/set: 2 at ≥90% → ok", test_reps_at_90_limit_ok),
    ("Reps/set: >4 at ≥80% → warning", test_reps_above_80_limit_warning),
    ("Reps/set: 4 at ≥80% → ok", test_reps_at_80_limit_ok),
    ("Avoid list: blocks exercise → error", test_avoid_list_blocks_exercise),
    ("Avoid list: case insensitive", test_avoid_list_case_insensitive),
    ("Avoid list: empty → no errors", test_avoid_list_empty_no_errors),
    ("Principle: max exercises → warning", test_principle_max_exercises_warning),
    ("Principle: comp not first → warning", test_principle_comp_lift_first_warning),
    ("Principle: comp first → ok", test_principle_comp_lift_first_ok),
    ("Principle: text recommendation → skipped", test_principle_text_recommendation_skipped),
    ("Duration: over limit → warning", test_duration_warning_when_too_long),
    ("Duration: within limit → ok", test_duration_no_warning_within_limit),
    ("Integration: clean session → valid", test_valid_session_no_errors),
    ("Integration: warmup sets → valid", test_valid_session_with_warmup_sets),
    ("Empty session: no exercises → error", test_empty_session_is_invalid),
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
    print("VALIDATE — Test Results")
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
