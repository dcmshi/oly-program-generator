# oly-agent/tests/test_orchestrator.py
"""
Tests for the orchestrator's run() pipeline.

All steps (assess, plan, retrieve, generate, validate, explain) are mocked.
No live DB, LLM, or API keys required.

Run: python tests/test_orchestrator.py
"""

import json
import os
import sys
from contextlib import ExitStack
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

from models import (
    AthleteContext,
    GenerationResult,
    ProgramPlan,
    RetrievalContext,
    SessionTemplate,
    ValidationResult,
    WeekTarget,
)

from shared.config import Settings


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
    "make_vector_loader":           "orchestrator._make_vector_loader",
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
        # None = no retriever: keeps these tests off OpenAI + Postgres even when
        # openai is installed and .env has real keys (retrieve_session_context → [])
        "make_vector_loader": None,
        "fetch_all": [],
        "execute_returning": 42,  # program_id
        "execute": None,
        "assess": _athlete_context(),
        "plan": _program_plan(),
        "retrieve": _retrieval_context(),
        "generate": _generation_result(),
        "validate": _validation_result(),
        # explain returns (rationale, input_tokens, output_tokens) — AGT-L7
        "explain": ("# Program rationale\nTest rationale text.", 100, 50),
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
        _full_mock_stack(stack)
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
        _full_mock_stack(stack, overrides={"generate": failed_result})
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


def _athlete_context_with(**athlete_overrides):
    ctx = _athlete_context()
    ctx.athlete.update(athlete_overrides)
    return ctx


def _two_session_plan():
    return ProgramPlan(
        phase="accumulation", duration_weeks=1, sessions_per_week=2, deload_week=None,
        weekly_targets=[_week_target(1)],
        session_templates=[_session_template(1), _session_template(2)],
        active_principles=[], supporting_chunks=[],
    )


def test_retrieve_called_with_settings():
    """A-L2: retrieve() must receive settings so vector_search_top_k is honored
    (it was omitted, silently forcing the default top_k)."""
    settings = _settings()
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        run(1, settings)
    assert mocks["retrieve"].call_args.kwargs.get("settings") is settings


def test_cost_limit_usd_zero_is_honored():
    """A-L8: an explicit athlete cost_limit_usd=0 must be respected, not treated
    as unset and replaced by the global default."""
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={
            "plan": _two_session_plan(),
            "assess": _athlete_context_with(cost_limit_usd=0),
        })
        result = run(1, _settings())  # global limit is 1.00
    assert result is not None
    # With limit 0 honored, session 2 is blocked; without the fix both would run.
    assert mocks["generate"].call_count == 1


def test_cost_limit_abort_writes_rationale():
    """A-L1: a cost-truncated program stores an explanatory rationale so it isn't
    silently mistaken for a finished program."""
    def _call_writes_cost_rationale(call):
        if len(call.args) < 3:
            return False
        sql, params = call.args[1], call.args[2]
        return "rationale" in sql and any(
            isinstance(p, str) and "Cost Limit" in p for p in (params or ())
        )
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={
            "plan": _two_session_plan(),
            "assess": _athlete_context_with(cost_limit_usd=0),
        })
        run(1, _settings())
    assert any(_call_writes_cost_rationale(c) for c in mocks["execute"].call_args_list)


def test_max_test_session_uses_current_maxes():
    """A-L6: the max-test build-up references CURRENT maxes (attempt a new PR),
    not projected/effective targets."""
    from orchestrator import _build_max_test_session
    ctx = _athlete_context()  # snatch=100, clean_and_jerk=125
    exercises = _build_max_test_session(ctx, {"snatch": 1, "clean & jerk": 2})
    snatch_attempt = next(e for e in exercises
                          if e["exercise_name"] == "Snatch" and e["is_max_attempt"])
    cj_attempt = next(e for e in exercises
                      if e["exercise_name"] == "Clean & Jerk" and e["is_max_attempt"])
    assert snatch_attempt["absolute_weight_kg"] == 100.0  # 100% of current snatch max
    assert cj_attempt["absolute_weight_kg"] == 125.0       # 100% of current C&J max


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

# ── audit5 agent-M1: athlete_snapshot must not persist credentials ────────────

def test_athlete_snapshot_excludes_credentials():
    from orchestrator import _build_athlete_snapshot
    athlete = {
        "id": 1, "name": "Test", "level": "intermediate",
        "password_hash": "$2b$12$secret", "username": "test",
        "is_admin": True, "created_at": "x", "updated_at": "y",
        "sessions_per_week": 4,
    }
    snap = _build_athlete_snapshot(athlete)
    for leaked in ("password_hash", "username", "is_admin", "created_at", "updated_at"):
        assert leaked not in snap, f"{leaked} must not be snapshotted (audit5-M1)"
    assert snap["name"] == "Test" and snap["level"] == "intermediate"


# ── audit5 agent-L5: 1-week (all-deload) realization must skip the max-test ────

def test_max_test_day_none_for_all_deload_realization():
    from types import SimpleNamespace

    from orchestrator import compute_peak_week
    # every week is deload (1-week realization taper) → no peak week to test on
    deload_weeks = [SimpleNamespace(week_number=1, is_deload=True)]
    assert compute_peak_week(deload_weeks) is None
    mixed = [SimpleNamespace(week_number=1, is_deload=False),
             SimpleNamespace(week_number=2, is_deload=True)]
    assert compute_peak_week(mixed) == 1


# ── AGT-L7: explain respects the cost guard and its spend is counted ──────────

def test_explain_skipped_when_cost_limit_reached():
    """Generation landing at the limit must not still fire the explain call."""
    settings = _settings()
    settings.cost_limit_per_program = 0.0  # first session runs, then over limit

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        result = run(1, settings)
    assert result == 42
    mocks["explain"].assert_not_called()


# ── WEB-M8: an expired deadline aborts before spending on LLM calls ───────────

def test_deadline_exceeded_aborts_and_marks_draft():
    """A monotonic deadline in the past must abort before any generation call,
    leaving a draft with a self-explanatory rationale, and return program_id."""
    import time as _time

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        result = run(1, _settings(), deadline=_time.monotonic() - 1)
    assert result == 42
    mocks["generate"].assert_not_called()


# ── AGT-H1: max-test day must clear the template fallback's day numbers ───────

def test_max_test_day_clears_template_fallback_days():
    """sessions_per_week=2 falls back to the 3-day template distribution, so
    day 3 is already taken — spw+1=3 would collide with
    UNIQUE(program_id, week_number, day_number) and kill a fully-paid run."""
    from types import SimpleNamespace

    from orchestrator import compute_max_test_day

    three_day_templates = [SimpleNamespace(day_number=d) for d in (1, 2, 3)]
    assert compute_max_test_day(three_day_templates, sessions_per_week=2) == 4


def test_max_test_day_normal_and_empty_cases():
    from types import SimpleNamespace

    from orchestrator import compute_max_test_day

    templates = [SimpleNamespace(day_number=d) for d in (1, 2, 3, 4)]
    assert compute_max_test_day(templates, sessions_per_week=4) == 5
    assert compute_max_test_day([], sessions_per_week=4) == 5

# ── max_sessions: the model-baseline slice of the real pipeline ──────────────

def test_max_sessions_caps_generation_and_labels_the_partial_draft():
    """`eval.model_baseline` runs a slice of a program: generation stops at
    the cap, the max-test session is skipped, EXPLAIN still runs, and the
    stored rationale says the program is partial."""
    plan = ProgramPlan(
        phase="realization",          # includes_max_test — must be skipped when capped
        duration_weeks=1,
        sessions_per_week=4,
        deload_week=None,
        weekly_targets=[_week_target(1)],
        session_templates=[_session_template(d) for d in (1, 2, 3, 4)],
        active_principles=[],
        supporting_chunks=[],
    )
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": plan})
        program_id = run(1, _settings(), max_sessions=2)

    assert program_id == 42
    assert mocks["generate"].call_count == 2
    assert mocks["explain"].call_count == 1
    # program row + 2 sessions, no max-test session
    assert mocks["execute_returning"].call_count == 3
    rationale_writes = [
        c for c in mocks["execute"].call_args_list
        if len(c.args) >= 3 and "rationale" in c.args[1]
    ]
    assert rationale_writes, "rationale never written"
    stored = rationale_writes[-1].args[2][0]
    assert stored.startswith("# Partial Program — Session Cap")
    assert "2 of 4 planned sessions" in stored
    assert "Test rationale text." in stored     # EXPLAIN output kept below the banner

    params = mocks["execute_returning"].call_args_list[0].args[2]
    generation_params = json.loads(params[-1])
    assert generation_params["max_sessions"] == 2
    assert generation_params["thinking"] == "disabled" and generation_params["effort"] is None   # production default


def test_generation_params_record_thinking_and_effort():
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        run(1, Settings(cost_limit_per_program=1.0, generation_model="claude-sonnet-5",
                        generation_thinking="disabled", generation_effort="low"))
    params = mocks["execute_returning"].call_args_list[0].args[2]
    generation_params = json.loads(params[-1])
    assert generation_params["model"] == "claude-sonnet-5"
    assert (generation_params["thinking"], generation_params["effort"]) == ("disabled", "low")
    assert "max_sessions" not in generation_params


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


# ── RAG-H3: principles are selected per session ──────────────────────────────

def test_principles_selected_per_session_by_movement_family():
    """A snatch-only rule reaches the snatch day's generate + validate calls and
    NOT the clean day's; an unconditional rule reaches both. The plan's list is
    a superset that the orchestrator narrows per session."""
    snatch_rule = {"id": 1, "principle_name": "snatch only", "priority": 9,
                   "condition": {"movement_family": "snatch"}, "recommendation": {}}
    any_rule = {"id": 2, "principle_name": "always", "priority": 5, "condition": None, "recommendation": {}}
    clean_day = SessionTemplate(day_number=2, label="C&J", primary_movement="clean",
                                secondary_movements=["jerk"], session_volume_share=0.5, notes="")
    two_day_plan = ProgramPlan(
        phase="accumulation", duration_weeks=1, sessions_per_week=2, deload_week=None,
        weekly_targets=[_week_target(1)], session_templates=[_session_template(1), clean_day],
        active_principles=[snatch_rule, any_rule], supporting_chunks=[],
    )
    retrieval = _retrieval_context()
    retrieval.active_principles = [snatch_rule, any_rule]

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": two_day_plan, "retrieve": retrieval})
        run(1, _settings())

    gen_calls = mocks["generate"].call_args_list
    assert len(gen_calls) == 2
    assert [p["id"] for p in gen_calls[0].kwargs["active_principles"]] == [1, 2]   # snatch day
    assert [p["id"] for p in gen_calls[1].kwargs["active_principles"]] == [2]      # clean day
    val_calls = mocks["validate"].call_args_list
    assert [p["id"] for p in val_calls[1].kwargs["active_principles"]] == [2]
    prompt_calls = mocks["build_session_prompt"].call_args_list
    assert [p["id"] for p in prompt_calls[1].kwargs["active_principles"]] == [2]


# ── RAG-H4: knowledge context is retrieved per session ───────────────────────

def test_session_context_retrieved_per_session_with_shared_cache():
    """retrieve_session_context runs once per session with the program-level
    cache, and the composed chunks reach build_session_prompt as context_chunks."""
    clean_day = SessionTemplate(day_number=2, label="C&J", primary_movement="clean",
                                secondary_movements=["jerk"], session_volume_share=0.5, notes="")
    two_day_plan = ProgramPlan(
        phase="accumulation", duration_weeks=1, sessions_per_week=2, deload_week=None,
        weekly_targets=[_week_target(1)], session_templates=[_session_template(1), clean_day],
        active_principles=[], supporting_chunks=[],
    )
    fake_chunks = [{"id": 5, "chunk_type": "periodization", "raw_content": "ctx", "source_id": 1}]

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": two_day_plan})
        rsc = stack.enter_context(patch("orchestrator.retrieve_session_context", return_value=fake_chunks))
        run(1, _settings())

    assert rsc.call_count == 2
    caches = {id(c.kwargs["cache"]) for c in rsc.call_args_list}
    assert len(caches) == 1, "all sessions must share one query cache"
    templates = [c.args[3] for c in rsc.call_args_list]
    assert [t.primary_movement for t in templates] == ["snatch", "clean"]
    prompt_calls = mocks["build_session_prompt"].call_args_list
    assert all(c.kwargs["context_chunks"] == fake_chunks for c in prompt_calls)
    attach_calls = mocks["attach_source_chunk_ids"].call_args_list
    assert all(c.args[1]["programming_rationale"] == fake_chunks for c in attach_calls)


# ── RAG-M5: orchestrator passes the labelled retrieval set + context_chunks ───

def test_orchestrator_builds_labelled_retrieval_set_for_generate_and_trace():
    chunks = [
        {"id": 5, "chunk_type": "periodization", "source_id": 1, "similarity": 0.6, "score": 0.65,
         "raw_content": "a", "session_query": "q"},
        {"id": 9, "chunk_type": "fault_correction", "source_id": 2, "similarity": 0.55, "score": 0.6,
         "raw_content": "b", "session_query": "q"},
    ]
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack)
        stack.enter_context(patch("orchestrator.retrieve_session_context", return_value=chunks))
        run(1, _settings())

    gen_kwargs = mocks["generate"].call_args.kwargs
    assert gen_kwargs["retrieval_set"] == [
        {"label": "C1", "id": 5, "chunk_type": "periodization", "source_id": 1, "similarity": 0.6, "score": 0.65, "session_query": "q"},
        {"label": "C2", "id": 9, "chunk_type": "fault_correction", "source_id": 2, "similarity": 0.55, "score": 0.6, "session_query": "q"},
    ]
    attach_ctx = mocks["attach_source_chunk_ids"].call_args.args[1]
    assert attach_ctx["context_chunks"] == chunks


# ── AUD-4: concurrent weeks + the week-1 block template ──────────────────────

import threading  # noqa: E402


def _multi_week_plan(weeks=3, days=2):
    return ProgramPlan(
        phase="accumulation", duration_weeks=weeks, sessions_per_week=days, deload_week=None,
        weekly_targets=[_week_target(w) for w in range(1, weeks + 1)],
        session_templates=[_session_template(d) for d in range(1, days + 1)],
        active_principles=[], supporting_chunks=[],
    )


def _gen_side_effect(record=None, barrier_weeks=(), barrier=None):
    """generate_session_with_retries stand-in: a fresh result per call, the call
    recorded as (week, day, conn, thread); weeks in `barrier_weeks` wait on
    `barrier` in their first session, which only passes when they overlap."""
    lock = threading.Lock()

    def _fake(**kw):
        with lock:
            if record is not None:
                record.append((kw["week_number"], kw["day_number"], kw["conn"], threading.get_ident()))
        if barrier is not None and kw["week_number"] in barrier_weeks and kw["day_number"] == 1:
            barrier.wait()
        return _generation_result()
    return _fake


def _session_insert_slots(mocks):
    """(week, day) of every program_sessions INSERT, in execution order."""
    return [
        (c.args[2][1], c.args[2][2]) for c in mocks["execute_returning"].call_args_list
        if "program_sessions" in c.args[1]
    ]


def test_week_concurrency_1_reproduces_sequential_order():
    """concurrency=1: every session on the main thread and the main connection,
    in (week, day) order, one connection opened in total."""
    calls = []
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": _multi_week_plan(3, 2)})
        mocks["generate"].side_effect = _gen_side_effect(calls)
        assert run(1, _settings(), week_concurrency=1) == 42
    slots = [(w, d) for w in (1, 2, 3) for d in (1, 2)]
    assert [(w, d) for w, d, _, _ in calls] == slots
    assert all(conn is mocks["conn"] for _, _, conn, _ in calls)
    assert {t for _, _, _, t in calls} == {threading.get_ident()}
    assert mocks["get_connection"].call_count == 1
    assert _session_insert_slots(mocks) == slots


def test_concurrent_weeks_use_per_thread_connections_and_keep_db_order():
    """concurrency>1: week 1 first on the main connection, weeks 2..4 overlap on
    worker threads with their own connections (committed + closed), every
    session is generated, and session rows are still inserted by (week, day)."""
    calls = []
    opened = []

    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": _multi_week_plan(4, 2)})
        main_conn = mocks["conn"]

        def _connect(_url):
            c = main_conn if not opened else MagicMock(name=f"worker_conn_{len(opened)}")
            opened.append(c)
            return c
        mocks["get_connection"].side_effect = _connect
        barrier = threading.Barrier(3, timeout=10)   # weeks 2, 3, 4 must be in flight together
        mocks["generate"].side_effect = _gen_side_effect(calls, barrier_weeks=(2, 3, 4), barrier=barrier)
        assert run(1, _settings(), week_concurrency=4) == 42

    slots = [(w, d) for w in (1, 2, 3, 4) for d in (1, 2)]
    assert sorted((w, d) for w, d, _, _ in calls) == slots
    assert [(w, d) for w, d, _, _ in calls[:2]] == [(1, 1), (1, 2)]      # week 1 first, alone
    assert all(conn is main_conn for w, _, conn, _ in calls if w == 1)
    by_week = {w: {id(conn) for ww, _, conn, _ in calls if ww == w} for w in (2, 3, 4)}
    assert all(len(v) == 1 for v in by_week.values()), "one connection per week"
    worker_conns = opened[1:]
    assert len(worker_conns) == 3
    assert set().union(*by_week.values()) == {id(c) for c in worker_conns}
    assert threading.get_ident() not in {t for w, _, _, t in calls if w > 1}
    for c in worker_conns:
        c.commit.assert_called()
        c.close.assert_called_once()
    assert _session_insert_slots(mocks) == slots                         # main conn, (week, day) order
    mocks["explain"].assert_called_once()
    sessions = mocks["explain"].call_args.kwargs["program_sessions"]
    assert [(s["week"], s["day"]) for s in sessions] == slots
    assert all(s["session_id"] == 42 for s in sessions)


def test_concurrent_weeks_never_exceed_the_cost_limit():
    """Each session costs 0.01 and the limit is 0.035: the sequential guard
    admits exactly 4 sessions (it stops once spent > limit). Concurrent weeks
    must admit no more — in-flight sessions count against the limit."""
    for concurrency in (1, 4):
        settings = _settings()
        settings.cost_limit_per_program = 0.035
        with ExitStack() as stack:
            mocks = _full_mock_stack(stack, overrides={"plan": _multi_week_plan(4, 2)})
            mocks["generate"].side_effect = _gen_side_effect()
            assert run(1, settings, week_concurrency=concurrency) == 42
        assert mocks["generate"].call_count == 4, (concurrency, mocks["generate"].call_count)
        mocks["explain"].assert_not_called()
        rationale = [c.args[2][0] for c in mocks["execute"].call_args_list
                     if len(c.args) >= 3 and "rationale" in c.args[1]]
        assert rationale and rationale[-1].startswith("# Generation Aborted — Cost Limit")
        assert "4 of 8 sessions" in rationale[-1]


def test_concurrent_weeks_worker_error_fails_the_run():
    """An exception in a worker week propagates like the sequential path: the
    run returns None and the main connection is rolled back."""
    def _fake(**kw):
        if kw["week_number"] == 3:
            raise RuntimeError("LLM down")
        return _generation_result()
    with ExitStack() as stack:
        mocks = _full_mock_stack(stack, overrides={"plan": _multi_week_plan(4, 2)})
        mocks["generate"].side_effect = _fake
        assert run(1, _settings(), week_concurrency=4) is None
    mocks["conn"].rollback.assert_called()


def test_week1_block_template_reaches_later_weeks_only():
    """Weeks 2..N get week 1's same-day session as block_template; week 1 gets none."""
    for concurrency in (1, 4):
        with ExitStack() as stack:
            mocks = _full_mock_stack(stack, overrides={"plan": _multi_week_plan(3, 2)})
            mocks["generate"].side_effect = _gen_side_effect()
            run(1, _settings(), week_concurrency=concurrency)
        by_slot = {
            (c.kwargs["week_number"], c.kwargs["session_template"].day_number): c.kwargs.get("block_template")
            for c in mocks["build_session_prompt"].call_args_list
        }
        assert by_slot[(1, 1)] is None and by_slot[(1, 2)] is None
        for w in (2, 3):
            for d in (1, 2):
                assert by_slot[(w, d)] == "Snatch 4x3 @ 75%"


def test_summarize_block_template_merges_and_caps():
    from orchestrator import summarize_block_template
    exs = [
        {"exercise_order": 1, "exercise_name": "Snatch", "sets": 2, "reps": 3, "intensity_pct": 55.0},
        {"exercise_order": 2, "exercise_name": "Snatch", "sets": 5, "reps": 2, "intensity_pct": 75.5},
        {"exercise_order": 3, "exercise_name": "Plank", "sets": 3, "reps": 1, "intensity_pct": None},
    ]
    assert summarize_block_template(exs) == "Snatch 2x3 @ 55%, 5x2 @ 75.5%; Plank 3x1"
    assert summarize_block_template([]) is None
    long = [{"exercise_order": i, "exercise_name": f"Exercise number {i}", "sets": 3, "reps": 5,
             "intensity_pct": 70} for i in range(40)]
    text = summarize_block_template(long)
    assert len(text) <= 400 and text.endswith(" …") and "Exercise number 0 3x5 @ 70%" in text


def test_block_template_section_renders_below_the_static_marker():
    from generate import build_session_prompt

    from shared.constants import PROMPT_STATIC_DYNAMIC_MARKER
    retrieval = _retrieval_context()
    retrieval.available_exercises = [{"name": "Snatch", "id": 1, "movement_family": "snatch"}]
    kwargs = dict(
        athlete_context=_athlete_context(), week_target=_week_target(2),
        session_template=_session_template(1), retrieval_context=retrieval,
        week_number=2, duration_weeks=4, already_prescribed=[], session_rep_target=15,
        cumulative_comp_reps=0, context_chunks=[],
    )
    with_anchor = build_session_prompt(**kwargs, block_template="Snatch 4x3 @ 75%")
    without = build_session_prompt(**kwargs)
    marker = with_anchor.index(PROMPT_STATIC_DYNAMIC_MARKER)
    assert with_anchor.index("## Block Template (Week 1, Day 1)") > marker
    assert "Week 1 prescribed this day as: Snatch 4x3 @ 75%" in with_anchor
    assert "Block Template" not in without
    # the static prefix is untouched, so the prompt cache still hits
    assert with_anchor[:marker] == without[:without.index(PROMPT_STATIC_DYNAMIC_MARKER)]
