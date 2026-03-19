# oly-agent/plan.py
"""
Step 2: PLAN — Determine program parameters.

Selects the training phase, block duration, volume/intensity targets,
and session structure. All deterministic — no LLM involved at this step.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import ValidationError
from shared.db import fetch_all
from shared.prilepin import compute_session_rep_target
from models import AthleteContext, ProgramPlan, WeekTarget, SessionTemplate
from phase_profiles import build_weekly_targets
from schemas import OutcomeSummary
from session_templates import get_session_templates

logger = logging.getLogger(__name__)


def plan(athlete_context: AthleteContext, conn, settings) -> ProgramPlan:
    """Determine the program shape from athlete context.

    Decision tree:
    - Has competition date?
      - >12 weeks -> accumulation (4 wks)
      - 8-12 weeks -> accumulation (4 wks)
      - 4-8 weeks  -> intensification (4 wks)
      - <4 weeks   -> realization
    - No competition date:
      - First program (cold start) -> map goal_type to phase, cap intensity
      - Has previous program -> advance phase along progression, adjust by outcome

    Returns a ProgramPlan with weekly targets and session templates.
    """
    phase, duration_weeks = _select_phase_and_duration(athlete_context)
    logger.info(f"Selected phase={phase}, duration={duration_weeks} weeks")

    # ── Build weekly targets ───────────────────────────────────
    raw_targets = build_weekly_targets(phase, duration_weeks, athlete_context.level)

    # ── Cold-start overrides ──────────────────────────────────
    intensity_ceiling_override = None
    max_complexity = 5
    if athlete_context.previous_program is None:
        ceiling_cap = 80.0 if athlete_context.level != "beginner" else 75.0
        intensity_ceiling_override = ceiling_cap
        raw_targets = [
            {**t, "intensity_ceiling": min(t["intensity_ceiling"], ceiling_cap)}
            for t in raw_targets
        ]
        duration_weeks = min(duration_weeks, 4)
        raw_targets = raw_targets[:duration_weeks]
        max_complexity = 2 if athlete_context.level == "beginner" else 3
        logger.info(
            f"Cold start: intensity cap={ceiling_cap}%, "
            f"duration={duration_weeks} wks, max_complexity={max_complexity}"
        )
    else:
        # ── Outcome-based volume/intensity adjustments ─────────
        raw_targets = _apply_outcome_adjustments(raw_targets, athlete_context.previous_program)
        if intensity_ceiling_override is None:
            # Track highest ceiling for metadata (non-cold-start always has None override)
            pass

    # ── Session templates ──────────────────────────────────────
    session_tmpl_dicts = get_session_templates(athlete_context.sessions_per_week)
    session_templates = [
        SessionTemplate(
            day_number=t["day_number"],
            label=t["label"],
            primary_movement=t["primary_movement"],
            secondary_movements=t["secondary_movements"],
            session_volume_share=t["session_volume_share"],
            notes=t.get("notes", ""),
        )
        for t in session_tmpl_dicts
    ]

    # ── Compute Prilepin rep targets per week ─────────────────
    deload_week = None
    if raw_targets and raw_targets[-1]["is_deload"]:
        deload_week = raw_targets[-1]["week_number"]

    weekly_targets = []
    for t in raw_targets:
        total_reps = sum(
            compute_session_rep_target(
                intensity_floor=t["intensity_floor"],
                intensity_ceiling=t["intensity_ceiling"],
                session_volume_share=s.session_volume_share,
                volume_modifier=t["volume_modifier"],
            )
            for s in session_templates
        )
        weekly_targets.append(WeekTarget(
            week_number=t["week_number"],
            volume_modifier=t["volume_modifier"],
            intensity_floor=t["intensity_floor"],
            intensity_ceiling=t["intensity_ceiling"],
            total_competition_lift_reps=total_reps,
            reps_per_set_range=t["reps_per_set_range"],
            is_deload=t["is_deload"],
        ))

    # ── Load relevant programming principles ──────────────────
    principles = _load_principles(conn, phase, athlete_context.level)

    return ProgramPlan(
        phase=phase,
        duration_weeks=duration_weeks,
        sessions_per_week=athlete_context.sessions_per_week,
        deload_week=deload_week,
        weekly_targets=weekly_targets,
        session_templates=session_templates,
        active_principles=principles,
        supporting_chunks=[],
        intensity_ceiling_override=intensity_ceiling_override,
        max_complexity=max_complexity,
    )


def _select_phase_and_duration(ctx: AthleteContext) -> tuple[str, int]:
    """Map athlete context to (phase, duration_weeks).

    Priority:
    1. Competition date drives phase selection (time-based periodization).
    2. Previous program phase drives progression (if no competition date).
    3. Goal type determines initial phase (cold start / no prior history).
    """
    weeks_out = ctx.weeks_to_competition
    goal = ctx.active_goal.get("goal") if ctx.active_goal else None

    if weeks_out is not None:
        if weeks_out > 12:
            return "accumulation", 4
        elif weeks_out >= 8:
            return "accumulation", 4
        elif weeks_out >= 4:
            return "intensification", 4
        else:
            return "realization", min(3, max(1, weeks_out))

    # Advance phase from previous program when no competition date
    if ctx.previous_program is not None:
        prev_phase = ctx.previous_program.get("phase")
        raw_outcome = ctx.previous_program.get("outcome_summary") or {}
        try:
            outcome = OutcomeSummary.model_validate(raw_outcome)
        except ValidationError as exc:
            logger.warning("outcome_summary validation failed — using defaults: %s", exc)
            outcome = OutcomeSummary()
        next_phase, next_duration = _advance_phase(prev_phase, outcome, goal)
        logger.info(f"Phase progression: {prev_phase} -> {next_phase} ({next_duration} wks)")
        return next_phase, next_duration

    goal_to_phase = {
        "general_strength": ("accumulation",    4),
        "technique_focus":  ("accumulation",    4),
        "pr_attempt":       ("intensification", 4),
        "work_capacity":    ("general_prep",    5),
        "return_to_sport":  ("general_prep",    3),
        "competition_prep": ("intensification", 4),
    }
    return goal_to_phase.get(goal, ("accumulation", 4))


# Standard periodization progression (loops back after realization)
_PHASE_SEQUENCE = ["general_prep", "accumulation", "intensification", "realization"]


def _advance_phase(prev_phase: str | None, outcome: OutcomeSummary, goal: str | None) -> tuple[str, int]:
    """Select the next phase given the previous phase and outcome signals.

    Rules:
    - Advance phase if adherence >= 70% and avg_make_rate >= 0.75
    - Stay in same phase if performance was poor (repeat block)
    - Realization always cycles back to accumulation (peaking -> rebuild)
    """
    from phase_profiles import PHASE_PROFILES

    adherence = outcome.adherence_pct
    make_rate = outcome.avg_make_rate
    rpe_dev = outcome.avg_rpe_deviation

    # Performance gate: advance only if athlete is ready
    ready_to_advance = adherence >= 70.0 and make_rate >= 0.75

    if not ready_to_advance:
        logger.info(
            f"Phase not advanced (adherence={adherence:.0f}%, make_rate={make_rate:.0%}) — repeating {prev_phase}"
        )

    if prev_phase not in _PHASE_SEQUENCE:
        # Unknown or missing — fall back to accumulation
        next_phase = "accumulation"
    elif prev_phase == "realization":
        # After peaking, always rebuild with accumulation
        next_phase = "accumulation"
    elif ready_to_advance:
        idx = _PHASE_SEQUENCE.index(prev_phase)
        next_phase = _PHASE_SEQUENCE[min(idx + 1, len(_PHASE_SEQUENCE) - 1)]
    else:
        next_phase = prev_phase

    # Exceptionally high RPE deviation → don't advance even if make rate was ok
    if rpe_dev > 1.5 and next_phase != prev_phase:
        logger.info(f"RPE deviation too high ({rpe_dev:+.2f}) — staying in {prev_phase}")
        next_phase = prev_phase

    duration = PHASE_PROFILES.get(next_phase, {}).get("default_weeks", 4)
    return next_phase, duration


def _apply_outcome_adjustments(raw_targets: list[dict], previous_program: dict) -> list[dict]:
    """Nudge volume modifiers and intensity ceilings based on previous program outcome.

    Adjustments (applied to non-deload weeks only):
    - Poor adherence (<70%): reduce volume_modifier by 10% to make program more manageable
    - Low make rate (<0.75): reduce intensity_ceiling by 3% (loads were too heavy)
    - High RPE deviation (>1.0): reduce volume_modifier by 5% (program was too fatiguing)
    - Excellent performance (adherence >90%, make_rate >0.85): small intensity boost (+2%)
    """
    raw_outcome = previous_program.get("outcome_summary") or {}
    if not raw_outcome:
        return raw_targets
    try:
        outcome = OutcomeSummary.model_validate(raw_outcome)
    except ValidationError as exc:
        logger.warning("outcome_summary validation failed — applying no adjustments: %s", exc)
        return raw_targets

    adherence = outcome.adherence_pct
    make_rate = outcome.avg_make_rate
    rpe_dev = outcome.avg_rpe_deviation

    vol_delta = 0.0
    int_delta = 0.0

    if adherence < 70.0:
        vol_delta -= 0.10
        logger.info(f"Outcome adjustment: adherence={adherence:.0f}% → volume -10%")
    if make_rate < 0.75:
        int_delta -= 3.0
        logger.info(f"Outcome adjustment: make_rate={make_rate:.0%} → intensity ceiling -3%")
    if rpe_dev > 1.0:
        vol_delta -= 0.05
        logger.info(f"Outcome adjustment: rpe_deviation={rpe_dev:+.2f} → volume -5%")
    if adherence >= 90.0 and make_rate >= 0.85:
        int_delta += 2.0
        logger.info(f"Outcome adjustment: excellent performance → intensity ceiling +2%")

    if vol_delta == 0.0 and int_delta == 0.0:
        return raw_targets

    adjusted = []
    for t in raw_targets:
        if t["is_deload"]:
            adjusted.append(t)
            continue
        new_vol = round(max(0.4, t["volume_modifier"] + vol_delta), 2)
        new_ceil = round(min(100.0, t["intensity_ceiling"] + int_delta), 1)
        adjusted.append({**t, "volume_modifier": new_vol, "intensity_ceiling": new_ceil})
    return adjusted


def _load_principles(conn, phase: str, athlete_level: str) -> list[dict]:
    """Load programming principles applicable to this phase and level."""
    return fetch_all(
        conn,
        """
        SELECT id, principle_name, recommendation, rationale, priority, condition
        FROM programming_principles
        WHERE (condition IS NULL
               OR condition->>'phase' IS NULL
               OR condition->>'phase' = %s)
          AND (condition IS NULL
               OR condition->'athlete_level' IS NULL
               OR condition->'athlete_level' @> to_jsonb(%s::text))
        ORDER BY priority DESC
        LIMIT 20
        """,
        (phase, athlete_level),
    )
