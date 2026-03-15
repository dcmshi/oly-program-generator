# oly-agent/feedback.py
"""
Feedback loop: compute program outcomes when a program completes.

Analyzes training logs vs prescriptions to produce a ProgramOutcome
that informs the next program's planning decisions.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.db import fetch_all, fetch_one, execute
from models import ProgramOutcome

logger = logging.getLogger(__name__)


def compute_outcome(program_id: int, athlete_id: int, conn) -> ProgramOutcome:
    """Compute outcome metrics when a program completes.

    Queries training logs vs prescriptions to compute:
    - Max progression (before vs after)
    - Session adherence
    - RPE deviation (actual vs target)
    - Make rate on competition lifts

    Also promotes any new maxes found in training logs to athlete_maxes.
    """
    # ── Session adherence ─────────────────────────────────────
    sessions_prescribed = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM program_sessions WHERE program_id = %s",
        (program_id,),
    )["cnt"]

    sessions_completed = fetch_one(
        conn,
        """
        SELECT COUNT(DISTINCT tl.session_id) AS cnt
        FROM training_logs tl
        JOIN program_sessions ps ON tl.session_id = ps.id
        WHERE ps.program_id = %s AND tl.session_id IS NOT NULL
        """,
        (program_id,),
    )["cnt"]

    adherence_pct = (sessions_completed / sessions_prescribed * 100) if sessions_prescribed else 0.0

    # ── RPE deviation (actual - target) ───────────────────────
    rpe_rows = fetch_all(
        conn,
        """
        SELECT tle.rpe - se.rpe_target AS deviation
        FROM training_log_exercises tle
        JOIN session_exercises se ON tle.session_exercise_id = se.id
        JOIN program_sessions ps ON se.session_id = ps.id
        WHERE ps.program_id = %s
          AND tle.rpe IS NOT NULL AND se.rpe_target IS NOT NULL
        """,
        (program_id,),
    )
    avg_rpe_deviation = (
        sum(r["deviation"] for r in rpe_rows) / len(rpe_rows) if rpe_rows else 0.0
    )

    # ── Make rate on competition lifts ────────────────────────
    make_rows = fetch_all(
        conn,
        """
        SELECT tle.make_rate, se.intensity_reference
        FROM training_log_exercises tle
        JOIN session_exercises se ON tle.session_exercise_id = se.id
        JOIN program_sessions ps ON se.session_id = ps.id
        WHERE ps.program_id = %s
          AND se.intensity_reference IN ('snatch', 'clean_and_jerk', 'clean')
          AND tle.make_rate IS NOT NULL
        """,
        (program_id,),
    )
    avg_make_rate = (
        sum(r["make_rate"] for r in make_rows) / len(make_rows) if make_rows else 0.0
    )

    # Per-lift make rate breakdown
    lift_buckets: dict[str, list[float]] = {}
    for r in make_rows:
        ref = r["intensity_reference"]
        lift_buckets.setdefault(ref, []).append(float(r["make_rate"]))
    make_rate_by_lift = {
        ref: round(sum(vals) / len(vals), 2)
        for ref, vals in sorted(lift_buckets.items())
    }

    # ── Volume signal ─────────────────────────────────────────
    weekly_reps_rows = fetch_all(
        conn,
        """
        SELECT ps.week_number,
               SUM(tle.sets_completed * array_length(tle.reps_per_set, 1)) AS weekly_reps
        FROM training_log_exercises tle
        JOIN training_logs tl ON tle.log_id = tl.id
        JOIN program_sessions ps ON tl.session_id = ps.id
        WHERE ps.program_id = %s AND tle.reps_per_set IS NOT NULL
        GROUP BY ps.week_number
        ORDER BY ps.week_number
        """,
        (program_id,),
    )
    avg_weekly_reps = (
        sum(r["weekly_reps"] or 0 for r in weekly_reps_rows) / len(weekly_reps_rows)
        if weekly_reps_rows else 0.0
    )

    # ── RPE trend ─────────────────────────────────────────────
    rpe_trend = _compute_trend(
        [r["deviation"] for r in rpe_rows[-6:]] if rpe_rows else []
    )
    make_rate_trend = _compute_trend(
        [float(r["make_rate"]) for r in make_rows[-6:]] if make_rows else [],
        invert=False,
    )

    # ── Max delta (before vs after) ───────────────────────────
    program_row = fetch_one(
        conn,
        "SELECT maxes_snapshot, created_at FROM generated_programs WHERE id = %s",
        (program_id,),
    )
    maxes_before = program_row.get("maxes_snapshot", {}) if program_row else {}

    current_maxes_rows = fetch_all(
        conn,
        """
        SELECT e.name, am.weight_kg
        FROM athlete_maxes am
        JOIN exercises e ON am.exercise_id = e.id
        WHERE am.athlete_id = %s AND am.max_type = 'current'
        """,
        (athlete_id,),
    )
    maxes_after = {r["name"]: float(r["weight_kg"]) for r in current_maxes_rows}

    maxes_delta: dict[str, float] = {}
    for name, after_kg in maxes_after.items():
        before_kg = maxes_before.get(name)
        if before_kg is not None:
            delta = after_kg - float(before_kg)
            if delta != 0:
                maxes_delta[name] = delta

    # ── Collect athlete feedback ──────────────────────────────
    feedback_rows = fetch_all(
        conn,
        """
        SELECT tl.athlete_notes
        FROM training_logs tl
        JOIN program_sessions ps ON tl.session_id = ps.id
        WHERE ps.program_id = %s AND tl.athlete_notes IS NOT NULL
        ORDER BY tl.log_date DESC
        LIMIT 5
        """,
        (program_id,),
    )
    athlete_feedback = (
        " | ".join(r["athlete_notes"] for r in feedback_rows) if feedback_rows else None
    )

    outcome = ProgramOutcome(
        program_id=program_id,
        athlete_id=athlete_id,
        maxes_delta=maxes_delta,
        sessions_prescribed=sessions_prescribed,
        sessions_completed=sessions_completed,
        adherence_pct=round(adherence_pct, 1),
        avg_rpe_deviation=round(avg_rpe_deviation, 2),
        avg_make_rate=round(avg_make_rate, 2),
        make_rate_by_lift=make_rate_by_lift,
        avg_weekly_reps=round(avg_weekly_reps, 1),
        rpe_trend=rpe_trend,
        make_rate_trend=make_rate_trend,
        athlete_feedback=athlete_feedback,
    )

    logger.info(
        f"Program {program_id} outcome: "
        f"adherence={adherence_pct:.0f}%, "
        f"avg_rpe_dev={avg_rpe_deviation:+.2f}, "
        f"avg_make_rate={avg_make_rate:.0%}, "
        f"maxes_delta={maxes_delta}"
    )
    return outcome


def save_outcome(outcome: ProgramOutcome, conn):
    """Persist outcome summary to generated_programs.outcome_summary."""
    import json
    execute(
        conn,
        """
        UPDATE generated_programs
        SET outcome_summary = %s, status = 'completed', updated_at = NOW()
        WHERE id = %s
        """,
        (
            json.dumps({
                "maxes_delta":        outcome.maxes_delta,
                "adherence_pct":      outcome.adherence_pct,
                "avg_rpe_deviation":  outcome.avg_rpe_deviation,
                "avg_make_rate":      outcome.avg_make_rate,
                "make_rate_by_lift":  outcome.make_rate_by_lift,
                "avg_weekly_reps":    outcome.avg_weekly_reps,
                "rpe_trend":          outcome.rpe_trend,
                "make_rate_trend":    outcome.make_rate_trend,
                "athlete_feedback":   outcome.athlete_feedback,
            }),
            outcome.program_id,
        ),
    )
    conn.commit()
    logger.info(f"Saved outcome for program {outcome.program_id}")


def _compute_trend(values: list[float], invert: bool = False) -> str:
    """Classify a sequence as ascending, stable, or descending.

    invert=True means higher values are bad (e.g., RPE deviation).
    """
    if len(values) < 3:
        return "stable"
    first_half = values[: len(values) // 2]
    second_half = values[len(values) // 2 :]
    diff = sum(second_half) / len(second_half) - sum(first_half) / len(first_half)
    if invert:
        diff = -diff
    if diff > 0.5:
        return "ascending"
    elif diff < -0.5:
        return "descending"
    return "stable"
