# web/queries/coach.py
"""DB queries for coach ↔ athlete links (COACH-2, migration 0021).

A link is made one of two ways: an athlete with a login grants a coach's
username access ('granted'), or a coach adds an athlete who has no login of
their own ('created'). Only the coach side of a link may act as the athlete.
"""

from web.async_db import async_execute, async_execute_returning, async_fetch_all, async_fetch_one


async def is_linked(conn, coach_id: int, athlete_id: int) -> bool:
    row = await async_fetch_one(
        conn, "SELECT 1 AS ok FROM coach_athletes WHERE coach_id = $1 AND athlete_id = $2", coach_id, athlete_id)
    return row is not None


async def list_coached_athletes(conn, coach_id: int) -> list[dict]:
    """The athletes this account coaches, with their active program (if any)."""
    return await async_fetch_all(conn, """
        SELECT a.id, a.name, a.level, a.sessions_per_week, ca.origin,
               (a.username IS NOT NULL AND a.password_hash IS NOT NULL) AS has_login,
               (SELECT g.name FROM generated_programs g
                 WHERE g.athlete_id = a.id AND g.status = 'active'
                 ORDER BY g.id DESC LIMIT 1) AS active_program
        FROM coach_athletes ca
        JOIN athletes a ON a.id = ca.athlete_id
        WHERE ca.coach_id = $1
        ORDER BY lower(a.name), a.id
    """, coach_id)


async def list_coaches(conn, athlete_id: int) -> list[dict]:
    """The accounts this athlete has given access to."""
    return await async_fetch_all(conn, """
        SELECT a.id, a.name, a.username, ca.origin, ca.created_at
        FROM coach_athletes ca
        JOIN athletes a ON a.id = ca.coach_id
        WHERE ca.athlete_id = $1
        ORDER BY lower(a.name), a.id
    """, athlete_id)


async def create_managed_athlete(conn, coach_id: int, *, name: str, level: str,
                                 sessions_per_week: int, bodyweight_kg: float | None,
                                 biological_sex: str | None) -> int:
    """An athlete without a login, coached by `coach_id`. The rest of the
    profile (maxes, faults, goals …) is filled in on the profile page while
    acting as them."""
    athlete_id = await async_execute_returning(conn, """
        INSERT INTO athletes (name, level, sessions_per_week, bodyweight_kg, biological_sex, timezone)
        VALUES ($1, $2, $3, $4, $5, (SELECT timezone FROM athletes WHERE id = $6))
        RETURNING id
    """, name, level, sessions_per_week, bodyweight_kg, biological_sex, coach_id)
    await async_execute(conn, "INSERT INTO coach_athletes (coach_id, athlete_id, origin) VALUES ($1, $2, 'created')",
                        coach_id, athlete_id)
    return athlete_id


async def find_account_by_username(conn, username: str) -> dict | None:
    """An account that can log in (a coach must be able to sign in to act)."""
    return await async_fetch_one(conn, """
        SELECT id, name, username FROM athletes
        WHERE username = $1 AND password_hash IS NOT NULL
    """, username)


async def grant(conn, athlete_id: int, coach_id: int) -> bool:
    """The athlete gives `coach_id` access. False when it already existed."""
    row = await async_fetch_one(conn, """
        INSERT INTO coach_athletes (coach_id, athlete_id, origin) VALUES ($1, $2, 'granted')
        ON CONFLICT (coach_id, athlete_id) DO NOTHING
        RETURNING coach_id
    """, coach_id, athlete_id)
    return row is not None


async def revoke(conn, coach_id: int, athlete_id: int) -> None:
    await async_execute(conn, "DELETE FROM coach_athletes WHERE coach_id = $1 AND athlete_id = $2",
                        coach_id, athlete_id)


async def remove_athlete(conn, coach_id: int, athlete_id: int) -> str | None:
    """A coach drops an athlete. One they created, with no login and no other
    coach, is deleted with all their data (nobody could reach it again);
    otherwise only the link goes. Returns "deleted", "unlinked" or None when
    there was no link."""
    row = await async_fetch_one(conn, """
        SELECT ca.origin,
               (a.username IS NOT NULL AND a.password_hash IS NOT NULL) AS has_login,
               (SELECT count(*) FROM coach_athletes o WHERE o.athlete_id = a.id AND o.coach_id <> $1) AS other_coaches
        FROM coach_athletes ca JOIN athletes a ON a.id = ca.athlete_id
        WHERE ca.coach_id = $1 AND ca.athlete_id = $2
    """, coach_id, athlete_id)
    if row is None:
        return None
    if row["origin"] == "created" and not row["has_login"] and row["other_coaches"] == 0:
        await async_execute(conn, "DELETE FROM athletes WHERE id = $1", athlete_id)
        return "deleted"
    await revoke(conn, coach_id, athlete_id)
    return "unlinked"
