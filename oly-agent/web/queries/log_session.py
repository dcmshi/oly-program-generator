# web/queries/log_session.py
"""DB queries for session logging."""

from datetime import date


def get_session_with_exercises(conn, session_id: int) -> dict | None:
    from shared.db import fetch_one, fetch_all
    session = fetch_one(
        conn,
        """
        SELECT ps.id, ps.week_number, ps.day_number, ps.session_label,
               ps.estimated_duration_minutes, ps.focus_area, ps.program_id,
               gp.name AS program_name, gp.athlete_id
        FROM program_sessions ps
        JOIN generated_programs gp ON gp.id = ps.program_id
        WHERE ps.id = %s
        """,
        (session_id,),
    )
    if not session:
        return None

    exercises = fetch_all(
        conn,
        """
        SELECT id, exercise_order, exercise_name, sets, reps,
               intensity_pct, absolute_weight_kg, rest_seconds, rpe_target
        FROM session_exercises
        WHERE session_id = %s
        ORDER BY exercise_order
        """,
        (session_id,),
    )
    session = dict(session)
    session["exercises"] = exercises
    return session


def get_existing_log(conn, session_id: int) -> dict | None:
    from shared.db import fetch_one
    return fetch_one(
        conn,
        "SELECT * FROM training_logs WHERE session_id = %s",
        (session_id,),
    )


def get_logged_exercises(conn, log_id: int) -> list[dict]:
    from shared.db import fetch_all
    return fetch_all(
        conn,
        """
        SELECT id, session_exercise_id, exercise_name, sets_completed,
               reps_per_set, weight_kg, rpe, make_rate, technical_notes,
               prescribed_weight_kg, weight_deviation_kg, rpe_deviation
        FROM training_log_exercises
        WHERE log_id = %s
        ORDER BY id
        """,
        (log_id,),
    )


def create_session_log(conn, athlete_id: int, session_id: int, form: dict) -> int:
    from shared.db import execute_returning

    def _float(v):
        try:
            return float(v) if v else None
        except (ValueError, TypeError):
            return None

    def _int(v):
        try:
            return int(v) if v else None
        except (ValueError, TypeError):
            return None

    log_date_str = form.get("log_date") or str(date.today())
    try:
        log_date = date.fromisoformat(log_date_str)
    except ValueError:
        log_date = date.today()

    log_id = execute_returning(
        conn,
        """
        INSERT INTO training_logs
            (athlete_id, session_id, log_date, overall_rpe,
             session_duration_minutes, bodyweight_kg, sleep_quality,
             stress_level, athlete_notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            athlete_id,
            session_id,
            log_date,
            _float(form.get("overall_rpe")),
            _int(form.get("duration")),
            _float(form.get("bodyweight")),
            _int(form.get("sleep_quality")),
            _int(form.get("stress_level")),
            form.get("notes") or None,
        ),
    )
    conn.commit()
    return log_id


def create_exercise_log(conn, log_id: int, form: dict):
    from shared.db import execute

    def _float(v):
        try:
            return float(v) if v else None
        except (ValueError, TypeError):
            return None

    def _int(v):
        try:
            return int(v) if v else None
        except (ValueError, TypeError):
            return None

    se_id = _int(form.get("session_exercise_id"))
    exercise_name = form.get("exercise_name", "").strip()
    weight_kg = _float(form.get("weight_kg"))
    rpe = _float(form.get("rpe"))
    prescribed_weight = _float(form.get("prescribed_weight_kg"))
    prescribed_rpe = _float(form.get("prescribed_rpe"))

    reps_raw = form.get("reps_per_set", "").strip()
    try:
        reps_per_set = [int(r.strip()) for r in reps_raw.split(",") if r.strip()]
    except (ValueError, AttributeError):
        reps_per_set = []

    make_rate_raw = _float(form.get("make_rate"))
    make_rate = make_rate_raw / 100.0 if make_rate_raw is not None else None

    weight_deviation = round(weight_kg - prescribed_weight, 2) if (weight_kg and prescribed_weight) else None
    rpe_deviation = round(rpe - prescribed_rpe, 1) if (rpe and prescribed_rpe) else None

    execute(
        conn,
        """
        INSERT INTO training_log_exercises
            (log_id, session_exercise_id, exercise_name, sets_completed,
             reps_per_set, weight_kg, rpe, make_rate, technical_notes,
             prescribed_weight_kg, weight_deviation_kg, rpe_deviation)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            log_id, se_id, exercise_name,
            _int(form.get("sets_completed")),
            reps_per_set or None,
            weight_kg, rpe, make_rate,
            form.get("technical_notes") or None,
            prescribed_weight, weight_deviation, rpe_deviation,
        ),
    )
    conn.commit()
