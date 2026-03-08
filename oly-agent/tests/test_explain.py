# oly-agent/tests/test_explain.py
"""
Tests for the EXPLAIN step (explain.py).

_build_explain_prompt is pure — tested directly.
explain() tested with a mocked LLM client to verify error handling.

Run: python tests/test_explain.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from explain import explain, _build_explain_prompt
from models import AthleteContext, ProgramPlan, WeekTarget, SessionTemplate

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

class _FakeSettings:
    explanation_model = "claude-sonnet-4-6"
    explanation_temperature = 0.7

def _ctx(level="intermediate", faults=None, weeks_to_comp=None, goal="general_strength",
         sessions_per_week=4):
    return AthleteContext(
        athlete={"name": "Test"},
        level=level,
        maxes={"snatch": 100.0, "clean_and_jerk": 120.0},
        active_goal={"goal": goal},
        previous_program=None,
        recent_logs=[],
        technical_faults=faults or [],
        injuries=[],
        sessions_per_week=sessions_per_week,
        weeks_to_competition=weeks_to_comp,
    )

def _plan(phase="accumulation", duration_weeks=4, deload_week=4):
    weekly_targets = [
        WeekTarget(week_number=w, volume_modifier=1.0 if w < 4 else 0.6,
                   intensity_floor=70, intensity_ceiling=80,
                   total_competition_lift_reps=20, reps_per_set_range=[3, 5],
                   is_deload=(w == deload_week))
        for w in range(1, duration_weeks + 1)
    ]
    session_templates = [
        SessionTemplate(day_number=1, label="Snatch + Squat", primary_movement="snatch",
                        secondary_movements=["squat"], session_volume_share=0.30)
    ]
    return ProgramPlan(
        phase=phase, duration_weeks=duration_weeks, sessions_per_week=4,
        deload_week=deload_week, weekly_targets=weekly_targets,
        session_templates=session_templates, active_principles=[], supporting_chunks=[],
    )

def _sessions():
    return [{
        "week_number": 1, "day_number": 1,
        "exercises": [
            {"exercise_name": "Snatch", "sets": 5, "reps": 3,
             "intensity_pct": 75, "intensity_reference": "snatch"},
            {"exercise_name": "Back Squat", "sets": 4, "reps": 4,
             "intensity_pct": 80, "intensity_reference": "back_squat"},
        ],
    }]


# ── _build_explain_prompt — pure ─────────────────────────────────────────────

def test_prompt_contains_athlete_level():
    prompt = _build_explain_prompt(_ctx(level="advanced"), _plan(), _sessions())
    assert "advanced" in prompt

def test_prompt_contains_phase():
    prompt = _build_explain_prompt(_ctx(), _plan(phase="intensification"), _sessions())
    assert "intensification" in prompt

def test_prompt_contains_duration():
    prompt = _build_explain_prompt(_ctx(), _plan(duration_weeks=4), _sessions())
    assert "4" in prompt

def test_prompt_contains_sample_exercises():
    prompt = _build_explain_prompt(_ctx(), _plan(), _sessions())
    assert "Snatch" in prompt
    assert "Back Squat" in prompt

def test_prompt_shows_competition_weeks_when_set():
    prompt = _build_explain_prompt(_ctx(weeks_to_comp=8), _plan(), _sessions())
    assert "8 weeks out" in prompt

def test_prompt_shows_no_competition_date_when_none():
    prompt = _build_explain_prompt(_ctx(weeks_to_comp=None), _plan(), _sessions())
    assert "no competition date" in prompt

def test_prompt_shows_faults():
    prompt = _build_explain_prompt(_ctx(faults=["forward_miss"]), _plan(), _sessions())
    assert "forward_miss" in prompt

def test_prompt_no_faults_says_none():
    prompt = _build_explain_prompt(_ctx(faults=[]), _plan(), _sessions())
    assert "none identified" in prompt

def test_prompt_marks_deload_week():
    prompt = _build_explain_prompt(_ctx(), _plan(deload_week=4), _sessions())
    assert "DELOAD" in prompt

def test_prompt_handles_empty_sessions():
    # Should not raise even with no sessions
    prompt = _build_explain_prompt(_ctx(), _plan(), [])
    assert len(prompt) > 0


# ── explain() — mocked LLM client ───────────────────────────────────────────

def test_explain_returns_rationale_text():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="This is the rationale.")]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
    mock_client.messages.create.return_value = mock_response

    result = explain(_ctx(), _plan(), _sessions(), mock_client, _FakeSettings())
    assert result == "This is the rationale."

def test_explain_calls_llm_with_correct_model():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Rationale.")]
    mock_response.usage = MagicMock(input_tokens=50, output_tokens=30)
    mock_client.messages.create.return_value = mock_response

    explain(_ctx(), _plan(), _sessions(), mock_client, _FakeSettings())
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-sonnet-4-6"

def test_explain_returns_error_string_on_llm_failure():
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API down")

    result = explain(_ctx(), _plan(), _sessions(), mock_client, _FakeSettings())
    assert "failed" in result.lower() or "error" in result.lower()


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
