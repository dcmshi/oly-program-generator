# oly-agent/tests/test_principle_matcher.py
"""
No-key tests for principle_matcher (RAG-H3): condition operators, array phases,
unknown facts, schema-drift keys, per-session state, and selection.

Run: PYTHONUTF8=1 uv run pytest tests/test_principle_matcher.py -q
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
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
