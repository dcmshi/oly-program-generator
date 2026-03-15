# web/queries/profile.py
"""DB queries for athlete profile viewing and editing."""


async def get_athlete(conn, athlete_id: int) -> dict | None:
    from web.async_db import async_fetch_one
    return await async_fetch_one(
        conn,
        """
        SELECT id, name, email, username, level, biological_sex,
               bodyweight_kg, height_cm, date_of_birth, weight_class,
               training_age_years, sessions_per_week, session_duration_minutes,
               available_equipment, technical_faults, injuries, notes,
               lift_emphasis, strength_limiters, competition_experience
        FROM athletes
        WHERE id = $1
        """,
        athlete_id,
    )


async def get_active_goal(conn, athlete_id: int) -> dict | None:
    from web.async_db import async_fetch_one
    return await async_fetch_one(
        conn,
        """
        SELECT id, goal, competition_date, competition_name,
               target_snatch_kg, target_cj_kg, target_total_kg, notes
        FROM athlete_goals
        WHERE athlete_id = $1 AND is_active = TRUE
        ORDER BY priority DESC, id DESC
        LIMIT 1
        """,
        athlete_id,
    )


async def upsert_goal(conn, athlete_id: int, data: dict):
    """Update the active goal row if one exists, otherwise insert a new one."""
    from web.async_db import async_fetch_one, async_execute

    def _float(v):
        try:
            return float(v) if v else None
        except (ValueError, TypeError):
            return None

    existing = await async_fetch_one(
        conn,
        "SELECT id FROM athlete_goals WHERE athlete_id = $1 AND is_active = TRUE ORDER BY priority DESC, id DESC LIMIT 1",
        athlete_id,
    )

    if existing:
        await async_execute(
            conn,
            """
            UPDATE athlete_goals SET
                goal               = $1,
                competition_date   = $2,
                competition_name   = $3,
                target_snatch_kg   = $4,
                target_cj_kg       = $5,
                target_total_kg    = $6,
                notes              = $7
            WHERE id = $8
            """,
            data["goal"],
            data.get("competition_date") or None,
            data.get("competition_name") or None,
            _float(data.get("target_snatch_kg")),
            _float(data.get("target_cj_kg")),
            _float(data.get("target_total_kg")),
            data.get("notes") or None,
            existing["id"],
        )
    else:
        await async_execute(
            conn,
            """
            INSERT INTO athlete_goals
                (athlete_id, goal, competition_date, competition_name,
                 target_snatch_kg, target_cj_kg, target_total_kg, notes, is_active, priority)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, 1)
            """,
            athlete_id,
            data["goal"],
            data.get("competition_date") or None,
            data.get("competition_name") or None,
            _float(data.get("target_snatch_kg")),
            _float(data.get("target_cj_kg")),
            _float(data.get("target_total_kg")),
            data.get("notes") or None,
        )


async def update_profile(conn, athlete_id: int, data: dict):
    from web.async_db import async_execute

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

    await async_execute(
        conn,
        """
        UPDATE athletes SET
            name                    = $1,
            email                   = $2,
            level                   = $3,
            biological_sex          = $4,
            date_of_birth           = $5,
            bodyweight_kg           = $6,
            height_cm               = $7,
            weight_class            = $8,
            training_age_years      = $9,
            sessions_per_week       = $10,
            session_duration_minutes= $11,
            available_equipment     = $12,
            technical_faults        = $13,
            injuries                = $14,
            notes                   = $15,
            lift_emphasis           = $16,
            strength_limiters       = $17,
            competition_experience  = $18,
            updated_at              = NOW()
        WHERE id = $19
        """,
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
        data.get("lift_emphasis") or "balanced",
        data.get("strength_limiters") or [],
        data.get("competition_experience") or "none",
        athlete_id,
    )


async def update_password(conn, athlete_id: int, new_hash: str):
    from web.async_db import async_execute
    await async_execute(
        conn,
        "UPDATE athletes SET password_hash = $1, updated_at = NOW() WHERE id = $2",
        new_hash, athlete_id,
    )


async def update_username(conn, athlete_id: int, new_username: str):
    from web.async_db import async_fetch_one, async_execute
    existing = await async_fetch_one(
        conn,
        "SELECT id FROM athletes WHERE username = $1 AND id != $2",
        new_username, athlete_id,
    )
    if existing:
        raise ValueError(f"Username '{new_username}' is already taken.")
    await async_execute(
        conn,
        "UPDATE athletes SET username = $1, updated_at = NOW() WHERE id = $2",
        new_username, athlete_id,
    )
