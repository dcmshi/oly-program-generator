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

from models import AthleteContext
from plan import _advance_phase, _apply_outcome_adjustments, _select_phase_and_duration, plan
from schemas import OutcomeSummary

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


# ── AGT-L6: stored sessions_per_week must match the template fallback ────────

def test_sessions_per_week_matches_template_fallback():
    """spw=1 falls back to the closest layout (2-day since PLAN-2 §2.1) — the
    stored program must say what the templates say (at 1–2/wk the mismatch
    escalated to AGT-H1). spw=2 now has its own layout."""
    with patch("plan.fetch_all", return_value=[]):
        p1 = plan(_ctx(sessions_per_week=1), None, _FakeSettings())
        p2 = plan(_ctx(sessions_per_week=2), None, _FakeSettings())
    assert p1.sessions_per_week == len(p1.session_templates) == 2
    assert p2.sessions_per_week == len(p2.session_templates) == 2


# ── AGT-M1: cold-start intensity cap must clamp the floor too ────────────────

def test_cold_start_floor_never_exceeds_ceiling():
    """Cold start caps the ceiling at 80/75 but left the floor unclamped, so a
    realization W1 (floor 85) prescribed a contradictory '85%–80%' range."""
    with patch("plan.fetch_all", return_value=[]):
        p = plan(_ctx(weeks_to_competition=2), None, _FakeSettings())
    for wt in p.weekly_targets:
        assert wt.intensity_floor <= wt.intensity_ceiling, (
            f"W{wt.week_number}: floor {wt.intensity_floor} > ceiling {wt.intensity_ceiling}"
        )

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

def test_cold_start_general_prep_keeps_deload():
    # Regression (A-M9): general_prep is a 5-week profile whose deload is the
    # LAST week. Capping a cold start to 4 weeks used to slice the tail off
    # (keeping the early ramp), dropping the deload so a beginner's first
    # program ended on its heaviest week. Rebuilding keeps peak + deload.
    with patch("plan.fetch_all", return_value=[]):
        result = plan(
            _ctx(previous_program=None, level="beginner",
                 active_goal=_goal("work_capacity")),
            None, _FakeSettings(),
        )
    assert result.duration_weeks == 4
    assert result.deload_week is not None, "cold-start general_prep dropped its deload week"
    assert result.weekly_targets[-1].is_deload, "last week of a capped general_prep must be the deload"
    # and no non-deload week should be the heaviest-then-abrupt-stop
    assert result.deload_week == 4


# ── Phase progression (_advance_phase) ──────────────────────────────────────

def _good_outcome(**kwargs):
    base = {"adherence_pct": 90.0, "avg_make_rate": 0.85, "avg_rpe_deviation": 0.2}
    base.update(kwargs)
    return OutcomeSummary.model_validate(base)

def test_accumulation_advances_to_intensification():
    phase, _ = _advance_phase("accumulation", _good_outcome(), None)
    assert phase == "intensification"

def test_intensification_advances_to_realization():
    phase, _ = _advance_phase("intensification", _good_outcome(), None)
    assert phase == "realization"

def test_realization_cycles_back_to_accumulation():
    phase, _ = _advance_phase("realization", _good_outcome(), None)
    assert phase == "accumulation"

def test_realization_returns_to_accumulation_even_with_high_rpe():
    # Regression: the RPE guard must not force an overreached athlete to
    # repeat a peaking block — realization always rebuilds with accumulation.
    phase, _ = _advance_phase("realization", _good_outcome(avg_rpe_deviation=2.0), None)
    assert phase == "accumulation"

def test_phase_not_advanced_low_adherence():
    phase, _ = _advance_phase("accumulation", _good_outcome(adherence_pct=60.0), None)
    assert phase == "accumulation"

def test_phase_not_advanced_low_make_rate():
    phase, _ = _advance_phase("accumulation", _good_outcome(avg_make_rate=0.65), None)
    assert phase == "accumulation"

def test_phase_not_advanced_high_rpe_deviation():
    phase, _ = _advance_phase("accumulation", _good_outcome(avg_rpe_deviation=2.0), None)
    assert phase == "accumulation"

def test_unknown_prev_phase_defaults_to_accumulation():
    phase, _ = _advance_phase("some_unknown_phase", _good_outcome(), None)
    assert phase == "accumulation"

def test_advance_phase_returns_profile_default_duration():
    _, weeks = _advance_phase("accumulation", _good_outcome(), None)
    assert weeks == 4  # intensification default

def test_phase_progression_used_in_select_when_no_competition():
    ctx = _ctx(previous_program={"phase": "accumulation", "outcome_summary": _good_outcome()})
    phase, _ = _select_phase_and_duration(ctx)
    assert phase == "intensification"

def test_competition_date_overrides_phase_progression():
    # Even with a previous program, weeks_to_competition wins
    ctx = _ctx(
        previous_program={"phase": "accumulation", "outcome_summary": _good_outcome()},
        weeks_to_competition=6,
    )
    phase, _ = _select_phase_and_duration(ctx)
    assert phase == "intensification"  # driven by weeks_to_comp, not progression


# ── Outcome adjustments (_apply_outcome_adjustments) ─────────────────────────

def _dummy_targets(n=4):
    return [
        {"week_number": i, "volume_modifier": 1.0, "intensity_floor": 70.0, "intensity_ceiling": 80.0, "is_deload": i == n}
        for i in range(1, n + 1)
    ]

def test_no_outcome_returns_unchanged():
    targets = _dummy_targets()
    result = _apply_outcome_adjustments(targets, {"outcome_summary": None})
    assert result == targets

def test_low_adherence_reduces_volume():
    targets = _dummy_targets()
    result = _apply_outcome_adjustments(targets, {"outcome_summary": {"adherence_pct": 60.0}})
    for t in result:
        if not t["is_deload"]:
            assert t["volume_modifier"] < 1.0

def test_low_make_rate_reduces_intensity_ceiling():
    targets = _dummy_targets()
    result = _apply_outcome_adjustments(targets, {"outcome_summary": {"avg_make_rate": 0.60}})
    for t in result:
        if not t["is_deload"]:
            assert t["intensity_ceiling"] < 80.0

def test_excellent_performance_increases_intensity():
    targets = _dummy_targets()
    outcome = {"adherence_pct": 95.0, "avg_make_rate": 0.90}
    result = _apply_outcome_adjustments(targets, {"outcome_summary": outcome})
    for t in result:
        if not t["is_deload"]:
            assert t["intensity_ceiling"] > 80.0

def test_deload_week_never_adjusted():
    targets = _dummy_targets()
    outcome = {"adherence_pct": 60.0, "avg_make_rate": 0.60}
    result = _apply_outcome_adjustments(targets, {"outcome_summary": outcome})
    deload = [t for t in result if t["is_deload"]]
    assert len(deload) == 1
    assert deload[0]["volume_modifier"] == 1.0
    assert deload[0]["intensity_ceiling"] == 80.0


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


# ── resolve_block_length (PLAN-1) ────────────────────────────────────────────

def test_block_length_default_is_level_aware():
    from plan import resolve_block_length

    from shared.constants import BLOCK_WEEKS_DEFAULT_BY_LEVEL
    assert resolve_block_length("accumulation", 4, _ctx(level="intermediate"), None) == 4
    assert resolve_block_length("accumulation", 4, _ctx(level="advanced"), None) == BLOCK_WEEKS_DEFAULT_BY_LEVEL["advanced"]["accumulation"]
    assert resolve_block_length("realization", 3, _ctx(level="elite"), None) == 3      # no level default → profile


def test_block_length_request_is_clamped_to_level_bounds():
    from plan import resolve_block_length
    assert resolve_block_length("accumulation", 4, _ctx(level="intermediate"), 6) == 6
    assert resolve_block_length("accumulation", 4, _ctx(level="intermediate"), 8) == 6   # intermediate max
    assert resolve_block_length("accumulation", 4, _ctx(level="beginner"), 6) == 4       # beginner max
    assert resolve_block_length("accumulation", 4, _ctx(level="advanced"), 1) == 2       # floor


def test_block_length_competition_date_wins():
    from plan import resolve_block_length
    assert resolve_block_length("realization", 2, _ctx(weeks_to_competition=2), 6) == 2


def test_plan_honours_requested_weeks_end_to_end():
    with patch("plan.fetch_all", return_value=[]):
        result = plan(_ctx(level="intermediate", previous_program={"phase": "realization", "outcome_summary": {}}),   # → accumulation rebuild
                      conn=None, settings=_FakeSettings(), duration_weeks=6)
    assert result.duration_weeks == 6 and len(result.weekly_targets) == 6
    assert result.weekly_targets[-1].is_deload                      # extension keeps the deload last


# ── training preferences (PLAN-2 §1.4 / §3.5 / §3.8) ─────────────────────────

def test_training_preferences_defaults_and_validation():
    from plan import training_preferences
    assert training_preferences({}) == {"warmups": "prescribed", "deload_style": "volume", "max_test": "auto", "deload_every_weeks": None}
    got = training_preferences({"exercise_preferences": {"avoid": ["x"], "prefs": {"warmups": "own", "deload_style": "bogus", "max_test": "never", "deload_every_weeks": 4}}})
    assert got == {"warmups": "own", "deload_style": "volume", "max_test": "never", "deload_every_weeks": 4}
    assert training_preferences({"exercise_preferences": {"prefs": {"deload_every_weeks": 7}}})["deload_every_weeks"] is None


def test_deload_preferences_shape_the_block():
    from phase_profiles import build_weekly_targets
    none = build_weekly_targets("accumulation", 4, "intermediate", deload_style="none")
    assert not any(w["is_deload"] for w in none)
    long = build_weekly_targets("accumulation", 8, "intermediate", deload_every_weeks=4)
    assert [w["week_number"] for w in long if w["is_deload"]] == [4, 8]
    assert long[3]["intensity_ceiling"] == long[7]["intensity_ceiling"]          # mid-block deload copies the profile deload
    short = build_weekly_targets("accumulation", 4, "intermediate", deload_every_weeks=4)
    assert [w["week_number"] for w in short if w["is_deload"]] == [4]            # never doubles the last week


def test_plan_reads_preferences_from_the_athlete_row():
    ctx = _ctx(level="intermediate", previous_program={"phase": "realization", "outcome_summary": {}})
    ctx.athlete["exercise_preferences"] = {"prefs": {"deload_style": "none"}}
    with patch("plan.fetch_all", return_value=[]):
        result = plan(ctx, None, _FakeSettings())
    assert result.deload_week is None and not any(w.is_deload for w in result.weekly_targets)


def test_outcome_nudges_are_proportional_to_the_miss():
    """PLAN-2 §1.8: a small miss is a small nudge; the old flat steps are the cap."""
    from phase_progression import compute_load_adjustments, compute_load_deltas
    vol, inten, labels = compute_load_deltas(adherence_pct=67.0, avg_make_rate=0.74, avg_rpe_deviation=1.1)
    assert -0.02 < vol < 0 and -0.5 < inten < 0 and labels[0] == "Volume -1% (low adherence)"
    vol, inten, labels = compute_load_deltas(adherence_pct=30.0, avg_make_rate=0.40, avg_rpe_deviation=3.0)
    assert vol == -0.15 and inten == -3.0                                  # −10 % −5 % / −3 pts caps
    assert labels == compute_load_adjustments(30.0, 0.40, 3.0)             # feedback shows the same strings
    assert compute_load_deltas(95.0, 0.9, 0.0)[1] == 2.0


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


# ── RAG-H3: principle candidates ─────────────────────────────────────────────

def test_load_principles_sql_matches_array_phases_and_caps_candidates():
    """`->>'phase' = %s` returned the text form of an array (never equal) and
    silently dropped multi-phase rules; `@> to_jsonb(text)` matches both the
    string and the array form. The LIMIT is the per-session matcher's pool."""
    from plan import _load_principles

    from shared.constants import MAX_PRINCIPLE_CANDIDATES

    captured = {}

    def fake_fetch_all(conn, sql, params):
        captured["sql"], captured["params"] = sql, params
        return []

    with patch("plan.fetch_all", side_effect=fake_fetch_all):
        _load_principles(None, "intensification", "advanced")

    assert "condition->'phase' @> to_jsonb(%s::text)" in captured["sql"]
    assert "condition->>'phase' =" not in captured["sql"]
    assert captured["params"] == ("intensification", "advanced", MAX_PRINCIPLE_CANDIDATES)
