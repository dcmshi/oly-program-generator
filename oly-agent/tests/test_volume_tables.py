# tests/test_volume_tables.py
"""
No-DB tests for the selectable volume tables (PLAN-3d): the Medvedev
derivation, session targets per table, the plan / prompt / validator wiring and
the preference plumbing.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.prilepin import compute_session_rep_target
from shared.volume_tables import (
    MEDVEDEV_ZONE_DISTRIBUTION,
    WEEKS_PER_MONTH,
    medvedev_distribution_lines,
    medvedev_period,
    medvedev_weekly_comp_reps,
    session_rep_target,
)


def test_medvedev_numbers_are_the_sources():
    # elite = the qualified athlete: 1,500 preparatory / 850 competition lifts a month, half comp lifts
    assert medvedev_weekly_comp_reps("elite", "accumulation") == round(1500 * 0.5 / WEEKS_PER_MONTH)
    assert medvedev_weekly_comp_reps("elite", "realization") == round(850 * 0.5 / WEEKS_PER_MONTH)
    assert medvedev_weekly_comp_reps("beginner", "accumulation") == medvedev_weekly_comp_reps("beginner", "realization")
    assert medvedev_weekly_comp_reps("intermediate", "accumulation", volume_modifier=0.5) == \
        round(1094 * 0.5 / WEEKS_PER_MONTH * 0.5)
    assert medvedev_weekly_comp_reps("unknown-level", "accumulation") == medvedev_weekly_comp_reps("intermediate", "accumulation")
    for period, zones in MEDVEDEV_ZONE_DISTRIBUTION.items():
        assert sum(share for _z, share in zones) == 100, period
    assert medvedev_period("intensification") == "competition" and medvedev_period("general_prep") == "preparatory"


def test_session_targets_per_table():
    kw = dict(intensity_floor=70, intensity_ceiling=80, session_volume_share=0.3, volume_modifier=1.0,
              sessions_per_week=4, level="elite", phase="accumulation")
    assert session_rep_target("prilepin", **kw) == compute_session_rep_target(70, 80, 0.3, 1.0, 4)
    assert session_rep_target("unknown", **kw) == session_rep_target("prilepin", **kw)
    assert session_rep_target("medvedev", **kw) == round(medvedev_weekly_comp_reps("elite", "accumulation") * 0.3)
    assert session_rep_target("medvedev", **{**kw, "session_volume_share": 0.001}) == 3     # MIN_SESSION_REPS


def test_distribution_lines():
    lines = medvedev_distribution_lines("realization")
    assert "competition period" in lines[0] and "13 % at >90%" in lines[0]
    assert "Prilepin's limits" in lines[1]


def _ctx(table):
    from tests.test_plan import _ctx as base
    ctx = base(previous_program={"id": 1, "phase": "general_prep", "outcome_summary": {}}, level="elite")
    ctx.athlete["exercise_preferences"] = {"prefs": {"volume_table": table}}
    return ctx


def test_plan_sizes_weeks_by_the_chosen_table():
    from plan import plan
    from tests.test_plan import _FakeSettings
    with patch("plan.fetch_all", return_value=[]):
        pril = plan(_ctx("prilepin"), None, _FakeSettings())
        med = plan(_ctx("medvedev"), None, _FakeSettings())
    assert all(wt.volume_table == "prilepin" for wt in pril.weekly_targets)
    assert all(wt.volume_table == "medvedev" for wt in med.weekly_targets)
    full = [wt for wt in med.weekly_targets if not wt.is_deload][0]
    expected = sum(round(medvedev_weekly_comp_reps("elite", med.phase, full.volume_modifier) * s.session_volume_share)
                   for s in med.session_templates)
    assert full.total_competition_lift_reps == expected
    assert full.total_competition_lift_reps != [w for w in pril.weekly_targets if not w.is_deload][0].total_competition_lift_reps


def test_prompt_and_validator_follow_the_table():
    from generate import _volume_table_section
    from models import WeekTarget
    from validate import validate_session
    wt = WeekTarget(week_number=1, volume_modifier=1.0, intensity_floor=70, intensity_ceiling=80,
                    total_competition_lift_reps=120, reps_per_set_range=[2, 4], is_deload=False, volume_table="medvedev")
    assert _volume_table_section(wt, "accumulation", {}).startswith("## Volume Table (Medvedyev)")
    wt.volume_table = "prilepin"
    assert "Medvedyev" not in _volume_table_section(wt, "accumulation", {})
    # 40 working snatch reps in one 70–80 % session: over Prilepin's hard cap, fine under Medvedev
    ex = [{"exercise_name": "Snatch", "exercise_order": 1, "sets": 10, "reps": 4, "intensity_pct": 75.0,
           "intensity_reference": "snatch", "rest_seconds": 120, "rpe_target": 7.0}]
    base = {"intensity_floor": 70, "intensity_ceiling": 80, "total_competition_lift_reps": 200, "reps_per_set_range": [2, 4]}
    assert any("Prilepin session volume excessive" in e
               for e in validate_session(ex, {**base, "volume_table": "prilepin"}, [], {}).errors)
    assert not any("Prilepin session volume" in e
                   for e in validate_session(ex, {**base, "volume_table": "medvedev"}, [], {}).errors)


def test_preference_is_accepted_and_defaulted():
    from plan import training_preferences
    assert training_preferences({"exercise_preferences": {"prefs": {"volume_table": "medvedev"}}})["volume_table"] == "medvedev"
    assert training_preferences({"exercise_preferences": {"prefs": {"volume_table": "roman"}}})["volume_table"] == "prilepin"
