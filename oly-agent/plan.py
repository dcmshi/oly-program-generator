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

from shared.db import fetch_all
from shared.prilepin import compute_session_rep_target
from models import AthleteContext, ProgramPlan, WeekTarget, SessionTemplate
from phase_profiles import build_weekly_targets
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
    - No competition date -> map goal_type to phase

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
    """Map athlete context to (phase, duration_weeks)."""
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

    goal_to_phase = {
        "general_strength": ("accumulation",    4),
        "technique_focus":  ("accumulation",    4),
        "pr_attempt":       ("intensification", 4),
        "work_capacity":    ("general_prep",    5),
        "return_to_sport":  ("general_prep",    3),
        "competition_prep": ("intensification", 4),
    }
    return goal_to_phase.get(goal, ("accumulation", 4))


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
