# oly-agent/tests/test_retrieve.py
"""
Tests for the RETRIEVE step (retrieve.py).

All tests mock fetch_all to avoid a live DB. vector_loader is passed as None
so similarity search is skipped — the routing and return shape are what's tested.

Run: python tests/test_retrieve.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from retrieve import retrieve
from models import AthleteContext, ProgramPlan, WeekTarget, SessionTemplate
from phase_profiles import build_weekly_targets
from session_templates import get_session_templates
from shared.prilepin import compute_session_rep_target

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

def _ctx(faults=None, injuries=None, level="intermediate", sessions_per_week=4):
    return AthleteContext(
        athlete={"name": "Test", "level": level},
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
