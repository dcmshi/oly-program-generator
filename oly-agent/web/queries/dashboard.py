# web/queries/dashboard.py
"""DB queries for the dashboard."""

from datetime import date, timedelta


def get_active_program(conn, athlete_id: int) -> dict | None:
    from shared.db import fetch_one
    return fetch_one(
        conn,
        """
        SELECT id, name, phase, status, start_date, duration_weeks, sessions_per_week
        FROM generated_programs
        WHERE athlete_id = %s AND status IN ('active', 'draft')
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC
        LIMIT 1
        """,
        (athlete_id,),
    )


def get_current_week_sessions(conn, program_id: int, week_number: int) -> list[dict]:
    from shared.db import fetch_all
    sessions = fetch_all(
        conn,
        """
        SELECT ps.id, ps.day_number, ps.session_label, ps.estimated_duration_minutes,
               ps.focus_area, tl.id AS log_id, tl.overall_rpe
        FROM program_sessions ps
        LEFT JOIN training_logs tl ON tl.session_id = ps.id
        WHERE ps.program_id = %s AND ps.week_number = %s
        ORDER BY ps.day_number
        """,
        (program_id, week_number),
    )
    return sessions


def get_adherence(conn, program_id: int, week_number: int) -> dict:
    from shared.db import fetch_one
    prescribed = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM program_sessions WHERE program_id = %s AND week_number <= %s",
        (program_id, week_number),
    )
    logged = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS cnt FROM training_logs tl
        JOIN program_sessions ps ON ps.id = tl.session_id
        WHERE ps.program_id = %s AND ps.week_number <= %s
        """,
        (program_id, week_number),
    )
    p = (prescribed or {}).get("cnt", 0)
    l = (logged or {}).get("cnt", 0)
    return {"prescribed": p, "logged": l, "pct": round(l / p * 100) if p else 0}


def get_warnings(conn, athlete_id: int) -> list[str]:
    from shared.db import fetch_all
    warnings = []
    cutoff = date.today() - timedelta(days=14)

    recent_logs = fetch_all(
        conn,
        """
        SELECT log_date, overall_rpe, sleep_quality, stress_level
        FROM training_logs
        WHERE athlete_id = %s AND log_date >= %s
        ORDER BY log_date DESC
        """,
        (athlete_id, cutoff),
    )
    for log in recent_logs:
        d = log["log_date"].strftime("%b %d") if hasattr(log["log_date"], "strftime") else str(log["log_date"])
        if log["overall_rpe"] and float(log["overall_rpe"]) >= 9.0:
            warnings.append(f"High session RPE ({log['overall_rpe']}) on {d}")
        if log["sleep_quality"] and int(log["sleep_quality"]) <= 2:
            warnings.append(f"Poor sleep (quality {log['sleep_quality']}/5) on {d}")
        if log["stress_level"] and int(log["stress_level"]) >= 4:
            warnings.append(f"High stress (level {log['stress_level']}/5) on {d}")

    ex_stats = fetch_all(
        conn,
        """
        SELECT tle.exercise_name,
               AVG(tle.rpe_deviation) AS avg_rpe_dev,
               AVG(tle.make_rate) AS avg_make_rate,
               COUNT(*) AS sessions
        FROM training_log_exercises tle
        JOIN training_logs tl ON tl.id = tle.log_id
        WHERE tl.athlete_id = %s AND tl.log_date >= %s AND tle.rpe IS NOT NULL
        GROUP BY tle.exercise_name
        HAVING COUNT(*) >= 2
        """,
        (athlete_id, cutoff),
    )
    for ex in ex_stats:
        if ex["avg_rpe_dev"] and float(ex["avg_rpe_dev"]) > 1.5:
            warnings.append(f"{ex['exercise_name']}: avg RPE +{float(ex['avg_rpe_dev']):.1f} above target")
        if ex["avg_make_rate"] and float(ex["avg_make_rate"]) < 0.70:
            warnings.append(f"{ex['exercise_name']}: make rate {float(ex['avg_make_rate'])*100:.0f}% — consider reducing intensity")

    return warnings
