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

from shared.db import fetch_one, fetch_all
from models import AthleteContext
from weight_resolver import build_maxes_dict

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

    # ── Previous program ───────────────────────────────────────
    previous_program = fetch_one(
        conn,
        """
        SELECT phase, duration_weeks, outcome_summary, end_date
        FROM generated_programs
        WHERE athlete_id = %s AND status = 'completed'
        ORDER BY end_date DESC LIMIT 1
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
        weeks_to_competition = max(0, delta.days // 7)

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
        sessions_per_week=athlete.get("sessions_per_week", 4),
        weeks_to_competition=weeks_to_competition,
    )


def estimate_missing_maxes(known_maxes: dict[str, float]) -> dict[str, tuple[float, str]]:
    """Estimate missing maxes from known competition lift maxes.

    Returns: {exercise_ref: (estimated_kg, "estimated")}
    """
    estimated = {}
    for exercise, config in MAX_ESTIMATION_RATIOS.items():
        if exercise not in known_maxes:
            ref_max = known_maxes.get(config["reference"])
            if ref_max:
                estimated[exercise] = (
                    round(ref_max * config["ratio"] * 2) / 2,  # round to 0.5kg
                    "estimated",
                )
    return estimated
