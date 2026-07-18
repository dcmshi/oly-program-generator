# oly-agent/assess.py
"""
Step 1: ASSESS — Gather athlete context from the database.

Queries the athlete profile, active goal, current maxes, previous program,
and recent training logs to build an AthleteContext object.
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Make shared/ importable when running from oly-agent/ or the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import AthleteContext
from weight_resolver import build_maxes_dict

from shared.db import fetch_all, fetch_one
from shared.formulas import round_kg

logger = logging.getLogger(__name__)

# Typical ratios for estimating missing maxes from competition lifts.
# Stored as max_type='estimated'; replaced by real data as athlete logs.
MAX_ESTIMATION_RATIOS = {
    "front_squat":     {"reference": "clean_and_jerk", "ratio": 1.10},
    "back_squat":      {"reference": "clean_and_jerk", "ratio": 1.30},
    "clean_pull":      {"reference": "clean_and_jerk", "ratio": 1.10},
    "clean_deadlift":  {"reference": "clean_and_jerk", "ratio": 1.20},
    "push_press":      {"reference": "clean_and_jerk", "ratio": 0.75},
    "snatch_pull":     {"reference": "snatch",          "ratio": 1.15},
    "snatch_deadlift": {"reference": "snatch",          "ratio": 1.25},
    "overhead_squat":  {"reference": "snatch",          "ratio": 0.90},
    # clean/jerk are first-class intensity_references (COMP_LIFT_REFS) the LLM
    # can prescribe against, but without a max source or ratio they resolved to
    # NULL kg — the athlete saw a % with no weight (audit5-L4). The C&J total is
    # gated by the weaker of the two, so each ≈ the C&J max.
    "clean":           {"reference": "clean_and_jerk", "ratio": 1.02},
    "jerk":            {"reference": "clean_and_jerk", "ratio": 1.0},
}


def assess(athlete_id: int, conn) -> AthleteContext:
    """Build an AthleteContext for the given athlete_id.

    Args:
        athlete_id: Primary key in the athletes table.
        conn: Open psycopg2 connection.

    Returns:
        AthleteContext with all fields populated (None where no data exists).

    Raises:
        ValueError if the athlete is not found.
    """
    # ── Profile ────────────────────────────────────────────────
    athlete = fetch_one(conn, "SELECT * FROM athletes WHERE id = %s", (athlete_id,))
    if not athlete:
        raise ValueError(f"Athlete {athlete_id} not found")

    # ── Active goal ────────────────────────────────────────────
    active_goal = fetch_one(
        conn,
        """
        SELECT * FROM athlete_goals
        WHERE athlete_id = %s AND is_active = TRUE
        ORDER BY priority LIMIT 1
        """,
        (athlete_id,),
    )

    # ── Current maxes ──────────────────────────────────────────
    max_rows = fetch_all(
        conn,
        """
        SELECT e.name, e.movement_family, am.weight_kg, am.date_achieved, am.rpe
        FROM athlete_maxes am
        JOIN exercises e ON am.exercise_id = e.id
        WHERE am.athlete_id = %s AND am.max_type = 'current'
        """,
        (athlete_id,),
    )
    maxes = build_maxes_dict(max_rows)
    recorded_maxes = dict(maxes)  # DB-recorded only, before the estimation merge (audit5-L3)

    # ── Fill in missing maxes via estimation ───────────────────
    estimated = estimate_missing_maxes(maxes)
    for exercise, estimated_kg in estimated.items():
        maxes[exercise] = estimated_kg
    if estimated:
        logger.info(f"Estimated {len(estimated)} missing maxes from competition lifts")
        if len(estimated) > 3:
            logger.warning(
                f"Athlete {athlete_id} has {len(estimated)} estimated maxes — "
                "program weights will be approximate. Consider testing more lifts before programming."
            )

    # ── Previous program ───────────────────────────────────────
    previous_program = fetch_one(
        conn,
        """
        SELECT phase, duration_weeks, outcome_summary, end_date
        FROM generated_programs
        WHERE athlete_id = %s AND status = 'completed'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (athlete_id,),
    )

    # ── Recent training logs (last 14 days) ────────────────────
    cutoff = date.today() - timedelta(days=14)
    recent_logs = fetch_all(
        conn,
        """
        SELECT tle.exercise_name, tle.weight_kg, tle.sets_completed,
               tle.rpe, tle.make_rate, tl.log_date
        FROM training_log_exercises tle
        JOIN training_logs tl ON tle.log_id = tl.id
        WHERE tl.athlete_id = %s AND tl.log_date >= %s
        ORDER BY tl.log_date DESC
        """,
        (athlete_id, cutoff),
    )

    # ── Weeks to competition ───────────────────────────────────
    weeks_to_competition = None
    if active_goal and active_goal.get("competition_date"):
        delta = active_goal["competition_date"] - date.today()
        if delta.days < 0:
            # A stale goal must not read as "competition this week" on every
            # generation forever (AGT-M2) — plan as if no competition is set.
            logger.warning(
                f"Goal competition_date {active_goal['competition_date']} is in the past — "
                "ignoring for planning. Update or clear the goal."
            )
        else:
            weeks_to_competition = delta.days // 7

    logger.info(
        f"Assessed athlete {athlete_id} ({athlete['name']}): "
        f"level={athlete['level']}, maxes={list(maxes.keys())}, "
        f"goal={active_goal['goal'] if active_goal else 'none'}, "
        f"weeks_to_comp={weeks_to_competition}"
    )

    return AthleteContext(
        athlete=athlete,
        level=athlete["level"],
        maxes=maxes,
        active_goal=active_goal,
        previous_program=previous_program,
        recent_logs=recent_logs,
        technical_faults=list(athlete.get("technical_faults") or []),
        injuries=list(athlete.get("injuries") or []),
        # `or 4`, not .get(..., 4): the column is nullable, so a SQL NULL makes
        # .get return None (the key exists) → TypeErrors downstream (A-L3).
        sessions_per_week=athlete.get("sessions_per_week") or 4,
        weeks_to_competition=weeks_to_competition,
        recorded_maxes=recorded_maxes,
    )


def estimate_missing_maxes(known_maxes: dict[str, float]) -> dict[str, float]:
    """Estimate missing maxes from known competition lift maxes.

    Returns: {exercise_ref: estimated_kg}
    """
    estimated = {}
    for exercise, config in MAX_ESTIMATION_RATIOS.items():
        if exercise not in known_maxes:
            ref_max = known_maxes.get(config["reference"])
            if ref_max:
                estimated[exercise] = round_kg(ref_max * config["ratio"])
    return estimated
