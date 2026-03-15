# oly-agent/tests/test_orchestrator.py
"""
Tests for the orchestrator's run() pipeline.

All steps (assess, plan, retrieve, generate, validate, explain) are mocked.
No live DB, LLM, or API keys required.

Run: python tests/test_orchestrator.py
"""

import os
import sys
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS = []
_INTEGRATION = os.getenv("INTEGRATION_TESTS", "").lower() in ("1", "true")


class _Skip(Exception):
    pass


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except _Skip as e:
        RESULTS.append(("SKIP", name, str(e)))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _integration_only():
    if not _INTEGRATION:
        raise _Skip("set INTEGRATION_TESTS=1 to enable")


# ── Fixtures ──────────────────────────────────────────────────────────────────

from shared.config import Settings
from models import (
    AthleteContext, ProgramPlan, RetrievalContext,
    GenerationResult, ValidationResult, WeekTarget, SessionTemplate,
)


def _settings():
    return Settings(cost_limit_per_program=1.00)


def _athlete_context():
    return AthleteContext(
        athlete={"id": 1, "name": "Test", "level": "intermediate", "sessions_per_week": 4,
                 "lift_emphasis": "balanced", "strength_limiters": [], "competition_experience": "none"},
        level="intermediate",
        maxes={"snatch": 100.0, "clean_and_jerk": 125.0},
        active_goal=None,
        previous_program=None,
        recent_logs=[],
        technical_faults=[],
        injuries=[],
        sessions_per_week=4,
        weeks_to_competition=None,
    )


def _week_target(week_number=1):
    return WeekTarget(
        week_number=week_number,
        volume_modifier=1.0,
        intensity_floor=70.0,
        intensity_ceiling=80.0,
        total_competition_lift_reps=20,
        reps_per_set_range=[3, 5],
        is_deload=False,
    )


def _session_template(day=1):
    return SessionTemplate(
        day_number=day,
        label="Snatch + Squat",
        primary_movement="snatch",
        secondary_movements=["squat"],
        session_volume_share=0.5,
        notes="",
    )


def _program_plan():
    return ProgramPlan(
        phase="accumulation",
        duration_weeks=1,
        sessions_per_week=1,
        deload_week=None,
        weekly_targets=[_week_target(1)],
        session_templates=[_session_template(1)],
        active_principles=[],
        supporting_chunks=[],
    )


def _retrieval_context():
    return RetrievalContext(
        fault_exercises={},
        template_references=[],
        programming_rationale=[],
        fault_correction_chunks=[],
        available_substitutions={},
        active_principles=[],
        prilepin_targets={},
        available_exercises=[{"name": "Snatch", "id": 1}],
    )


def _generation_result(exercises=None):
    return GenerationResult(
        exercises=exercises or [{
            "exercise_order": 1, "exercise_name": "Snatch",
            "sets": 4, "reps": 3, "intensity_pct": 75.0,
            "intensity_reference": "snatch", "absolute_weight_kg": None,
            "rpe_target": 7.5, "rest_seconds": 180,
            "is_max_attempt": False, "selection_rationale": "Main lift",
            "source_principle_ids": [], "source_chunk_ids": [],
        }],
        raw_response='{"exercises": [...]}',
        input_tokens=500,
        output_tokens=200,
        status="success",
        error_message=None,
        attempt_number=1,
    )


def _validation_result():
    return ValidationResult(
        is_valid=True,
        errors=[],
        warnings=[],
        session_comp_reps={"70-80": 12},
    )


# ── Common patch targets ──────────────────────────────────────────────────────

_PATCHES = {
    "get_connection":               "orchestrator.get_connection",
    "fetch_all":                    "orchestrator.fetch_all",
    "execute_returning":            "orchestrator.execute_returning",
    "execute":                      "orchestrator.execute",
    "assess":                       "orchestrator.assess",
    "plan":                         "orchestrator.plan",
    "retrieve":                     "orchestrator.retrieve",
    "generate":                     "orchestrator.generate_session_with_retries",
    "validate":                     "orchestrator.validate_session",
    "explain":                      "orchestrator.explain",
    "create_llm_client":            "orchestrator.create_llm_client",
    "estimate_cost":                "orchestrator.estimate_cost",
    "resolve_exercise_ids":         "orchestrator.resolve_exercise_ids",
    "resolve_weights":              "orchestrator.resolve_weights",
    "attach_source_chunk_ids":      "orchestrator.attach_source_chunk_ids",
    "apply_projected_maxes":        "orchestrator.apply_projected_maxes",
    "compute_session_rep_target":   "shared.prilepin.compute_session_rep_target",
    "build_session_prompt":         "orchestrator.build_session_prompt",
}


def _full_mock_stack(stack: ExitStack, overrides: dict = None) -> dict:
    """Enter all standard patches and return a dict of mock objects."""
    overrides = overrides or {}
    mocks = {}
    mock_conn = MagicMock()
    mock_conn.commit = MagicMock()
    mock_conn.rollback = MagicMock()
    mock_conn.close = MagicMock()

    defaults = {
        "get_connection": mock_conn,
        "fetch_all": [],
        "execute_returning": 42,  # program_id
        "execute": None,
        "assess": _athlete_context(),
        "plan": _program_plan(),
        "retrieve": _retrieval_context(),
        "generate": _generation_result(),
        "validate": _validation_result(),
        "explain": "# Program rationale\nTest rationale text.",
        "create_llm_client": MagicMock(),
        "estimate_cost": 0.01,
        "resolve_exercise_ids": lambda exs, lu: exs,
        "resolve_weights": lambda exs, mx: exs,
        "attach_source_chunk_ids": lambda exs, ctx: exs,
        "apply_projected_maxes": lambda mx, goal, phase: mx,
        "compute_session_rep_target": 15,
        "build_session_prompt": "prompt text",
    }

    for key, target in _PATCHES.items():
        rv = overrides.get(key, defaults[key])
        # Functions used as pass-throughs need side_effect, not return_value
        if callable(rv) and key in ("resolve_exercise_ids", "resolve_weights",
                                    "attach_source_chunk_ids", "apply_projected_maxes"):
            m = stack.enter_context(patch(target, side_effect=rv))
        else:
            m = stack.enter_context(patch(target, return_value=rv))
        mocks[key] = m

    # get_connection returns the conn object directly
    mocks["get_connection"].return_value = mock_conn
    mocks["conn"] = mock_conn
    return mocks


# ── Tests ─────────────────────────────────────────────────────────────────────

from orchestrator import run


def test_dry_run_returns_none():
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        result = run(1, _settings(), dry_run=True)
    assert result is None
    mocks["generate"].assert_not_called()
    mocks["explain"].assert_not_called()
    mocks["execute_returning"].assert_not_called()


def test_dry_run_calls_assess_and_plan():
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        run(1, _settings(), dry_run=True)
    mocks["assess"].assert_called_once_with(1, mocks["conn"])
    mocks["plan"].assert_called_once()


def test_full_generation_returns_program_id():
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        result = run(1, _settings())
    assert result == 42


def test_full_generation_calls_all_steps():
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        run(1, _settings())
    mocks["assess"].assert_called_once()
    mocks["plan"].assert_called_once()
    mocks["retrieve"].assert_called_once()
    mocks["generate"].assert_called_once()
    mocks["validate"].assert_called_once()
    mocks["explain"].assert_called_once()


def test_generation_failure_exercises_stores_empty_session():
    """When generation returns None exercises, an empty session is stored."""
    failed_result = _generation_result(exercises=None)
    failed_result.exercises = None

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"generate": failed_result})
        result = run(1, _settings())
    # Program should still be created (not None)
    assert result == 42


def test_cost_limit_exceeded_returns_program_id():
    """When cost exceeds the limit mid-program, pipeline aborts but returns program_id.

    The guard checks cumulative_cost > limit *before* each session.
    With limit=0.0 and one session costing 0.01, the first session runs
    (0.0 > 0.0 is False) but a second session would be blocked.
    """
    settings = _settings()
    settings.cost_limit_per_program = 0.0  # first session runs, subsequent ones blocked

    # 2-session plan so the second session hits the limit
    two_session_plan = ProgramPlan(
        phase="accumulation", duration_weeks=1, sessions_per_week=2, deload_week=None,
        weekly_targets=[_week_target(1)],
        session_templates=[_session_template(1), _session_template(2)],
        active_principles=[], supporting_chunks=[],
    )

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": two_session_plan})
        result = run(1, settings)

    # Program is created and returned even on cost-limit abort
    assert result is not None
    # First session runs; second is blocked by the cost guard
    assert mocks["generate"].call_count == 1


def test_exception_in_assess_returns_none():
    """Any exception in the pipeline rolls back and returns None."""
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        mocks["assess"].side_effect = ValueError("Athlete not found")
        result = run(1, _settings())
    assert result is None
    mocks["conn"].rollback.assert_called()


def test_exception_in_generate_returns_none():
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        mocks["generate"].side_effect = RuntimeError("LLM timeout")
        result = run(1, _settings())
    assert result is None


def test_conn_always_closed():
    """DB connection is closed even when an exception is raised."""
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        mocks["assess"].side_effect = RuntimeError("crash")
        run(1, _settings())
    mocks["conn"].close.assert_called()


def test_full_generation_commits_twice():
    """Program record commit + rationale commit = at least 2 commits."""
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        run(1, _settings())
    assert mocks["conn"].commit.call_count >= 2


def test_no_max_test_for_accumulation():
    """Accumulation phase should not generate a max test session."""
    plan = _program_plan()
    assert plan.phase == "accumulation"

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        run(1, _settings())
    # execute_returning called once for the program INSERT, once for the session INSERT
    # (no extra max_test session)
    assert mocks["execute_returning"].call_count == 2  # program + 1 session


def test_multi_week_program_generates_correct_session_count():
    """A 2-week, 2-session-per-week program should generate 4 sessions."""
    plan = ProgramPlan(
        phase="accumulation",
        duration_weeks=2,
        sessions_per_week=2,
        deload_week=None,
        weekly_targets=[_week_target(1), _week_target(2)],
        session_templates=[_session_template(1), _session_template(2)],
        active_principles=[],
        supporting_chunks=[],
    )
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": plan})
        run(1, _settings())
    # generate called once per session: 2 weeks × 2 sessions = 4 times
    assert mocks["generate"].call_count == 4


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    skipped = sum(1 for r in RESULTS if r[0] == "SKIP")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed")
