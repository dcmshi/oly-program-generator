# web/queries/export.py
"""DB query for training log CSV export."""


def get_full_training_log(conn, athlete_id: int) -> list[dict]:
    """Return all logged sessions + exercises for an athlete, one row per exercise entry.

    Sessions with no logged exercises produce a single row with null exercise fields.
    """
    from shared.db import fetch_all

    return fetch_all(
        conn,
        """
        SELECT
            tl.log_date,
            gp.name                     AS program_name,
            ps.week_number,
            ps.day_number,
            ps.session_label,
            tl.overall_rpe              AS session_rpe,
            tl.session_duration_minutes AS duration_min,
            tl.bodyweight_kg,
            tl.sleep_quality,
            tl.stress_level,
            tl.athlete_notes            AS session_notes,
            tle.exercise_name,
            tle.sets_completed,
            tle.reps_per_set,
            tle.weight_kg,
            tle.prescribed_weight_kg,
            tle.weight_deviation_kg,
            tle.rpe                     AS exercise_rpe,
            tle.rpe_deviation,
            tle.make_rate,
            tle.technical_notes
        FROM training_logs tl
        JOIN program_sessions  ps  ON ps.id  = tl.session_id
        JOIN generated_programs gp ON gp.id  = ps.program_id
        LEFT JOIN training_log_exercises tle ON tle.log_id = tl.id
        WHERE tl.athlete_id = %s
        ORDER BY tl.log_date, tl.id, tle.id
        """,
        (athlete_id,),
    )
