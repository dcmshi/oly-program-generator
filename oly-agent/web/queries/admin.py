# web/queries/admin.py
"""DB queries for the /admin/jobs page."""


async def get_recent_jobs(conn, limit: int = 100) -> list[dict]:
    """Aggregate generation_log by program_id, newest first.

    Returns one row per program with rolled-up cost, attempt counts,
    status, and the most recent error message (if any).
    """
    rows = await conn.fetch(
        """
        SELECT
            gl.program_id,
            a.name                                            AS athlete_name,
            gp.phase,
            gp.status                                         AS program_status,
            MIN(gl.created_at)                                AS started_at,
            EXTRACT(EPOCH FROM (MAX(gl.created_at) - MIN(gl.created_at)))
                                                              AS duration_seconds,
            COUNT(*)                                          AS total_attempts,
            COUNT(*) FILTER (WHERE gl.status = 'success')    AS successful_sessions,
            COUNT(*) FILTER (WHERE gl.status = 'failed')     AS failed_sessions,
            COALESCE(SUM(gl.estimated_cost_usd), 0)          AS total_cost_usd,
            MAX(gl.error_message)                             AS last_error,
            MAX(gl.validation_errors::text)
                FILTER (WHERE gl.status = 'failed')           AS last_validation_errors
        FROM generation_log gl
        JOIN generated_programs gp ON gp.id = gl.program_id
        JOIN athletes a ON a.id = gp.athlete_id
        GROUP BY gl.program_id, a.name, gp.phase, gp.status
        ORDER BY MIN(gl.created_at) DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def get_job_detail(conn, program_id: int) -> list[dict]:
    """Return individual generation_log rows for a single program."""
    rows = await conn.fetch(
        """
        SELECT
            id,
            week_number,
            day_number,
            attempt_number,
            model,
            input_tokens,
            output_tokens,
            estimated_cost_usd,
            status,
            validation_errors,
            error_message,
            created_at
        FROM generation_log
        WHERE program_id = $1
        ORDER BY week_number, day_number, attempt_number
        """,
        program_id,
    )
    return [dict(r) for r in rows]
