# oly-agent/tests/test_retrieve.py
"""
Tests for the RETRIEVE step (retrieve.py).

All tests mock fetch_all to avoid a live DB. vector_loader is passed as None
so similarity search is skipped — the routing and return shape are what's tested.

Run: python tests/test_retrieve.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import AthleteContext, ProgramPlan, SessionTemplate, WeekTarget
from phase_profiles import build_weekly_targets
from retrieve import build_session_query, compose_session_context, retrieve, retrieve_session_context
from session_templates import get_session_templates

from shared.constants import VECTOR_SEARCH_MIN_SIMILARITY

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

def _ctx(faults=None, injuries=None, level="intermediate", sessions_per_week=4,
         lift_emphasis="balanced", strength_limiters=None):
    return AthleteContext(
        athlete={
            "name": "Test", "level": level,
            "lift_emphasis": lift_emphasis,
            "strength_limiters": strength_limiters or [],
        },
        level=level,
        maxes={"snatch": 100.0, "clean_and_jerk": 120.0},
        active_goal={"goal": "general_strength"},
        previous_program=None,
        recent_logs=[],
        technical_faults=faults or [],
        injuries=injuries or [],
        sessions_per_week=sessions_per_week,
        weeks_to_competition=None,
    )


def _mock_vector_loader(return_chunks=None):
    """Return a mock vector_loader whose similarity_search returns empty lists."""
    vl = MagicMock()
    vl.similarity_search.return_value = return_chunks or []
    return vl

def _plan(phase="accumulation", max_complexity=3):
    raw = build_weekly_targets(phase, 4, "intermediate")
    tmpls = get_session_templates(4)
    session_templates = [
        SessionTemplate(
            day_number=t["day_number"], label=t["label"],
            primary_movement=t["primary_movement"],
            secondary_movements=t["secondary_movements"],
            session_volume_share=t["session_volume_share"],
        )
        for t in tmpls
    ]
    weekly_targets = [
        WeekTarget(
            week_number=t["week_number"], volume_modifier=t["volume_modifier"],
            intensity_floor=t["intensity_floor"], intensity_ceiling=t["intensity_ceiling"],
            total_competition_lift_reps=20,
            reps_per_set_range=t["reps_per_set_range"], is_deload=t["is_deload"],
        )
        for t in raw
    ]
    return ProgramPlan(
        phase=phase, duration_weeks=4, sessions_per_week=4, deload_week=4,
        weekly_targets=weekly_targets, session_templates=session_templates,
        active_principles=[], supporting_chunks=[], max_complexity=max_complexity,
    )


# ── audit5 agent-M3: fault retrieval must cover jerk and squat families ──────

def test_fault_retrieval_covers_jerk_and_squat_families():
    """The loop queried only ('snatch','clean'), so the selectable jerk fault
    (dip_forward) and squat correctives were unreachable — the prompt then
    showed 'no specific exercises' and disabled the fault-coverage check."""
    families = []

    def capture(conn, sql, params):
        if "movement_family = %s" in sql:  # the fault-family lookup, not the SELECT list
            families.append(params[2])
        return []

    with patch("retrieve.fetch_all", side_effect=capture):
        retrieve(_ctx(faults=["dip_forward"]), _plan(), conn=None, vector_loader=None)
    assert {"snatch", "clean", "jerk", "squat"} <= set(families), \
        f"fault retrieval must cover jerk+squat families, queried: {families}"


# ── No faults, no injuries, no vector_loader ────────────────────────────────

def test_returns_retrieval_context():
    from models import RetrievalContext
    # fetch_all called for: template_references, available_exercises
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(), _plan(), conn=None, vector_loader=None)
    assert isinstance(result, RetrievalContext)

def test_no_faults_gives_empty_fault_exercises():
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(faults=[]), _plan(), conn=None, vector_loader=None)
    assert result.fault_exercises == {}

def test_no_injuries_gives_empty_substitutions():
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(injuries=[]), _plan(), conn=None, vector_loader=None)
    assert result.available_substitutions == {}

def test_no_vector_loader_gives_empty_chunks():
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(), _plan(), conn=None, vector_loader=None)
    assert result.programming_rationale == []
    assert result.fault_correction_chunks == []

def test_available_exercises_populated_from_db():
    fake_exercises = [
        {"id": 1, "name": "Snatch", "movement_family": "snatch", "category": "competition_variant",
         "complexity_level": 1, "faults_addressed": [], "primary_purpose": "Competition lift",
         "typical_intensity_low": 70, "typical_intensity_high": 90,
         "typical_sets_low": 3, "typical_sets_high": 6,
         "typical_reps_low": 1, "typical_reps_high": 3},
    ]
    with patch("retrieve.fetch_all", side_effect=[[], fake_exercises]):
        result = retrieve(_ctx(), _plan(), conn=None, vector_loader=None)
    assert len(result.available_exercises) == 1
    assert result.available_exercises[0]["name"] == "Snatch"


# ── With technical faults ────────────────────────────────────────────────────

def test_faults_trigger_exercise_lookup_per_family():
    fault_exercise = {
        "name": "Snatch Balance", "category": "variation", "primary_purpose": "Fix forward miss",
        "faults_addressed": ["forward_miss"], "complexity_level": 2,
        "typical_intensity_low": 70, "typical_intensity_high": 80,
        "typical_sets_low": 3, "typical_sets_high": 5,
        "typical_reps_low": 2, "typical_reps_high": 3,
    }
    # fetch_all calls: snatch/clean/jerk/squat fault exercises (audit5-M3),
    # template_references, available_exercises
    with patch("retrieve.fetch_all", side_effect=[[fault_exercise], [], [], [], [], []]):
        result = retrieve(_ctx(faults=["forward_miss"]), _plan(), conn=None, vector_loader=None)
    assert "snatch" in result.fault_exercises
    assert result.fault_exercises["snatch"][0]["name"] == "Snatch Balance"

def test_faults_with_no_matching_exercises_gives_empty_dict():
    # Both family queries return []
    with patch("retrieve.fetch_all", side_effect=[[], [], [], [], [], []]):
        result = retrieve(_ctx(faults=["some_fault"]), _plan(), conn=None, vector_loader=None)
    assert result.fault_exercises == {}


# ── With injuries ────────────────────────────────────────────────────────────

def test_injuries_trigger_substitution_lookup():
    sub_row = {
        "exercise_id": 1, "original_name": "Clean & Jerk",
        "substitute_name": "Hang Clean", "primary_purpose": "Reduce knee stress",
        "substitution_context": "injury_modification", "notes": "",
    }
    # fetch_all calls: template_references, available_exercises, substitutions
    with patch("retrieve.fetch_all", side_effect=[[], [], [sub_row]]):
        result = retrieve(_ctx(injuries=["knee"]), _plan(), conn=None, vector_loader=None)
    assert "Clean & Jerk" in result.available_substitutions
    assert result.available_substitutions["Clean & Jerk"][0]["substitute_name"] == "Hang Clean"


# ── Prilepin targets ─────────────────────────────────────────────────────────

def test_prilepin_targets_populated_from_weekly_targets():
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(), _plan(phase="accumulation"), conn=None, vector_loader=None)
    # Should have at least one zone key
    assert len(result.prilepin_targets) >= 1

def test_active_principles_passed_through_from_plan():
    principles = [{"id": 1, "principle_name": "Test", "recommendation": {}}]
    p = _plan()
    p.active_principles = principles
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(), p, conn=None, vector_loader=None)
    assert result.active_principles == principles


# ── Vector search: all faults searched ──────────────────────────────────────

def test_all_faults_searched_not_just_first_two():
    """similarity_search is called once per fault, not capped at 2."""
    vl = _mock_vector_loader()
    faults = ["forward_lean", "early_arm_bend", "slow_turnover", "press_out"]
    # fetch_all calls: snatch fault exercises, clean fault exercises,
    # template_references, available_exercises
    with patch("retrieve.fetch_all", side_effect=[[], [], [], [], [], []]):
        retrieve(_ctx(faults=faults), _plan(), conn=None, vector_loader=vl)

    fault_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "correcting" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
    ]
    assert len(fault_queries) == 4, (
        f"Expected 4 fault searches (one per fault), got {len(fault_queries)}"
    )
    for fault in faults:
        # fault ids are humanised in the query (RAG-L1): early_arm_bend → "early arm bend"
        assert any(fault.replace("_", " ") in q for q in fault_queries), f"Fault '{fault}' not searched"


def test_two_faults_still_searched():
    """The all-faults path works correctly when there are exactly 2 faults."""
    vl = _mock_vector_loader()
    faults = ["forward_lean", "press_out"]
    with patch("retrieve.fetch_all", side_effect=[[], [], [], [], [], []]):
        retrieve(_ctx(faults=faults), _plan(), conn=None, vector_loader=vl)

    fault_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "correcting" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
    ]
    assert len(fault_queries) == 2


# ── Vector search: lift_emphasis in queries ───────────────────────────────────

def test_snatch_biased_emphasis_in_session_query():
    """lift_emphasis=snatch_biased is included in every session query string
    (session queries are built per session by build_session_query — RAG-H4)."""
    plan = _plan()
    session_queries = [
        build_session_query(_ctx(lift_emphasis="snatch_biased"), plan, tmpl, plan.weekly_targets[0])
        for tmpl in plan.session_templates
    ]
    assert len(session_queries) == 4
    assert all("snatch biased lift focus" in q for q in session_queries), (
        f"Expected 'snatch biased lift focus' in session queries: {session_queries}"
    )


def test_balanced_emphasis_not_added_to_query():
    """lift_emphasis=balanced adds nothing to the query (it's the default)."""
    plan = _plan()
    session_queries = [
        build_session_query(_ctx(lift_emphasis="balanced"), plan, tmpl, plan.weekly_targets[0])
        for tmpl in plan.session_templates
    ]
    assert all("focus" not in q for q in session_queries), (
        f"'balanced' should not add focus context: {session_queries}"
    )


# ── Vector search: strength_limiters searches ─────────────────────────────────

def test_strength_limiters_trigger_extra_searches():
    """Each strength limiter produces an additional vector search."""
    vl = _mock_vector_loader()
    limiters = ["squat_limited", "overhead_limited"]
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        retrieve(_ctx(strength_limiters=limiters), _plan(), conn=None, vector_loader=vl)

    limiter_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "strength development" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
    ]
    assert len(limiter_queries) == 2, (
        f"Expected 2 limiter searches, got {len(limiter_queries)}: {limiter_queries}"
    )
    assert any("squat" in q for q in limiter_queries)
    assert any("overhead" in q for q in limiter_queries)


def test_strength_limiters_term_cleaned_in_query():
    """'_limited' suffix is stripped from the query term (e.g. 'squat_limited' → 'squat')."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        retrieve(_ctx(strength_limiters=["squat_limited"]), _plan(), conn=None, vector_loader=vl)

    limiter_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "strength development" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
    ]
    assert len(limiter_queries) == 1
    assert "squat_limited" not in limiter_queries[0]
    assert "squat" in limiter_queries[0]


def test_no_strength_limiters_no_extra_search():
    """Empty strength_limiters list produces no limiter-specific searches."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        retrieve(_ctx(strength_limiters=[]), _plan(), conn=None, vector_loader=vl)

    limiter_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "strength development" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
    ]
    assert limiter_queries == []


# ── Prompt: fault_correction_chunks in context block ─────────────────────────

def test_fault_correction_chunks_returned_when_faults_present():
    """fault_correction_chunks are populated from vector search when athlete has faults."""
    fault_chunk = {
        "id": 99, "chunk_type": "fault_correction",
        "raw_content": "Forward lean is corrected by...", "similarity": 0.8,
    }
    vl = _mock_vector_loader(return_chunks=[fault_chunk])
    with patch("retrieve.fetch_all", side_effect=[[], [], [], [], [], []]):
        result = retrieve(
            _ctx(faults=["forward_lean"]), _plan(), conn=None, vector_loader=vl
        )
    assert len(result.fault_correction_chunks) > 0
    assert result.fault_correction_chunks[0]["chunk_type"] == "fault_correction"


def test_fault_correction_chunks_empty_when_no_faults():
    """fault_correction_chunks stay empty when athlete has no technical faults."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(faults=[]), _plan(), conn=None, vector_loader=vl)
    assert result.fault_correction_chunks == []


# ── T1: min_similarity kwarg asserted on every call site ─────────────────────

def _get_call_query(call):
    return call.kwargs.get("query") or (call.args[0] if call.args else "")


def test_session_template_search_passes_min_similarity():
    """similarity_search for a session passes min_similarity=0.45 and the soft
    type preference. Session searches moved out of retrieve() into
    retrieve_session_context (RAG-H4) — retrieve() must not run them any more."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        ctx_out = retrieve(_ctx(), _plan(), conn=None, vector_loader=vl)
    assert not [c for c in vl.similarity_search.call_args_list if "exercise selection" in _get_call_query(c)], \
        "retrieve() must not issue program-level session queries"

    plan = _plan()
    retrieve_session_context(vl, _ctx(), plan, plan.session_templates[0], plan.weekly_targets[0], ctx_out, cache={})
    session_calls = [c for c in vl.similarity_search.call_args_list
                     if "exercise selection" in _get_call_query(c)]
    assert len(session_calls) == 1
    call = session_calls[0]
    assert call.kwargs.get("min_similarity") == VECTOR_SEARCH_MIN_SIMILARITY
    assert call.kwargs.get("preferred_chunk_types") == ["programming_rationale", "periodization"]
    assert "chunk_types" not in call.kwargs


def test_fault_search_passes_min_similarity():
    """similarity_search for fault correction passes min_similarity=0.45."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], [], [], [], [], []]):
        retrieve(_ctx(faults=["forward_lean"]), _plan(), conn=None, vector_loader=vl)

    fault_calls = [c for c in vl.similarity_search.call_args_list
                   if "correcting" in _get_call_query(c)]
    assert len(fault_calls) > 0
    for call in fault_calls:
        assert call.kwargs.get("min_similarity") == VECTOR_SEARCH_MIN_SIMILARITY, (
            f"Expected min_similarity={VECTOR_SEARCH_MIN_SIMILARITY}, "
            f"got {call.kwargs.get('min_similarity')}"
        )


def test_limiter_search_passes_min_similarity():
    """similarity_search for strength limiters passes min_similarity=0.45."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        retrieve(_ctx(strength_limiters=["squat_limited"]), _plan(), conn=None, vector_loader=vl)

    limiter_calls = [c for c in vl.similarity_search.call_args_list
                     if "strength development" in _get_call_query(c)]
    assert len(limiter_calls) > 0
    for call in limiter_calls:
        assert call.kwargs.get("min_similarity") == VECTOR_SEARCH_MIN_SIMILARITY, (
            f"Expected min_similarity={VECTOR_SEARCH_MIN_SIMILARITY}, "
            f"got {call.kwargs.get('min_similarity')}"
        )


def test_session_template_search_exception_caught():
    """similarity_search raising for a session is caught — the session gets an
    empty context (and the failure is cached so it isn't retried 16 times)."""
    vl = _mock_vector_loader()
    vl.similarity_search.side_effect = RuntimeError("DB connection lost")
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(), _plan(), conn=None, vector_loader=vl)
    assert result.programming_rationale == []
    plan, cache = _plan(), {}
    chunks = retrieve_session_context(vl, _ctx(), plan, plan.session_templates[0], plan.weekly_targets[0], result, cache=cache)
    queries = [k for k in cache if not k.startswith("\x00")]   # skip the AUD-3 state entry
    assert chunks == [] and len(queries) == 1


def test_fault_search_exception_caught():
    """similarity_search raising for fault search is caught — not re-raised."""
    vl = _mock_vector_loader()
    vl.similarity_search.side_effect = RuntimeError("Embedding API error")
    with patch("retrieve.fetch_all", side_effect=[[], [], [], [], [], []]):
        result = retrieve(_ctx(faults=["forward_lean"]), _plan(), conn=None, vector_loader=vl)
    assert result.fault_correction_chunks == []


def test_limiter_search_exception_caught():
    """similarity_search raising for limiter search is caught — not re-raised."""
    vl = _mock_vector_loader()
    vl.similarity_search.side_effect = RuntimeError("timeout")
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(
            _ctx(strength_limiters=["squat_limited"]), _plan(), conn=None, vector_loader=vl
        )
    assert result.programming_rationale == []


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


# ── RAG-H2: chunk_type is a soft preference, never a hard filter ─────────────

def _all_search_calls(faults=None, limiters=None):
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", return_value=[]):
        retrieve(_ctx(faults=faults, strength_limiters=limiters), _plan(), conn=None, vector_loader=vl)
    return vl.similarity_search.call_args_list


def test_no_search_uses_hard_chunk_type_filter():
    """The hard `chunk_types` filter reached 15% of the corpus (0 of the deload
    chunks); every production search must use `preferred_chunk_types` instead."""
    calls = _all_search_calls(faults=["early_arm_bend"], limiters=["squat_limited"])
    assert calls, "expected similarity_search calls"
    for call in calls:
        assert "chunk_types" not in call.kwargs, f"hard filter still used: {call.kwargs}"
        assert call.kwargs.get("preferred_chunk_types"), f"no preference passed: {call.kwargs}"


def test_session_and_limiter_searches_prefer_rationale_and_periodization():
    calls = _all_search_calls(limiters=["squat_limited"])
    prefs = [tuple(c.kwargs["preferred_chunk_types"]) for c in calls]
    assert all(set(p) == {"programming_rationale", "periodization"} for p in prefs), prefs


def test_limiter_search_no_longer_names_never_assigned_methodology():
    calls = _all_search_calls(limiters=["squat_limited"])
    assert all("methodology" not in c.kwargs["preferred_chunk_types"] for c in calls)


def test_fault_search_prefers_fault_correction():
    calls = _all_search_calls(faults=["early_arm_bend"])
    fault_calls = [c for c in calls if "correcting" in c.kwargs["query"]]
    assert fault_calls and all(c.kwargs["preferred_chunk_types"] == ["fault_correction"] for c in fault_calls)


# ── RAG-H4: per-session retrieval + context composition ──────────────────────

def _c(id_, source_id, score, chunk_type="periodization", fault=None):
    d = {"id": id_, "source_id": source_id, "similarity": score, "score": score,
         "chunk_type": chunk_type, "raw_content": f"chunk {id_}"}
    if fault:
        d["fault"] = fault
    return d


def test_build_session_query_names_movement_phase_band_and_humanizes_tokens():
    plan = _plan(phase="accumulation")
    wt = plan.weekly_targets[0]
    q = build_session_query(_ctx(faults=["early_arm_bend"], lift_emphasis="snatch_biased",
                                 strength_limiters=["squat_limited"]), plan, plan.session_templates[1], wt)
    assert "clean session" in q and "jerk, pull support" in q
    assert "accumulation phase" in q
    lo = int(wt.intensity_floor // 5 * 5)
    assert f"at {lo}-" in q and "% intensity" in q
    assert "early arm bend" in q and "early_arm_bend" not in q
    assert "snatch biased lift focus" in q and "squat limited" in q
    assert "deload" not in q
    deload_wt = next(t for t in plan.weekly_targets if t.is_deload)
    assert "deload week" in build_session_query(_ctx(), plan, plan.session_templates[0], deload_wt)


def test_session_queries_differ_per_template_and_are_cached_per_band():
    """Every template gets its own query (the old code queried [:2]); two weeks
    in the same intensity band share one search via the cache."""
    vl = _mock_vector_loader()
    plan = _plan()
    rc = MagicMock(fault_correction_chunks=[])
    cache = {}
    for tmpl in plan.session_templates:
        retrieve_session_context(vl, _ctx(), plan, tmpl, plan.weekly_targets[0], rc, cache=cache)
    assert vl.similarity_search.call_count == 4
    queries = [c.kwargs["query"] for c in vl.similarity_search.call_args_list]
    assert len(set(queries)) == 4

    same_band = WeekTarget(week_number=2, volume_modifier=1.0,
                           intensity_floor=plan.weekly_targets[0].intensity_floor,
                           intensity_ceiling=plan.weekly_targets[0].intensity_ceiling,
                           total_competition_lift_reps=20, reps_per_set_range=[2, 4], is_deload=False)
    retrieve_session_context(vl, _ctx(), plan, plan.session_templates[0], same_band, rc, cache=cache)
    assert vl.similarity_search.call_count == 4, "same template + band must hit the cache"


def test_retrieve_session_context_without_vector_loader_is_empty():
    plan = _plan()
    assert retrieve_session_context(None, _ctx(), plan, plan.session_templates[0], plan.weekly_targets[0],
                                    MagicMock(fault_correction_chunks=[]), cache={}) == []


def test_compose_orders_by_score_and_caps_total():
    session = [_c(1, 10, 0.50), _c(2, 11, 0.70), _c(3, 12, 0.60), _c(4, 13, 0.65), _c(5, 14, 0.90)]
    out = compose_session_context(session, [], has_faults=False)
    assert [c["id"] for c in out] == [5, 2, 4, 3]


def test_compose_round_robins_fault_chunks_across_faults_first():
    faults = [
        _c(101, 20, 0.95, "fault_correction", fault="early_arm_bend"),
        _c(102, 21, 0.94, "fault_correction", fault="early_arm_bend"),
        _c(201, 22, 0.80, "fault_correction", fault="jumping_forward"),
    ]
    session = [_c(1, 30, 0.9), _c(2, 31, 0.8)]
    out = compose_session_context(session, faults, has_faults=True)
    assert [c["id"] for c in out] == [101, 201, 1, 2], "one chunk per fault, best first, then session chunks"


def test_compose_ignores_fault_chunks_when_athlete_has_no_faults():
    out = compose_session_context([_c(1, 30, 0.9)], [_c(101, 20, 0.99, "fault_correction", fault="x")], has_faults=False)
    assert [c["id"] for c in out] == [1]


def test_compose_caps_chunks_per_source_and_dedupes_ids():
    session = [_c(1, 7, 0.9), _c(2, 7, 0.8), _c(3, 7, 0.7), _c(4, 8, 0.6), _c(1, 7, 0.9)]
    out = compose_session_context(session, [], has_faults=False)
    assert [c["id"] for c in out] == [1, 2, 4], "max 2 from source 7, duplicate id dropped"


def test_compose_per_source_cap_is_per_group_not_shared_with_fault_chunks():
    """DOG-1: both fault chunks and every session candidate came from the same
    book (Everett), so a shared cap left the session with only the two fault
    chunks. Fault and session picks each get their own per-source budget."""
    faults = [_c(101, 507, 0.95, "fault_correction", fault="slow_turnover"),
              _c(102, 507, 0.90, "fault_correction", fault="slow_turnover")]
    session = [_c(1, 507, 0.9), _c(2, 507, 0.8), _c(3, 507, 0.7)]
    out = compose_session_context(session, faults, has_faults=True)
    assert [c["id"] for c in out] == [101, 102, 1, 2], "2 fault + 2 session chunks from the same source"
    # the cap still holds inside each group, and ids never repeat across groups
    out = compose_session_context([_c(101, 507, 0.9), _c(2, 507, 0.8), _c(3, 507, 0.7)], faults, has_faults=True)
    assert [c["id"] for c in out] == [101, 102, 2, 3]


# ── RAG-M1: every production search is hybrid ────────────────────────────────

def test_all_production_searches_pass_hybrid_flag():
    from shared.constants import HYBRID_SEARCH_ENABLED

    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", return_value=[]):
        rc = retrieve(_ctx(faults=["early_arm_bend"], strength_limiters=["squat_limited"]), _plan(),
                      conn=None, vector_loader=vl)
    plan = _plan()
    retrieve_session_context(vl, _ctx(), plan, plan.session_templates[0], plan.weekly_targets[0], rc, cache={})
    calls = vl.similarity_search.call_args_list
    assert len(calls) >= 3  # fault + limiter + session
    assert all(c.kwargs.get("hybrid") is HYBRID_SEARCH_ENABLED for c in calls), [c.kwargs for c in calls]


# ── RAG-L1: fault query embeds words, not identifiers ─────────────────────────

def test_fault_query_humanises_underscored_fault_ids():
    from retrieve import build_fault_query

    q = build_fault_query("early_arm_bend", "intermediate")
    assert "early arm bend" in q and "_" not in q and "intermediate athlete" in q

    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", return_value=[]):
        retrieve(_ctx(faults=["jumping_forward"]), _plan(), conn=None, vector_loader=vl)
    fault_calls = [c.kwargs["query"] for c in vl.similarity_search.call_args_list if "correcting" in c.kwargs["query"]]
    assert fault_calls == ["correcting jumping forward in weightlifting, intermediate athlete"]


# ── AUD-3: program-level context diversity ───────────────────────────────────

def _fault_pool(n=5, source=507, fault="slow_turnover"):
    return [_c(100 + i, source, 0.9 - i * 0.01, "fault_correction", fault=fault) for i in range(n)]


def test_stateful_compose_rotates_fault_chunks_across_sessions():
    """Program 29: the same two fault chunks sat in C1-C2 of all 24 sessions.
    With a program state each session takes the least-shown fault chunks first
    (rank breaks ties), so the fault slots walk the fault's ranked list."""
    from retrieve import ContextDiversityState

    state = ContextDiversityState()
    faults = _fault_pool(5)
    picks = []
    for _ in range(5):
        out = compose_session_context([_c(1, 30, 0.8), _c(2, 31, 0.7)], faults, has_faults=True,
                                      state=state, program_source_share=1.0)
        picks.append([c["id"] for c in out[:2]])
    assert picks[:3] == [[100, 101], [102, 103], [104, 100]], picks
    counts = {cid: sum(cid in p for p in picks) for cid in range(100, 105)}
    assert set(counts.values()) == {2}, counts   # 10 fault slots spread evenly over 5 chunks
    assert state.sessions == 5 and state.slots == 20


def test_stateless_compose_is_unchanged_without_state():
    faults = _fault_pool(5)
    for _ in range(3):
        out = compose_session_context([_c(1, 30, 0.8)], faults, has_faults=True)
        assert [c["id"] for c in out] == [100, 101, 1]


def test_rotation_round_robins_across_faults_too():
    from retrieve import ContextDiversityState

    state = ContextDiversityState()
    faults = _fault_pool(2, fault="a") + [_c(200 + i, 600 + i, 0.8 - i * 0.01, "fault_correction", fault="b")
                                          for i in range(2)]
    first = compose_session_context([], faults, has_faults=True, state=state, program_source_share=1.0)
    second = compose_session_context([], faults, has_faults=True, state=state, program_source_share=1.0)
    assert [c["id"] for c in first] == [100, 200]
    assert [c["id"] for c in second] == [101, 201]


def test_program_source_cap_prefers_other_sources_once_a_book_holds_its_share():
    """Once source 507 holds its share of the program's slots, sessions pass it
    over while other candidates exist."""
    from retrieve import ContextDiversityState

    state = ContextDiversityState()
    session = [_c(1, 507, 0.9), _c(2, 507, 0.85), _c(3, 30, 0.6), _c(4, 31, 0.55), _c(5, 32, 0.5)]
    faults = [_c(101, 507, 0.95, "fault_correction", fault="x"), _c(102, 507, 0.9, "fault_correction", fault="x")]
    s1 = compose_session_context(session, faults, has_faults=True, state=state, program_source_share=0.5)
    # session 1: horizon 4 slots → allowance 2; the fault picks use it, session picks go elsewhere
    assert [c["id"] for c in s1] == [101, 102, 3, 4], s1
    for _ in range(3):
        compose_session_context(session, faults, has_faults=True, state=state, program_source_share=0.5)
        assert state.source_slots[507] <= 0.5 * state.slots, state.source_slots


def test_program_source_cap_never_empties_a_session():
    """DOG-1: when every candidate comes from the capped book the second pass
    fills the slots anyway — the cap reorders, it never starves."""
    from retrieve import ContextDiversityState

    state = ContextDiversityState()
    faults = [_c(101, 507, 0.95, "fault_correction", fault="x"), _c(102, 507, 0.9, "fault_correction", fault="x")]
    session = [_c(1, 507, 0.9), _c(2, 507, 0.8), _c(3, 507, 0.7)]
    for _ in range(4):
        out = compose_session_context(session, faults, has_faults=True, state=state, program_source_share=0.1)
        assert len(out) == 4, out
        assert [c.get("fault") for c in out[:2]] == ["x", "x"], "fault picks stay first (RAG-M5 label order)"
        assert [c["id"] for c in out[2:]] == [1, 2], "session picks in rank order"


def test_session_picks_keep_rank_order_across_the_two_passes():
    from retrieve import ContextDiversityState

    state = ContextDiversityState()
    state.source_slots[507] = 10
    state.slots = 10
    session = [_c(1, 507, 0.9), _c(2, 30, 0.5)]
    out = compose_session_context(session, [], has_faults=False, state=state, program_source_share=0.4)
    assert [c["id"] for c in out] == [1, 2], "the capped chunk comes back in pass 2 but keeps its rank"


def test_retrieve_session_context_keeps_the_state_in_the_program_cache():
    from retrieve import CONTEXT_STATE_KEY, ContextDiversityState

    plan = _plan()
    vl = _mock_vector_loader([_c(1, 30, 0.8), _c(2, 31, 0.7)])
    rc = MagicMock(fault_correction_chunks=_fault_pool(4))
    cache = {}
    ids = []
    for tmpl in plan.session_templates[:2]:
        out = retrieve_session_context(vl, _ctx(faults=["slow_turnover"]), plan, tmpl, plan.weekly_targets[0],
                                       rc, cache=cache)
        ids.append([c["id"] for c in out[:2]])
    assert isinstance(cache[CONTEXT_STATE_KEY], ContextDiversityState)
    assert ids == [[100, 101], [102, 103]], ids
    # an explicit state wins over the cached one
    own = ContextDiversityState()
    retrieve_session_context(vl, _ctx(faults=["slow_turnover"]), plan, plan.session_templates[0],
                             plan.weekly_targets[0], rc, cache=cache, state=own)
    assert own.sessions == 1 and cache[CONTEXT_STATE_KEY].sessions == 2


def test_context_state_is_thread_safe_under_concurrent_composes():
    """Weeks run concurrently (AUD-4). Concurrent composes on one state must
    neither lose updates nor break the per-session invariants."""
    import threading

    from retrieve import context_state_for

    cache = {}
    states = []
    grabbers = [threading.Thread(target=lambda: states.append(context_state_for(cache))) for _ in range(16)]
    for t in grabbers:
        t.start()
    for t in grabbers:
        t.join()
    assert len({id(s) for s in states}) == 1, "one state per program cache"

    state = states[0]
    faults = _fault_pool(5)
    session = [_c(i, 30 + i % 3, 0.9 - i * 0.01) for i in range(1, 11)]
    results, errors = [], []

    def _worker():
        try:
            for _ in range(25):
                results.append(compose_session_context(session, faults, has_faults=True, state=state))
        except Exception as e:   # surfaced below
            errors.append(e)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert state.sessions == 200 and state.slots == sum(len(r) for r in results) == 800
    assert sum(state.chunk_uses[c] for c in range(100, 105)) == 400
    for out in results:
        assert len({c["id"] for c in out}) == len(out) == 4


# ── AUD-3: optional listwise rerank (rerank.py) ──────────────────────────────

def _llm_reply(text, input_tokens=1000, output_tokens=20):
    block = MagicMock(type="text", text=text)
    return MagicMock(content=[block], stop_reason="end_turn",
                     usage=MagicMock(input_tokens=input_tokens, output_tokens=output_tokens,
                                     cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _reranker(reply=None, side_effect=None, model="deepseek/deepseek-v4.1-flash", **kw):
    from rerank import ListwiseReranker

    client = MagicMock()
    if side_effect is not None:
        client.messages.create.side_effect = side_effect
    else:
        client.messages.create.return_value = reply
    return ListwiseReranker(client, model, **kw), client


def test_rerank_reorders_by_the_model_ranking_and_appends_omitted_ids():
    cands = [_c(1, 10, 0.9), _c(2, 11, 0.8), _c(3, 12, 0.7), _c(4, 13, 0.6)]
    rr, client = _reranker(_llm_reply('{"ranking": [3, 1, 3, 99]}'))
    out = rr.rerank("snatch pulls", cands)
    assert [c["id"] for c in out] == [3, 1, 2, 4], "ranked first (deduped, out-of-range dropped), rest in order"
    assert out[0]["rerank_score"] > out[1]["rerank_score"] > out[-1]["rerank_score"]
    assert "rerank_score" not in cands[0], "input rows are not mutated"
    assert rr.calls == 1 and rr.cost_usd > 0
    # cached per (model, query, candidate ids): no second API call
    assert [c["id"] for c in rr.rerank("snatch pulls", cands, top_k=2)] == [3, 1]
    assert client.messages.create.call_count == 1


def test_rerank_request_is_schema_constrained_with_thinking_disabled():
    from rerank import RERANK_SCHEMA

    rr, client = _reranker(_llm_reply('{"ranking": [2, 1]}'))
    rr.rerank("q", [_c(1, 10, 0.9), _c(2, 11, 0.8)])
    kw = client.messages.create.call_args.kwargs
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": RERANK_SCHEMA}
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["model"] == "deepseek/deepseek-v4.1-flash"
    assert "[1]" in kw["messages"][0]["content"] and "[2]" in kw["messages"][0]["content"]


def test_rerank_only_reorders_the_top_n():
    cands = [_c(i, 10 + i, 1.0 - i / 10) for i in range(1, 6)]
    rr, client = _reranker(_llm_reply('{"ranking": [2, 1]}'), top_n=2)
    out = rr.rerank("q", cands)
    assert [c["id"] for c in out] == [2, 1, 3, 4, 5]
    assert "[3]" not in client.messages.create.call_args.kwargs["messages"][0]["content"]


def test_rerank_malformed_reply_keeps_the_retrieval_order():
    cands = [_c(1, 10, 0.9), _c(2, 11, 0.8), _c(3, 12, 0.7)]
    for text in ("not json", '{"order": [2, 1]}', '{"ranking": [0, 7, "x"]}', '[2, 1]'):
        rr, _ = _reranker(_llm_reply(text))
        out = rr.rerank("q", cands, top_k=2)
        assert [c["id"] for c in out] == [1, 2], text
        assert rr.failures == 1 and rr.calls == 0


def test_rerank_api_error_keeps_the_retrieval_order_and_is_not_cached():
    cands = [_c(1, 10, 0.9), _c(2, 11, 0.8)]
    rr, client = _reranker(side_effect=RuntimeError("upstream 400"))
    assert [c["id"] for c in rr.rerank("q", cands)] == [1, 2]
    assert rr.failures == 1
    client.messages.create.side_effect = None
    client.messages.create.return_value = _llm_reply('{"ranking": [2, 1]}')
    assert [c["id"] for c in rr.rerank("q", cands)] == [2, 1], "a failure is retried on the next call"


def test_rerank_is_a_noop_for_fewer_than_two_candidates():
    rr, client = _reranker(_llm_reply('{"ranking": [1]}'))
    assert rr.rerank("q", []) == [] and [c["id"] for c in rr.rerank("q", [_c(1, 10, 0.9)])] == [1]
    client.messages.create.assert_not_called()


def test_retrieve_session_context_fetches_top_n_and_composes_in_rerank_order():
    from shared.constants import RERANK_TOP_N

    plan = _plan()
    cands = [_c(1, 10, 0.9), _c(2, 11, 0.8), _c(3, 12, 0.7), _c(4, 13, 0.6)]
    vl = _mock_vector_loader(cands)
    rr, _ = _reranker(_llm_reply('{"ranking": [4, 3, 2, 1]}'))
    out = retrieve_session_context(vl, _ctx(), plan, plan.session_templates[0], plan.weekly_targets[0],
                                   MagicMock(fault_correction_chunks=[]), cache={}, reranker=rr)
    assert vl.similarity_search.call_args.kwargs["top_k"] >= RERANK_TOP_N
    assert [c["id"] for c in out] == [4, 3, 2, 1], "compose sorts by rerank_score, not the retrieval score"


def test_rerank_off_by_default_in_production_paths():
    from retrieve import default_reranker

    from shared.constants import RERANK_ENABLED, VECTOR_SEARCH_DEFAULT_TOP_K

    assert RERANK_ENABLED is False
    assert default_reranker({}) is None
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", return_value=[]):
        retrieve(_ctx(faults=["early_arm_bend"]), _plan(), conn=None, vector_loader=vl)
    assert all(c.kwargs["top_k"] == VECTOR_SEARCH_DEFAULT_TOP_K for c in vl.similarity_search.call_args_list)


def test_default_reranker_build_failure_disables_rerank():
    import retrieve as r

    with patch.object(r, "RERANK_ENABLED", True), \
         patch("rerank.ListwiseReranker.from_settings", side_effect=ValueError("no key")):
        cache = {}
        assert r.default_reranker(cache, settings=MagicMock()) is None
        assert cache[r.RERANKER_KEY] is None, "the failure is remembered for the program"
