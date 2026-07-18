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

from models import AthleteContext, ProgramPlan

from shared.llm import create_message_with_retries, estimate_cost

logger = logging.getLogger(__name__)


def explain(
    athlete_context: AthleteContext,
    plan: ProgramPlan,
    program_sessions: list[dict],
    llm_client,
    settings,
) -> str:
    """Generate a plain-language rationale for the program.

    Args:
        athlete_context: ASSESS output
        plan: PLAN output
        program_sessions: All generated sessions with their exercises
        llm_client: Anthropic client
        settings: AgentSettings

    Returns:
        (rationale_text, input_tokens, output_tokens) — the token counts feed
        the orchestrator's per-program cost tracking, which previously
        excluded the explain call's spend entirely (AGT-L7).
    """
    prompt = _build_explain_prompt(athlete_context, plan, program_sessions)

    try:
        response = create_message_with_retries(
            llm_client,
            max_attempts=settings.max_generation_retries + 1,
            base_delay=settings.retry_delay_seconds,
            model=settings.explanation_model,
            max_tokens=1024,
            temperature=settings.explanation_temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        rationale = response.content[0].text.strip()
        cost = estimate_cost(response.usage.input_tokens, response.usage.output_tokens)
        logger.info(
            f"Generated rationale ({len(rationale)} chars, "
            f"{response.usage.input_tokens} in / {response.usage.output_tokens} out, "
            f"~${cost:.4f})"
        )
        return rationale, response.usage.input_tokens, response.usage.output_tokens
    except Exception as e:
        logger.error(f"Explain step failed after retries: {e}")
        return f"[Rationale generation failed: {e}]", 0, 0


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

    # Summarize exercise selection across the program (first session as example)
    first_session = program_sessions[0] if program_sessions else {}
    first_exercises = first_session.get("exercises", [])
    sample_exercises = "\n".join(
        f"  {ex.get('exercise_name')} {ex.get('sets')}x{ex.get('reps')} "
        f"@ {ex.get('intensity_pct')}% ({ex.get('intensity_reference')})"
        for ex in first_exercises[:4]
    )

    # weeks_to_comp == 0 (meet within a week) is the most time-critical case, so
    # it must NOT be treated as "no date" — only None means unset (A-M8).
    if weeks_to_comp is None:
        comp_context = "no competition date set"
    elif weeks_to_comp == 0:
        comp_context = "competition this week"
    else:
        comp_context = f"{weeks_to_comp} weeks out from competition"

    return f"""An Olympic weightlifting athlete has been prescribed the following training program.
Write a clear, direct explanation for the athlete (not a coach) covering the 5 points below.
Use plain language. Avoid unexplained jargon. Target 3-5 short paragraphs.

## Athlete
Level: {athlete_context.level}
Goal: {goal} ({comp_context})
Technical faults: {faults}
Sessions per week: {plan.sessions_per_week}

## Program Structure
Phase: {plan.phase}
Duration: {plan.duration_weeks} weeks

Weekly progression:
{weeks_summary}

## Sample Session (Week 1, Day 1)
{sample_exercises}

## Write an explanation covering:
1. Why this training phase was selected for this athlete's goal and timeline
2. How volume and intensity progress across the weeks (and why)
3. Key exercise choices — what they target and why they matter for this athlete
4. What the athlete should expect to feel week by week (energy, soreness, performance)
5. What signals would indicate the program needs adjustment (RPE consistently too high, missing lifts, etc.)

Write for the athlete. Be specific and practical, not generic."""
