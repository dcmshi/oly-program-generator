# oly-agent/tests/test_plan.py
"""
Tests for the PLAN step (plan.py).

_select_phase_and_duration is pure — tested directly.
plan() cold-start and standard paths tested with a mocked DB (fetch_all patched
to return [] for the principles query, avoiding any live connection).

Run: python tests/test_plan.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from plan import plan, _select_phase_and_duration
from models import AthleteContext

RESULTS = []

def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, str(e)))


# ── Fixtures ────────────────────────────────────────────────────────────────

class _FakeSettings:
    pass

def _ctx(
    level="intermediate",
    sessions_per_week=4,
    weeks_to_competition=None,
    active_goal=None,
    previous_program=None,
    faults=None,
):
    return AthleteContext(
        athlete={"name": "Test", "level": level},
        level=level,
        maxes={"snatch": 100.0, "clean_and_jerk": 120.0},
        active_goal=active_goal,
        previous_program=previous_program,
        recent_logs=[],
        technical_faults=faults or [],
        injuries=[],
        sessions_per_week=sessions_per_week,
        weeks_to_competition=weeks_to_competition,
    )

def _goal(goal_type):
    return {"goal": goal_type, "is_active": True}


# ── _select_phase_and_duration — pure ────────────────────────────────────────

def test_far_out_competition_gives_accumulation():
    phase, weeks = _select_phase_and_duration(_ctx(weeks_to_competition=20))
    assert phase == "accumulation"
    assert weeks == 4

def test_8_to_12_weeks_gives_accumulation():
    phase, weeks = _select_phase_and_duration(_ctx(weeks_to_competition=10))
    assert phase == "accumulation"
    assert weeks == 4

def test_4_to_8_weeks_gives_intensification():
    phase, weeks = _select_phase_and_duration(_ctx(weeks_to_competition=6))
    assert phase == "intensification"
    assert weeks == 4

def test_under_4_weeks_gives_realization():
    phase, weeks = _select_phase_and_duration(_ctx(weeks_to_competition=3))
    assert phase == "realization"
    assert weeks == 3

def test_realization_clamps_to_at_least_1_week():
    phase, weeks = _select_phase_and_duration(_ctx(weeks_to_competition=0))
    assert phase == "realization"
    assert weeks == 1

def test_no_competition_general_strength_gives_accumulation():
    phase, weeks = _select_phase_and_duration(_ctx(active_goal=_goal("general_strength")))
    assert phase == "accumulation"
    assert weeks == 4

def test_no_competition_competition_prep_gives_intensification():
    phase, weeks = _select_phase_and_duration(_ctx(active_goal=_goal("competition_prep")))
    assert phase == "intensification"
    assert weeks == 4

def test_no_competition_unknown_goal_defaults_to_accumulation():
    phase, weeks = _select_phase_and_duration(_ctx(active_goal=_goal("something_weird")))
    assert phase == "accumulation"
    assert weeks == 4

def test_no_competition_no_goal_defaults_to_accumulation():
    phase, weeks = _select_phase_and_duration(_ctx())
    assert phase == "accumulation"
    assert weeks == 4

def test_work_capacity_gives_general_prep():
    phase, weeks = _select_phase_and_duration(_ctx(active_goal=_goal("work_capacity")))
    assert phase == "general_prep"
    assert weeks == 5


# ── plan() — cold-start overrides ───────────────────────────────────────────

def test_cold_start_intermediate_caps_intensity_at_80():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program=None, level="intermediate"), None, _FakeSettings())
    assert result.intensity_ceiling_override == 80.0
    for wt in result.weekly_targets:
        assert wt.intensity_ceiling <= 80.0, f"Week {wt.week_number} ceiling {wt.intensity_ceiling} > 80"

def test_cold_start_beginner_caps_intensity_at_75():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program=None, level="beginner"), None, _FakeSettings())
    assert result.intensity_ceiling_override == 75.0

def test_cold_start_caps_duration_at_4_weeks():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program=None, weeks_to_competition=20), None, _FakeSettings())
    assert result.duration_weeks <= 4

def test_cold_start_beginner_max_complexity_is_2():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program=None, level="beginner"), None, _FakeSettings())
    assert result.max_complexity == 2

def test_cold_start_intermediate_max_complexity_is_3():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program=None, level="intermediate"), None, _FakeSettings())
    assert result.max_complexity == 3

def test_returning_athlete_no_intensity_override():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program={"phase": "accumulation"}), None, _FakeSettings())
    assert result.intensity_ceiling_override is None


# ── plan() — output shape ────────────────────────────────────────────────────

def test_plan_returns_correct_session_count():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(sessions_per_week=4), None, _FakeSettings())
    assert len(result.session_templates) == 4

def test_plan_weekly_targets_match_duration():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program={"phase": "x"}, weeks_to_competition=None,
                           active_goal=_goal("general_strength")), None, _FakeSettings())
    assert len(result.weekly_targets) == result.duration_weeks

def test_plan_deload_week_set_for_accumulation():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program={"phase": "x"}), None, _FakeSettings())
    assert result.deload_week is not None

def test_plan_total_reps_positive():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(previous_program={"phase": "x"}), None, _FakeSettings())
    for wt in result.weekly_targets:
        if not wt.is_deload:
            assert wt.total_competition_lift_reps > 0


# ── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
