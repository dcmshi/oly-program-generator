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

from complexes import annotate_complexes
from dosing import dose_session
from models import (
    GenerationResult,
)
from session_prompt import (  # noqa: F401 — re-exported: generate was their home
    _BARE_WEEK_LABEL_RE,
    _INSTRUCTIONS_SECTION,
    _RATIO_TARGETS,
    _WARMUP_RULE_OWN,
    _WARMUP_RULE_PRESCRIBED,
    _already_prescribed_section,
    _athlete_profile_section,
    _available_complexes_section,
    _available_exercises_section,
    _avoid_section,
    _block_template_section,
    _deload_rule,
    _exercise_line,
    _fault_correction_section,
    _maxes_section,
    _pick_template_week,
    _previous_program_section,
    _prilepin_section,
    _principles_section,
    _program_plan_body,
    _programming_context_section,
    _projected_lifts,
    _range,
    _ratios_section,
    _recent_training_section,
    _reps_per_set_line,
    _rules_section,
    _select_context_chunks,
    _session_template_section,
    _strength_curve_lines,
    _substitutions_section,
    _templates_section,
    _truncate_at_word,
    _volume_table_section,
    _warn_if_prompt_large,
    build_session_prompt,
    format_principle_line,
    previous_program_structure_lines,
    prompt_content_blocks,
    render_template_reference,
    split_prompt_for_caching,
    summarize_recent_logs,
)
from validate import storable_errors, validate_session

from shared.constants import (
    LLM_MAX_TOKENS_CEILING,
)
from shared.llm import (
    billed_cost,
    create_message_with_retries,
    estimate_cost,
    json_schema_kwargs,
    message_text,
    sampling_kwargs,
    served_tier,
    thinking_kwargs,
    usage_tokens,
)

logger = logging.getLogger(__name__)


# ── JSON parsing ───────────────────────────────────────────────

def _is_exercise_list(result) -> bool:
    """A parsed result is usable only if it's a list of dicts (empty is fine).

    A list of strings (`["Snatch 5x2 @75%", ...]`) or other non-dict items is
    rejected here so it flows into the parse-retry path instead of reaching the
    caller, where `ex.get(...)` would raise AttributeError and abort the run.
    """
    return isinstance(result, list) and all(isinstance(x, dict) for x in result)


_INT_FIELDS = ("sets", "reps", "rest_seconds", "exercise_order")
_FLOAT_FIELDS = ("intensity_pct", "rpe_target")

# The session reply's schema (STRUCT-1), sent as `output_config.format` so the
# API guarantees a parseable, typed reply: the salvage paths in
# parse_llm_response and the string→number coercion become fallbacks for
# replies produced without it. Field list mirrors the prompt's Instructions
# block. `exercise_name` stays a free string — the catalogue is per program and
# a per-program enum would recompile the grammar and invalidate the prompt
# cache each time; validate_exercise_names + the validation retry cover it.
SESSION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "exercises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "exercise_name": {"type": "string"},
                    "exercise_order": {"type": "integer"},
                    "sets": {"type": "integer"},
                    "reps": {"type": "integer"},
                    "intensity_pct": {"type": ["number", "null"]},
                    "intensity_reference": {"type": "string"},
                    "rest_seconds": {"type": "integer"},
                    "rpe_target": {"type": "number"},
                    "selection_rationale": {"type": "string"},
                    "source_principle_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["exercise_name", "exercise_order", "sets", "reps", "intensity_pct",
                             "intensity_reference", "rest_seconds", "rpe_target",
                             "selection_rationale", "source_principle_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["exercises"],
    "additionalProperties": False,
}


def _coerce_numeric_fields(exercises: list[dict]) -> list[dict]:
    """Coerce numeric fields the LLM sometimes emits as strings ("75").

    Strings passed validation (which compares via float()) and then crashed
    arithmetic in the orchestrator/resolver, aborting the whole run (AGT-L1).
    Unparseable values become None so validation flags them into a retry.
    Booleans become None too (psycopg2 sends SQL true → INSERT dies), and a
    fractional value in an int field ("2.9" reps) is rejected rather than
    silently truncated past the Prilepin reps/set check (audit2-L1).
    """
    for ex in exercises:
        for field in _INT_FIELDS:
            if field in ex and ex[field] is not None:
                if isinstance(ex[field], bool):
                    ex[field] = None
                    continue
                try:
                    as_float = float(ex[field])
                    ex[field] = int(as_float) if as_float.is_integer() else None
                except (TypeError, ValueError):
                    ex[field] = None
        for field in _FLOAT_FIELDS:
            if field in ex and ex[field] is not None:
                if isinstance(ex[field], bool):
                    ex[field] = None
                    continue
                try:
                    ex[field] = float(ex[field])
                except (TypeError, ValueError):
                    ex[field] = None
        # source_principle_ids is an INT[]; a non-int element ("P-3") or a bare
        # scalar passed parse+validate and then IntegrityError'd the save after
        # all LLM spend (audit5-M4). Keep int-castable elements, drop the rest.
        if "source_principle_ids" in ex:
            ex["source_principle_ids"] = _coerce_int_list(ex["source_principle_ids"])
    return exercises


def _coerce_int_list(value) -> list[int]:
    """Best-effort list-of-ints from LLM output (scalar → [scalar], junk dropped)."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[int] = []
    for item in items:
        if isinstance(item, bool) or item is None:
            continue
        try:
            f = float(item)
        except (TypeError, ValueError):
            continue
        if f.is_integer():
            out.append(int(f))
    return out


def parse_llm_response(raw_response: str) -> list[dict]:
    """Parse LLM response into exercise list.

    Handles: markdown fences, preamble text, single-object responses, and
    numeric fields emitted as strings (coerced once here — AGT-L1).
    Raises ValueError if all parsing attempts fail or the parsed JSON is not a
    list of objects (so malformed-but-parseable output triggers a retry).
    """
    text = raw_response.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Direct parse — the schema reply is {"exercises": [...]}; a bare array or a
    # single object are the pre-schema shapes.
    try:
        result = json.loads(text)
        if isinstance(result, dict) and isinstance(result.get("exercises"), list):
            result = result["exercises"]
        elif isinstance(result, dict):
            result = [result]
        if _is_exercise_list(result):
            return _coerce_numeric_fields(result)
    except json.JSONDecodeError:
        pass

    # Find JSON array in response — skip empty arrays so [] inside a dict field
    # doesn't shadow the real object that follows
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            result = json.loads(match.group())
            if _is_exercise_list(result) and len(result) > 0:
                return _coerce_numeric_fields(result)
        except json.JSONDecodeError:
            pass

    # Find JSON object and wrap
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return _coerce_numeric_fields([result])
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
        # exercise_name may be present-but-null in LLM output; coerce to str so
        # .lower() never raises (a blank name still fails the membership check).
        name = str(ex.get("exercise_name") or "")
        if name.lower() not in available_lower:
            # A blank name substring-matches every catalogue entry — suggest
            # nothing rather than the first 3 alphabetical exercises (AGT-L8).
            close = [n for n in available_exercises
                     if name.lower() in n.lower() or n.lower() in name.lower()] if name else []
            if close:
                errors.append(
                    f"Unknown exercise '{name}'. Did you mean: {', '.join(close[:3])}?"
                )
            else:
                errors.append(f"Unknown exercise '{name}'. Not in available exercises list.")
    return errors


# ── Session generation with retries ───────────────────────────

def _repair_suffix(exercises: list[dict], problem: str, errors: list[str], instruction: str) -> str:
    """The retry tail: the previous attempt's exercises and its errors, so the
    model repairs that session instead of writing a new one blind (Self-Refine
    with external feedback). Below the marker, so the cached prefix is kept."""
    previous = [{k: ex.get(k) for k in ("exercise_order", "exercise_name", "sets", "reps", "intensity_pct",
                                        "intensity_reference")} for ex in exercises]
    return (f"\n\nIMPORTANT: Your previous response {problem}:\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\nYour previous exercises were:\n" + json.dumps(previous, ensure_ascii=False, default=str)
            + f"\n{instruction}")


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
    fault_exercise_names: list[str] | None = None,
    retrieval_set: list[dict] | None = None,
    week_already_prescribed: list[dict] | None = None,
    available_complexes: list[dict] | None = None,
    session_rep_target: int | None = None,
) -> GenerationResult:
    """Generate one session with parse + validation retries.

    Retry flow:
    1. LLM call -> parse JSON -> validate names -> validate (Step 5)
    2. Parse error: retry with "respond only with valid JSON" appended
    3. Dosing: code fits sets × reps × % to the week (dosing.dose_session) —
       the model chooses exercises, so number errors don't cost a retry
    4. Validation error: retry with the previous exercises and the errors, asked
       to change only what the errors require (a repair, not a new session)
    5. Max retries exhausted: the storable candidate with the fewest errors is
       returned flagged (status "flagged"), else a failed GenerationResult

    retrieval_set: the labelled knowledge chunks the prompt showed
    ([{"label": "C1", "id": …, "similarity": …}, …]); written to
    generation_log.retrieval_set on every attempt so retrieval can be audited
    per call (RAG-M5).

    Each attempt's generation_log row is priced with that attempt's cache
    tokens, and carries validate_session's warnings in parsed_response
    (``_log_generation``).
    """
    # the current attempt's cache tokens and billed cost, for its log row
    attempt_cache = {"read": 0, "creation": 0, "billed": None, "tier": None}

    def _log(*args, **kwargs):
        kwargs.setdefault("dosing_edits", attempt_cache.get("dosing"))
        _log_generation(*args, retrieval_set=retrieval_set, cache_read_tokens=attempt_cache["read"],
                        cache_creation_tokens=attempt_cache["creation"], billed_usd=attempt_cache["billed"],
                        service_tier=attempt_cache["tier"], **kwargs)

    def _attempt(status, raw, exercises=None, tokens=(0, 0), **kwargs):
        """This attempt's generation_log row: the session, attempt number, model
        and current prompt are filled in here (CLEAN-4 — they were repeated in
        six call sites). Reads `attempt` / `current_prompt` at call time."""
        _log(conn, program_id, week_number, day_number, attempt, settings.generation_model,
             current_prompt, raw, exercises, tokens[0], tokens[1], status, **kwargs)

    max_attempts = settings.max_generation_retries + settings.max_parse_retries
    current_prompt = prompt
    last_raw = ""
    last_validation = None
    best: tuple[int, list[dict], list[str], int] | None = None   # (n errors, exercises, errors, attempt)
    # Accumulate tokens across ALL attempts — each retry is a full paid LLM call,
    # and the returned totals feed the orchestrator's per-program cost guard.
    # Returning only the final attempt's tokens let the guard undercount by up to
    # max_attempts× (A-M2).
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read = 0
    total_cache_creation = 0
    total_billed: float | None = 0.0   # None once a paid attempt carries no bill
    max_tokens = settings.generation_max_tokens   # grows on truncation, see below
    # Request shape per model family: Sonnet 5 / Opus 5 reject `temperature`
    # and run adaptive thinking unless told otherwise; 4.x accept the
    # temperature and ignore thinking when omitted. Resolved once per session.
    # json_schema_kwargs merges its `output_config.format` with the effort's
    # `output_config.effort` — both live under the same key.
    request_kwargs = json_schema_kwargs(SESSION_SCHEMA, {
        **sampling_kwargs(settings.generation_model, settings.generation_temperature),
        **thinking_kwargs(
            settings.generation_model,
            _setting_str(settings, "generation_thinking"),
            _setting_str(settings, "generation_effort"),
        ),
    })

    for attempt in range(1, max_attempts + 1):
        logger.info(f"  Generating W{week_number}D{day_number} (attempt {attempt}/{max_attempts})")
        attempt_cache.update(read=0, creation=0, billed=None, dosing=None)

        # ── LLM call ─────────────────────────────────────────
        try:
            response = create_message_with_retries(
                llm_client,
                base_delay=settings.retry_delay_seconds,
                model=settings.generation_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt_content_blocks(current_prompt)}],
                **request_kwargs,
            )
            last_raw = message_text(response)  # text blocks only — v5 may lead with thinking
            usage = usage_tokens(response.usage)
            input_tokens = usage["input"]
            output_tokens = usage["output"]
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_cache_read += usage["cache_read"]
            total_cache_creation += usage["cache_creation"]
            billed = billed_cost(response.usage)
            attempt_cache.update(read=usage["cache_read"], creation=usage["cache_creation"], billed=billed,
                                 tier=served_tier(response.usage))
            total_billed = None if billed is None or total_billed is None else total_billed + billed
        except Exception as e:
            logger.error(f"  LLM API error (attempt {attempt}): {e}")
            _attempt("failed", str(e), error_message=str(e))
            time.sleep(settings.retry_delay_seconds * attempt)
            continue

        # ── Truncation ───────────────────────────────────────
        # stop_reason == max_tokens means the output budget ran out — on
        # Sonnet 5 / Opus 5 with adaptive thinking, usually before a single
        # text block was written. Re-sending the same request cannot
        # succeed; grow the budget for the next attempt instead (MODEL-1).
        if getattr(response, "stop_reason", None) == "max_tokens":
            grown = min(max_tokens * 2, LLM_MAX_TOKENS_CEILING)
            error_message = (
                f"Response truncated at max_tokens={max_tokens} (stop_reason=max_tokens, "
                f"{output_tokens} output tokens, {len(last_raw)} chars of text)"
                + (f" — retrying with max_tokens={grown}" if grown > max_tokens else
                   f" — already at the {LLM_MAX_TOKENS_CEILING} ceiling; set GENERATION_THINKING=disabled "
                   f"or a lower GENERATION_EFFORT for {settings.generation_model}")
            )
            logger.warning(f"  {error_message} (attempt {attempt})")
            _attempt("parse_error", last_raw, tokens=(input_tokens, output_tokens), error_message=error_message)
            max_tokens = grown
            time.sleep(settings.retry_delay_seconds)
            continue

        # ── Parse ─────────────────────────────────────────────
        try:
            exercises = parse_llm_response(last_raw)
        except ValueError as e:
            logger.warning(f"  Parse error (attempt {attempt}): {e}")
            _attempt("parse_error", last_raw, tokens=(input_tokens, output_tokens), error_message=str(e))
            current_prompt = prompt + (
                "\n\nIMPORTANT: Your previous response was not valid JSON. "
                "Respond with ONLY the JSON object. No markdown, no explanation."
            )
            time.sleep(settings.retry_delay_seconds)
            continue

        # ── Validate exercise names ───────────────────────────
        name_errors = validate_exercise_names(
            exercises, list(available_exercise_names) + [c["name"] for c in (available_complexes or [])])
        if name_errors:
            logger.warning(f"  Exercise name errors (attempt {attempt}): {name_errors}")
            _attempt("validation_error", last_raw, exercises, (input_tokens, output_tokens),
                     validation_errors=name_errors)
            current_prompt = prompt + _repair_suffix(
                exercises, "contained invalid exercise names", name_errors,
                "Replace only those exercises, with names from the Available Exercises list exactly as written.")
            time.sleep(settings.retry_delay_seconds)
            continue

        # Complexes carry their definition from here on (PLAN-3a): reps = the
        # complex total, complex_id, scheme note — the validator judges them
        # by their competition-lift components.
        exercises = annotate_complexes(exercises, available_complexes)
        exercises, dosing_edits = dose_session(exercises, week_target, session_rep_target)
        attempt_cache["dosing"] = dosing_edits
        if dosing_edits:
            logger.info(f"  Dosing W{week_number}D{day_number}: {dosing_edits}")

        # ── Step 5 validation ─────────────────────────────────
        last_validation = validate_session(
            session_exercises=exercises,
            week_target=week_target,
            active_principles=active_principles,
            athlete=athlete,
            week_cumulative_reps=week_cumulative_reps,
            fault_exercise_names=fault_exercise_names,
            week_already_prescribed=week_already_prescribed,
        )
        if not last_validation.is_valid:
            logger.warning(f"  Validation errors (attempt {attempt}): {last_validation.errors}")
            _attempt("validation_error", last_raw, exercises, (input_tokens, output_tokens),
                     validation_errors=last_validation.errors, validation_warnings=last_validation.warnings)
            if not storable_errors(exercises, athlete):
                n = len(last_validation.errors)
                if best is None or n < best[0]:
                    best = (n, exercises, list(last_validation.errors), attempt)
            current_prompt = prompt + _repair_suffix(
                exercises, "failed validation", last_validation.errors,
                "Keep every exercise that is not named in an error; change only what the errors require.")
            time.sleep(settings.retry_delay_seconds)
            continue

        # ── Success ───────────────────────────────────────────
        if last_validation.warnings:
            logger.info(f"  Validation warnings (non-blocking): {last_validation.warnings}")

        _attempt("success", last_raw, exercises, (input_tokens, output_tokens),
                 validation_warnings=last_validation.warnings)
        return GenerationResult(
            exercises=exercises,
            raw_response=last_raw,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            status="success",
            error_message=None,
            attempt_number=attempt,
            cache_read_tokens=total_cache_read,
            cache_creation_tokens=total_cache_creation,
            billed_cost_usd=total_billed,
        )

    # All retries exhausted: keep the best storable candidate, flagged, rather
    # than an empty session the athlete can't train from
    if best is not None:
        n, exercises, errors, attempt = best
        logger.error(f"  W{week_number}D{day_number}: retries exhausted — saving attempt {attempt} "
                     f"with {n} unresolved validation error(s): {errors}")
        return GenerationResult(
            exercises=exercises,
            raw_response=last_raw,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            status="flagged",
            error_message=f"Saved with unresolved validation errors: {errors}",
            attempt_number=max_attempts,
            cache_read_tokens=total_cache_read,
            cache_creation_tokens=total_cache_creation,
            billed_cost_usd=total_billed,
        )
    last_errors = last_validation.errors if last_validation else ["parse failure"]
    logger.error(
        f"  Failed W{week_number}D{day_number} after {max_attempts} attempts: {last_errors}"
    )
    return GenerationResult(
        exercises=None,
        raw_response=last_raw,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        status="failed",
        error_message=f"Exhausted retries. Last errors: {last_errors}",
        attempt_number=max_attempts,
        cache_read_tokens=total_cache_read,
        cache_creation_tokens=total_cache_creation,
        billed_cost_usd=total_billed,
    )


def _setting_str(settings, name: str) -> str:
    """A str-valued optional setting, tolerant of partial/mocked settings objects."""
    value = getattr(settings, name, "")
    return value if isinstance(value, str) else ""


def _log_generation(
    conn, program_id, week_number, day_number,
    attempt, model, prompt, raw_response, parsed,
    input_tokens, output_tokens, status,
    validation_errors=None, error_message=None, retrieval_set=None,
    cache_read_tokens=0, cache_creation_tokens=0, validation_warnings=None, billed_usd=None,
    service_tier=None, dosing_edits=None,
):
    """Insert a row into generation_log (incl. the labelled retrieval set — RAG-M5).

    estimated_cost_usd includes the attempt's prompt-cache reads / writes
    (billed at 0.1x / 1.25x input) — input_tokens is only the uncached part.
    parsed_response is ``{"exercises": [...], "_validation_warnings": [...], "_dosing_edits": [...]}``:
    the table has no warnings column, and folding warnings into
    validation_errors would inflate every error count (eval/model_baseline);
    ``_validation_warnings`` is null when Step 5 didn't run on the attempt.
    """
    cost = estimate_cost(input_tokens, output_tokens, model, cache_read_tokens=cache_read_tokens,
                         cache_creation_tokens=cache_creation_tokens, billed_usd=billed_usd,
                         service_tier=service_tier)
    parsed_response = (
        json.dumps({"exercises": parsed, "_validation_warnings": validation_warnings,
                    "_dosing_edits": dosing_edits}, default=str)
        if parsed else None
    )
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO generation_log
                (program_id, week_number, day_number, attempt_number,
                 model, prompt_text, raw_response, parsed_response,
                 input_tokens, output_tokens, estimated_cost_usd, status,
                 validation_errors, error_message, retrieval_set)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                program_id, week_number, day_number, attempt,
                model, prompt, raw_response,
                parsed_response,
                input_tokens, output_tokens, cost, status,
                validation_errors, error_message,
                json.dumps(retrieval_set, default=str) if retrieval_set is not None else None,
            ),
        )
        conn.commit()
