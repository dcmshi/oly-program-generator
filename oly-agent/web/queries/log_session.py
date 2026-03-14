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


def get_exercise_log_entry(conn, tle_id: int) -> dict | None:
    from shared.db import fetch_one
    return fetch_one(
        conn,
        """
        SELECT id, session_exercise_id, exercise_name, sets_completed,
               reps_per_set, weight_kg, rpe, make_rate, technical_notes,
               prescribed_weight_kg, weight_deviation_kg, rpe_deviation
        FROM training_log_exercises
        WHERE id = %s
        """,
        (tle_id,),
    )


def update_exercise_log(conn, tle_id: int, form: dict):
    """Update a training_log_exercises row from form values, recomputing deviations."""
    from shared.db import execute, fetch_one

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

    weight_kg = _float(form.get("weight_kg"))
    rpe = _float(form.get("rpe"))
    make_rate_raw = _float(form.get("make_rate"))
    make_rate = make_rate_raw / 100.0 if make_rate_raw is not None else None

    reps_raw = form.get("reps_per_set", "").strip()
    try:
        reps_per_set = [int(r.strip()) for r in reps_raw.split(",") if r.strip()]
    except (ValueError, AttributeError):
        reps_per_set = []

    # Fetch stored prescribed values to recompute deviations
    existing = fetch_one(
        conn,
        "SELECT prescribed_weight_kg, session_exercise_id FROM training_log_exercises WHERE id = %s",
        (tle_id,),
    )
    prescribed_weight = float(existing["prescribed_weight_kg"]) if existing and existing["prescribed_weight_kg"] else None
    prescribed_rpe = None
    if existing and existing["session_exercise_id"]:
        se = fetch_one(
            conn,
            "SELECT rpe_target FROM session_exercises WHERE id = %s",
            (existing["session_exercise_id"],),
        )
        if se and se["rpe_target"] is not None:
            prescribed_rpe = float(se["rpe_target"])

    weight_deviation = round(weight_kg - prescribed_weight, 2) if (weight_kg and prescribed_weight) else None
    rpe_deviation = round(rpe - prescribed_rpe, 1) if (rpe and prescribed_rpe) else None

    execute(
        conn,
        """
        UPDATE training_log_exercises
        SET sets_completed = %s, reps_per_set = %s, weight_kg = %s, rpe = %s,
            make_rate = %s, technical_notes = %s,
            weight_deviation_kg = %s, rpe_deviation = %s
        WHERE id = %s
        """,
        (
            _int(form.get("sets_completed")),
            reps_per_set or None,
            weight_kg, rpe, make_rate,
            form.get("technical_notes") or None,
            weight_deviation, rpe_deviation,
            tle_id,
        ),
    )
    conn.commit()


def delete_exercise_log(conn, tle_id: int):
    from shared.db import execute
    execute(conn, "DELETE FROM training_log_exercises WHERE id = %s", (tle_id,))
    conn.commit()


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


def maybe_promote_max(
    conn, athlete_id: int, session_exercise_id: int, weight_kg: float, log_date
) -> bool:
    """If this session exercise is a max attempt, upsert athlete_maxes when weight is a new PR.

    Returns True if a new max was recorded.
    """
    from shared.db import fetch_one, execute

    se = fetch_one(
        conn,
        "SELECT exercise_id, is_max_attempt FROM session_exercises WHERE id = %s",
        (session_exercise_id,),
    )
    if not se or not se["is_max_attempt"] or not se["exercise_id"]:
        return False

    exercise_id = se["exercise_id"]

    current = fetch_one(
        conn,
        """
        SELECT weight_kg FROM athlete_maxes
        WHERE athlete_id = %s AND exercise_id = %s AND max_type = 'current'
        """,
        (athlete_id, exercise_id),
    )
    if current and float(current["weight_kg"]) >= weight_kg:
        return False

    execute(
        conn,
        """
        INSERT INTO athlete_maxes
            (athlete_id, exercise_id, weight_kg, max_type, date_achieved, notes)
        VALUES (%s, %s, %s, 'current', %s, 'Auto-promoted from max testing session')
        ON CONFLICT (athlete_id, exercise_id) WHERE max_type = 'current'
        DO UPDATE SET
            weight_kg    = EXCLUDED.weight_kg,
            date_achieved = EXCLUDED.date_achieved,
            notes        = EXCLUDED.notes
        """,
        (athlete_id, exercise_id, weight_kg, log_date),
    )
    conn.commit()
    return True


def update_session_log(conn, log_id: int, form: dict):
    """Update an existing training_log row with new form values."""
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

    log_date_str = form.get("log_date") or str(date.today())
    try:
        log_date = date.fromisoformat(log_date_str)
    except ValueError:
        log_date = date.today()

    execute(
        conn,
        """
        UPDATE training_logs
        SET log_date = %s, overall_rpe = %s, session_duration_minutes = %s,
            bodyweight_kg = %s, sleep_quality = %s, stress_level = %s,
            athlete_notes = %s
        WHERE id = %s
        """,
        (
            log_date,
            _float(form.get("overall_rpe")),
            _int(form.get("duration")),
            _float(form.get("bodyweight")),
            _int(form.get("sleep_quality")),
            _int(form.get("stress_level")),
            form.get("notes") or None,
            log_id,
        ),
    )
    conn.commit()


def create_exercise_log(conn, log_id: int, form: dict) -> int:
    """Insert a new training_log_exercises row. Returns the new row id."""
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

    tle_id = execute_returning(
        conn,
        """
        INSERT INTO training_log_exercises
            (log_id, session_exercise_id, exercise_name, sets_completed,
             reps_per_set, weight_kg, rpe, make_rate, technical_notes,
             prescribed_weight_kg, weight_deviation_kg, rpe_deviation)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
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
    return tle_id
