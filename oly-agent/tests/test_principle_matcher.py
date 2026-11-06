# oly-agent/tests/test_principle_matcher.py
"""
No-key tests for principle_matcher (RAG-H3): condition operators, array phases,
unknown facts, schema-drift keys, per-session state, and selection.

Run: PYTHONUTF8=1 uv run pytest tests/test_principle_matcher.py -q
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import AthleteContext, ProgramPlan, SessionTemplate
from principle_matcher import (
    KNOWN_CONDITION_KEYS,
    build_session_state,
    compare,
    condition_matches,
    query_terms,
    select_principles,
)

# ── compare ───────────────────────────────────────────────────────────────────

def test_compare_operators():
    assert compare({"lte": 2}, 2) and compare({"lte": 2}, 1) and not compare({"lte": 2}, 3)
    assert compare({"gte": 2}, 2) and not compare({"gte": 2}, 1.5)
    assert compare({"lt": 0.7}, 0.65) and not compare({"lt": 0.7}, 0.7)
    assert compare({"gt": 9.0}, 9.5) and not compare({"gt": 9.0}, 9.0)
    assert compare({"eq": 3}, 3) and not compare({"eq": 3}, 4)
    assert compare({"between": [3, 5]}, 4) and compare({"between": [3, 5]}, 5) and not compare({"between": [3, 5]}, 6)


def test_compare_all_operators_in_one_object_must_hold():
    assert compare({"gte": 2, "lte": 4}, 3)
    assert not compare({"gte": 2, "lte": 4}, 5)


def test_compare_rejects_unknown_operator_and_malformed_between():
    assert not compare({"approx": 3}, 3)
    assert not compare({"between": [3]}, 3)
    assert not compare({"lte": "two"}, 1)


def test_compare_list_membership_is_case_insensitive():
    assert compare(["intensification", "realization"], "Realization")
    assert not compare(["intensification", "realization"], "accumulation")


def test_compare_scalars_numeric_or_string():
    assert compare("snatch", "Snatch")
    assert compare("2", 2.0)
    assert not compare("snatch", "clean")


def test_compare_unknown_actual_never_matches():
    """A condition on a fact we don't have (cold start, no competition) is NOT
    satisfied — the rule must not be enforced on missing data."""
    assert not compare({"lte": 2}, None)
    assert not compare(["a"], None)
    assert not compare("a", None)


# ── condition_matches ─────────────────────────────────────────────────────────

_STATE = {
    "phase": "accumulation", "athlete_level": "intermediate",
    "weeks_out_from_competition": 10, "training_age_years": 3.0,
    "week_of_block": 2, "movement_family": "snatch",
    "recent_make_rate": 0.8, "rpe_average_last_week": 8.0,
}


def test_condition_none_or_empty_always_applies():
    assert condition_matches(None, _STATE)
    assert condition_matches({}, _STATE)
    assert condition_matches("not a dict", _STATE)


def test_array_valued_phase_matches():
    """The SQL `->>'phase' = %s` dropped these outright (2/161 on the corpus)."""
    assert condition_matches({"phase": ["accumulation", "intensification"]}, _STATE)
    assert not condition_matches({"phase": ["intensification", "realization"]}, _STATE)


def test_movement_family_gates_by_session_primary():
    """60 of 161 corpus principles carry movement_family; it was never evaluated."""
    assert condition_matches({"movement_family": "snatch"}, _STATE)
    assert not condition_matches({"movement_family": "clean"}, _STATE)


def test_weeks_out_taper_rule_not_served_far_from_competition():
    taper = {"weeks_out_from_competition": {"lte": 2}, "phase": "realization"}
    assert not condition_matches(taper, _STATE)
    assert condition_matches(taper, {**_STATE, "weeks_out_from_competition": 1, "phase": "realization"})


def test_weeks_out_rule_not_served_when_no_competition_set():
    assert not condition_matches({"weeks_out_from_competition": {"lte": 2}}, {**_STATE, "weeks_out_from_competition": None})


def test_schema_drift_keys_are_ignored_not_fatal():
    """Keys outside the extraction schema (RAG-L9) can't be evaluated; the rule
    keeps today's behaviour instead of disappearing."""
    assert condition_matches({"athlete_characteristics": "tall", "phase": "accumulation"}, _STATE)
    assert "athlete_characteristics" not in KNOWN_CONDITION_KEYS


def test_null_condition_values_are_skipped():
    assert condition_matches({"phase": None, "movement_family": "snatch"}, _STATE)


# ── build_session_state ───────────────────────────────────────────────────────

def _ctx(weeks_out=None, previous=None, logs=None, training_age=None):
    return AthleteContext(
        athlete={"name": "T", "level": "intermediate", "training_age_years": training_age},
        level="intermediate", maxes={}, active_goal=None, previous_program=previous,
        recent_logs=logs or [], technical_faults=[], injuries=[], sessions_per_week=4,
        weeks_to_competition=weeks_out,
    )


def _plan(phase="accumulation"):
    return ProgramPlan(phase=phase, duration_weeks=4, sessions_per_week=4, deload_week=4,
                       weekly_targets=[], session_templates=[], active_principles=[], supporting_chunks=[])


def _tmpl(primary="clean"):
    return SessionTemplate(day_number=2, label="C&J", primary_movement=primary,
                           secondary_movements=["jerk"], session_volume_share=0.3)


def test_state_decrements_weeks_out_per_week_and_reads_outcome_and_logs():
    ctx = _ctx(weeks_out=6, previous={"outcome_summary": {"avg_make_rate": 0.72}},
               logs=[{"rpe": 8.0}, {"rpe": 9.0}, {"rpe": None}], training_age=2.5)
    state = build_session_state(ctx, _plan("intensification"), week_number=3, session_template=_tmpl("clean"))
    assert state == {
        "phase": "intensification", "athlete_level": "intermediate",
        "weeks_out_from_competition": 4, "training_age_years": 2.5,
        "week_of_block": 3, "movement_family": "clean",
        "recent_make_rate": 0.72, "rpe_average_last_week": 8.5, "has_injuries": False,
        "is_youth": False, "olympic_lifting_experience": False,
    }


def test_state_unknown_facts_are_none():
    state = build_session_state(_ctx(), _plan(), week_number=1, session_template=_tmpl("snatch"))
    assert state["weeks_out_from_competition"] is None
    assert state["recent_make_rate"] is None
    assert state["rpe_average_last_week"] is None
    assert state["training_age_years"] is None


def test_state_weeks_out_floors_at_zero():
    state = build_session_state(_ctx(weeks_out=1), _plan(), week_number=4, session_template=_tmpl())
    assert state["weeks_out_from_competition"] == 0


# ── select_principles ─────────────────────────────────────────────────────────

def test_select_filters_and_orders_by_priority():
    cands = [
        {"id": 1, "principle_name": "any", "priority": 5, "condition": None},
        {"id": 2, "principle_name": "snatch only", "priority": 9, "condition": {"movement_family": "snatch"}},
        {"id": 3, "principle_name": "clean only", "priority": 7, "condition": {"movement_family": "clean"}},
        {"id": 4, "principle_name": "late taper", "priority": 10, "condition": {"weeks_out_from_competition": {"lte": 2}}},
    ]
    snatch_day = select_principles(cands, _STATE)
    assert [p["id"] for p in snatch_day] == [2, 1]
    clean_day = select_principles(cands, {**_STATE, "movement_family": "clean"})
    assert [p["id"] for p in clean_day] == [3, 1]
    assert [p["id"] for p in select_principles(cands, _STATE, limit=1)] == [2]


# ── AUD-1: relevance ranking, category cap, determinism ──────────────────────

def _p(pid, priority, category=None, name="", rationale="", rec=None, condition=None):
    return {"id": pid, "principle_name": name, "priority": priority, "category": category,
            "rationale": rationale, "recommendation": rec or {"x": 1}, "condition": condition}


def test_query_terms_drop_stopwords_boilerplate_and_plurals():
    terms = query_terms("exercise selection for a snatch session with pulls, squats support "
                        "during the accumulation phase at 70-80% intensity, intermediate athlete")
    assert {"snatch", "pull", "squat", "accumulation", "intensity", "intermediate", "selection"} <= terms
    assert not terms & {"session", "support", "phase", "athlete", "the", "with", "during"}
    assert query_terms(None) == set() and query_terms("") == set()


def test_select_ranks_by_priority_plus_query_overlap():
    """A priority-7 snatch-pull rule outranks an unrelated priority-8 rule on a
    snatch-pull day; without a query the old priority order stands."""
    cands = [
        _p(1, 8, "volume", name="Weekly tonnage for squats"),
        _p(2, 7, "exercise_selection", name="Snatch pulls before snatch",
           rationale="Pulls at 90-100% of the snatch build the finish."),
        _p(3, 7, "intensity", name="Unrelated rule", rec={"prefer_exercises": ["snatch pull"]}),
    ]
    q = "exercise selection for a snatch session with snatch pull support"
    assert [p["id"] for p in select_principles(cands, _STATE)] == [1, 2, 3]
    # 2: 7 + overlap(snatch, pull) = 9; 3: 7 + 2 via its recommended exercise
    # names = 9 (id tiebreak); 1: 8 + 0.
    assert [p["id"] for p in select_principles(cands, _STATE, query=q)] == [2, 3, 1]


def test_select_caps_principles_per_category():
    from shared.constants import MAX_PRINCIPLES_PER_CATEGORY
    cands = [_p(i, 10, "volume") for i in range(1, 8)] + [_p(20, 2, "deload"), _p(21, 1, None)]
    chosen = select_principles(cands, _STATE, limit=8, query="snatch")
    assert sum(1 for p in chosen if p["category"] == "volume") == MAX_PRINCIPLES_PER_CATEGORY
    assert [p["id"] for p in chosen] == [1, 2, 3, 20, 21]   # lowest ids win the tie; uncategorised is uncapped


def test_select_is_deterministic_with_id_tiebreak():
    cands = [_p(9, 5, "volume"), _p(3, 5, "intensity"), _p(6, 5, "peaking")]
    first = [p["id"] for p in select_principles(cands, _STATE, query="clean")]
    assert first == [3, 6, 9]
    assert [p["id"] for p in select_principles(list(reversed(cands)), _STATE, query="clean")] == first


def test_select_still_applies_conditions_with_query():
    cands = [_p(1, 9, "volume", name="snatch volume", condition={"movement_family": "clean"}),
             _p(2, 1, "volume", name="other")]
    assert [p["id"] for p in select_principles(cands, _STATE, query="snatch volume")] == [2]


def test_injury_specific_principles_only_reach_injured_athletes():
    """Program 32: an injury-rehab rule ('avoid full clean, full snatch, squats')
    has no condition the vocabulary can express, so it read as unconditional."""
    from principle_matcher import is_injury_specific, select_principles

    rehab = {"id": 235, "principle_name": "Emphasize heavy pulls during knee injury recovery", "priority": 9,
             "rationale": "Pulls keep strength while the knee heals.", "category": "exercise_selection",
             "condition": {}, "recommendation": {"avoid_exercises": ["full clean"]}}
    normal = {"id": 1, "principle_name": "Competition lifts first", "priority": 5, "rationale": "Fresh CNS.",
              "category": "exercise_selection", "condition": {}, "recommendation": {"competition_lifts_first": True}}
    tendon = {**normal, "id": 2, "principle_name": "Load management", "rationale": "Patellar tendinopathy flares."}
    assert is_injury_specific(rehab) and is_injury_specific(tendon) and not is_injury_specific(normal)
    healthy = select_principles([rehab, normal, tendon], {"has_injuries": False})
    assert [p["id"] for p in healthy] == [1]
    injured = select_principles([rehab, normal, tendon], {"has_injuries": True})
    assert {p["id"] for p in injured} == {235, 1, 2}
    assert {p["id"] for p in select_principles([rehab, normal], {})} == {235, 1}       # no flag → unfiltered


def test_build_session_state_carries_has_injuries():
    from principle_matcher import build_session_state

    def ctx(injuries):
        return SimpleNamespace(weeks_to_competition=None, previous_program=None, recent_logs=[],
                               level="intermediate", athlete={}, injuries=injuries)
    plan = SimpleNamespace(phase="accumulation")
    tmpl = SimpleNamespace(primary_movement="snatch")
    assert build_session_state(ctx([]), plan, 1, tmpl)["has_injuries"] is False
    assert build_session_state(ctx(["left knee"]), plan, 1, tmpl)["has_injuries"] is True


def _rule(pid, name, condition=None, recommendation=None, priority=5):
    return {"id": pid, "principle_name": name, "priority": priority, "rationale": "",
            "category": None, "condition": condition or {}, "recommendation": recommendation or {}}


def test_youth_principles_only_reach_youth_and_junior_athletes():
    """Program 53: '[1376] Youth Volume and Intensity Moderation' is stored as
    {"athlete_level": ["beginner"]} and reached a 35-year-old beginner."""
    from principle_matcher import is_youth_specific, select_principles

    youth = _rule(1376, "Youth Volume and Intensity Moderation", {"athlete_level": ["beginner"]})
    young = _rule(3824, "Reduced Max Strength Phase for Young Athletes")
    plates = _rule(1419, "Protect Growth Plates in Young Athletes from Elbow Overload")
    under = _rule(961, "Age 7 and Under - Bodyweight Only")
    junior = _rule(4244, "Junior strength training intensity cap")
    adult = _rule(1, "Dynamic Warm-Up Requirement")
    fifteen = _rule(2, "Fifteen-Minute Warm-Up")          # 'teen' inside a word is not a youth term
    rules = [youth, young, plates, under, junior, adult, fifteen]
    assert all(is_youth_specific(r) for r in (youth, young, plates, under, junior))
    assert not is_youth_specific(adult) and not is_youth_specific(fifteen)
    base = {"athlete_level": "beginner"}
    assert {p["id"] for p in select_principles(rules, {**base, "is_youth": False})} == {1, 2}
    assert len(select_principles(rules, {**base, "is_youth": True})) == len(rules)
    assert len(select_principles(rules, base)) == len(rules)                      # no flag → unfiltered


def test_novice_lift_bans_skipped_for_athletes_who_have_done_the_lifts():
    """Program 53: 'Beginner Foundation Phase' (avoid snatch / C&J, beginner,
    training age 0–0.25) stripped every full lift from a beginner who had just
    finished a 9-week Olympic-lifting program."""
    from principle_matcher import is_novice_lift_ban, select_principles

    foundation = _rule(5073, "Beginner Foundation Phase",
                       {"athlete_level": ["beginner"], "training_age_years": {"between": [0, 0.25]}},
                       {"avoid_exercises": ["snatch", "clean_and_jerk", "clean", "jerk"]})
    post = _rule(5074, "Post-Foundation Progression", {"training_age_years": {"between": [0.25, 1]}},
                 {"avoid_exercises": ["snatch", "clean_and_jerk"]})
    transition = _rule(4660, "Schedule active rest month at end of annual plan", {"phase": "transition"},
                       {"avoid_exercises": ["snatch", "clean and jerk"]})
    accessory = _rule(1, "Beginner accessory choice", {"athlete_level": ["beginner"]},
                      {"avoid_exercises": ["leg extension"]})
    assert is_novice_lift_ban(foundation) and is_novice_lift_ban(post)
    assert not is_novice_lift_ban(transition) and not is_novice_lift_ban(accessory)
    rules = [foundation, post, transition, accessory]
    base = {"athlete_level": "beginner", "training_age_years": 0.25, "phase": "transition"}   # every condition holds
    assert len(select_principles(rules, base)) == 4
    assert {p["id"] for p in select_principles(rules, {**base, "olympic_lifting_experience": True})} == {4660, 1}


def test_build_session_state_carries_youth_and_lifting_experience():
    from principle_matcher import build_session_state

    def ctx(band, experienced):
        return SimpleNamespace(weeks_to_competition=None, previous_program=None, recent_logs=[],
                               level="beginner", athlete={"olympic_lifting_experience": experienced},
                               injuries=[], age_band=band)
    plan, tmpl = SimpleNamespace(phase="general_prep"), SimpleNamespace(primary_movement="snatch")
    assert build_session_state(ctx("youth", False), plan, 1, tmpl)["is_youth"] is True
    assert build_session_state(ctx("junior", False), plan, 1, tmpl)["is_youth"] is True
    assert build_session_state(ctx("masters", False), plan, 1, tmpl)["is_youth"] is False
    assert build_session_state(ctx(None, False), plan, 1, tmpl)["is_youth"] is False
    assert build_session_state(ctx(None, True), plan, 1, tmpl)["olympic_lifting_experience"] is True


def test_scope_less_rep_caps_never_reach_the_prompt():
    """DOG-2l: '[700] Avoid Extreme Volume That Risks Rhabdomyolysis'
    ({"total_reps_max": 50}, empty condition) was read as a 50-rep session cap.
    `total_reps_max` has no recorded scope, so it is hidden from the prompt
    line. A rule with nothing else to recommend is not selected."""
    from generate import format_principle_line
    from principle_matcher import only_hidden_recommendation, prompt_recommendation

    rhabdo = _rule(700, "Avoid Extreme Volume That Risks Rhabdomyolysis", {},
                   {"total_reps_max": 50, "avoid_exercises": ["100-rep squat sets"]}, priority=10)
    bare_cap = _rule(2151, "Optimal Reps at 90% Per Session", {}, {"total_reps_max": 10}, priority=8)
    ceiling = _rule(3, "Ceiling rule", {}, {"intensity_ceiling": 85})
    assert prompt_recommendation(rhabdo) == {"avoid_exercises": ["100-rep squat sets"]}
    assert only_hidden_recommendation(bare_cap) and not only_hidden_recommendation(rhabdo)
    assert not only_hidden_recommendation(ceiling)
    assert not only_hidden_recommendation(_rule(4, "No recommendation"))   # SQL's job, not this filter's
    assert [p["id"] for p in select_principles([rhabdo, bare_cap, ceiling], {})] == [700, 3]
    line = format_principle_line(rhabdo)
    assert "total_reps_max" not in line and "50" not in line and "100-rep squat sets" in line
