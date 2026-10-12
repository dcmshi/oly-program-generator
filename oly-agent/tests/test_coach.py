"""COACH-1: coach constraints — record, application to the athlete context,
forced phase in PLAN, the prompt / explain sections, and the web form parser."""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from coach import (
    COACH_NOTE_MAX_CHARS,
    CoachConstraintError,
    CoachConstraints,
    apply_constraints,
    coach_prompt_section,
)

TODAY = date(2026, 10, 11)


def _ctx(**kw):
    from tests.test_plan import _ctx as plan_ctx
    ctx = plan_ctx(**kw)
    ctx.athlete.update({"lift_emphasis": "balanced", "strength_limiters": ["pull_limited"],
                        "exercise_preferences": {"avoid": ["Jerk Dip Squat"], "prefs": {"warmups": "on"}},
                        "injuries": ["left wrist"], "technical_faults": ["slow_turnover"],
                        "sessions_per_week": 4})
    return ctx


# ── CoachConstraints ─────────────────────────────────────────────────────────

def test_constraints_validate_their_vocabulary_and_ranges():
    for bad in ({"phase": "deload"}, {"lift_emphasis": "squat_biased"}, {"sessions_per_week": 9},
                {"note": "x" * (COACH_NOTE_MAX_CHARS + 1)}):
        with pytest.raises(CoachConstraintError):
            CoachConstraints(**bad)
    with pytest.raises(CoachConstraintError):
        CoachConstraints.from_json({"weeks": 6})                  # unknown key — weeks travel separately


def test_constraints_round_trip_through_json_and_drop_unset_fields():
    c = CoachConstraints(phase="intensification", strength_limiters=[], competition_date=date(2026, 12, 5),
                         note="  No max singles before week 4  ")
    data = c.to_json()
    assert data == {"phase": "intensification", "strength_limiters": [], "competition_date": "2026-12-05",
                    "note": "No max singles before week 4"}
    assert CoachConstraints.from_json(data).to_json() == data
    assert CoachConstraints().is_empty() and not CoachConstraints(strength_limiters=[]).is_empty()


# ── apply_constraints ────────────────────────────────────────────────────────

def test_apply_overrides_a_copy_and_leaves_the_profile_context_alone():
    ctx = _ctx()
    c = CoachConstraints(sessions_per_week=3, lift_emphasis="cj_biased", strength_limiters=["squat_limited"],
                         technical_faults=[], injuries=["left knee"], avoid_exercises=["Jerk from Rack"],
                         competition_date=date(2026, 12, 6), note="More pause work")
    out = apply_constraints(ctx, c, today=TODAY)

    assert out.sessions_per_week == 3 and out.athlete["sessions_per_week"] == 3
    assert out.athlete["lift_emphasis"] == "cj_biased"
    assert out.athlete["strength_limiters"] == ["squat_limited"]
    assert out.technical_faults == [] and out.athlete["technical_faults"] == []
    assert out.injuries == ["left knee"] and out.athlete["injuries"] == ["left knee"]
    assert out.athlete["exercise_preferences"] == {"avoid": ["Jerk from Rack"], "prefs": {"warmups": "on"}}
    assert out.weeks_to_competition == 8
    assert out.active_goal["competition_date"] == date(2026, 12, 6)
    assert out.coach_note == "More pause work" and out.coach_constraints["lift_emphasis"] == "cj_biased"

    # the original context — and the profile row inside it — is untouched
    assert ctx.sessions_per_week != 3 or ctx.athlete["sessions_per_week"] == 4
    assert ctx.athlete["lift_emphasis"] == "balanced"
    assert ctx.athlete["exercise_preferences"]["avoid"] == ["Jerk Dip Squat"]
    assert ctx.coach_note is None and ctx.coach_constraints is None


def test_apply_keeps_unset_fields_and_returns_the_context_when_empty():
    ctx = _ctx()
    assert apply_constraints(ctx, None) is ctx
    assert apply_constraints(ctx, CoachConstraints()) is ctx
    out = apply_constraints(ctx, CoachConstraints(note="Keep squats to 2 days"))
    assert out.athlete["strength_limiters"] == ["pull_limited"] and out.injuries == ctx.injuries
    assert out.coach_constraints == {"note": "Keep squats to 2 days"}


def test_apply_rejects_a_past_meet_date():
    with pytest.raises(CoachConstraintError):
        apply_constraints(_ctx(), CoachConstraints(competition_date=date(2026, 10, 1)), today=TODAY)


# ── PLAN: a forced phase ─────────────────────────────────────────────────────

def test_plan_forced_phase_skips_the_decision_tree_and_the_meet_date():
    from plan import plan
    from tests.test_plan import _FakeSettings
    ctx = _ctx(previous_program={"id": 1, "phase": "accumulation", "outcome_summary": {}}, weeks_to_competition=20)
    with patch("plan.fetch_all", return_value=[]):
        p = plan(ctx, None, _FakeSettings(), duration_weeks=5, phase="intensification")
        default = plan(ctx, None, _FakeSettings(), phase="realization")
    assert p.phase == "intensification" and p.duration_weeks == 5 and len(p.weekly_targets) == 5
    assert default.phase == "realization" and default.duration_weeks >= 1


def test_intensity_cap_validates_its_range():
    for bad in (59, 101):
        with pytest.raises(CoachConstraintError):
            CoachConstraints(intensity_cap=bad)
    assert CoachConstraints.from_json({"intensity_cap": 85}).to_json() == {"intensity_cap": 85}
    assert not CoachConstraints(intensity_cap=85).is_empty()


def test_plan_intensity_cap_clamps_every_week_ceiling_and_floor():
    from plan import plan
    from tests.test_plan import _FakeSettings
    ctx = _ctx(previous_program={"id": 1, "phase": "intensification", "outcome_summary": {}})
    with patch("plan.fetch_all", return_value=[]):
        free = plan(ctx, None, _FakeSettings(), phase="realization")
        capped = plan(ctx, None, _FakeSettings(), phase="realization", intensity_cap=85)
        block = plan(ctx, None, _FakeSettings(), block={"phase": "realization", "weeks": 3}, intensity_cap=85)
    assert max(w.intensity_ceiling for w in free.weekly_targets) > 85      # the phase runs above the cap
    for p in (capped, block):
        assert all(w.intensity_ceiling <= 85 and w.intensity_floor <= w.intensity_ceiling for w in p.weekly_targets)
        assert p.intensity_ceiling_override == 85
    # below the cap the weeks are unchanged
    for a, b in zip(free.weekly_targets, capped.weekly_targets, strict=True):
        if a.intensity_ceiling <= 85:
            assert (a.intensity_floor, a.intensity_ceiling) == (b.intensity_floor, b.intensity_ceiling)


def test_max_test_is_skipped_under_a_cap_below_100():
    import dataclasses

    import orchestrator
    from plan import plan
    from tests.test_plan import _FakeSettings
    ctx = _ctx(previous_program={"id": 1, "phase": "intensification", "outcome_summary": {}})
    ctx.athlete["exercise_preferences"]["prefs"]["max_test"] = "always"
    with patch("plan.fetch_all", return_value=[]):
        p = plan(ctx, None, _FakeSettings(), phase="realization")
    for cap, expected in ((90, 0), (100, 1), (None, 1)):
        c = dataclasses.replace(ctx, coach_constraints={"intensity_cap": cap} if cap else None)
        sessions: list[dict] = []
        with patch.object(orchestrator, "_build_max_test_session", return_value=[]), \
             patch.object(orchestrator, "_save_session", return_value=7):
            orchestrator._add_max_test_session(None, 1, c, p, {}, sessions)
        assert len(sessions) == expected, cap


# ── Prompt and EXPLAIN sections ──────────────────────────────────────────────

def test_prompt_section_is_empty_without_a_brief():
    assert coach_prompt_section(None, None) == ""
    section = coach_prompt_section("No max singles before week 4",
                                   {"lift_emphasis": "cj_biased", "strength_limiters": ["squat_limited"],
                                    "note": "No max singles before week 4"})
    assert section.startswith("## Coach Instructions")
    assert "lift emphasis: cj_biased; strength limiters: squat_limited" in section
    assert section.endswith("Coach note: No max singles before week 4")


def test_session_prompt_carries_the_brief_above_the_marker_only_when_set():
    from generate import split_prompt_for_caching
    from tests.test_generate_utils import _make_athlete, _make_prompt

    assert "## Coach Instructions" not in _make_prompt()   # golden fixtures stay byte-identical
    athlete = _make_athlete()
    athlete.coach_note = "More pause work"
    athlete.coach_constraints = {"lift_emphasis": "cj_biased", "note": "More pause work"}
    static, dynamic = split_prompt_for_caching(_make_prompt(athlete))
    assert "## Coach Instructions" in static and "## Coach Instructions" not in dynamic
    assert "Coach choices: lift emphasis: cj_biased" in static and "Coach note: More pause work" in static


def test_explain_prompt_mentions_the_coach_brief():
    from explain import _build_explain_prompt
    from tests.test_explain import _ctx as explain_ctx
    from tests.test_explain import _plan, _sessions

    ctx = explain_ctx()
    assert "Coach's Brief" not in _build_explain_prompt(ctx, _plan(), _sessions())
    ctx.coach_note = "Keep squats to 2 days"
    ctx.coach_constraints = {"sessions_per_week": 3, "note": "Keep squats to 2 days"}
    prompt = _build_explain_prompt(ctx, _plan(), _sessions())
    assert "## Coach's Brief" in prompt and "sessions per week: 3" in prompt
    assert "Mention where the program follows the coach's choices." in prompt


# ── Web: the form parser ─────────────────────────────────────────────────────

class _Form(dict):
    def getlist(self, key):
        value = self.get(key)
        return value if isinstance(value, list) else ([] if value is None else [value])


def _profile():
    from web.coach_form import profile_defaults
    return profile_defaults(
        {"sessions_per_week": 4, "lift_emphasis": "balanced", "strength_limiters": ["pull_limited"],
         "technical_faults": ["slow_turnover"], "injuries": ["left wrist"],
         "exercise_preferences": {"avoid": ["Jerk Dip Squat"]}},
        {"competition_date": date(2026, 12, 5)})


def _untouched_form(**over):
    form = _Form(coach_mode="on", coach_phase="", coach_sessions_per_week="4", coach_lift_emphasis="balanced",
                 coach_strength_limiters=["pull_limited"], coach_technical_faults=["slow_turnover"],
                 coach_injuries="Left wrist", coach_avoid_exercises="jerk dip squat",
                 coach_competition_date="2026-12-05", coach_note="")
    form.update(over)
    return form


def test_form_left_as_prefilled_is_a_normal_run():
    from web.coach_form import parse_coach_form
    assert parse_coach_form(_untouched_form(), _profile(), TODAY) == (None, [])


def test_form_keeps_only_what_the_coach_changed():
    from web.coach_form import parse_coach_form
    form = _untouched_form(coach_phase="intensification", coach_sessions_per_week="3",
                           coach_strength_limiters=[], coach_injuries="left wrist, left knee",
                           coach_competition_date="2026-12-12", coach_note=" More pause work ")
    coach, errors = parse_coach_form(form, _profile(), TODAY)
    assert errors == []
    assert coach == {"phase": "intensification", "sessions_per_week": 3, "strength_limiters": [],
                     "injuries": ["left wrist", "left knee"], "competition_date": "2026-12-12",
                     "note": "More pause work"}


def test_form_errors_are_reported_not_raised():
    from web.coach_form import parse_coach_form
    coach, errors = parse_coach_form(
        _untouched_form(coach_phase="deload", coach_sessions_per_week="9", coach_lift_emphasis="x",
                        coach_strength_limiters=["bogus"], coach_competition_date="2026-01-01"),
        _profile(), TODAY)
    assert coach is None and len(errors) == 5


def test_form_intensity_cap_blank_is_none_and_bad_values_are_errors():
    from web.coach_form import parse_coach_form
    assert parse_coach_form(_untouched_form(coach_intensity_cap=""), _profile(), TODAY) == (None, [])
    assert parse_coach_form(_untouched_form(coach_intensity_cap="85%"), _profile(), TODAY) == (
        {"intensity_cap": 85}, [])
    for bad in ("50", "eighty", "85.5"):
        coach, errors = parse_coach_form(_untouched_form(coach_intensity_cap=bad), _profile(), TODAY)
        assert coach is None and len(errors) == 1


def test_prompt_section_states_the_cap_as_a_percentage():
    section = coach_prompt_section(None, {"intensity_cap": 85})
    assert "intensity cap: 85% of max on the competition lifts" in section


def test_parse_list_text_splits_trims_and_dedupes():
    from web.options import parse_list_text
    assert parse_list_text("left knee,  lower   back\nLeft Knee; ") == ["left knee", "lower back"]
    assert parse_list_text("") == [] and parse_list_text(None) == []
