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

from models import AthleteContext, ProgramPlan, SessionTemplate, WeekTarget
from phase_profiles import build_weekly_targets
from phase_progression import compute_load_deltas, decide_next_phase
from pydantic import ValidationError
from schemas import OutcomeSummary
from session_templates import get_session_templates

from shared.constants import (
    BLOCK_WEEKS_DEFAULT_BY_LEVEL,
    BLOCK_WEEKS_MAX_BY_LEVEL,
    BLOCK_WEEKS_MIN,
    DELOAD_EVERY_WEEKS_OPTIONS,
    MAX_PRINCIPLE_CANDIDATES,
    TRAINING_PREFERENCE_DEFAULTS,
    TRAINING_PREFERENCE_OPTIONS,
)
from shared.db import fetch_all
from shared.prilepin import compute_session_rep_target

logger = logging.getLogger(__name__)


def plan(athlete_context: AthleteContext, conn, settings, duration_weeks: int | None = None) -> ProgramPlan:
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

    `duration_weeks` (PLAN-1) overrides the phase default when the athlete asked
    for a length; it is clamped to the level's bounds and ignored when a
    competition date fixes the block (realization runs to the meet).

    Returns a ProgramPlan with weekly targets and session templates.
    """
    phase, default_weeks = _select_phase_and_duration(athlete_context)
    duration_weeks = resolve_block_length(phase, default_weeks, athlete_context, duration_weeks)
    logger.info(f"Selected phase={phase}, duration={duration_weeks} weeks")
    prefs = training_preferences(athlete_context.athlete)

    # ── Build weekly targets ───────────────────────────────────
    raw_targets = build_weekly_targets(phase, duration_weeks, athlete_context.level,
                                       deload_every_weeks=prefs.get("deload_every_weeks"),
                                       deload_style=prefs["deload_style"])

    # ── Cold-start overrides ──────────────────────────────────
    intensity_ceiling_override = None
    max_complexity = 5
    if athlete_context.previous_program is None:
        ceiling_cap = 80.0 if athlete_context.level != "beginner" else 75.0
        intensity_ceiling_override = ceiling_cap
        # Cap the duration, then REBUILD from the profile so its own shortening
        # convention applies (drop early ramp-up weeks, KEEP peak + deload).
        # Slicing raw_targets[:duration_weeks] kept the early ramp and discarded
        # the deload tail, so a beginner's first program ended on its heaviest
        # week with no deload (A-M9).
        duration_weeks = min(duration_weeks, 4)
        raw_targets = build_weekly_targets(phase, duration_weeks, athlete_context.level,
                                           deload_every_weeks=prefs.get("deload_every_weeks"),
                                           deload_style=prefs["deload_style"])
        # Clamp the floor to the capped ceiling too — realization starts at
        # floor 85, so capping only the ceiling emitted a contradictory
        # "85%–80%" range in the prompt (AGT-M1; mirrors plan.py's
        # outcome-adjustment clamp).
        raw_targets = [
            {**t,
             "intensity_ceiling": min(t["intensity_ceiling"], ceiling_cap),
             "intensity_floor": min(t["intensity_floor"], ceiling_cap)}
            for t in raw_targets
        ]
        max_complexity = 2 if athlete_context.level == "beginner" else 3
        logger.info(
            f"Cold start: intensity cap={ceiling_cap}%, "
            f"duration={duration_weeks} wks, max_complexity={max_complexity}"
        )
    else:
        # ── Outcome-based volume/intensity adjustments ─────────
        raw_targets = _apply_outcome_adjustments(raw_targets, athlete_context.previous_program)

    # ── Session templates ──────────────────────────────────────
    session_tmpl_dicts = get_session_templates(
        athlete_context.sessions_per_week, athlete_context.athlete.get("lift_emphasis")
    )
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
                sessions_per_week=len(session_templates),
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
        # The distribution may have fallen back to a different frequency
        # (3/4/5) — store the ACTUAL day count so program metadata matches the
        # sessions that exist (AGT-L6; at 1–2/wk the mismatch fed AGT-H1).
        sessions_per_week=len(session_templates) or athlete_context.sessions_per_week,
        deload_week=deload_week,
        weekly_targets=weekly_targets,
        session_templates=session_templates,
        active_principles=principles,
        supporting_chunks=[],
        intensity_ceiling_override=intensity_ceiling_override,
        max_complexity=max_complexity,
    )


def training_preferences(athlete: dict) -> dict:
    """The athlete's training preferences with defaults filled in (PLAN-2):
    `warmups`, `deload_style`, `max_test`, `deload_every_weeks` (int | None).
    Unknown values fall back to the default so a hand-edited row can't break
    planning."""
    raw = ((athlete or {}).get("exercise_preferences") or {}).get("prefs") or {}
    prefs = {}
    for key, options in TRAINING_PREFERENCE_OPTIONS.items():
        prefs[key] = raw.get(key) if raw.get(key) in options else TRAINING_PREFERENCE_DEFAULTS[key]
    every = raw.get("deload_every_weeks")
    prefs["deload_every_weeks"] = int(every) if isinstance(every, (int, float)) and int(every) in DELOAD_EVERY_WEEKS_OPTIONS else None
    return prefs


def resolve_block_length(phase: str, default_weeks: int, ctx: AthleteContext, requested: int | None) -> int:
    """PLAN-1: the block length actually used.

    - competition date set → the phase/length the date implies, never overridden
    - requested → clamped to [BLOCK_WEEKS_MIN, BLOCK_WEEKS_MAX_BY_LEVEL[level]]
    - otherwise → the level's default for this phase, else the profile default
    """
    if ctx.weeks_to_competition is not None:
        if requested is not None and requested != default_weeks:
            logger.info(f"Requested {requested} weeks ignored — competition in {ctx.weeks_to_competition} weeks fixes the block at {default_weeks}")
        return default_weeks
    level = ctx.level if ctx.level in BLOCK_WEEKS_MAX_BY_LEVEL else "intermediate"
    upper = BLOCK_WEEKS_MAX_BY_LEVEL[level]
    if requested is not None:
        clamped = max(BLOCK_WEEKS_MIN, min(upper, int(requested)))
        if clamped != requested:
            logger.info(f"Requested {requested} weeks clamped to {clamped} for a {level} athlete")
        return clamped
    return BLOCK_WEEKS_DEFAULT_BY_LEVEL.get(level, {}).get(phase, default_weeks)


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
        if weeks_out >= 8:
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


def _advance_phase(prev_phase: str | None, outcome: OutcomeSummary, goal: str | None) -> tuple[str, int]:
    """Select the next phase + duration given the previous phase and outcome.

    The phase *decision* lives in phase_progression.decide_next_phase so it can't
    drift from feedback._compute_phase_verdict (see A-H3). Here we just attach the
    phase's default duration.
    """
    from phase_profiles import PHASE_PROFILES

    next_phase, advanced, status = decide_next_phase(
        prev_phase, outcome.adherence_pct, outcome.avg_make_rate, outcome.avg_rpe_deviation
    )
    if not advanced:
        logger.info(f"Phase not advanced ({status}): {prev_phase} → {next_phase}")

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

    # Proportional to the miss, one source with feedback's verdict labels (PLAN-2 §1.8).
    vol_delta, int_delta, labels = compute_load_deltas(adherence, make_rate, rpe_dev)
    for label in labels:
        logger.info(f"Outcome adjustment: {label}")

    if vol_delta == 0.0 and int_delta == 0.0:
        return raw_targets

    adjusted = []
    for t in raw_targets:
        if t["is_deload"]:
            adjusted.append(t)
            continue
        new_vol = round(max(0.4, t["volume_modifier"] + vol_delta), 2)
        # Clamp to [intensity_floor, 100] — repeated negative nudges must not
        # push the ceiling below the week's floor
        new_ceil = round(max(t.get("intensity_floor", 0.0), min(100.0, t["intensity_ceiling"] + int_delta)), 1)
        adjusted.append({**t, "volume_modifier": new_vol, "intensity_ceiling": new_ceil})
    return adjusted


def _load_principles(conn, phase: str, athlete_level: str) -> list[dict]:
    """Load the CANDIDATE principles for this phase and level.

    This is a SQL superset: `jsonb @> to_jsonb(text)` matches both a string
    condition (`"phase": "accumulation"`) and an array one
    (`"phase": ["accumulation", "intensification"]`) — the previous
    `condition->>'phase' = %s` silently excluded every array-valued phase
    (RAG-H3). The other condition keys (movement_family, weeks-out, week-of-
    block, make rate, RPE, training age) are evaluated per session by
    `principle_matcher.select_principles` in the orchestrator, so the cap only
    needs to leave enough candidates for that pass.
    """
    return fetch_all(
        conn,
        """
        SELECT id, principle_name, recommendation, rationale, priority, condition
        FROM programming_principles
        WHERE duplicate_of IS NULL
          AND (condition IS NULL
               OR condition->'phase' IS NULL
               OR condition->'phase' @> to_jsonb(%s::text))
          AND (condition IS NULL
               OR condition->'athlete_level' IS NULL
               OR condition->'athlete_level' @> to_jsonb(%s::text))
        ORDER BY priority DESC
        LIMIT %s
        """,
        (phase, athlete_level, MAX_PRINCIPLE_CANDIDATES),
    )
