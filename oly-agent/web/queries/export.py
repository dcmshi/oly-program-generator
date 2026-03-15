# web/queries/export.py
"""DB queries for CSV exports."""


async def get_program_for_export(conn, program_id: int, athlete_id: int) -> tuple[dict | None, list[dict]]:
    """Return (program_meta, exercise_rows) for a single program.

    exercise_rows has one entry per session exercise, ordered by week → day → exercise_order.
    Returns (None, []) if the program doesn't exist or belongs to another athlete.
    """
    from web.async_db import async_fetch_one, async_fetch_all

    program = await async_fetch_one(
        conn,
        """
        SELECT id, name, phase, status, start_date, duration_weeks, sessions_per_week
        FROM generated_programs
        WHERE id = $1 AND athlete_id = $2
        """,
        program_id, athlete_id,
    )
    if not program:
        return None, []

    rows = await async_fetch_all(
        conn,
        """
        SELECT
            ps.week_number,
            ps.day_number,
            ps.session_label,
            ps.focus_area,
            ps.estimated_duration_minutes   AS session_duration_min,
            se.exercise_order,
            se.exercise_name,
            se.sets,
            se.reps,
            se.intensity_pct,
            se.intensity_reference,
            se.absolute_weight_kg,
            se.rpe_target,
            se.rest_seconds,
            se.backoff_sets,
            se.backoff_intensity_pct,
            se.is_max_attempt,
            se.notes
        FROM program_sessions ps
        JOIN session_exercises se ON se.session_id = ps.id
        WHERE ps.program_id = $1
        ORDER BY ps.week_number, ps.day_number, se.exercise_order
        """,
        program_id,
    )
    return program, rows


async def get_full_training_log(conn, athlete_id: int) -> list[dict]:
    """Return all logged sessions + exercises for an athlete, one row per exercise entry.

    Sessions with no logged exercises produce a single row with null exercise fields.
    """
    from web.async_db import async_fetch_all

    return await async_fetch_all(
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
        WHERE tl.athlete_id = $1
        ORDER BY tl.log_date, tl.id, tle.id
        """,
        athlete_id,
    )
