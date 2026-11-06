# oly-agent/explain.py
"""
Step 6: EXPLAIN — Generate program-level rationale.

One LLM call after all sessions are generated. Produces a 3-5 paragraph
explanation stored in generated_programs.rationale.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from coach import coach_prompt_section
from complexes import prescription_label
from generate import _setting_str
from models import AthleteContext, ProgramPlan

from shared.config import Settings
from shared.constants import (
    EXPLAIN_EXERCISES_PER_SESSION_WHEN_CAPPED,
    EXPLAIN_PROGRAM_VIEW_MAX_CHARS,
    LLM_MAX_TOKENS_CEILING,
)
from shared.exercise_mapping import is_warmup_set
from shared.llm import (
    FlexUnavailable,
    billed_cost,
    create_message_with_retries,
    estimate_cost,
    message_text,
    sampling_kwargs,
    served_tier,
    thinking_kwargs,
)

logger = logging.getLogger(__name__)


def explain(
    athlete_context: AthleteContext,
    plan: ProgramPlan,
    program_sessions: list[dict],
    llm_client,
    settings,
) -> tuple[str, int, int, float]:
    """Generate a plain-language rationale for the program.

    Args:
        athlete_context: ASSESS output
        plan: PLAN output
        program_sessions: All generated sessions with their exercises
        llm_client: Anthropic client
        settings: AgentSettings

    Returns:
        (rationale_text, input_tokens, output_tokens, cost_usd) — the cost
        (OpenRouter's bill, else the list-price estimate) feeds the
        orchestrator's per-program cost tracking, which previously excluded the
        explain call's spend entirely (AGT-L7).
    """
    prompt = _build_explain_prompt(athlete_context, plan, program_sessions)
    request_kwargs = {
        # Sonnet 5 / Opus 5 reject `temperature`; 4.x ignore an omitted `thinking`
        **sampling_kwargs(settings.explanation_model, settings.explanation_temperature),
        **thinking_kwargs(
            settings.explanation_model,
            _setting_str(settings, "explanation_thinking"),
            _setting_str(settings, "explanation_effort"),
        ),
    }

    try:
        max_tokens = _explanation_max_tokens(settings)
        while True:
            response = create_message_with_retries(
                llm_client,
                max_attempts=settings.max_generation_retries + 1,
                base_delay=settings.retry_delay_seconds,
                model=settings.explanation_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                **request_kwargs,
            )
            # Adaptive thinking (Sonnet 5 / Opus 5) counts against max_tokens and can
            # spend a 1,024 budget before the rationale starts; grow and retry once
            # per doubling up to the ceiling rather than storing a truncated text.
            if getattr(response, "stop_reason", None) != "max_tokens" or max_tokens >= LLM_MAX_TOKENS_CEILING:
                break
            grown = min(max_tokens * 2, LLM_MAX_TOKENS_CEILING)
            logger.warning(
                f"Rationale truncated at max_tokens={max_tokens} (stop_reason=max_tokens) — "
                f"retrying with max_tokens={grown}"
            )
            max_tokens = grown
        rationale = message_text(response).strip()
        cost = estimate_cost(
            response.usage.input_tokens, response.usage.output_tokens, settings.explanation_model,
            billed_usd=billed_cost(response.usage), service_tier=served_tier(response.usage),
        )
        logger.info(
            f"Generated rationale ({len(rationale)} chars, "
            f"{response.usage.input_tokens} in / {response.usage.output_tokens} out, "
            f"~${cost:.4f})"
        )
        return rationale, response.usage.input_tokens, response.usage.output_tokens, cost
    except (Exception, FlexUnavailable) as e:   # a declined standard-tier fallback keeps the program
        logger.error(f"Explain step failed after retries: {e}")
        return f"[Rationale generation failed: {e}]", 0, 0, 0.0


def _explanation_max_tokens(settings) -> int:
    """The configured budget; a partial / mocked settings object gets the
    config default (2,048), not the 1,024 that Sonnet 5 rationales overran."""
    default = Settings.explanation_max_tokens
    value = getattr(settings, "explanation_max_tokens", default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _intensity_label(ex: dict) -> str:
    pct = ex.get("intensity_pct")
    if pct is not None:
        try:
            return f" @{float(pct):g}%"
        except (TypeError, ValueError):
            pass
    rpe = ex.get("rpe_target")
    return f" RPE {rpe}" if rpe is not None else ""


def _exercise_label(ex: dict) -> str:
    return f"{ex.get('exercise_name')} {prescription_label(ex)}{_intensity_label(ex)}"


def _session_line(session: dict, max_exercises: int | None = None) -> str:
    working = [ex for ex in session.get("exercises") or []
               if not is_warmup_set(ex.get("intensity_reference"), ex.get("intensity_pct"))]
    shown = working if max_exercises is None else working[:max_exercises]
    body = "; ".join(_exercise_label(ex) for ex in shown) or "(no exercises)"
    if len(shown) < len(working):
        body += f"; +{len(working) - len(shown)} more"
    label = f" {session['label']}" if session.get("label") else ""
    return f"W{session.get('week', '?')} D{session.get('day', '?')}{label}: {body}"


def program_view_lines(program_sessions: list[dict],
                       max_chars: int = EXPLAIN_PROGRAM_VIEW_MAX_CHARS) -> str:
    """Every session as one line, in (week, day) order, so the rationale can
    describe the progression the program actually has (AGENT-0b — it used to
    see four exercises of Week 1 Day 1). Over `max_chars`, each line keeps its
    first EXPLAIN_EXERCISES_PER_SESSION_WHEN_CAPPED exercises; still over, the
    tail is cut with a count of the sessions left out."""
    if not program_sessions:
        return "(no sessions generated)"
    ordered = sorted(program_sessions, key=lambda s: (s.get("week") or 0, s.get("day") or 0))
    lines = [_session_line(s) for s in ordered]
    if sum(len(line) + 1 for line in lines) > max_chars:
        lines = [_session_line(s, EXPLAIN_EXERCISES_PER_SESSION_WHEN_CAPPED) for s in ordered]
    kept, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > max_chars:
            kept.append(f"(… {len(lines) - i} more sessions not shown)")
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def _previous_block_summary(prev: dict | None) -> str:
    """One line on the block this program follows, so the rationale can say
    what changes and why instead of treating the athlete as history-less (DOG-1e)."""
    if not prev:
        return "none — first program"
    parts = [f"{prev.get('phase', 'unknown')} phase, {prev.get('duration_weeks', '?')} weeks"]
    structure = prev.get("structure") or {}
    cycles = [c.get("label") for c in structure.get("cycles") or [] if c.get("label")]
    if cycles:
        parts.append(" → ".join(str(c) for c in cycles))
    most_used = [m.get("exercise_name") for m in (structure.get("most_used") or [])[:5]]
    if most_used:
        parts.append("built around " + ", ".join(str(m) for m in most_used))
    outcome = prev.get("outcome_summary") or {}
    if isinstance(outcome, dict) and outcome.get("adherence_pct") is not None:
        parts.append(f"adherence {outcome['adherence_pct']}%")
    return "; ".join(parts)


def _coach_block(athlete_context: AthleteContext) -> str:
    """COACH-1: the coach's brief, so the rationale says which choices were the
    coach's; "" on a normal run (the prompt is then unchanged)."""
    section = coach_prompt_section(getattr(athlete_context, "coach_note", None),
                                   getattr(athlete_context, "coach_constraints", None))
    if not section:
        return ""
    brief = section.replace("## Coach Instructions", "## Coach's Brief", 1)
    return f"\n{brief}\nMention where the program follows the coach's choices.\n"


def _build_explain_prompt(
    athlete_context: AthleteContext,
    plan: ProgramPlan,
    program_sessions: list[dict],
) -> str:
    """Assemble the prompt for the program rationale call."""
    faults = ", ".join(athlete_context.technical_faults) or "none identified"
    goal = athlete_context.active_goal.get("goal", "general_strength") if athlete_context.active_goal else "general_strength"
    weeks_to_comp = athlete_context.weeks_to_competition

    # Summarize weekly structure
    week_lines = []
    for wt in plan.weekly_targets:
        deload_note = " (DELOAD)" if wt.is_deload else ""
        week_lines.append(
            f"  Week {wt.week_number}{deload_note}: "
            f"{wt.intensity_floor}-{wt.intensity_ceiling}% intensity, "
            f"volume modifier {wt.volume_modifier:.0%}"
        )
    weeks_summary = "\n".join(week_lines)

    program_view = program_view_lines(program_sessions)

    # weeks_to_comp == 0 (meet within a week) is the most time-critical case, so
    # it must NOT be treated as "no date" — only None means unset (A-M8).
    previous_block = _previous_block_summary(athlete_context.previous_program)
    if weeks_to_comp is None:
        comp_context = "no competition date set"
    elif weeks_to_comp == 0:
        comp_context = "competition this week"
    else:
        comp_context = f"{weeks_to_comp} weeks out from competition"
    coach = _coach_block(athlete_context)

    return f"""An Olympic weightlifting athlete has been prescribed the following training program.
Write a clear, direct explanation for the athlete (not a coach) covering the 5 points below.
Use plain language. Avoid unexplained jargon. Target 3-5 short paragraphs.

## Athlete
Level: {athlete_context.level}
Goal: {goal} ({comp_context})
Technical faults: {faults}
Sessions per week: {plan.sessions_per_week}
Previous program: {previous_block}

## Program Structure
Phase: {plan.phase}
Duration: {plan.duration_weeks} weeks

Weekly progression:
{weeks_summary}

## Sessions (every session, warm-up sets left out; sets×reps @ % of max)
{program_view}
{coach}
## Write an explanation covering:
1. Why this training phase was selected for this athlete's goal and timeline
2. How volume and intensity progress across the weeks (and why) — describe only what the sessions above show
3. Key exercise choices — what they target and why they matter for this athlete
4. What the athlete should expect to feel week by week (energy, soreness, performance)
5. What signals would indicate the program needs adjustment (RPE consistently too high, missing lifts, etc.)

Write for the athlete. Be specific and practical, not generic."""
