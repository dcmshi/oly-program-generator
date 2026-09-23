# oly-agent/tests/test_demographics.py
"""AUD-5: biological sex, age (from date_of_birth), bodyweight and weight
class reach the pipeline — ASSESS mapping, the Athlete Profile prompt line,
the session-query qualifier, and (unchanged) principle-matcher state.

No DB, no API keys.
"""

import json
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from assess import assess, athlete_demographics
from demographics import age_band, age_from_dob, demographics_line, query_qualifiers
from generate import build_session_prompt
from models import AthleteContext, ProgramPlan, RetrievalContext, SessionTemplate, WeekTarget
from principle_matcher import KNOWN_CONDITION_KEYS, build_session_state
from retrieve import build_session_query

from shared.constants import PROMPT_STATIC_DYNAMIC_MARKER

TODAY = date(2026, 9, 22)


def _ctx(**demo):
    return AthleteContext(
        athlete={
            "name": "Test", "level": "intermediate", "sessions_per_week": 4,
            "session_duration_minutes": 90, "lift_emphasis": "balanced",
            "strength_limiters": [], "competition_experience": "none",
            "exercise_preferences": {"avoid": []}, "available_equipment": ["barbell"],
        },
        level="intermediate",
        maxes={"snatch": 100.0, "clean_and_jerk": 120.0},
        active_goal=None, previous_program=None, recent_logs=[],
        technical_faults=[], injuries=[], sessions_per_week=4,
        weeks_to_competition=None,
        **demo,
    )


def _plan():
    return ProgramPlan(phase="accumulation", duration_weeks=4, sessions_per_week=4, deload_week=4,
                       weekly_targets=[], session_templates=[], active_principles=[], supporting_chunks=[])


_TMPL = SessionTemplate(1, "Snatch + Squat", "snatch", ["squat"], 0.30)
_WEEK = WeekTarget(1, 1.0, 72.0, 82.0, 18, [2, 4], False)


# ── age / band ────────────────────────────────────────────────────────────────

def test_age_from_dob_counts_whole_years_around_the_birthday():
    assert age_from_dob(date(1988, 9, 22), TODAY) == 38
    assert age_from_dob(date(1988, 9, 23), TODAY) == 37
    assert age_from_dob(None, TODAY) is None
    assert age_from_dob("1988-09-22", TODAY) is None
    assert age_from_dob(date(2030, 1, 1), TODAY) is None


def test_age_bands_follow_iwf_categories():
    assert [age_band(a) for a in (15, 17, 18, 20, 21, 34, 35, 60)] == [
        "youth", "youth", "junior", "junior", "senior", "senior", "masters", "masters",
    ]
    assert age_band(None) is None


# ── ASSESS ───────────────────────────────────────────────────────────────────

def test_athlete_demographics_maps_profile_fields():
    demo = athlete_demographics(
        {"biological_sex": "female", "date_of_birth": date(1988, 5, 1),
         "bodyweight_kg": Decimal("63.5"), "weight_class": "64", "age": 99},
        today=TODAY,
    )
    assert demo == {"biological_sex": "female", "age_years": 38, "age_band": "masters",
                    "bodyweight_kg": 63.5, "weight_class": "64"}


def test_athlete_demographics_missing_fields_are_none():
    """The retired `age` column is never read; blanks map to None."""
    demo = athlete_demographics({"age": 40, "biological_sex": "", "weight_class": ""}, today=TODAY)
    assert demo == {"biological_sex": None, "age_years": None, "age_band": None,
                    "bodyweight_kg": None, "weight_class": None}


def test_assess_populates_demographics_on_context():
    athlete = {"id": 1, "name": "T", "level": "intermediate", "sessions_per_week": 4,
               "technical_faults": [], "injuries": [], "timezone": "America/Toronto",
               "biological_sex": "male", "date_of_birth": date(2009, 1, 1),
               "bodyweight_kg": 72.0, "weight_class": "73"}
    with patch("assess.fetch_one", side_effect=[athlete, None, None]), \
         patch("assess.fetch_all", side_effect=[[], []]), \
         patch("assess.today_in_tz", return_value=TODAY) as tz:
        ctx = assess(1, None)
    tz.assert_called_once_with("America/Toronto")
    assert (ctx.biological_sex, ctx.age_years, ctx.age_band) == ("male", 17, "youth")
    assert (ctx.bodyweight_kg, ctx.weight_class) == (72.0, "73")


# ── Prompt ───────────────────────────────────────────────────────────────────

def _prompt(ctx):
    retrieval = RetrievalContext(
        fault_exercises={}, template_references=[], programming_rationale=[],
        fault_correction_chunks=[], available_substitutions={}, active_principles=[],
        prilepin_targets={}, available_exercises=[],
    )
    return build_session_prompt(
        ctx, _WEEK, _TMPL, retrieval, week_number=1, duration_weeks=4,
        already_prescribed=[], session_rep_target=6, cumulative_comp_reps=0,
        phase="accumulation", context_chunks=[],
    )


def test_demographics_line_is_concise_and_skips_unknowns():
    full = _ctx(biological_sex="female", age_years=38, age_band="masters",
                bodyweight_kg=63.5, weight_class="64")
    assert demographics_line(full) == "Demographics: female, age 38 (masters), bodyweight 63.5 kg, 64 kg class"
    assert demographics_line(_ctx(bodyweight_kg=89.0)) == "Demographics: bodyweight 89 kg"
    assert demographics_line(_ctx()) == ""


def test_prompt_athlete_block_carries_demographics_in_static_prefix():
    prompt = _prompt(_ctx(biological_sex="female", age_years=16, age_band="youth",
                          bodyweight_kg=58.2, weight_class="59"))
    line = "Level: intermediate\nDemographics: female, age 16 (youth), bodyweight 58.2 kg, 59 kg class\nSessions/week:"
    assert line in prompt
    assert prompt.index("Demographics:") < prompt.index(PROMPT_STATIC_DYNAMIC_MARKER)


def test_prompt_without_demographics_is_unchanged():
    prompt = _prompt(_ctx())
    assert "Demographics" not in prompt
    assert "Level: intermediate\nSessions/week:" in prompt


# ── Session query ────────────────────────────────────────────────────────────

def test_session_query_qualifier_for_female_youth_masters():
    base = build_session_query(_ctx(), _plan(), _TMPL, _WEEK)
    fem = build_session_query(_ctx(biological_sex="female", age_band="masters"), _plan(), _TMPL, _WEEK)
    assert fem == base.replace("intermediate athlete", "intermediate athlete, female athlete, masters athlete")
    youth = build_session_query(_ctx(age_band="youth"), _plan(), _TMPL, _WEEK)
    assert "youth athlete" in youth


def test_default_senior_male_unknown_queries_unchanged():
    base = build_session_query(_ctx(), _plan(), _TMPL, _WEEK)
    assert base == ("exercise selection for a snatch session with squat support, "
                    "during the accumulation phase at 70-85% intensity, intermediate athlete")
    for demo in ({"biological_sex": "male"}, {"age_band": "senior", "age_years": 30},
                 {"age_band": "junior", "age_years": 19}, {"bodyweight_kg": 89.0, "weight_class": "89"}):
        assert build_session_query(_ctx(**demo), _plan(), _TMPL, _WEEK) == base, demo
    assert query_qualifiers(_ctx(biological_sex="male", age_band="senior")) == []


def test_golden_session_queries_still_match_production_builder():
    """The golden set's session queries were built for a default athlete; the
    AUD-5 qualifier must not move them."""
    from eval.queries import production_queries

    golden = json.loads((Path(__file__).parent.parent / "eval" / "golden.json").read_text(encoding="utf-8"))
    prod = {q["id"]: q["query"] for q in production_queries() if q["kind"] == "session"}
    gold = {q["id"]: q["query"] for q in golden["queries"] if q["id"].startswith("session:")}
    assert gold and all(prod[i] == gold[i] for i in gold if i in prod)


# ── Principle matcher ────────────────────────────────────────────────────────

def test_matcher_has_no_demographic_condition_keys_and_state_is_unchanged():
    """Sex / age are not condition keys (principle_extractor.CONDITION_KEYS),
    so the session state does not carry them — a follow-up schema change."""
    assert not {"age_years", "biological_sex", "age_band"} & KNOWN_CONDITION_KEYS
    plain = _ctx()
    demo = replace(plain, biological_sex="female", age_years=40, age_band="masters")
    assert build_session_state(demo, _plan(), 1, _TMPL) == build_session_state(plain, _plan(), 1, _TMPL)
