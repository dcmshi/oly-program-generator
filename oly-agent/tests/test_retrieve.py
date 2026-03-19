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

from retrieve import retrieve
from models import AthleteContext, ProgramPlan, WeekTarget, SessionTemplate
from phase_profiles import build_weekly_targets
from session_templates import get_session_templates
from shared.prilepin import compute_session_rep_target
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
    # fetch_all calls: snatch fault exercises, clean fault exercises,
    # template_references, available_exercises
    with patch("retrieve.fetch_all", side_effect=[[fault_exercise], [], [], []]):
        result = retrieve(_ctx(faults=["forward_miss"]), _plan(), conn=None, vector_loader=None)
    assert "snatch" in result.fault_exercises
    assert result.fault_exercises["snatch"][0]["name"] == "Snatch Balance"

def test_faults_with_no_matching_exercises_gives_empty_dict():
    # Both family queries return []
    with patch("retrieve.fetch_all", side_effect=[[], [], [], []]):
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
    with patch("retrieve.fetch_all", side_effect=[[], [], [], []]):
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
        assert any(fault in q for q in fault_queries), f"Fault '{fault}' not searched"


def test_two_faults_still_searched():
    """The all-faults path works correctly when there are exactly 2 faults."""
    vl = _mock_vector_loader()
    faults = ["forward_lean", "press_out"]
    with patch("retrieve.fetch_all", side_effect=[[], [], [], []]):
        retrieve(_ctx(faults=faults), _plan(), conn=None, vector_loader=vl)

    fault_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "correcting" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
    ]
    assert len(fault_queries) == 2


# ── Vector search: lift_emphasis in queries ───────────────────────────────────

def test_snatch_biased_emphasis_in_session_query():
    """lift_emphasis=snatch_biased is included in session template query strings."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        retrieve(_ctx(lift_emphasis="snatch_biased"), _plan(), conn=None, vector_loader=vl)

    session_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "exercise selection" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
    ]
    assert len(session_queries) > 0
    assert all("snatch biased lift focus" in q for q in session_queries), (
        f"Expected 'snatch biased lift focus' in session queries: {session_queries}"
    )


def test_balanced_emphasis_not_added_to_query():
    """lift_emphasis=balanced adds nothing to the query (it's the default)."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        retrieve(_ctx(lift_emphasis="balanced"), _plan(), conn=None, vector_loader=vl)

    session_queries = [
        call.kwargs.get("query") or call.args[0]
        for call in vl.similarity_search.call_args_list
        if "exercise selection" in (call.kwargs.get("query") or (call.args[0] if call.args else ""))
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
    with patch("retrieve.fetch_all", side_effect=[[], [], [], []]):
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
    """similarity_search for session templates passes min_similarity=0.45."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        retrieve(_ctx(), _plan(), conn=None, vector_loader=vl)

    session_calls = [c for c in vl.similarity_search.call_args_list
                     if "exercise selection" in _get_call_query(c)]
    assert len(session_calls) > 0
    for call in session_calls:
        assert call.kwargs.get("min_similarity") == VECTOR_SEARCH_MIN_SIMILARITY, (
            f"Expected min_similarity={VECTOR_SEARCH_MIN_SIMILARITY}, "
            f"got {call.kwargs.get('min_similarity')}"
        )


def test_fault_search_passes_min_similarity():
    """similarity_search for fault correction passes min_similarity=0.45."""
    vl = _mock_vector_loader()
    with patch("retrieve.fetch_all", side_effect=[[], [], [], []]):
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
    """similarity_search raising for session template is caught — not re-raised."""
    vl = _mock_vector_loader()
    vl.similarity_search.side_effect = RuntimeError("DB connection lost")
    with patch("retrieve.fetch_all", side_effect=[[], []]):
        result = retrieve(_ctx(), _plan(), conn=None, vector_loader=vl)
    assert result.programming_rationale == []


def test_fault_search_exception_caught():
    """similarity_search raising for fault search is caught — not re-raised."""
    vl = _mock_vector_loader()
    vl.similarity_search.side_effect = RuntimeError("Embedding API error")
    with patch("retrieve.fetch_all", side_effect=[[], [], [], []]):
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
