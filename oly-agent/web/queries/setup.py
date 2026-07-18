# web/queries/setup.py
"""DB queries for athlete account creation (setup wizard)."""

from datetime import date

from web.formparse import parse_float as _float  # finite + bounded (WEB-L4)
from web.formparse import parse_int as _int


def _date(v):
    """Parse an ISO date string to datetime.date — asyncpg rejects raw strings
    on DATE params (WEB-H3). Unparseable/blank values become NULL."""
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(v)
    except (ValueError, TypeError):
        return None


async def username_taken(conn, username: str) -> bool:
    from web.async_db import async_fetch_one
    row = await async_fetch_one(conn, "SELECT 1 FROM athletes WHERE username = $1", username)
    return row is not None


async def create_athlete(conn, data: dict, password_hash: str) -> int:
    """Insert a new athlete row (with credentials) and return the new athlete_id."""
    from web.async_db import async_execute_returning

    return await async_execute_returning(
        conn,
        """
        INSERT INTO athletes (
            name, email, level, biological_sex,
            bodyweight_kg, height_cm, date_of_birth, weight_class,
            training_age_years, sessions_per_week, session_duration_minutes,
            available_equipment, injuries, technical_faults,
            username, password_hash, notes,
            lift_emphasis, strength_limiters, competition_experience
        )
        VALUES ($1,$2,$3,$4, $5,$6,$7,$8, $9,$10,$11, $12,$13,$14, $15,$16,$17, $18,$19,$20)
        RETURNING id
        """,
        data["name"],
        data.get("email") or None,
        data["level"],
        data.get("biological_sex") or None,
        _float(data.get("bodyweight_kg")),
        _float(data.get("height_cm")),
        _date(data.get("date_of_birth")),
        data.get("weight_class") or None,
        _float(data.get("training_age_years")),
        # CHECK BETWEEN 1 AND 14 — out-of-range falls back to the default (audit3-M2)
        _int(data.get("sessions_per_week"), lo=1, hi=14) or 4,
        _int(data.get("session_duration_minutes"), lo=1) or 90,
        data.get("available_equipment") or [],
        data.get("injuries") or None,
        data.get("technical_faults") or [],
        data["username"],
        password_hash,
        data.get("notes") or None,
        data.get("lift_emphasis") or "balanced",
        data.get("strength_limiters") or [],
        data.get("competition_experience") or "none",
    )


async def create_maxes(conn, athlete_id: int, name_weight_pairs: list[tuple[str, float]]):
    """Bulk-insert current maxes for a new athlete, looking up exercise IDs by name."""
    from web.async_db import async_execute, async_fetch_all

    if not name_weight_pairs:
        return

    names = [name for name, _ in name_weight_pairs]
    rows = await async_fetch_all(conn, "SELECT id, name FROM exercises WHERE name = ANY($1)", names)
    name_to_id = {r["name"]: r["id"] for r in rows}

    today = date.today()
    for exercise_name, weight_kg in name_weight_pairs:
        exercise_id = name_to_id.get(exercise_name)
        if not exercise_id:
            continue
        await async_execute(
            conn,
            """
            INSERT INTO athlete_maxes (athlete_id, exercise_id, weight_kg, max_type, date_achieved)
            VALUES ($1, $2, $3, 'current', $4)
            ON CONFLICT (athlete_id, exercise_id) WHERE max_type = 'current'
            DO UPDATE SET weight_kg = EXCLUDED.weight_kg, date_achieved = EXCLUDED.date_achieved
            """,
            athlete_id, exercise_id, weight_kg, today,
        )


async def create_goal(
    conn,
    athlete_id: int,
    goal_type: str,
    competition_date=None,
    target_snatch_kg=None,
    target_cj_kg=None,
):
    from web.async_db import async_execute

    await async_execute(
        conn,
        """
        INSERT INTO athlete_goals
            (athlete_id, goal, competition_date, target_snatch_kg, target_cj_kg, is_active, priority)
        VALUES ($1, $2, $3, $4, $5, TRUE, 1)
        """,
        athlete_id,
        goal_type,
        _date(competition_date),
        _float(target_snatch_kg),
        _float(target_cj_kg),
    )
