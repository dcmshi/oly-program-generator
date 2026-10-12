"""Scoring for the template-parse model comparison (ING-EVAL-1) — pure, no model."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from template_model_compare import REFERENCE, score_section, spot_passes

REF = {
    "weeks_required": [1, 2], "weeks_allowed": [1, 2, 3],
    "sessions": [2, 3], "exercises": [3, 4], "intensity_pct_max": 100,
    "spot": [
        {"week": 1, "name_re": "back squat", "sets": 3, "reps": 8, "intensity_pct": 65},
        {"week": None, "name_re": "bench", "absent": True},
    ],
}


def _parsed(squat_sets=3, extra_week=None, intensity=65):
    weeks = [
        {"week_number": 1, "sessions": [
            {"day": "Monday", "exercises": [
                {"name": "Back Squat", "sets": squat_sets, "reps": "8", "intensity_pct": intensity},
                {"name": "Snatch", "sets": 5, "reps": 2}]},
            {"day": "Wednesday", "exercises": []},           # rest day: not a session
        ]},
        {"week_number": 2, "sessions": [{"day": "Day 1", "exercises": [{"name": "Clean", "sets": 4, "reps": 2}]}]},
    ]
    if extra_week is not None:
        weeks.append({"week_number": extra_week, "sessions": []})
    return {"weeks": weeks}


def test_a_faithful_parse_scores_all_ok():
    s = score_section(_parsed(), REF)
    assert s["weeks_ok"] and s["sessions_ok"] and s["exercises_ok"]
    assert s["sessions"] == 2 and s["exercises"] == 3
    assert s["spot_passed"] == 2 and s["all_ok"]


def test_a_wave_split_into_rows_counts_as_one_exercise():
    parsed = _parsed()
    parsed["weeks"][0]["sessions"][0]["exercises"].append({"name": "back  squat", "sets": 2, "reps": 8})
    assert score_section(parsed, REF)["exercises"] == 3


def test_reps_compare_as_text_and_wrong_sets_fail_the_spot():
    assert spot_passes(_parsed(), REF["spot"][0])                   # "8" == 8
    assert not spot_passes(_parsed(squat_sets=8), REF["spot"][0])   # reps/sets swapped
    assert not score_section(_parsed(squat_sets=8), REF)["all_ok"]


def test_weeks_outside_the_allowed_set_and_kg_read_as_percent_are_flagged():
    s = score_section(_parsed(extra_week=7, intensity=120), REF)
    assert s["weeks_extra"] == [7] and not s["weeks_ok"]
    assert s["intensity_over_cap"] == 1 and not s["all_ok"]


def test_absent_spot_fails_when_the_lift_is_invented():
    parsed = _parsed()
    parsed["weeks"][1]["sessions"][0]["exercises"].append({"name": "Bench Press"})
    assert not spot_passes(parsed, REF["spot"][1])


def test_empty_parse_scores_zero_without_raising():
    s = score_section({}, REF)
    assert s["weeks_missing"] == [1, 2] and s["exercises_off_by"] == 3 and not s["all_ok"]


def test_reference_file_is_well_formed():
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    ids = [s["id"] for s in ref["sections"]]
    assert len(ids) == len(set(ids))
    for s in ref["sections"]:
        assert set(s["weeks_required"]) <= set(s["weeks_allowed"])
        assert s["sessions"][0] <= s["sessions"][1] and s["exercises"][0] <= s["exercises"][1]
        assert s["sha256"] and s["spot"]
