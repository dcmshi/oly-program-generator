# web/queries/profile.py
"""DB queries for athlete profile viewing and editing."""


def get_athlete(conn, athlete_id: int) -> dict | None:
    from shared.db import fetch_one
    return fetch_one(
        conn,
        """
        SELECT id, name, email, username, level, biological_sex,
               bodyweight_kg, height_cm, date_of_birth, weight_class,
               training_age_years, sessions_per_week, session_duration_minutes,
               available_equipment, technical_faults, injuries, notes
        FROM athletes
        WHERE id = %s
        """,
        (athlete_id,),
    )


def update_profile(conn, athlete_id: int, data: dict):
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

    execute(
        conn,
        """
        UPDATE athletes SET
            name                    = %s,
            email                   = %s,
            level                   = %s,
            biological_sex          = %s,
            date_of_birth           = %s,
            bodyweight_kg           = %s,
            height_cm               = %s,
            weight_class            = %s,
            training_age_years      = %s,
            sessions_per_week       = %s,
            session_duration_minutes= %s,
            available_equipment     = %s,
            technical_faults        = %s,
            injuries                = %s,
            notes                   = %s,
            updated_at              = NOW()
        WHERE id = %s
        """,
        (
            data["name"],
            data.get("email") or None,
            data["level"],
            data.get("biological_sex") or None,
            data.get("date_of_birth") or None,
            _float(data.get("bodyweight_kg")),
            _float(data.get("height_cm")),
            data.get("weight_class") or None,
            _float(data.get("training_age_years")),
            _int(data.get("sessions_per_week")) or 4,
            _int(data.get("session_duration_minutes")) or 90,
            data.get("available_equipment") or [],
            data.get("technical_faults") or [],
            data.get("injuries") or None,
            data.get("notes") or None,
            athlete_id,
        ),
    )


def update_password(conn, athlete_id: int, new_hash: str):
    from shared.db import execute
    execute(
        conn,
        "UPDATE athletes SET password_hash = %s, updated_at = NOW() WHERE id = %s",
        (new_hash, athlete_id),
    )


def update_username(conn, athlete_id: int, new_username: str):
    from shared.db import fetch_one, execute
    existing = fetch_one(
        conn,
        "SELECT id FROM athletes WHERE username = %s AND id != %s",
        (new_username, athlete_id),
    )
    if existing:
        raise ValueError(f"Username '{new_username}' is already taken.")
    execute(
        conn,
        "UPDATE athletes SET username = %s, updated_at = NOW() WHERE id = %s",
        (new_username, athlete_id),
    )
