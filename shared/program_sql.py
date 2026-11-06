# shared/program_sql.py
"""
Program lifecycle SQL kept once for both drivers (CLEAN-3).

The web app runs these through asyncpg (`$1`, `$2` placeholders); the CLIs and
eval harnesses through psycopg2 (`%s`-style). Each statement is written once
in asyncpg form and converted with `psycopg(sql)` / `psycopg_params(...)`, so
an invariant like "NULL the training-log back-references before deleting a
program" (those FKs have no ON DELETE CASCADE — CLAUDE.md) cannot drift
between the two copies it used to live in.

Deleting:  UNLINK_LOG_EXERCISES, UNLINK_LOGS, DELETE_PROGRAMS — in that order,
           in one transaction. Params: ($1 program ids int[], $2 athlete id or
           NULL). With an athlete id every statement is scoped to that
           athlete's programs, so a caller can never detach another athlete's
           logs even if the router's ownership check is bypassed.
Activating: SUPERSEDE_OTHER_ACTIVE then ACTIVATE_DRAFT. Params: ($1 athlete
           id, $2 program id). Only a draft becomes active (audit5 web-L1).
"""

import re

UNLINK_LOG_EXERCISES = """
    UPDATE training_log_exercises SET session_exercise_id = NULL
    WHERE session_exercise_id IN (
        SELECT se.id FROM session_exercises se
        JOIN program_sessions ps ON ps.id = se.session_id
        JOIN generated_programs gp ON gp.id = ps.program_id
        WHERE ps.program_id = ANY($1::int[]) AND ($2::int IS NULL OR gp.athlete_id = $2::int)
    )"""

UNLINK_LOGS = """
    UPDATE training_logs SET session_id = NULL
    WHERE session_id IN (
        SELECT ps.id FROM program_sessions ps
        JOIN generated_programs gp ON gp.id = ps.program_id
        WHERE ps.program_id = ANY($1::int[]) AND ($2::int IS NULL OR gp.athlete_id = $2::int)
    )"""

# cascades to program_sessions → session_exercises → generation_log
DELETE_PROGRAMS = """
    DELETE FROM generated_programs
    WHERE id = ANY($1::int[]) AND ($2::int IS NULL OR athlete_id = $2::int)"""

DELETE_STATEMENTS: tuple[str, ...] = (UNLINK_LOG_EXERCISES, UNLINK_LOGS, DELETE_PROGRAMS)

SUPERSEDE_OTHER_ACTIVE = """
    UPDATE generated_programs SET status = 'superseded', updated_at = NOW()
    WHERE athlete_id = $1 AND status = 'active' AND id != $2"""

ACTIVATE_DRAFT = """
    UPDATE generated_programs SET status = 'active', updated_at = NOW()
    WHERE id = $2 AND athlete_id = $1 AND status = 'draft'"""

_PLACEHOLDER = re.compile(r"\$(\d+)")


def psycopg(sql: str) -> str:
    """asyncpg `$n` placeholders → psycopg2 named `%(pn)s` (a `$n` used twice
    stays one parameter). The statements above contain no literal `%`."""
    return _PLACEHOLDER.sub(r"%(p\1)s", sql)


def psycopg_params(*args) -> dict:
    """Positional arguments in `$n` order → psycopg2's named parameters."""
    return {f"p{i}": v for i, v in enumerate(args, 1)}
