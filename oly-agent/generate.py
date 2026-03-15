# oly-agent/generate.py
"""
Step 4: GENERATE — Build the program session by session.

One LLM call per session. Each call receives:
- Athlete profile + current maxes
- Week targets (intensity range, volume modifier, rep targets)
- Session template (primary + secondary movements)
- Already-prescribed exercises earlier in the week (for cumulative volume tracking)
- Available exercises + active principles + retrieved knowledge
"""

import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.constants import (
    DEFAULT_SESSION_DURATION_MINUTES,
    MAX_PRINCIPLES_IN_PROMPT,
    SNIPPET_MAX_CHARS,
)
from shared.llm import estimate_cost
from models import (
    AthleteContext, ProgramPlan, RetrievalContext,
    WeekTarget, SessionTemplate, GenerationResult,
)
from validate import validate_session

logger = logging.getLogger(__name__)


# ── JSON parsing ───────────────────────────────────────────────

def parse_llm_response(raw_response: str) -> list[dict]:
    """Parse LLM response into exercise list.

    Handles: markdown fences, preamble text, single-object responses.
    Raises ValueError if all parsing attempts fail.
    """
    text = raw_response.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            result = [result]
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Find JSON array in response
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Find JSON object and wrap
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return [result]
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}...")


def validate_exercise_names(
    exercises: list[dict],
    available_exercises: list[str],
) -> list[str]:
    """Check that all exercise names match the available list.

    Returns list of error strings for mismatched names.
    """
    available_lower = {name.lower(): name for name in available_exercises}
    errors = []
    for ex in exercises:
        name = ex.get("exercise_name", "")
        if name.lower() not in available_lower:
            close = [n for n in available_exercises
                     if name.lower() in n.lower() or n.lower() in name.lower()]
            if close:
                errors.append(
                    f"Unknown exercise '{name}'. Did you mean: {', '.join(close[:3])}?"
                )
            else:
                errors.append(f"Unknown exercise '{name}'. Not in available exercises list.")
    return errors


# ── Prompt builder ─────────────────────────────────────────────

def build_session_prompt(
    athlete_context: AthleteContext,
    week_target: WeekTarget,
    session_template: SessionTemplate,
    retrieval_context: RetrievalContext,
    week_number: int,
    duration_weeks: int,
    already_prescribed: list[dict],
    session_rep_target: int,
    cumulative_comp_reps: int,
    effective_maxes: dict[str, float] | None = None,
) -> str:
    """Assemble the full prompt for one session generation call."""

    # ── Athlete summary ──────────────────────────────────────
    faults_str = ", ".join(athlete_context.technical_faults) or "none identified"
    injuries_str = ", ".join(athlete_context.injuries) or "none"
    avoid = athlete_context.athlete.get("exercise_preferences", {}).get("avoid", [])
    avoid_str = ", ".join(avoid) or "none"
    lift_emphasis = athlete_context.athlete.get("lift_emphasis") or "balanced"
    strength_limiters_str = (
        ", ".join(athlete_context.athlete.get("strength_limiters") or []) or "none identified"
    )
    competition_experience = athlete_context.athlete.get("competition_experience") or "none"
    available_equipment = athlete_context.athlete.get("available_equipment") or []
    has_blocks = "blocks" in available_equipment

    # Use effective_maxes (projected targets in realization) if provided,
    # otherwise fall back to current recorded maxes.
    display_maxes = effective_maxes if effective_maxes is not None else athlete_context.maxes

    # Detect which lifts are using projected targets vs current maxes
    projected_lifts = []
    if effective_maxes is not None:
        for ref in ("snatch", "clean_and_jerk"):
            if effective_maxes.get(ref) != athlete_context.maxes.get(ref):
                projected_lifts.append(ref)

    maxes_header = (
        "Working Maxes (realization phase — snatch/C&J calculated off competition targets)"
        if projected_lifts else "Current Maxes"
    )
    maxes_lines = "\n".join(
        f"  {ref}: {kg}kg{'  ← target' if ref in projected_lifts else ''}"
        for ref, kg in sorted(display_maxes.items())
    )

    # ── Available exercises ───────────────────────────────────
    # Group by movement family, show typical prescription
    ex_lines = []
    for e in retrieval_context.available_exercises:
        line = (
            f"  {e['name']} [{e['movement_family']}] "
            f"(complexity {e['complexity_level']}) — "
            f"typical: {e.get('typical_sets_low', '?')}-{e.get('typical_sets_high', '?')} sets x "
            f"{e.get('typical_reps_low', '?')}-{e.get('typical_reps_high', '?')} reps @ "
            f"{e.get('typical_intensity_low', '?')}-{e.get('typical_intensity_high', '?')}%"
        )
        if e.get("faults_addressed"):
            line += f" | addresses: {', '.join(e['faults_addressed'])}"
        ex_lines.append(line)
    exercises_block = "\n".join(ex_lines)

    # ── Fault emphasis ────────────────────────────────────────
    fault_lines = []
    for family, exs in retrieval_context.fault_exercises.items():
        for e in exs[:3]:
            fault_lines.append(
                f"  {e['name']} — {e.get('primary_purpose', '')} "
                f"(addresses: {', '.join(e.get('faults_addressed') or [])})"
            )
    fault_block = "\n".join(fault_lines) if fault_lines else "  None"

    # ── Substitutions (injury modifications) ──────────────────
    sub_lines = []
    for orig_name, subs in retrieval_context.available_substitutions.items():
        for s in subs[:2]:
            sub_lines.append(
                f"  {orig_name} → {s['substitute_name']}: {s.get('notes', '')}".strip()
            )
    substitutions_block = "\n".join(sub_lines) if sub_lines else "  None"

    # ── Principles ────────────────────────────────────────────
    principle_lines = []
    for p in retrieval_context.active_principles[:MAX_PRINCIPLES_IN_PROMPT]:
        rec = p.get("recommendation", {})
        rec_str = json.dumps(rec) if isinstance(rec, dict) else str(rec)
        principle_lines.append(f"  [{p['id']}] {p['principle_name']}: {rec_str}")
    principles_block = "\n".join(principle_lines) if principle_lines else "  None"

    # ── Programming context (retrieved chunks) ─────────────────
    chunk_lines = []
    for c in retrieval_context.programming_rationale[:4]:
        excerpt = c.get("raw_content", c.get("content", ""))[:SNIPPET_MAX_CHARS]
        chunk_lines.append(f"  [{c.get('chunk_type', '?')}] {excerpt}...")
    context_block = "\n".join(chunk_lines) if chunk_lines else "  (none retrieved)"

    # ── Already prescribed this week ─────────────────────────
    if already_prescribed:
        ap_lines = [
            f"  D{ex.get('day_number', '?')}: {ex.get('exercise_name')} "
            f"{ex.get('sets')}x{ex.get('reps')} @ {ex.get('intensity_pct')}%"
            f" ({ex.get('intensity_reference', '')})"
            for ex in already_prescribed
        ]
        already_block = "\n".join(ap_lines)
    else:
        already_block = "  (first session of the week)"

    remaining_reps = max(0, session_rep_target - cumulative_comp_reps)

    # ── Prilepin summary ──────────────────────────────────────
    prilepin_lines = []
    for zone, data in retrieval_context.prilepin_targets.items():
        prilepin_lines.append(
            f"  Zone {zone}%: optimal {data['optimal_total_reps']} reps/week "
            f"(range {data['total_reps_range_low']}-{data['total_reps_range_high']}), "
            f"{data['reps_per_set_low']}-{data['reps_per_set_high']} reps/set"
        )
    prilepin_block = "\n".join(prilepin_lines) if prilepin_lines else "  (standard Prilepin guidelines apply)"

    # ── Previous program summary ──────────────────────────────
    prev_prog = athlete_context.previous_program
    if prev_prog:
        outcome = prev_prog.get("outcome_summary") or {}
        prev_lines = [
            f"  Phase: {prev_prog.get('phase', 'unknown')} ({prev_prog.get('duration_weeks', '?')} weeks)",
            f"  Adherence: {outcome.get('adherence_pct', 'N/A')}%",
            f"  Avg make rate on competition lifts: {outcome.get('avg_make_rate', 'N/A')}",
        ]
        by_lift = outcome.get("make_rate_by_lift") or {}
        if by_lift:
            lift_parts = [f"{k.replace('_', ' ')} {v:.0%}" for k, v in by_lift.items()]
            prev_lines.append(f"  Make rate by lift: {', '.join(lift_parts)}")
        prev_lines += [
            f"  Avg RPE deviation: {outcome.get('avg_rpe_deviation', 'N/A'):+.2f}"
            if isinstance(outcome.get("avg_rpe_deviation"), (int, float)) else
            f"  Avg RPE deviation: N/A",
            f"  RPE trend: {outcome.get('rpe_trend', 'N/A')}",
            f"  Make rate trend: {outcome.get('make_rate_trend', 'N/A')}",
        ]
        deltas = outcome.get("maxes_delta") or {}
        if deltas:
            delta_parts = [f"{k} {v:+.1f}kg" for k, v in deltas.items()]
            prev_lines.append(f"  Strength progress: {', '.join(delta_parts)}")
        feedback = outcome.get("athlete_feedback")
        if feedback:
            prev_lines.append(f'  Athlete notes: "{feedback[:200]}"')
        prev_program_block = "\n".join(prev_lines)
    else:
        prev_program_block = "  None — this is the athlete's first program."

    prompt = f"""You are an Olympic weightlifting programming assistant. Generate a training session as a JSON array.

You MUST:
- Prescribe exercises as structured JSON (array of objects)
- Stay within the intensity range provided
- Select exercises ONLY from the Available Exercises list (exact name match required)
- Respect all active programming principles
- Provide a brief selection_rationale for each exercise (1-2 sentences)
- Reference principle IDs in source_principle_ids where applicable
- Include 2-3 warmup sets (50-60%) before each competition lift or heavy pull (snatch, clean, jerk, clean & jerk). Warmup sets are 2-3 reps, ordered first in the session. Use the same exercise name as the working sets (e.g. "Snatch" warmups before "Snatch" working sets).

You MUST NOT:
- Exceed the intensity ceiling of {week_target.intensity_ceiling}%
- Prescribe more reps per set than Prilepin's chart allows for the intensity zone
- Include exercises from the avoid list
- Include exercises the athlete cannot perform due to injuries
{"- Prescribe any exercise requiring lifting blocks (e.g. any from-blocks variation) — athlete does not have blocks available." if not has_blocks else ""}
{"- Exceed 3 sets or 3 reps per set on any competition lift — this is a DELOAD week. Prioritize movement quality over load." if week_target.is_deload else ""}

## Athlete Profile
Name: {athlete_context.athlete['name']}
Level: {athlete_context.level}
Sessions/week: {athlete_context.sessions_per_week}
Session duration: {athlete_context.athlete.get('session_duration_minutes', DEFAULT_SESSION_DURATION_MINUTES)} min
Lift emphasis: {lift_emphasis} (snatch_biased = more snatch volume/variants; cj_biased = more C&J volume/variants; balanced = equal)
Strength limiters: {strength_limiters_str}
Competition experience: {competition_experience}
Equipment available: {", ".join(available_equipment) if available_equipment else "standard (barbell, plates, rack)"}
Technical faults: {faults_str}
Injuries: {injuries_str}

## {maxes_header}
{maxes_lines}

## Previous Program
{prev_program_block}

## Program Plan
Phase: {week_target.__class__.__name__} — Week {week_number} of {duration_weeks}
Intensity range: {week_target.intensity_floor}% – {week_target.intensity_ceiling}%
Volume modifier: {week_target.volume_modifier:.2f} (1.0 = baseline)
Reps per set (comp lifts): {week_target.reps_per_set_range[0]}–{week_target.reps_per_set_range[1]}
Deload week: {'YES — reduce all loads' if week_target.is_deload else 'No'}

## Session Template
Day {session_template.day_number}: {session_template.label}
Primary movement: {session_template.primary_movement}
Supporting work: {', '.join(session_template.secondary_movements)}
Session note: {session_template.notes}
Target competition lift reps this session: {session_rep_target}

## Prilepin's Chart Reference
{prilepin_block}

## Already Prescribed This Week
{already_block}
Cumulative competition lift reps so far: {cumulative_comp_reps}
Remaining session rep budget: {remaining_reps}

## Available Exercises
{exercises_block}

## Exercises to Emphasize (fault correction)
{fault_block}

## Exercises to Avoid
{avoid_str}

## Injury Substitutions
{substitutions_block}

## Active Principles
{principles_block}

## Programming Context (from knowledge base)
{context_block}

## Instructions
Generate this session as a JSON array. Each object must include:
- exercise_name (exact match from Available Exercises)
- exercise_order (1-indexed)
- sets (integer >= 1)
- reps (integer >= 1)
- intensity_pct (percentage of the reference max, or null for bodyweight/unloaded)
- intensity_reference (which max to use: "snatch", "clean_and_jerk", "back_squat", "front_squat", etc.)
- rest_seconds (integer)
- rpe_target (float 6.0–10.0)
- selection_rationale (1-2 sentences explaining why this exercise and prescription)
- source_principle_ids (array of principle IDs from Active Principles, or empty array)

Respond ONLY with a valid JSON array. No markdown, no preamble, no explanation outside the JSON."""

    return prompt


# ── Session generation with retries ───────────────────────────

def generate_session_with_retries(
    prompt: str,
    llm_client,
    settings,
    available_exercise_names: list[str],
    week_target: dict,
    athlete: dict,
    active_principles: list[dict],
    week_cumulative_reps: dict,
    program_id: int,
    week_number: int,
    day_number: int,
    conn,
) -> GenerationResult:
    """Generate one session with parse + validation retries.

    Retry flow:
    1. LLM call -> parse JSON -> validate names -> validate (Step 5)
    2. Parse error: retry with "respond only with valid JSON" appended
    3. Validation error: retry with errors included in prompt
    4. Max retries exhausted: return failed GenerationResult
    """
    max_attempts = settings.max_generation_retries + settings.max_parse_retries
    current_prompt = prompt
    last_raw = ""
    last_validation = None

    for attempt in range(1, max_attempts + 1):
        logger.info(f"  Generating W{week_number}D{day_number} (attempt {attempt}/{max_attempts})")

        # ── LLM call ─────────────────────────────────────────
        try:
            response = llm_client.messages.create(
                model=settings.generation_model,
                max_tokens=settings.generation_max_tokens,
                temperature=settings.generation_temperature,
                messages=[{"role": "user", "content": current_prompt}],
            )
            last_raw = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        except Exception as e:
            logger.error(f"  LLM API error (attempt {attempt}): {e}")
            _log_generation(
                conn, program_id, week_number, day_number,
                attempt, settings.generation_model,
                current_prompt, str(e), None,
                0, 0, "failed", error_message=str(e),
            )
            time.sleep(settings.retry_delay_seconds * attempt)
            continue

        # ── Parse ─────────────────────────────────────────────
        try:
            exercises = parse_llm_response(last_raw)
        except ValueError as e:
            logger.warning(f"  Parse error (attempt {attempt}): {e}")
            _log_generation(
                conn, program_id, week_number, day_number,
                attempt, settings.generation_model,
                current_prompt, last_raw, None,
                input_tokens, output_tokens, "parse_error",
                error_message=str(e),
            )
            current_prompt = prompt + (
                "\n\nIMPORTANT: Your previous response was not valid JSON. "
                "Respond with ONLY a JSON array. No markdown, no explanation."
            )
            time.sleep(settings.retry_delay_seconds)
            continue

        # ── Validate exercise names ───────────────────────────
        name_errors = validate_exercise_names(exercises, available_exercise_names)
        if name_errors:
            logger.warning(f"  Exercise name errors (attempt {attempt}): {name_errors}")
            _log_generation(
                conn, program_id, week_number, day_number,
                attempt, settings.generation_model,
                current_prompt, last_raw, exercises,
                input_tokens, output_tokens, "validation_error",
                validation_errors=name_errors,
            )
            current_prompt = prompt + (
                "\n\nIMPORTANT: Your previous response contained invalid exercise names:\n"
                + "\n".join(f"- {e}" for e in name_errors)
                + "\nUse ONLY exercise names from the Available Exercises list exactly as written."
            )
            time.sleep(settings.retry_delay_seconds)
            continue

        # ── Step 5 validation ─────────────────────────────────
        last_validation = validate_session(
            session_exercises=exercises,
            week_target=week_target,
            active_principles=active_principles,
            athlete=athlete,
            week_cumulative_reps=week_cumulative_reps,
        )
        if not last_validation.is_valid:
            logger.warning(f"  Validation errors (attempt {attempt}): {last_validation.errors}")
            _log_generation(
                conn, program_id, week_number, day_number,
                attempt, settings.generation_model,
                current_prompt, last_raw, exercises,
                input_tokens, output_tokens, "validation_error",
                validation_errors=last_validation.errors,
            )
            current_prompt = prompt + (
                "\n\nIMPORTANT: Your previous response failed validation:\n"
                + "\n".join(f"- {e}" for e in last_validation.errors)
                + "\nFix these issues in your next response."
            )
            time.sleep(settings.retry_delay_seconds)
            continue

        # ── Success ───────────────────────────────────────────
        if last_validation.warnings:
            logger.info(f"  Validation warnings (non-blocking): {last_validation.warnings}")

        _log_generation(
            conn, program_id, week_number, day_number,
            attempt, settings.generation_model,
            current_prompt, last_raw, exercises,
            input_tokens, output_tokens, "success",
        )
        return GenerationResult(
            exercises=exercises,
            raw_response=last_raw,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status="success",
            error_message=None,
            attempt_number=attempt,
        )

    # All retries exhausted
    last_errors = last_validation.errors if last_validation else ["parse failure"]
    logger.error(
        f"  Failed W{week_number}D{day_number} after {max_attempts} attempts: {last_errors}"
    )
    return GenerationResult(
        exercises=None,
        raw_response=last_raw,
        input_tokens=0,
        output_tokens=0,
        status="failed",
        error_message=f"Exhausted retries. Last errors: {last_errors}",
        attempt_number=max_attempts,
    )


def _log_generation(
    conn, program_id, week_number, day_number,
    attempt, model, prompt, raw_response, parsed,
    input_tokens, output_tokens, status,
    validation_errors=None, error_message=None,
):
    """Insert a row into generation_log."""
    cost = estimate_cost(input_tokens, output_tokens)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO generation_log
                (program_id, week_number, day_number, attempt_number,
                 model, prompt_text, raw_response, parsed_response,
                 input_tokens, output_tokens, estimated_cost_usd, status,
                 validation_errors, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                program_id, week_number, day_number, attempt,
                model, prompt, raw_response,
                json.dumps(parsed) if parsed else None,
                input_tokens, output_tokens, cost, status,
                validation_errors, error_message,
            ),
        )
        conn.commit()
