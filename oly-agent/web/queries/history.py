# web/queries/history.py
"""DB queries for exercise history view."""


def get_exercise_history(conn, athlete_id: int, exercise_name: str) -> list[dict]:
    """All logged entries for a given exercise, most recent first."""
    from shared.db import fetch_all

    return fetch_all(
        conn,
        """
        SELECT
            tl.log_date,
            gp.name         AS program_name,
            ps.week_number,
            ps.day_number,
            tle.sets_completed,
            tle.reps_per_set,
            tle.weight_kg,
            tle.prescribed_weight_kg,
            tle.weight_deviation_kg,
            tle.rpe,
            tle.rpe_deviation,
            tle.make_rate,
            tle.technical_notes
        FROM training_log_exercises tle
        JOIN training_logs tl      ON tl.id  = tle.log_id
        JOIN program_sessions ps   ON ps.id  = tl.session_id
        JOIN generated_programs gp ON gp.id  = ps.program_id
        WHERE tl.athlete_id = %s
          AND LOWER(tle.exercise_name) = LOWER(%s)
        ORDER BY tl.log_date DESC, tl.id DESC
        """,
        (athlete_id, exercise_name),
    )


def compute_history_summary(rows: list[dict]) -> dict:
    """Compute summary stats from history rows."""
    weights = [float(r["weight_kg"]) for r in rows if r["weight_kg"] is not None]
    if not weights:
        return {"total": len(rows), "best_kg": None, "latest_kg": None, "trend": None}

    # Trend: compare avg of 3 most recent vs next 3
    trend = None
    if len(weights) >= 4:
        recent = sum(weights[:3]) / 3
        older  = sum(weights[3 : min(6, len(weights))]) / len(weights[3 : min(6, len(weights))])
        diff = recent - older
        if diff > 1.5:
            trend = "up"
        elif diff < -1.5:
            trend = "down"
        else:
            trend = "flat"

    return {
        "total":     len(rows),
        "best_kg":   max(weights),
        "latest_kg": weights[0] if weights else None,
        "trend":     trend,
    }
