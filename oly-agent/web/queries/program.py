# web/queries/program.py
"""DB queries for the program view."""

# In-memory cache for exercise name → id lookups.
# exercises are static seed data that never change at runtime.
_exercise_id_cache: dict[str, int] = {}  # lower(name) → id


async def get_program(conn, program_id: int) -> dict | None:
    from web.async_db import async_fetch_one
    return await async_fetch_one(
        conn,
        "SELECT * FROM generated_programs WHERE id = $1",
        program_id,
    )


async def get_all_programs(conn, athlete_id: int) -> list[dict]:
    from web.async_db import async_fetch_all
    return await async_fetch_all(
        conn,
        """
        SELECT id, name, phase, status, start_date, duration_weeks, sessions_per_week,
               created_at, outcome_summary
        FROM generated_programs
        WHERE athlete_id = $1
        ORDER BY created_at DESC
        """,
        athlete_id,
    )


async def get_program_volume_by_week(conn, program_id: int) -> list[dict]:
    """Compute prescribed and actual weekly volume (sets × reps × weight_kg).

    Returns a list of {week, prescribed, actual} dicts sorted by week.
    actual is None for weeks with no logged exercises (renders as a gap in the chart).
    """
    from web.async_db import async_fetch_all

    se_rows = await async_fetch_all(
        conn,
        """
        SELECT ps.week_number, se.sets, se.reps, se.absolute_weight_kg
        FROM program_sessions ps
        JOIN session_exercises se ON se.session_id = ps.id
        WHERE ps.program_id = $1 AND se.absolute_weight_kg IS NOT NULL
        """,
        program_id,
    )

    tle_rows = await async_fetch_all(
        conn,
        """
        SELECT ps.week_number, tle.sets_completed, tle.reps_per_set, tle.weight_kg
        FROM program_sessions ps
        JOIN training_logs tl ON tl.session_id = ps.id
        JOIN training_log_exercises tle ON tle.log_id = tl.id
        WHERE ps.program_id = $1
          AND tle.weight_kg IS NOT NULL
          AND tle.sets_completed IS NOT NULL
        """,
        program_id,
    )

    prescribed: dict[int, float] = {}
    for row in se_rows:
        wk = row["week_number"]
        try:
            reps = int(str(row["reps"]).split(",")[0].split("-")[0].strip())
        except (ValueError, TypeError, AttributeError):
            continue
        prescribed[wk] = prescribed.get(wk, 0.0) + row["sets"] * reps * float(row["absolute_weight_kg"])

    actual: dict[int, float] = {}
    for row in tle_rows:
        wk = row["week_number"]
        rps = row["reps_per_set"]
        if not rps:
            continue
        try:
            avg_reps = sum(int(r) for r in rps) / len(rps)
        except (ValueError, TypeError):
            continue
        actual[wk] = actual.get(wk, 0.0) + row["sets_completed"] * avg_reps * float(row["weight_kg"])

    all_weeks = sorted(set(list(prescribed.keys()) + list(actual.keys())))
    return [
        {
            "week": wk,
            "prescribed": round(prescribed.get(wk, 0.0)),
            "actual": round(actual[wk]) if wk in actual else None,
        }
        for wk in all_weeks
    ]


async def get_program_weeks(conn, program_id: int) -> list[dict]:
    """Return sessions grouped by week, each with their exercises."""
    from web.async_db import async_fetch_all
    sessions = await async_fetch_all(
        conn,
        """
        SELECT ps.id, ps.week_number, ps.day_number, ps.session_label,
               ps.estimated_duration_minutes, ps.focus_area,
               tl.id AS log_id
        FROM program_sessions ps
        LEFT JOIN training_logs tl ON tl.session_id = ps.id
        WHERE ps.program_id = $1
        ORDER BY ps.week_number, ps.day_number
        """,
        program_id,
    )

    exercises_by_session: dict[int, list] = {}
    all_session_ids = [s["id"] for s in sessions]
    if all_session_ids:
        exercises = await async_fetch_all(
            conn,
            """
            SELECT session_id, exercise_order, exercise_name, sets, reps,
                   intensity_pct, absolute_weight_kg, rest_seconds, rpe_target,
                   selection_rationale
            FROM session_exercises
            WHERE session_id = ANY($1::int[])
            ORDER BY session_id, exercise_order
            """,
            all_session_ids,
        )
        for ex in exercises:
            exercises_by_session.setdefault(ex["session_id"], []).append(ex)

    # Group sessions by week
    weeks: dict[int, list] = {}
    for s in sessions:
        s["exercises"] = exercises_by_session.get(s["id"], [])
        weeks.setdefault(s["week_number"], []).append(s)

    return [{"week_number": wn, "sessions": ws} for wn, ws in sorted(weeks.items())]


async def complete_program(conn, program_id: int, athlete_id: int) -> dict:
    """Compute outcome metrics and mark program as completed.

    feedback.py uses psycopg2 internally, so we open a dedicated synchronous
    connection for that work and leave the asyncpg conn untouched.
    Returns the outcome dict (also persisted to generated_programs.outcome_summary).
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from feedback import compute_outcome, save_outcome
    from shared.db import get_connection
    from web.deps import get_settings

    sync_conn = get_connection(get_settings().database_url)
    try:
        outcome = compute_outcome(program_id, athlete_id, sync_conn)
        save_outcome(outcome, sync_conn)
        sync_conn.commit()
    finally:
        sync_conn.close()
    return outcome


async def delete_program(conn, program_id: int, athlete_id: int):
    """Permanently delete a program and all its sessions/exercises.

    Nulls out the back-references from training_logs and training_log_exercises
    first (those FKs have no ON DELETE CASCADE), then deletes the program row
    which cascades to program_sessions → session_exercises → generation_log.
    Training log entries themselves are preserved (just unlinked from the program).
    """
    from web.async_db import async_execute
    # Unlink training_log_exercises from session_exercises being deleted
    await async_execute(
        conn,
        """
        UPDATE training_log_exercises
        SET session_exercise_id = NULL
        WHERE session_exercise_id IN (
            SELECT se.id FROM session_exercises se
            JOIN program_sessions ps ON ps.id = se.session_id
            WHERE ps.program_id = $1
        )
        """,
        program_id,
    )
    # Unlink training_logs from sessions being deleted
    await async_execute(
        conn,
        """
        UPDATE training_logs
        SET session_id = NULL
        WHERE session_id IN (
            SELECT id FROM program_sessions WHERE program_id = $1
        )
        """,
        program_id,
    )
    # Delete the program — cascades to program_sessions, session_exercises, generation_log
    await async_execute(
        conn,
        "DELETE FROM generated_programs WHERE id = $1 AND athlete_id = $2",
        program_id, athlete_id,
    )


async def abandon_program(conn, program_id: int):
    from web.async_db import async_execute
    await async_execute(
        conn,
        "UPDATE generated_programs SET status = 'abandoned', updated_at = NOW() WHERE id = $1",
        program_id,
    )


async def get_athlete_maxes(conn, athlete_id: int) -> list[dict]:
    """Return recorded maxes plus estimated maxes for any missing exercises.

    Each row includes is_estimated=True/False so the template can style them differently.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from web.async_db import async_fetch_all
    from shared.exercise_mapping import EXERCISE_NAME_TO_INTENSITY_REF
    from weight_resolver import build_maxes_dict
    from assess import estimate_missing_maxes

    rows = await async_fetch_all(
        conn,
        """
        SELECT e.name AS exercise_name, am.weight_kg, am.date_achieved
        FROM athlete_maxes am
        JOIN exercises e ON am.exercise_id = e.id
        WHERE am.athlete_id = $1 AND am.max_type = 'current'
        ORDER BY e.name
        """,
        athlete_id,
    )
    result = [dict(r, is_estimated=False) for r in rows]

    known = build_maxes_dict([{"name": r["exercise_name"], "weight_kg": r["weight_kg"]} for r in rows])
    estimated = estimate_missing_maxes(known)
    ref_to_name = {v: k for k, v in EXERCISE_NAME_TO_INTENSITY_REF.items()}
    for ref, (kg, _) in sorted(estimated.items()):
        name = ref_to_name.get(ref, ref.replace("_", " ").title())
        result.append({
            "exercise_name": name,
            "weight_kg": kg,
            "date_achieved": None,
            "is_estimated": True,
        })

    result.sort(key=lambda r: r["exercise_name"])
    return result


async def delete_athlete_max(conn, athlete_id: int, exercise_name: str):
    from web.async_db import async_execute
    exercise_id = await _get_exercise_id(conn, exercise_name)
    if exercise_id is None:
        raise ValueError(f"Exercise '{exercise_name}' not found")
    await async_execute(
        conn,
        """
        DELETE FROM athlete_maxes
        WHERE athlete_id = $1 AND exercise_id = $2 AND max_type = 'current'
        """,
        athlete_id, exercise_id,
    )


async def _get_exercise_id(conn, exercise_name: str) -> int | None:
    """Look up exercise_id by name (case-insensitive), with in-process cache."""
    from web.async_db import async_fetch_all, async_fetch_one
    key = exercise_name.lower()
    if key in _exercise_id_cache:
        return _exercise_id_cache[key]
    # Cache miss: populate the full exercise name→id map in one query
    if not _exercise_id_cache:
        rows = await async_fetch_all(conn, "SELECT id, name FROM exercises")
        for row in rows:
            _exercise_id_cache[row["name"].lower()] = row["id"]
        if key in _exercise_id_cache:
            return _exercise_id_cache[key]
    # Fall back to single lookup (handles exercises added after cache was populated)
    row = await async_fetch_one(conn, "SELECT id FROM exercises WHERE LOWER(name) = LOWER($1)", exercise_name)
    if row:
        _exercise_id_cache[key] = row["id"]
        return row["id"]
    return None


async def _write_athlete_max(
    conn, athlete_id: int, exercise_id: int, weight_kg: float, date_achieved, notes: str | None = None
):
    """Upsert the 'current' max row for (athlete_id, exercise_id).

    When notes is None the existing notes value is preserved (COALESCE).
    """
    from web.async_db import async_execute
    await async_execute(
        conn,
        """
        INSERT INTO athlete_maxes (athlete_id, exercise_id, weight_kg, max_type, date_achieved, notes)
        VALUES ($1, $2, $3, 'current', $4, $5)
        ON CONFLICT (athlete_id, exercise_id) WHERE max_type = 'current'
        DO UPDATE SET weight_kg     = EXCLUDED.weight_kg,
                      date_achieved = EXCLUDED.date_achieved,
                      notes         = COALESCE(EXCLUDED.notes, athlete_maxes.notes)
        """,
        athlete_id, exercise_id, weight_kg, date_achieved, notes,
    )


async def upsert_athlete_max(
    conn, athlete_id: int, exercise_name: str, weight_kg: float, date_achieved
) -> tuple[bool, float | None]:
    """Insert or update the 'current' max for a given exercise name.

    Looks up exercise_id from the exercises table by name (case-insensitive).
    Raises ValueError if the exercise name is not found.
    Returns (is_pr, previous_kg) — is_pr is True when weight_kg beats the previous record.
    """
    from web.async_db import async_fetch_one
    exercise_id = await _get_exercise_id(conn, exercise_name)
    if exercise_id is None:
        raise ValueError(f"Exercise '{exercise_name}' not found in exercises table")

    existing = await async_fetch_one(
        conn,
        "SELECT weight_kg FROM athlete_maxes WHERE athlete_id = $1 AND exercise_id = $2 AND max_type = 'current'",
        athlete_id, exercise_id,
    )
    prev_kg = float(existing["weight_kg"]) if existing else None
    is_pr = prev_kg is None or weight_kg > prev_kg

    await _write_athlete_max(conn, athlete_id, exercise_id, weight_kg, date_achieved)
    return is_pr, prev_kg


async def activate_program(conn, program_id: int, athlete_id: int):
    from web.async_db import async_execute
    # Supersede any currently active program
    await async_execute(
        conn,
        """
        UPDATE generated_programs SET status = 'superseded', updated_at = NOW()
        WHERE athlete_id = $1 AND status = 'active' AND id != $2
        """,
        athlete_id, program_id,
    )
    await async_execute(
        conn,
        "UPDATE generated_programs SET status = 'active', updated_at = NOW() WHERE id = $1",
        program_id,
    )
