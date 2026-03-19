# oly-agent/tests/test_generate_utils.py
"""
Tests for the pure utility functions in generate.py:
  - parse_llm_response()
  - validate_exercise_names()

No DB or API keys needed.

Run: python tests/test_generate_utils.py
"""

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from generate import (
    build_session_prompt, parse_llm_response, validate_exercise_names,
    generate_session_with_retries,
)
from models import AthleteContext, RetrievalContext, WeekTarget, SessionTemplate, GenerationResult
from shared.constants import MAX_RECENT_LOGS_IN_PROMPT


# ── Prompt builder helpers ────────────────────────────────────

def _make_athlete(*, recent_logs=None, previous_program=None, faults=None):
    return AthleteContext(
        athlete={
            "name": "Test Athlete", "level": "intermediate",
            "sessions_per_week": 4, "session_duration_minutes": 90,
            "lift_emphasis": "balanced", "strength_limiters": [],
            "competition_experience": "none",
            "exercise_preferences": {"avoid": []},
            "available_equipment": ["barbell", "rack"],
        },
        level="intermediate",
        maxes={"snatch": 100.0, "clean_and_jerk": 120.0},
        active_goal=None,
        previous_program=previous_program,
        recent_logs=recent_logs or [],
        technical_faults=faults or [],
        injuries=[],
        sessions_per_week=4,
        weeks_to_competition=None,
    )


def _make_retrieval(*, template_references=None, programming_rationale=None,
                    fault_correction_chunks=None, fault_exercises=None):
    return RetrievalContext(
        fault_exercises=fault_exercises or {},
        template_references=template_references or [],
        programming_rationale=programming_rationale or [],
        fault_correction_chunks=fault_correction_chunks or [],
        available_substitutions={},
        active_principles=[],
        prilepin_targets={},
        available_exercises=[
            {"name": "Snatch", "movement_family": "snatch", "complexity_level": 3,
             "typical_sets_low": 3, "typical_sets_high": 6,
             "typical_reps_low": 1, "typical_reps_high": 3,
             "typical_intensity_low": 75, "typical_intensity_high": 90,
             "faults_addressed": []},
        ],
    )


def _chunk(id_, chunk_type, text):
    return {"id": id_, "chunk_type": chunk_type, "raw_content": text, "similarity": 0.8}


def _make_prompt(athlete=None, retrieval=None):
    athlete = athlete or _make_athlete()
    retrieval = retrieval or _make_retrieval()
    week_target = WeekTarget(1, 1.0, 72.0, 82.0, 18, [2, 4], False)
    session_tmpl = SessionTemplate(1, "Snatch + Squat", "snatch", ["squat"], 0.30)
    return build_session_prompt(
        athlete, week_target, session_tmpl, retrieval,
        week_number=1, duration_weeks=4,
        already_prescribed=[], session_rep_target=6, cumulative_comp_reps=0,
    )


VALID_EXERCISE = {
    "exercise_name": "Snatch",
    "exercise_order": 1,
    "sets": 4,
    "reps": 3,
    "intensity_pct": 75,
    "intensity_reference": "snatch",
    "rest_seconds": 180,
    "rpe_target": 7.5,
    "selection_rationale": "Primary competition lift for the session.",
    "source_principle_ids": [],
}

AVAILABLE = ["Snatch", "Clean & Jerk", "Back Squat", "Front Squat", "Snatch Pull"]


# ── parse_llm_response ────────────────────────────────────────

def test_parse_plain_json_array():
    """Plain JSON array is parsed directly."""
    raw = json.dumps([VALID_EXERCISE])
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["exercise_name"] == "Snatch"
    return True, ""


def test_parse_json_with_markdown_fences():
    """JSON wrapped in ```json ... ``` fences is stripped."""
    raw = f"```json\n{json.dumps([VALID_EXERCISE])}\n```"
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    return True, ""


def test_parse_json_with_plain_fences():
    """JSON wrapped in ``` ... ``` (no language tag) is stripped."""
    raw = f"```\n{json.dumps([VALID_EXERCISE])}\n```"
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    return True, ""


def test_parse_json_array_embedded_in_text():
    """JSON array embedded after preamble text is extracted."""
    raw = f"Here is the session:\n{json.dumps([VALID_EXERCISE])}\nEnd."
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    return True, ""


def test_parse_single_object_wrapped_in_list():
    """A single JSON object (not array) is wrapped in a list."""
    raw = json.dumps(VALID_EXERCISE)
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["exercise_name"] == "Snatch"
    return True, ""


def test_parse_multiple_exercises():
    """Multiple exercises in an array are all returned."""
    exercises = [dict(VALID_EXERCISE, exercise_order=i, exercise_name=f"Exercise {i}")
                 for i in range(1, 6)]
    raw = json.dumps(exercises)
    result = parse_llm_response(raw)
    assert len(result) == 5
    return True, ""


def test_parse_invalid_json_raises():
    """Completely invalid JSON raises ValueError."""
    try:
        parse_llm_response("This is not JSON at all.")
        return False, "Expected ValueError, got none"
    except ValueError:
        return True, ""


def test_parse_empty_array():
    """Empty JSON array returns empty list."""
    result = parse_llm_response("[]")
    assert result == []
    return True, ""


def test_parse_preserves_all_fields():
    """All fields in the exercise dict are preserved."""
    raw = json.dumps([VALID_EXERCISE])
    result = parse_llm_response(raw)
    for key in VALID_EXERCISE:
        assert key in result[0], f"Missing field: {key}"
    return True, ""


# ── validate_exercise_names ───────────────────────────────────

def test_validate_all_valid_names():
    """All valid names → no errors."""
    exercises = [{"exercise_name": name} for name in AVAILABLE]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert errors == [], errors
    return True, ""


def test_validate_unknown_name_returns_error():
    """Unknown exercise name returns an error."""
    exercises = [{"exercise_name": "Log Press"}]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert len(errors) == 1
    assert "Log Press" in errors[0]
    return True, ""


def test_validate_close_match_suggests_alternative():
    """Name that partially matches an available exercise includes a suggestion."""
    exercises = [{"exercise_name": "Power Snatch"}]  # 'Snatch' is a substring
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert len(errors) == 1
    assert "Snatch" in errors[0], errors[0]  # suggestion included
    return True, ""


def test_validate_case_insensitive():
    """Exercise name matching is case-insensitive — 'snatch' matches 'Snatch'."""
    exercises = [{"exercise_name": "snatch"}]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert errors == [], f"Expected no errors (case-insensitive match), got: {errors}"
    return True, ""


def test_validate_multiple_invalid():
    """Multiple invalid names all produce errors."""
    exercises = [
        {"exercise_name": "Log Press"},
        {"exercise_name": "Snatch"},  # valid
        {"exercise_name": "Deadlift"},
    ]
    errors = validate_exercise_names(exercises, AVAILABLE)
    assert len(errors) == 2, errors
    return True, ""


def test_validate_empty_list():
    """Empty exercise list → no errors."""
    errors = validate_exercise_names([], AVAILABLE)
    assert errors == []
    return True, ""


# ── build_session_prompt: recent_logs ────────────────────────

def test_recent_logs_section_present():
    """## Recent Training section always appears in the prompt."""
    prompt = _make_prompt()
    assert "## Recent Training" in prompt
    return True, ""


def test_recent_logs_empty_shows_fallback():
    """No logs → fallback message, not a crash or blank section."""
    prompt = _make_prompt(_make_athlete(recent_logs=[]))
    assert "No recent sessions logged" in prompt
    return True, ""


def test_recent_logs_entries_formatted():
    """Log entries are formatted with date, exercise, weight, sets, RPE, make rate."""
    logs = [
        {"log_date": date(2026, 3, 15), "exercise_name": "Snatch",
         "weight_kg": 88.0, "sets_completed": 5, "rpe": 8.0, "make_rate": 0.9},
    ]
    prompt = _make_prompt(_make_athlete(recent_logs=logs))
    assert "2026-03-15" in prompt
    assert "Snatch" in prompt
    assert "88.0kg" in prompt
    assert "RPE 8.0" in prompt
    assert "make 90%" in prompt
    return True, ""


def test_recent_logs_missing_rpe_and_make_rate_ok():
    """Entries with null RPE and make_rate don't crash or show 'RPE None'/'make None'."""
    logs = [
        {"log_date": date(2026, 3, 14), "exercise_name": "Back Squat",
         "weight_kg": 140.0, "sets_completed": 4, "rpe": None, "make_rate": None},
    ]
    prompt = _make_prompt(_make_athlete(recent_logs=logs))
    assert "Back Squat" in prompt
    assert "RPE None" not in prompt
    assert "make None" not in prompt
    return True, ""


def test_recent_logs_capped_at_max():
    """More than MAX_RECENT_LOGS_IN_PROMPT entries are capped."""
    logs = [
        {"log_date": date(2026, 3, 15), "exercise_name": f"Exercise {i}",
         "weight_kg": 100.0, "sets_completed": 3, "rpe": 7.0, "make_rate": 0.8}
        for i in range(MAX_RECENT_LOGS_IN_PROMPT + 5)
    ]
    prompt = _make_prompt(_make_athlete(recent_logs=logs))
    # The exercise beyond the cap should not appear
    assert f"Exercise {MAX_RECENT_LOGS_IN_PROMPT}" not in prompt
    assert f"Exercise {MAX_RECENT_LOGS_IN_PROMPT - 1}" in prompt
    return True, ""


# ── build_session_prompt: template_references ─────────────────

def test_template_references_section_present():
    """## Similar Program Templates section always appears in the prompt."""
    prompt = _make_prompt()
    assert "## Similar Program Templates" in prompt
    return True, ""


def test_template_references_empty_shows_fallback():
    """No templates → fallback message, not blank."""
    prompt = _make_prompt(retrieval=_make_retrieval(template_references=[]))
    assert "none matched" in prompt
    return True, ""


def test_template_references_shows_name_and_notes():
    """Template name and notes appear; program_structure JSON is excluded."""
    templates = [
        {"name": "Soviet Accumulation", "notes": "High volume classical lifts.",
         "program_structure": {"weeks": [{"volume": 100}]}},
    ]
    prompt = _make_prompt(retrieval=_make_retrieval(template_references=templates))
    assert "Soviet Accumulation" in prompt
    assert "High volume classical lifts" in prompt
    # program_structure JSON should not be dumped into the prompt
    assert '"volume"' not in prompt
    return True, ""


def test_template_references_capped_at_two():
    """Only the first 2 templates are included."""
    templates = [
        {"name": f"Template {i}", "notes": f"Notes {i}."} for i in range(4)
    ]
    prompt = _make_prompt(retrieval=_make_retrieval(template_references=templates))
    assert "Template 0" in prompt
    assert "Template 1" in prompt
    assert "Template 2" not in prompt
    return True, ""


# ── build_session_prompt: make_rate_by_lift directive ─────────

def test_weak_lift_directive_appears_below_threshold():
    """Directive line appears when a lift's make rate is below 75%."""
    prog = {"phase": "accumulation", "duration_weeks": 4,
            "outcome_summary": {"adherence_pct": 85, "avg_make_rate": 0.75,
                "make_rate_by_lift": {"snatch": 0.88, "clean_and_jerk": 0.62},
                "avg_rpe_deviation": 0.3, "rpe_trend": "stable",
                "make_rate_trend": "stable", "maxes_delta": {}, "athlete_feedback": None}}
    prompt = _make_prompt(_make_athlete(previous_program=prog))
    assert "clean and jerk make rate was below 75%" in prompt
    assert "reduce intensity" in prompt
    return True, ""


def test_no_directive_when_all_lifts_above_threshold():
    """No directive line when all lifts are >= 75% make rate."""
    prog = {"phase": "accumulation", "duration_weeks": 4,
            "outcome_summary": {"adherence_pct": 90, "avg_make_rate": 0.85,
                "make_rate_by_lift": {"snatch": 0.88, "clean_and_jerk": 0.80},
                "avg_rpe_deviation": 0.2, "rpe_trend": "stable",
                "make_rate_trend": "stable", "maxes_delta": {}, "athlete_feedback": None}}
    prompt = _make_prompt(_make_athlete(previous_program=prog))
    assert "reduce intensity on those lifts" not in prompt
    return True, ""


def test_no_directive_when_no_previous_program():
    """First program (no previous) — no directive and no crash."""
    prompt = _make_prompt(_make_athlete(previous_program=None))
    assert "first program" in prompt
    assert "reduce intensity on those lifts" not in prompt
    return True, ""


def test_multiple_weak_lifts_all_named():
    """All lifts below threshold are named in the directive."""
    prog = {"phase": "accumulation", "duration_weeks": 4,
            "outcome_summary": {"adherence_pct": 80, "avg_make_rate": 0.65,
                "make_rate_by_lift": {"snatch": 0.60, "clean_and_jerk": 0.65},
                "avg_rpe_deviation": 0.5, "rpe_trend": "stable",
                "make_rate_trend": "stable", "maxes_delta": {}, "athlete_feedback": None}}
    prompt = _make_prompt(_make_athlete(previous_program=prog))
    assert "snatch" in prompt
    assert "clean and jerk" in prompt
    assert "reduce intensity on those lifts" in prompt
    return True, ""


# ── build_session_prompt: fault_correction_chunks in context ──

def test_fault_correction_chunks_appear_in_context_block():
    """fault_correction chunks surface in ## Programming Context when athlete has faults."""
    retrieval = _make_retrieval(
        fault_correction_chunks=[_chunk(1, "fault_correction", "Forward lean fix: pause squats.")],
        programming_rationale=[_chunk(2, "periodization", "Accumulation phase volume.")],
    )
    prompt = _make_prompt(_make_athlete(faults=["forward_lean"]), retrieval)
    assert "Forward lean fix" in prompt
    assert "## Programming Context" in prompt
    return True, ""


def test_fault_correction_chunks_prioritized_before_rationale():
    """With faults, fault_correction chunks appear before programming_rationale in context."""
    retrieval = _make_retrieval(
        fault_correction_chunks=[_chunk(1, "fault_correction", "FAULT_TEXT_UNIQUE")],
        programming_rationale=[_chunk(2, "periodization", "RATIONALE_TEXT_UNIQUE")],
    )
    prompt = _make_prompt(_make_athlete(faults=["forward_lean"]), retrieval)
    assert prompt.index("FAULT_TEXT_UNIQUE") < prompt.index("RATIONALE_TEXT_UNIQUE")
    return True, ""


def test_context_block_capped_at_four_chunks():
    """Context block never exceeds 4 chunks even when both lists are large."""
    retrieval = _make_retrieval(
        fault_correction_chunks=[_chunk(i, "fault_correction", f"Fault chunk {i}") for i in range(5)],
        programming_rationale=[_chunk(i+10, "periodization", f"Rationale chunk {i}") for i in range(5)],
    )
    prompt = _make_prompt(_make_athlete(faults=["forward_lean"]), retrieval)
    context_start = prompt.index("## Programming Context")
    context_end = prompt.index("\n\n##", context_start + 1)
    context_section = prompt[context_start:context_end]
    chunk_count = context_section.count("  [")
    assert chunk_count <= 4, f"Expected ≤ 4 chunks in context block, got {chunk_count}"
    return True, ""


def test_no_fault_correction_when_no_faults():
    """Without technical faults, fault_correction chunks are not prioritized."""
    retrieval = _make_retrieval(
        fault_correction_chunks=[_chunk(1, "fault_correction", "FAULT_ONLY_TEXT")],
        programming_rationale=[_chunk(2, "periodization", "RATIONALE_TEXT")],
    )
    prompt = _make_prompt(_make_athlete(faults=None), retrieval)
    assert "FAULT_ONLY_TEXT" not in prompt
    assert "RATIONALE_TEXT" in prompt
    return True, ""


def test_context_deduplicates_by_chunk_id():
    """A chunk present in both lists is only shown once."""
    shared_chunk = _chunk(42, "fault_correction", "SHARED_CONTENT")
    retrieval = _make_retrieval(
        fault_correction_chunks=[shared_chunk],
        programming_rationale=[shared_chunk],
    )
    prompt = _make_prompt(_make_athlete(faults=["forward_lean"]), retrieval)
    assert prompt.count("SHARED_CONTENT") == 1
    return True, ""


# ── build_session_prompt: fault cross-reference (Group C) ────

def _fault_ex(name, faults_addressed, purpose="Corrects the fault"):
    return {
        "name": name, "category": "variation", "complexity_level": 2,
        "primary_purpose": purpose, "faults_addressed": faults_addressed,
        "typical_sets_low": 3, "typical_sets_high": 5,
        "typical_reps_low": 2, "typical_reps_high": 3,
        "typical_intensity_low": 70, "typical_intensity_high": 80,
    }


def test_fault_block_groups_by_fault():
    """Each fault gets its own line listing exercises that address it."""
    retrieval = _make_retrieval(
        fault_exercises={
            "snatch": [_fault_ex("Snatch Balance", ["forward_miss"]),
                       _fault_ex("Pause Snatch", ["forward_miss"])],
        }
    )
    prompt = _make_prompt(_make_athlete(faults=["forward_miss"]), retrieval)
    assert "'forward_miss':" in prompt
    assert "Snatch Balance" in prompt
    return True, ""


def test_fault_block_no_exercises_fallback_message():
    """When no exercises are retrieved for a fault, fallback text is shown."""
    retrieval = _make_retrieval(fault_exercises={})
    prompt = _make_prompt(_make_athlete(faults=["press_out"]), retrieval)
    # fault_exercises is empty so fault_block = "  None"
    assert "Fault Correction Exercises" in prompt
    assert "None" in prompt
    return True, ""


def test_fault_block_multiple_faults_each_listed():
    """Multiple faults each appear as a separate cross-reference line."""
    retrieval = _make_retrieval(
        fault_exercises={
            "snatch": [
                _fault_ex("Snatch Balance", ["forward_miss"]),
                _fault_ex("Muscle Snatch", ["early_arm_bend"]),
            ],
        }
    )
    prompt = _make_prompt(_make_athlete(faults=["forward_miss", "early_arm_bend"]), retrieval)
    assert "'forward_miss':" in prompt
    assert "'early_arm_bend':" in prompt
    return True, ""


def test_fault_block_exercise_not_shown_under_wrong_fault():
    """An exercise that addresses fault A is not listed under fault B."""
    retrieval = _make_retrieval(
        fault_exercises={
            "snatch": [_fault_ex("Snatch Balance", ["forward_miss"])],
        }
    )
    prompt = _make_prompt(_make_athlete(faults=["forward_miss", "early_arm_bend"]), retrieval)
    # Snatch Balance addresses forward_miss only
    forward_idx = prompt.index("'forward_miss':")
    arm_idx = prompt.index("'early_arm_bend':")
    # Find "Snatch Balance" — it must appear after forward_miss label, not after early_arm_bend
    snatch_balance_idx = prompt.index("Snatch Balance")
    assert snatch_balance_idx > forward_idx, "Snatch Balance should appear after forward_miss label"
    # early_arm_bend line should not contain Snatch Balance
    # (the line for early_arm_bend ends at the next newline)
    arm_line_end = prompt.index("\n", arm_idx)
    arm_line = prompt[arm_idx:arm_line_end]
    assert "Snatch Balance" not in arm_line
    return True, ""


def test_fault_block_header_updated():
    """Fault section uses the prescriptive header, not the old 'Exercises to Emphasize' label."""
    prompt = _make_prompt()
    assert "Fault Correction Exercises" in prompt
    assert "Exercises to Emphasize" not in prompt
    return True, ""


# ── build_session_prompt: lift ratios (Group C) ───────────────

def _make_athlete_with_maxes(maxes):
    """Helper: create an AthleteContext with specific maxes."""
    return AthleteContext(
        athlete={
            "name": "Test", "level": "intermediate",
            "sessions_per_week": 4, "session_duration_minutes": 90,
            "lift_emphasis": "balanced", "strength_limiters": [],
            "competition_experience": "none",
            "exercise_preferences": {"avoid": []},
            "available_equipment": [],
        },
        level="intermediate",
        maxes=maxes,
        active_goal=None,
        previous_program=None,
        recent_logs=[],
        technical_faults=[],
        injuries=[],
        sessions_per_week=4,
        weeks_to_competition=None,
    )


def test_lift_ratios_section_present():
    """Lift ratios section appears in prompt."""
    prompt = _make_prompt()
    assert "Lift Ratios" in prompt
    return True, ""


def test_lift_ratios_computed_correctly():
    """Sn/C&J ratio is correctly formatted when both maxes are recorded."""
    # snatch=80, C&J=100 → 80% — below target 77-83%, on target
    athlete = _make_athlete_with_maxes({"snatch": 80.0, "clean_and_jerk": 100.0})
    prompt = _make_prompt(athlete)
    assert "Sn/C&J" in prompt
    assert "80%" in prompt
    return True, ""


def test_lift_ratios_below_target_flags_structural_work():
    """When a ratio is below target, the 'below target' note appears."""
    # snatch=60, C&J=100 → 60% — well below 77-83% target
    athlete = _make_athlete_with_maxes({"snatch": 60.0, "clean_and_jerk": 100.0})
    prompt = _make_prompt(athlete)
    assert "below target" in prompt
    return True, ""


def test_lift_ratios_missing_max_skipped():
    """When a lift max is absent, that ratio line is omitted gracefully."""
    # Only snatch recorded, no C&J or squat
    athlete = _make_athlete_with_maxes({"snatch": 100.0})
    prompt = _make_prompt(athlete)
    # Section should exist but Sn/C&J line should not appear
    assert "Lift Ratios" in prompt
    assert "Sn/C&J" not in prompt
    return True, ""


def test_lift_ratios_fallback_when_no_maxes():
    """When no maxes are recorded at all, fallback text is shown."""
    athlete = _make_athlete_with_maxes({})
    prompt = _make_prompt(athlete)
    assert "insufficient maxes" in prompt
    return True, ""


# ── T4: parse_llm_response fallback branches (lines 69-70, 75-80) ─────────────

def test_parse_object_after_prose_hits_object_branch():
    """Bare object embedded after prose preamble is extracted and wrapped (lines 75-78)."""
    ex = dict(VALID_EXERCISE)
    raw = f"Here is the exercise:\n{json.dumps(ex)}"
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["exercise_name"] == "Snatch"
    return True, ""


def test_parse_invalid_array_falls_through_to_object_branch():
    """Invalid JSON in array regex match causes exception (line 70) and falls to object branch."""
    ex = dict(VALID_EXERCISE)
    # "[not valid]" triggers array branch but fails json.loads; "{...}" succeeds in object branch
    raw = f"[not valid json] {json.dumps(ex)}"
    result = parse_llm_response(raw)
    assert isinstance(result, list) and len(result) == 1
    return True, ""


def test_parse_invalid_object_raises_value_error():
    """Invalid JSON in both array and object branches raises ValueError (line 80 hit)."""
    try:
        parse_llm_response("[invalid array] {invalid object}")
        return False, "Expected ValueError was not raised"
    except ValueError:
        return True, ""


# ── T5: generate_session_with_retries retry paths (lines 495-622) ─────────────

_SIMPLE_WEEK_TARGET = {
    "week_number": 1, "intensity_floor": 70, "intensity_ceiling": 80,
    "volume_modifier": 1.0, "reps_per_set_range": [3, 5],
    "is_deload": False, "total_competition_lift_reps": 18,
}
_SIMPLE_ATHLETE = {
    "name": "Test", "session_duration_minutes": 90,
    "exercise_preferences": {"avoid": []}, "technical_faults": [],
    "strength_limiters": [],
}
_AVAILABLE_NAMES = ["Snatch", "Clean & Jerk", "Back Squat"]
_VALID_SESSION = [{
    "exercise_name": "Snatch", "exercise_order": 1,
    "sets": 4, "reps": 3, "intensity_pct": 75, "intensity_reference": "snatch",
    "rest_seconds": 180, "rpe_target": 7.5,
    "selection_rationale": "Primary lift.", "source_principle_ids": [],
}]


def _make_settings(retries=1, parse_retries=1):
    return SimpleNamespace(
        max_generation_retries=retries,
        max_parse_retries=parse_retries,
        generation_model="claude-sonnet-4-6",
        generation_max_tokens=2048,
        generation_temperature=0.7,
        retry_delay_seconds=0,
    )


def _make_llm_response(text):
    r = MagicMock()
    r.content = [MagicMock(text=text)]
    r.usage.input_tokens = 100
    r.usage.output_tokens = 50
    return r


def _call_generate(llm, settings=None):
    return generate_session_with_retries(
        prompt="test prompt",
        llm_client=llm,
        settings=settings or _make_settings(),
        available_exercise_names=_AVAILABLE_NAMES,
        week_target=_SIMPLE_WEEK_TARGET,
        athlete=_SIMPLE_ATHLETE,
        active_principles=[],
        week_cumulative_reps={},
        program_id=1,
        week_number=1,
        day_number=1,
        conn=MagicMock(),
    )


def test_generate_session_success_first_attempt():
    """Valid JSON on first attempt returns success result."""
    llm = MagicMock()
    llm.messages.create.return_value = _make_llm_response(json.dumps(_VALID_SESSION))
    result = _call_generate(llm)
    assert result.status == "success"
    assert result.attempt_number == 1
    return True, ""


def test_generate_session_parse_error_retries():
    """Parse error on first attempt triggers retry with modified prompt."""
    llm = MagicMock()
    llm.messages.create.side_effect = [
        _make_llm_response("not json at all"),
        _make_llm_response(json.dumps(_VALID_SESSION)),
    ]
    result = _call_generate(llm)
    assert result.status == "success"
    assert result.attempt_number == 2
    second_call_prompt = llm.messages.create.call_args_list[1].kwargs["messages"][0]["content"]
    assert "not valid JSON" in second_call_prompt
    return True, ""


def test_generate_session_name_error_retries():
    """Invalid exercise name triggers retry with error list in prompt."""
    bad_session = [dict(_VALID_SESSION[0], exercise_name="Log Press")]
    llm = MagicMock()
    llm.messages.create.side_effect = [
        _make_llm_response(json.dumps(bad_session)),
        _make_llm_response(json.dumps(_VALID_SESSION)),
    ]
    result = _call_generate(llm)
    assert result.status == "success"
    second_call_prompt = llm.messages.create.call_args_list[1].kwargs["messages"][0]["content"]
    assert "Log Press" in second_call_prompt
    return True, ""


def test_generate_session_all_retries_exhausted():
    """All retries exhausted returns GenerationResult with status=failed."""
    llm = MagicMock()
    llm.messages.create.return_value = _make_llm_response("not json")
    result = _call_generate(llm, _make_settings(retries=1, parse_retries=1))
    assert result.status == "failed"
    assert result.exercises is None
    return True, ""


# ── Runner ────────────────────────────────────────────────────

TESTS = [
    # parse_llm_response
    ("parse: plain JSON array", test_parse_plain_json_array),
    ("parse: JSON with ```json fences", test_parse_json_with_markdown_fences),
    ("parse: JSON with plain ``` fences", test_parse_json_with_plain_fences),
    ("parse: JSON array embedded in text", test_parse_json_array_embedded_in_text),
    ("parse: single object → wrapped in list", test_parse_single_object_wrapped_in_list),
    ("parse: multiple exercises returned", test_parse_multiple_exercises),
    ("parse: invalid JSON → ValueError", test_parse_invalid_json_raises),
    ("parse: empty array → empty list", test_parse_empty_array),
    ("parse: all fields preserved", test_parse_preserves_all_fields),
    # validate_exercise_names
    ("validate names: all valid → no errors", test_validate_all_valid_names),
    ("validate names: unknown → error", test_validate_unknown_name_returns_error),
    ("validate names: close match → suggestion", test_validate_close_match_suggests_alternative),
    ("validate names: case insensitive match", test_validate_case_insensitive),
    ("validate names: multiple invalid", test_validate_multiple_invalid),
    ("validate names: empty list → no errors", test_validate_empty_list),
    # build_session_prompt: recent_logs
    ("prompt recent_logs: section present", test_recent_logs_section_present),
    ("prompt recent_logs: empty → fallback message", test_recent_logs_empty_shows_fallback),
    ("prompt recent_logs: entries formatted correctly", test_recent_logs_entries_formatted),
    ("prompt recent_logs: null RPE/make_rate no crash", test_recent_logs_missing_rpe_and_make_rate_ok),
    ("prompt recent_logs: capped at MAX_RECENT_LOGS_IN_PROMPT", test_recent_logs_capped_at_max),
    # build_session_prompt: template_references
    ("prompt templates: section present", test_template_references_section_present),
    ("prompt templates: empty → fallback message", test_template_references_empty_shows_fallback),
    ("prompt templates: name + notes shown, structure excluded", test_template_references_shows_name_and_notes),
    ("prompt templates: capped at 2", test_template_references_capped_at_two),
    # build_session_prompt: make_rate_by_lift directive
    ("prompt make_rate: directive when lift < 75%", test_weak_lift_directive_appears_below_threshold),
    ("prompt make_rate: no directive when all lifts >= 75%", test_no_directive_when_all_lifts_above_threshold),
    ("prompt make_rate: no directive on first program", test_no_directive_when_no_previous_program),
    ("prompt make_rate: multiple weak lifts all named", test_multiple_weak_lifts_all_named),
    # build_session_prompt: fault_correction_chunks in context block
    ("prompt context: fault chunks appear when faults present", test_fault_correction_chunks_appear_in_context_block),
    ("prompt context: fault chunks prioritized before rationale", test_fault_correction_chunks_prioritized_before_rationale),
    ("prompt context: capped at 4 chunks total", test_context_block_capped_at_four_chunks),
    ("prompt context: no fault chunks without faults", test_no_fault_correction_when_no_faults),
    ("prompt context: deduplicates by chunk id", test_context_deduplicates_by_chunk_id),
    # build_session_prompt: fault cross-reference (Group C)
    ("prompt fault xref: groups by fault", test_fault_block_groups_by_fault),
    ("prompt fault xref: no exercises → fallback", test_fault_block_no_exercises_fallback_message),
    ("prompt fault xref: multiple faults each listed", test_fault_block_multiple_faults_each_listed),
    ("prompt fault xref: exercise under correct fault only", test_fault_block_exercise_not_shown_under_wrong_fault),
    ("prompt fault xref: prescriptive header used", test_fault_block_header_updated),
    # build_session_prompt: lift ratios (Group C)
    ("prompt ratios: section present", test_lift_ratios_section_present),
    ("prompt ratios: Sn/C&J computed and formatted", test_lift_ratios_computed_correctly),
    ("prompt ratios: below target flags structural work", test_lift_ratios_below_target_flags_structural_work),
    ("prompt ratios: missing max → ratio omitted", test_lift_ratios_missing_max_skipped),
    ("prompt ratios: no maxes → fallback text", test_lift_ratios_fallback_when_no_maxes),
    # T4: parse_llm_response fallback branches
    ("parse: object after prose → object branch (lines 75-78)", test_parse_object_after_prose_hits_object_branch),
    ("parse: invalid array falls through to object branch (line 70)", test_parse_invalid_array_falls_through_to_object_branch),
    ("parse: both branches invalid → ValueError (line 80)", test_parse_invalid_object_raises_value_error),
    # T5: generate_session_with_retries retry paths
    ("generate: success on first attempt", test_generate_session_success_first_attempt),
    ("generate: parse error triggers retry with JSON reminder", test_generate_session_parse_error_retries),
    ("generate: name error triggers retry with error list", test_generate_session_name_error_retries),
    ("generate: all retries exhausted → status=failed", test_generate_session_all_retries_exhausted),
]


def main():
    failures = []
    results = []
    for name, fn in TESTS:
        try:
            ok, msg = fn()
            results.append((name, ok, msg))
            if not ok:
                failures.append(name)
        except Exception as e:
            results.append((name, False, str(e)))
            failures.append(name)

    print(f"\n{'='*50}")
    print("GENERATE UTILS — Test Results")
    print(f"{'='*50}")
    for name, ok, msg in results:
        status = "✓" if ok else "✗"
        print(f"  {status} {name}" + (f"\n      {msg}" if not ok else ""))

    total = len(TESTS)
    passed = total - len(failures)
    print(f"\n{passed}/{total} passed")
    if failures:
        print(f"\nFailed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
