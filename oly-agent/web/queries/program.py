# web/queries/program.py
"""DB queries for the program view."""

# In-memory cache for exercise name → id lookups.
# exercises are static seed data that never change at runtime.
_exercise_id_cache: dict[str, int] = {}  # lower(name) → id


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


def complete_program(conn, program_id: int, athlete_id: int) -> dict:
    """Compute outcome metrics and mark program as completed.

    Returns the outcome dict (also persisted to generated_programs.outcome_summary).
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from feedback import compute_outcome, save_outcome

    outcome = compute_outcome(program_id, athlete_id, conn)
    save_outcome(outcome, conn)
    return outcome


def abandon_program(conn, program_id: int):
    from shared.db import execute
    execute(
        conn,
        "UPDATE generated_programs SET status = 'abandoned', updated_at = NOW() WHERE id = %s",
        (program_id,),
    )
    conn.commit()


def get_athlete_maxes(conn, athlete_id: int) -> list[dict]:
    from shared.db import fetch_all
    return fetch_all(
        conn,
        """
        SELECT e.name AS exercise_name, am.weight_kg, am.date_achieved
        FROM athlete_maxes am
        JOIN exercises e ON am.exercise_id = e.id
        WHERE am.athlete_id = %s AND am.max_type = 'current'
        ORDER BY e.name
        """,
        (athlete_id,),
    )


def _get_exercise_id(conn, exercise_name: str) -> int | None:
    """Look up exercise_id by name (case-insensitive), with in-process cache."""
    from shared.db import fetch_all, fetch_one
    key = exercise_name.lower()
    if key in _exercise_id_cache:
        return _exercise_id_cache[key]
    # Cache miss: populate the full exercise name→id map in one query
    if not _exercise_id_cache:
        rows = fetch_all(conn, "SELECT id, name FROM exercises")
        for row in rows:
            _exercise_id_cache[row["name"].lower()] = row["id"]
        if key in _exercise_id_cache:
            return _exercise_id_cache[key]
    # Fall back to single lookup (handles exercises added after cache was populated)
    row = fetch_one(conn, "SELECT id FROM exercises WHERE LOWER(name) = LOWER(%s)", (exercise_name,))
    if row:
        _exercise_id_cache[key] = row["id"]
        return row["id"]
    return None


def upsert_athlete_max(
    conn, athlete_id: int, exercise_name: str, weight_kg: float, date_achieved
):
    """Insert or update the 'current' max for a given exercise name.

    Looks up exercise_id from the exercises table by name (case-insensitive).
    Raises ValueError if the exercise name is not found.
    """
    from shared.db import execute
    exercise_id = _get_exercise_id(conn, exercise_name)
    if exercise_id is None:
        raise ValueError(f"Exercise '{exercise_name}' not found in exercises table")
    execute(
        conn,
        """
        INSERT INTO athlete_maxes (athlete_id, exercise_id, weight_kg, date_achieved, max_type)
        VALUES (%s, %s, %s, %s, 'current')
        ON CONFLICT (athlete_id, exercise_id) WHERE max_type = 'current'
        DO UPDATE SET weight_kg = EXCLUDED.weight_kg,
                      date_achieved = EXCLUDED.date_achieved
        """,
        (athlete_id, exercise_id, weight_kg, date_achieved),
    )
    conn.commit()


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
