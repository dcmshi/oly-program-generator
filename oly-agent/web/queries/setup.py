# web/queries/setup.py
"""DB queries for athlete account creation (setup wizard)."""

from datetime import date


def username_taken(conn, username: str) -> bool:
    from shared.db import fetch_one
    row = fetch_one(conn, "SELECT 1 FROM athletes WHERE username = %s", (username,))
    return row is not None


def create_athlete(conn, data: dict, password_hash: str) -> int:
    """Insert a new athlete row (with credentials) and return the new athlete_id."""
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

    return execute_returning(
        conn,
        """
        INSERT INTO athletes (
            name, email, level, biological_sex,
            bodyweight_kg, height_cm, age, weight_class,
            training_age_years, sessions_per_week, session_duration_minutes,
            available_equipment, injuries, technical_faults,
            username, password_hash, notes
        )
        VALUES (%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s)
        RETURNING id
        """,
        (
            data["name"],
            data.get("email") or None,
            data["level"],
            data.get("biological_sex") or None,
            _float(data.get("bodyweight_kg")),
            _float(data.get("height_cm")),
            _int(data.get("age")),
            data.get("weight_class") or None,
            _float(data.get("training_age_years")),
            _int(data.get("sessions_per_week")) or 4,
            _int(data.get("session_duration_minutes")) or 90,
            data.get("available_equipment") or [],
            data.get("injuries") or None,
            data.get("technical_faults") or [],
            data["username"],
            password_hash,
            data.get("notes") or None,
        ),
    )


def create_maxes(conn, athlete_id: int, name_weight_pairs: list[tuple[str, float]]):
    """Bulk-insert current maxes for a new athlete, looking up exercise IDs by name."""
    from shared.db import fetch_all, execute

    if not name_weight_pairs:
        return

    names = [name for name, _ in name_weight_pairs]
    rows = fetch_all(conn, "SELECT id, name FROM exercises WHERE name = ANY(%s)", (names,))
    name_to_id = {r["name"]: r["id"] for r in rows}

    today = date.today()
    for exercise_name, weight_kg in name_weight_pairs:
        exercise_id = name_to_id.get(exercise_name)
        if not exercise_id:
            continue
        execute(
            conn,
            """
            INSERT INTO athlete_maxes (athlete_id, exercise_id, weight_kg, max_type, date_achieved)
            VALUES (%s, %s, %s, 'current', %s)
            ON CONFLICT (athlete_id, exercise_id) WHERE max_type = 'current'
            DO UPDATE SET weight_kg = EXCLUDED.weight_kg, date_achieved = EXCLUDED.date_achieved
            """,
            (athlete_id, exercise_id, weight_kg, today),
        )


def create_goal(
    conn,
    athlete_id: int,
    goal_type: str,
    competition_date=None,
    target_snatch_kg=None,
    target_cj_kg=None,
):
    from shared.db import execute

    def _float(v):
        try:
            return float(v) if v else None
        except (ValueError, TypeError):
            return None

    def _date(v):
        if not v:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None

    execute(
        conn,
        """
        INSERT INTO athlete_goals
            (athlete_id, goal, competition_date, target_snatch_kg, target_cj_kg, is_active, priority)
        VALUES (%s, %s, %s, %s, %s, TRUE, 1)
        """,
        (
            athlete_id,
            goal_type,
            _date(competition_date),
            _float(target_snatch_kg),
            _float(target_cj_kg),
        ),
    )
