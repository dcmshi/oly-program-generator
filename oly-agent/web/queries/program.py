# web/queries/program.py
"""DB queries for the program view."""


def get_program(conn, program_id: int) -> dict | None:
    from shared.db import fetch_one
    return fetch_one(
        conn,
        "SELECT * FROM generated_programs WHERE id = %s",
        (program_id,),
    )


def get_all_programs(conn, athlete_id: int) -> list[dict]:
    from shared.db import fetch_all
    return fetch_all(
        conn,
        """
        SELECT id, name, phase, status, start_date, duration_weeks, sessions_per_week, created_at
        FROM generated_programs
        WHERE athlete_id = %s
        ORDER BY created_at DESC
        """,
        (athlete_id,),
    )


def get_program_weeks(conn, program_id: int) -> list[dict]:
    """Return sessions grouped by week, each with their exercises."""
    from shared.db import fetch_all
    sessions = fetch_all(
        conn,
        """
        SELECT ps.id, ps.week_number, ps.day_number, ps.session_label,
               ps.estimated_duration_minutes, ps.focus_area,
               tl.id AS log_id
        FROM program_sessions ps
        LEFT JOIN training_logs tl ON tl.session_id = ps.id
        WHERE ps.program_id = %s
        ORDER BY ps.week_number, ps.day_number
        """,
        (program_id,),
    )

    exercises_by_session: dict[int, list] = {}
    all_session_ids = [s["id"] for s in sessions]
    if all_session_ids:
        placeholders = ",".join(["%s"] * len(all_session_ids))
        exercises = fetch_all(
            conn,
            f"""
            SELECT session_id, exercise_order, exercise_name, sets, reps,
                   intensity_pct, absolute_weight_kg, rest_seconds, rpe_target,
                   selection_rationale
            FROM session_exercises
            WHERE session_id IN ({placeholders})
            ORDER BY session_id, exercise_order
            """,
            tuple(all_session_ids),
        )
        for ex in exercises:
            exercises_by_session.setdefault(ex["session_id"], []).append(ex)

    # Group sessions by week
    weeks: dict[int, list] = {}
    for s in sessions:
        s["exercises"] = exercises_by_session.get(s["id"], [])
        weeks.setdefault(s["week_number"], []).append(s)

    return [{"week_number": wn, "sessions": ws} for wn, ws in sorted(weeks.items())]


def activate_program(conn, program_id: int, athlete_id: int):
    from shared.db import execute
    # Supersede any currently active program
    execute(
        conn,
        """
        UPDATE generated_programs SET status = 'superseded', updated_at = NOW()
        WHERE athlete_id = %s AND status = 'active' AND id != %s
        """,
        (athlete_id, program_id),
    )
    execute(
        conn,
        "UPDATE generated_programs SET status = 'active', updated_at = NOW() WHERE id = %s",
        (program_id,),
    )
    conn.commit()
