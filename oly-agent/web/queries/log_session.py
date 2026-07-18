# web/queries/log_session.py
"""DB queries for session logging."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from web.formparse import parse_float as _float  # noqa: F401 (WEB-L4)
from web.formparse import parse_int as _int

from shared.constants import MAX_LOG_BACKFILL_DAYS


def _parse_reps(raw) -> list[int]:
    """Parse the comma-separated reps field into a bounded int list.

    Entries outside 1..999 invalidate the whole list — a huge Python int parses
    fine but overflows the INT[] column into a 500 (audit3-L2).
    """
    try:
        reps = [int(r.strip()) for r in (raw or "").strip().split(",") if r.strip()]
    except (ValueError, AttributeError):
        return []
    if any(r < 1 or r > 999 for r in reps):
        return []
    return reps


def _parse_log_date(form: dict, today: date | None = None) -> date:
    """Parse the submitted log date, clamping out-of-range values to today.

    A training log can't be in the future and shouldn't be absurdly far in the
    past — an unparseable or out-of-window date (e.g. a typo'd '3000-01-01')
    falls back to today rather than being stored as-is (W-L7).

    `today` is the ATHLETE's local today (W-L5): clamping against the server's
    calendar re-dated a valid athlete-local date to yesterday for anyone east
    of the server (WEB-M2).
    """
    today = today or date.today()
    log_date_str = form.get("log_date") or str(today)
    try:
        parsed = date.fromisoformat(log_date_str)
    except ValueError:
        return today
    earliest = today - timedelta(days=MAX_LOG_BACKFILL_DAYS)
    if parsed > today or parsed < earliest:
        return today
    return parsed


async def get_session_with_exercises(conn, session_id: int) -> dict | None:
    from web.async_db import async_fetch_all, async_fetch_one
    session = await async_fetch_one(
        conn,
        """
        SELECT ps.id, ps.week_number, ps.day_number, ps.session_label,
               ps.estimated_duration_minutes, ps.focus_area, ps.program_id,
               gp.name AS program_name, gp.athlete_id
        FROM program_sessions ps
        JOIN generated_programs gp ON gp.id = ps.program_id
        WHERE ps.id = $1
        """,
        session_id,
    )
    if not session:
        return None

    exercises = await async_fetch_all(
        conn,
        """
        SELECT id, exercise_order, exercise_name, sets, reps,
               intensity_pct, absolute_weight_kg, rest_seconds, rpe_target
        FROM session_exercises
        WHERE session_id = $1
        ORDER BY exercise_order
        """,
        session_id,
    )
    session = dict(session)
    session["exercises"] = exercises
    return session


async def get_existing_log(conn, session_id: int) -> dict | None:
    from web.async_db import async_fetch_one
    # ORDER BY id: deterministic if legacy duplicate rows exist (WEB-L3)
    return await async_fetch_one(
        conn,
        "SELECT * FROM training_logs WHERE session_id = $1 ORDER BY id LIMIT 1",
        session_id,
    )


async def get_exercise_log_entry(conn, tle_id: int, log_id: int) -> dict | None:
    """Scoped by log_id — the caller has ownership-checked the log, so a
    sequential tle_id belonging to another athlete must not be returned (WEB-H1)."""
    from web.async_db import async_fetch_one
    return await async_fetch_one(
        conn,
        """
        SELECT id, session_exercise_id, exercise_name, sets_completed,
               reps_per_set, weight_kg, rpe, make_rate, technical_notes,
               prescribed_weight_kg, weight_deviation_kg, rpe_deviation
        FROM training_log_exercises
        WHERE id = $1 AND log_id = $2
        """,
        tle_id, log_id,
    )


async def update_exercise_log(conn, tle_id: int, form: dict, log_id: int):
    """Update a training_log_exercises row from form values, recomputing deviations.

    Scoped by log_id so a tle_id belonging to another athlete's log cannot be modified.
    """
    from web.async_db import async_execute, async_fetch_one

    weight_kg = _float(form.get("weight_kg"))
    rpe = _float(form.get("rpe"))
    make_rate_raw = _float(form.get("make_rate"))
    make_rate = make_rate_raw / 100.0 if make_rate_raw is not None else None

    reps_per_set = _parse_reps(form.get("reps_per_set"))

    # Fetch stored prescribed values to recompute deviations
    existing = await async_fetch_one(
        conn,
        "SELECT prescribed_weight_kg, session_exercise_id FROM training_log_exercises WHERE id = $1 AND log_id = $2",
        tle_id, log_id,
    )
    prescribed_weight = float(existing["prescribed_weight_kg"]) if existing and existing["prescribed_weight_kg"] else None
    prescribed_rpe = None
    if existing and existing["session_exercise_id"]:
        se = await async_fetch_one(
            conn,
            "SELECT rpe_target FROM session_exercises WHERE id = $1",
            existing["session_exercise_id"],
        )
        if se and se["rpe_target"] is not None:
            prescribed_rpe = float(se["rpe_target"])

    weight_deviation = round(weight_kg - prescribed_weight, 2) if (weight_kg and prescribed_weight) else None
    rpe_deviation = round(rpe - prescribed_rpe, 1) if (rpe and prescribed_rpe) else None

    # NOT NULL columns — same defaults as create_exercise_log (WEB-M5)
    sets_completed = _int(form.get("sets_completed")) or (len(reps_per_set) if reps_per_set else 1)
    if weight_kg is None:
        weight_kg = 0.0

    await async_execute(
        conn,
        """
        UPDATE training_log_exercises
        SET sets_completed = $1, reps_per_set = $2, weight_kg = $3, rpe = $4,
            make_rate = $5, technical_notes = $6,
            weight_deviation_kg = $7, rpe_deviation = $8
        WHERE id = $9 AND log_id = $10
        """,
        sets_completed,
        reps_per_set or None,
        weight_kg, rpe, make_rate,
        form.get("technical_notes") or None,
        weight_deviation, rpe_deviation,
        tle_id, log_id,
    )


async def get_log_by_id(conn, log_id: int) -> dict | None:
    from web.async_db import async_fetch_one
    return await async_fetch_one(conn, "SELECT * FROM training_logs WHERE id = $1", log_id)


async def delete_exercise_log(conn, tle_id: int, log_id: int):
    from web.async_db import async_execute
    await async_execute(
        conn,
        "DELETE FROM training_log_exercises WHERE id = $1 AND log_id = $2",
        tle_id, log_id,
    )


async def get_logged_exercises(conn, log_id: int) -> list[dict]:
    from web.async_db import async_fetch_all
    return await async_fetch_all(
        conn,
        """
        SELECT id, session_exercise_id, exercise_name, sets_completed,
               reps_per_set, weight_kg, rpe, make_rate, technical_notes,
               prescribed_weight_kg, weight_deviation_kg, rpe_deviation
        FROM training_log_exercises
        WHERE log_id = $1
        ORDER BY id
        """,
        log_id,
    )


async def create_session_log(conn, athlete_id: int, session_id: int, form: dict, today: date | None = None) -> int:
    from web.async_db import async_execute_returning

    log_date = _parse_log_date(form, today)

    # ON CONFLICT: a double-submit race (two INSERTs past the existence check)
    # updates the row instead of raising on the partial unique index (WEB-L3).
    return await async_execute_returning(
        conn,
        """
        INSERT INTO training_logs
            (athlete_id, session_id, log_date, overall_rpe,
             session_duration_minutes, bodyweight_kg, sleep_quality,
             stress_level, athlete_notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (session_id) WHERE session_id IS NOT NULL DO UPDATE
            SET log_date = EXCLUDED.log_date,
                overall_rpe = EXCLUDED.overall_rpe,
                session_duration_minutes = EXCLUDED.session_duration_minutes,
                bodyweight_kg = EXCLUDED.bodyweight_kg,
                sleep_quality = EXCLUDED.sleep_quality,
                stress_level = EXCLUDED.stress_level,
                athlete_notes = EXCLUDED.athlete_notes
        RETURNING id
        """,
        athlete_id,
        session_id,
        log_date,
        _float(form.get("overall_rpe")),
        _int(form.get("duration"), lo=1),
        _float(form.get("bodyweight")),
        # CHECK (BETWEEN 1 AND 5) — out-of-range stores NULL, not a 500 (audit2-L3)
        _int(form.get("sleep_quality"), lo=1, hi=5),
        _int(form.get("stress_level"), lo=1, hi=5),
        form.get("notes") or None,
    )


async def maybe_promote_max(
    conn, athlete_id: int, session_exercise_id: int, weight_kg: float, log_date
) -> bool:
    """If this session exercise is a max attempt, upsert athlete_maxes when weight is a new PR.

    Returns True if a new max was recorded.
    """
    from web.async_db import async_fetch_one
    from web.queries.program import _write_athlete_max

    se = await async_fetch_one(
        conn,
        "SELECT exercise_id, is_max_attempt FROM session_exercises WHERE id = $1",
        session_exercise_id,
    )
    if not se or not se["is_max_attempt"] or not se["exercise_id"]:
        return False

    exercise_id = se["exercise_id"]

    current = await async_fetch_one(
        conn,
        "SELECT weight_kg FROM athlete_maxes WHERE athlete_id = $1 AND exercise_id = $2 AND max_type = 'current'",
        athlete_id, exercise_id,
    )
    if current and float(current["weight_kg"]) >= weight_kg:
        return False

    await _write_athlete_max(
        conn, athlete_id, exercise_id, weight_kg, log_date,
        notes="Auto-promoted from max testing session",
    )
    return True


async def update_session_log(conn, log_id: int, form: dict, today: date | None = None):
    """Update an existing training_log row with new form values."""
    from web.async_db import async_execute

    log_date = _parse_log_date(form, today)

    await async_execute(
        conn,
        """
        UPDATE training_logs
        SET log_date = $1, overall_rpe = $2, session_duration_minutes = $3,
            bodyweight_kg = $4, sleep_quality = $5, stress_level = $6,
            athlete_notes = $7
        WHERE id = $8
        """,
        log_date,
        _float(form.get("overall_rpe")),
        _int(form.get("duration"), lo=1),
        _float(form.get("bodyweight")),
        _int(form.get("sleep_quality"), lo=1, hi=5),
        _int(form.get("stress_level"), lo=1, hi=5),
        form.get("notes") or None,
        log_id,
    )


async def create_exercise_log(conn, log_id: int, form: dict) -> int:
    """Insert a new training_log_exercises row. Returns the new row id."""
    from web.async_db import async_execute_returning, async_fetch_one

    se_id = _int(form.get("session_exercise_id"))
    if se_id is not None:
        # The id comes from the client — only link it when it actually belongs
        # to this log's session, else a fabricated/cross-tenant reference lands
        # in the FK and skews deviation/make-rate stats (WEB-L9).
        linked = await async_fetch_one(
            conn,
            """
            SELECT 1 FROM session_exercises se
            JOIN training_logs tl ON tl.session_id = se.session_id
            WHERE se.id = $1 AND tl.id = $2
            """,
            se_id, log_id,
        )
        if linked is None:
            se_id = None
    exercise_name = form.get("exercise_name", "").strip()
    weight_kg = _float(form.get("weight_kg"))
    rpe = _float(form.get("rpe"))
    prescribed_weight = _float(form.get("prescribed_weight_kg"))
    prescribed_rpe = _float(form.get("prescribed_rpe"))

    reps_per_set = _parse_reps(form.get("reps_per_set"))

    make_rate_raw = _float(form.get("make_rate"))
    make_rate = make_rate_raw / 100.0 if make_rate_raw is not None else None

    weight_deviation = round(weight_kg - prescribed_weight, 2) if (weight_kg and prescribed_weight) else None
    rpe_deviation = round(rpe - prescribed_rpe, 1) if (rpe and prescribed_rpe) else None

    # Both columns are NOT NULL — a blank Sets or Weight field (bodyweight
    # accessory) must default, not 500 on the constraint (WEB-M5). Weight 0 =
    # unloaded; sets default to the number of rep entries when present.
    sets_completed = _int(form.get("sets_completed")) or (len(reps_per_set) if reps_per_set else 1)
    if weight_kg is None:
        weight_kg = 0.0

    return await async_execute_returning(
        conn,
        """
        INSERT INTO training_log_exercises
            (log_id, session_exercise_id, exercise_name, sets_completed,
             reps_per_set, weight_kg, rpe, make_rate, technical_notes,
             prescribed_weight_kg, weight_deviation_kg, rpe_deviation)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING id
        """,
        log_id, se_id, exercise_name,
        sets_completed,
        reps_per_set or None,
        weight_kg, rpe, make_rate,
        form.get("technical_notes") or None,
        prescribed_weight, weight_deviation, rpe_deviation,
    )
