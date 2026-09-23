# tests/test_strength_curve.py
"""
No-DB tests for the strength-work curve (PLAN-3c): the per-phase squat / pull
bands, their attachment to each week, the Program Plan prompt lines, and
validate.py check 13.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_profiles import PHASE_PROFILES, STRENGTH_CURVE, strength_targets
from validate import _check_strength_curve, strength_category, validate_session


def test_every_phase_has_sourced_sane_bands():
    for phase in (*PHASE_PROFILES, "deload"):
        bands = STRENGTH_CURVE[phase]
        for kind in ("squat", "pull"):
            b = bands[kind]
            assert 0 < b["floor"] < b["ceiling"] <= 115, (phase, kind)
            assert 1 <= b["reps"][0] <= b["reps"][1] <= 6, (phase, kind)
            assert b["sources"] and all(isinstance(i, int) for i in b["sources"]), (phase, kind)
    # the squat band rises from accumulation to intensification, then tapers
    assert STRENGTH_CURVE["intensification"]["squat"]["floor"] > STRENGTH_CURVE["accumulation"]["squat"]["floor"]
    assert STRENGTH_CURVE["realization"]["squat"]["ceiling"] < STRENGTH_CURVE["intensification"]["squat"]["ceiling"]


def test_deload_weeks_use_the_deload_row():
    assert strength_targets("accumulation", is_deload=True) is STRENGTH_CURVE["deload"]
    assert strength_targets("accumulation", is_deload=False) is STRENGTH_CURVE["accumulation"]
    assert strength_targets("unknown", is_deload=False) is None


def test_plan_attaches_bands_to_every_week():
    from plan import plan
    from tests.test_plan import _ctx, _FakeSettings
    with patch("plan.fetch_all", return_value=[]):
        p = plan(_ctx(previous_program={"id": 1, "phase": "general_prep", "outcome_summary": {}}), None,
                 _FakeSettings())
    for wt in p.weekly_targets:
        expected = STRENGTH_CURVE["deload"] if wt.is_deload else STRENGTH_CURVE[p.phase]
        assert wt.strength_targets is expected, wt.week_number


def test_program_plan_prompt_lines():
    from generate import _strength_curve_lines
    from models import WeekTarget
    wt = WeekTarget(week_number=1, volume_modifier=1.0, intensity_floor=70, intensity_ceiling=80,
                    total_competition_lift_reps=60, reps_per_set_range=[2, 4], is_deload=False,
                    strength_targets=STRENGTH_CURVE["accumulation"])
    lines = _strength_curve_lines(wt)
    assert "Squats (% of the squat max): 70–80% × 3–5 reps" in lines
    assert "Pulls (% of the lift's max): 90–110% × 2–4 reps" in lines
    wt.strength_targets = None
    assert _strength_curve_lines(wt) == ""


def _ex(name, pct, ref):
    return {"exercise_name": name, "exercise_order": 1, "sets": 4, "reps": 3, "intensity_pct": pct,
            "intensity_reference": ref, "rest_seconds": 120, "rpe_target": 8.0}


def test_strength_category():
    assert strength_category(_ex("Back Squat", 75, "back_squat")) == "squat"
    assert strength_category(_ex("Front Squat", 75, "front_squat")) == "squat"
    assert strength_category(_ex("Back Squat", 75, "clean_and_jerk")) is None     # not off a squat max
    assert strength_category(_ex("Overhead Squat", 70, "snatch")) is None
    assert strength_category(_ex("Clean Pull", 100, "clean")) == "pull"
    assert strength_category(_ex("Snatch Deadlift", 100, "snatch")) == "pull"
    assert strength_category(_ex("Pull-up", 0, "clean")) is None
    assert strength_category({**_ex("Clean Pull + Clean", 80, "clean"), "complex": {"id": 3}}) is None


def test_check_13_warns_outside_the_band_only():
    week = {"strength_targets": STRENGTH_CURVE["accumulation"]}
    warnings: list[str] = []
    _check_strength_curve([_ex("Back Squat", 78, "back_squat"),      # in band
                           _ex("Back Squat", 84, "back_squat"),      # within +5 tolerance
                           _ex("Back Squat", 92, "back_squat"),      # outside
                           _ex("Back Squat", 60, "back_squat"),      # warm-up — ignored
                           _ex("Clean Pull", 120, "clean")], week, warnings)
    assert len(warnings) == 2
    assert "Back Squat at 92" in warnings[0] and "squat band 70–80%" in warnings[0]
    assert "Clean Pull at 120" in warnings[1]
    none: list[str] = []
    _check_strength_curve([_ex("Back Squat", 99, "back_squat")], {}, none)     # no bands → no check
    assert none == []


def test_check_13_is_a_warning_in_validate_session():
    week = {"intensity_floor": 70, "intensity_ceiling": 80, "total_competition_lift_reps": 60,
            "reps_per_set_range": [2, 4], "strength_targets": STRENGTH_CURVE["accumulation"]}
    res = validate_session([_ex("Back Squat", 95, "back_squat")], week, [], {"exercise_preferences": {}})
    assert res.is_valid and any("squat band" in w for w in res.warnings)
