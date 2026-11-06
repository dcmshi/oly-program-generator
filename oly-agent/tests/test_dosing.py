"""dosing.dose_session — code fits sets × reps × % to the week; the model picks exercises."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dosing import dose_session
from phase_profiles import classic_reps_range, variant_reps_range


def _week(**kw):
    wt = {"intensity_floor": 61, "intensity_ceiling": 69, "reps_per_set_range": [4, 6],
          "classic_reps_per_set_range": [2, 3], "volume_table": "prilepin",
          "strength_targets": {"squat": {"floor": 65, "ceiling": 75, "reps": [4, 6]},
                               "pull": {"floor": 85, "ceiling": 100, "reps": [3, 4]}}}
    wt.update(kw)
    return wt


def _ex(name, sets, reps, pct, ref="snatch", rpe=None, order=1):
    return {"exercise_name": name, "exercise_order": order, "sets": sets, "reps": reps,
            "intensity_pct": pct, "intensity_reference": ref, "rpe_target": rpe}


def test_a_session_that_fits_comes_back_unchanged():
    exs = [_ex("Snatch", 6, 3, 65)]
    out, edits = dose_session(exs, _week(), session_rep_target=18)
    assert edits == [] and (out[0]["sets"], out[0]["reps"], out[0]["intensity_pct"]) == (6, 3, 65)


def test_classic_lifts_take_their_own_rep_range_and_variants_the_weeks():
    exs = [_ex("Snatch", 3, 5, 65, order=1), _ex("Hang Power Snatch (above knee)", 3, 5, 65, order=2),
           _ex("Clean & Jerk", 4, 4, 65, ref="clean_and_jerk", order=3)]
    dose_session(exs, _week(), session_rep_target=None)
    assert [e["reps"] for e in exs] == [3, 5, 2]     # classic 2–3 · variant 4–6 · C&J ≤ 2


def test_without_a_classic_range_the_full_lifts_follow_the_week():
    exs = [_ex("Snatch", 3, 5, 65)]
    dose_session(exs, _week(classic_reps_per_set_range=None), session_rep_target=None)
    assert exs[0]["reps"] == 5


def test_ceiling_clamp_moves_the_rpe_with_the_load():
    exs = [_ex("Power Snatch", 3, 4, 79, rpe=8.0)]
    _, edits = dose_session(exs, _week(), session_rep_target=None)
    assert exs[0]["intensity_pct"] == 69 and exs[0]["rpe_target"] == 7.0
    assert "week ceiling 69%" in edits[0]


def test_prilepin_per_set_limit_caps_reps_at_heavy_loads():
    exs = [_ex("Power Snatch", 3, 4, 92)]
    dose_session(exs, _week(intensity_floor=85, intensity_ceiling=95, reps_per_set_range=[1, 6]),
                 session_rep_target=None)
    assert exs[0]["reps"] == 2


def test_warm_ups_below_the_floor_are_left_alone():
    exs = [_ex("Snatch", 2, 5, 55, order=1), _ex("Snatch", 6, 3, 65, order=2)]
    _, edits = dose_session(exs, _week(), session_rep_target=18)
    assert (exs[0]["sets"], exs[0]["reps"]) == (2, 5) and edits == []


def test_sets_grow_to_the_session_target_primary_lift_first_on_ties():
    exs = [_ex("Snatch", 2, 3, 65, order=1), _ex("Power Snatch", 2, 3, 65, order=2)]
    _, edits = dose_session(exs, _week(), session_rep_target=24)
    total = sum(e["sets"] * e["reps"] for e in exs)
    assert 21 <= total <= 27 and exs[0]["sets"] >= exs[1]["sets"] and exs[0]["sets"] > 2
    assert any("sets (session target 24" in e for e in edits)


def test_sets_shrink_from_the_row_with_most_sets():
    exs = [_ex("Snatch", 4, 3, 65, order=1), _ex("Power Snatch", 8, 3, 65, order=2)]
    dose_session(exs, _week(), session_rep_target=18)
    total = sum(e["sets"] * e["reps"] for e in exs)
    assert 15 <= total <= 20 and exs[1]["sets"] < 8        # Power Snatch's reps rise to the variant range (4)


def test_no_prilepin_zone_ends_over_its_range():
    exs = [_ex("Snatch", 8, 3, 85, order=1), _ex("Power Snatch", 8, 3, 85, order=2)]
    dose_session(exs, _week(intensity_floor=80, intensity_ceiling=90, reps_per_set_range=[2, 4]),
                 session_rep_target=None)
    assert sum(e["sets"] * e["reps"] for e in exs) <= 20      # 80–90 zone range tops out at 20


def test_squats_and_pulls_move_into_the_strength_band():
    exs = [_ex("Back Squat", 3, 8, 82, ref="back_squat", rpe=8.0),
           _ex("Clean Pull", 3, 3, 120, ref="clean", order=2)]
    dose_session(exs, _week(), session_rep_target=None)
    assert (exs[0]["intensity_pct"], exs[0]["reps"]) == (75, 6) and exs[0]["rpe_target"] == 7.5
    assert exs[1]["intensity_pct"] == 100


def test_accessories_are_left_as_written():
    exs = [_ex("Pendlay Row", 3, 8, 65, ref="barbell_row"), _ex("Plank", 3, 1, None, ref="bodyweight", order=2)]
    _, edits = dose_session(exs, _week(), session_rep_target=None)
    assert edits == [] and exs[0]["reps"] == 8


def test_classic_reps_range_by_athlete():
    assert classic_reps_range("general_prep", [4, 6], "beginner", False) is None
    assert classic_reps_range("general_prep", [4, 6], "beginner", True) == [2, 3]
    assert classic_reps_range("realization", [1, 2], "advanced", False) == [1, 2]
    assert classic_reps_range("accumulation", [1, 2], "intermediate", False) == [2, 2]   # never above the week


def test_variants_take_their_own_range_and_are_not_raised_to_the_weeks():
    """DOSE-1a: program 57 raised Hang Power Snatch / Push Jerk 2–3 to 4 (the
    week's 4–6); variants now have VARIANT_LIFT_REPS_PER_SET."""
    exs = [_ex("Hang Power Snatch", 4, 2, 65, order=1), _ex("Push Jerk", 4, 3, 65, ref="clean_and_jerk", order=2),
           _ex("Power Clean", 3, 5, 65, ref="clean", order=3)]
    _, edits = dose_session(exs, _week(variant_reps_per_set_range=[2, 3]), session_rep_target=None)
    assert [e["reps"] for e in exs] == [2, 3, 3]
    assert edits[0] == "Power Clean: 3×5 @ 65% → 3×3 @ 65% (variant range 2–3)"


def test_variant_reps_range_by_athlete_and_phase():
    assert variant_reps_range("general_prep", [4, 6], "beginner", False) is None    # learning: the week's range
    assert variant_reps_range("general_prep", [4, 6], "intermediate", False) == [2, 3]
    assert variant_reps_range("intensification", [1, 3], "advanced", False) == [1, 3]
    assert variant_reps_range("realization", [1, 2], "advanced", False) == [1, 2]


def test_added_sets_spread_across_the_sessions_lifts():
    """DOSE-1b: growth used to pile every added set onto the first row."""
    exs = [_ex("Clean & Jerk", 3, 2, 65, ref="clean_and_jerk", order=1),
           _ex("Power Clean", 3, 2, 65, ref="clean", order=2)]
    dose_session(exs, _week(variant_reps_per_set_range=[2, 3]), session_rep_target=24)
    assert [e["sets"] for e in exs] == [6, 5] or [e["sets"] for e in exs] == [6, 6]
    assert abs(exs[0]["sets"] - exs[1]["sets"]) <= 1


def test_deload_sessions_are_never_filled_up_to_the_target():
    """DOSE-1b: program 57's deload days became Clean & Jerk 3 → 8 sets."""
    exs = [_ex("Clean & Jerk", 3, 2, 65, ref="clean_and_jerk")]
    _, edits = dose_session(exs, _week(is_deload=True, intensity_floor=55), session_rep_target=16)
    assert exs[0]["sets"] == 3 and edits == []
    over = [_ex("Snatch", 9, 2, 65)]
    dose_session(over, _week(is_deload=True, intensity_floor=55), session_rep_target=10)
    assert over[0]["sets"] * 2 <= 11                      # still trimmed down to the target
