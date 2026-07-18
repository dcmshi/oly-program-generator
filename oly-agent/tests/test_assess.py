# oly-agent/tests/test_assess.py
"""
Tests for the ASSESS step (assess.py).

Pure-logic tests (estimate_missing_maxes) need no DB or API keys.
assess() tests mock fetch_one / fetch_all to avoid DB dependency.

Run: python tests/test_assess.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from assess import MAX_ESTIMATION_RATIOS, assess, estimate_missing_maxes

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

def _athlete(**overrides):
    base = {
        "id": 1, "name": "Test Athlete", "level": "intermediate",
        "sessions_per_week": 4, "technical_faults": ["forward_miss"],
        "injuries": [],
    }
    return {**base, **overrides}

def _goal(days_out=None, goal_type="general_strength"):
    return {
        "goal": goal_type, "is_active": True, "priority": 1,
        "competition_date": (date.today() + timedelta(days=days_out)) if days_out is not None else None,
    }


# ── estimate_missing_maxes — pure logic ─────────────────────────────────────

def test_estimates_from_clean_and_jerk():
    known = {"clean_and_jerk": 100.0}
    result = estimate_missing_maxes(known)
    expected_front_squat = round(100.0 * MAX_ESTIMATION_RATIOS["front_squat"]["ratio"] * 2) / 2
    assert "front_squat" in result
    # estimate_missing_maxes now returns a plain {ref: kg} dict (R8)
    assert result["front_squat"] == expected_front_squat

def test_estimates_from_snatch():
    known = {"snatch": 80.0}
    result = estimate_missing_maxes(known)
    assert "snatch_pull" in result
    assert "snatch_deadlift" in result
    assert "overhead_squat" in result

def test_does_not_overwrite_known_maxes():
    known = {"clean_and_jerk": 100.0, "back_squat": 150.0}
    result = estimate_missing_maxes(known)
    assert "back_squat" not in result, "Should not estimate what is already known"

def test_empty_known_returns_empty():
    result = estimate_missing_maxes({})
    assert result == {}, "With no known maxes nothing can be estimated"

def test_rounds_estimates_to_half_kg():
    known = {"snatch": 77.0}
    result = estimate_missing_maxes(known)
    # snatch_pull ratio = 1.15 → 77 * 1.15 = 88.55 → rounds to 88.5
    assert result["snatch_pull"] == 88.5

def test_no_cross_contamination():
    # front_squat derives from clean_and_jerk which is missing
    known = {"snatch": 80.0}
    result = estimate_missing_maxes(known)
    assert "front_squat" not in result
    assert "back_squat" not in result

def test_all_ratios_covered():
    known = {"snatch": 100.0, "clean_and_jerk": 120.0}
    result = estimate_missing_maxes(known)
    for exercise in MAX_ESTIMATION_RATIOS:
        assert exercise in result, f"{exercise} not estimated despite both source maxes being known"


# ── assess() — mocked DB ────────────────────────────────────────────────────

def test_assess_raises_for_missing_athlete():
    with patch("assess.fetch_one", return_value=None):
        try:
            assess(999, None)
            raise AssertionError("Expected ValueError")
        except ValueError as e:
            assert "999" in str(e)

# assess() makes 3 fetch_one calls: athlete, active_goal, previous_program
# and 2 fetch_all calls: max_rows, recent_logs

def test_assess_returns_athlete_context():
    with patch("assess.fetch_one", side_effect=[_athlete(), None, None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.level == "intermediate"
    assert ctx.sessions_per_week == 4
    assert ctx.weeks_to_competition is None

def test_assess_null_sessions_per_week_defaults_to_4():
    # Regression (A-L3): the column is nullable, so a SQL NULL makes
    # .get("sessions_per_week", 4) return None (key exists) → downstream
    # TypeError. `or 4` coalesces it.
    athlete_null_spw = _athlete(sessions_per_week=None)
    with patch("assess.fetch_one", side_effect=[athlete_null_spw, None, None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.sessions_per_week == 4

def test_assess_computes_weeks_to_competition():
    with patch("assess.fetch_one", side_effect=[_athlete(), _goal(days_out=56), None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.weeks_to_competition == 8

def test_assess_past_competition_gives_none():
    # Superseded by AGT-M2: a past date used to clamp to 0 ("competition this
    # week") forever; it now reads as no competition set.
    with patch("assess.fetch_one", side_effect=[_athlete(), _goal(days_out=-14), None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.weeks_to_competition is None

def test_assess_no_goal_gives_none_weeks():
    with patch("assess.fetch_one", side_effect=[_athlete(), None, None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.weeks_to_competition is None
    assert ctx.active_goal is None

def test_assess_technical_faults_defaults_to_empty_list():
    athlete_no_faults = _athlete(technical_faults=None)
    with patch("assess.fetch_one", side_effect=[athlete_no_faults, None, None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.technical_faults == []

def test_assess_injuries_defaults_to_empty_list():
    athlete_no_injuries = _athlete(injuries=None)
    with patch("assess.fetch_one", side_effect=[athlete_no_injuries, None, None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.injuries == []

def test_assess_estimates_missing_maxes_from_snatch():
    # DB returns only snatch max; assess() should derive snatch_pull etc.
    snatch_row = {"name": "Snatch", "movement_family": "snatch",
                  "weight_kg": 100.0, "date_achieved": date.today(), "rpe": None}
    with patch("assess.fetch_one", side_effect=[_athlete(), None, None]):
        with patch("assess.fetch_all", side_effect=[[snatch_row], []]):
            ctx = assess(1, None)
    assert "snatch" in ctx.maxes
    assert "snatch_pull" in ctx.maxes, "snatch_pull should be estimated"
    assert "overhead_squat" in ctx.maxes, "overhead_squat should be estimated"

def test_assess_propagates_recent_logs():
    log_row = {"exercise_name": "Snatch", "weight_kg": 90.0, "sets_completed": 5,
               "rpe": 8.0, "make_rate": 0.9, "log_date": date.today()}
    with patch("assess.fetch_one", side_effect=[_athlete(), None, None]):
        with patch("assess.fetch_all", side_effect=[[], [log_row]]):
            ctx = assess(1, None)
    assert len(ctx.recent_logs) == 1
    assert ctx.recent_logs[0]["exercise_name"] == "Snatch"


# ── audit5 agent-L4: clean and jerk must be estimable so weights resolve ─────

def test_clean_and_jerk_refs_estimated():
    """A prescription referencing 'clean' or 'jerk' resolved to NULL kg (no max
    source, no estimation ratio) — the athlete saw a % with no weight."""
    known = {"clean_and_jerk": 120.0}
    result = estimate_missing_maxes(known)
    assert "clean" in result, "clean must be estimable from clean_and_jerk (audit5-L4)"
    assert "jerk" in result, "jerk must be estimable from clean_and_jerk (audit5-L4)"
    assert result["clean"] >= 120.0 and result["jerk"] >= 120.0


# ── AGT-M2: past competition_date must not read as "competition this week" ───

def test_past_competition_date_treated_as_none():
    """A stale goal dated before today clamped to weeks_to_competition=0 on
    every generation — perpetual 1-week realization until hand-edited."""
    past_goal = _goal(days_out=-30)
    with patch("assess.fetch_one", side_effect=[_athlete(), past_goal, None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.weeks_to_competition is None, ctx.weeks_to_competition


def test_future_competition_date_still_counts():
    goal = _goal(days_out=21)
    with patch("assess.fetch_one", side_effect=[_athlete(), goal, None]):
        with patch("assess.fetch_all", side_effect=[[], []]):
            ctx = assess(1, None)
    assert ctx.weeks_to_competition == 3, ctx.weeks_to_competition


# ── AGT-H2: previous-program lookup must not order by never-written end_date ─

def test_previous_program_ordered_by_updated_at():
    """end_date is NULL on every completed program (nothing writes it), so
    ORDER BY end_date DESC picks an arbitrary row once an athlete has two
    completed programs. Order by updated_at instead."""
    sqls = []

    def fake_fetch_one(conn, sql, params=None):
        sqls.append(sql)
        return _athlete() if len(sqls) == 1 else None

    with patch("assess.fetch_one", side_effect=fake_fetch_one):
        with patch("assess.fetch_all", side_effect=[[], []]):
            assess(1, None)
    prev_sql = next(s for s in sqls if "generated_programs" in s)
    assert "updated_at DESC" in prev_sql, prev_sql
    assert "ORDER BY end_date" not in prev_sql, prev_sql


def test_save_outcome_stamps_end_date():
    """feedback.save_outcome must write end_date at completion so the column
    stops being permanently NULL (AGT-H2)."""
    from unittest.mock import MagicMock

    import feedback
    from models import ProgramOutcome

    captured = {}

    def fake_execute(conn, sql, params):
        captured["sql"] = sql

    outcome = ProgramOutcome(
        program_id=1, athlete_id=1, maxes_delta={}, sessions_prescribed=1,
        sessions_completed=1, adherence_pct=100.0, avg_rpe_deviation=0.0,
        avg_make_rate=0.9, make_rate_by_lift={}, phase_verdict=None,
        avg_weekly_reps=0.0, rpe_trend="stable", make_rate_trend="stable",
        athlete_feedback=None,
    )
    with patch.object(feedback, "execute", fake_execute):
        feedback.save_outcome(outcome, MagicMock())
    assert "end_date" in captured["sql"], "save_outcome must stamp end_date (AGT-H2)"


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
