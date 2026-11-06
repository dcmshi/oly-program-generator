# oly-agent/program_store.py
"""
The one psycopg2 program writer (CLEAN-2).

The orchestrator (generated programs) and the CSV / Markdown importers (coach-
written programs) used to INSERT `generated_programs` / `program_sessions` /
`session_exercises` each in their own words, and the importer reached into
orchestrator privates for the athlete snapshot and the duration estimate.
They now share this module, so a new `session_exercises` column (the way
`complex_id` arrived with PLAN-3a) is added in one place: SESSION_EXERCISE_COLUMNS.

The functions don't commit; callers own their transactions. The SQL helpers
are this module's globals (`execute`, `execute_returning`, `fetch_all`), which
is what tests patch.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.constants import MIN_SESSION_DURATION_MINUTES
from shared.db import execute_returning, fetch_all
from shared.formulas import estimate_session_minutes

# Account / bookkeeping columns kept out of generated_programs.athlete_snapshot.
SNAPSHOT_EXCLUDE = frozenset({"created_at", "updated_at", "password_hash", "username", "is_admin"})

# Every session_exercises column a writer may set, in INSERT order, with the
# default a writer that doesn't know the field gets.
SESSION_EXERCISE_COLUMNS: tuple[tuple[str, object], ...] = (
    ("exercise_order", None),
    ("exercise_id", None),
    ("exercise_name", None),
    ("sets", None),
    ("reps", None),
    ("intensity_pct", None),
    ("intensity_reference", None),
    ("absolute_weight_kg", None),
    ("rpe_target", None),
    ("rest_seconds", None),
    ("is_max_attempt", False),
    ("selection_rationale", None),
    ("source_principle_ids", []),
    ("source_chunk_ids", []),
    ("complex_id", None),
    ("notes", None),
)
_LIST_COLUMNS = {"source_principle_ids", "source_chunk_ids"}

_INSERT_SESSION_EXERCISE = (
    "INSERT INTO session_exercises (session_id, "
    + ", ".join(c for c, _ in SESSION_EXERCISE_COLUMNS)
    + ") VALUES (" + ", ".join(["%s"] * (len(SESSION_EXERCISE_COLUMNS) + 1)) + ") RETURNING id"
)


def athlete_snapshot(athlete: dict) -> dict:
    """The athlete row as stored on a program (no credentials / bookkeeping)."""
    return {k: v for k, v in athlete.items() if k not in SNAPSHOT_EXCLUDE}


def estimate_duration(exercises: list[dict]) -> int:
    """Rough session duration estimate in minutes (floored at the minimum)."""
    return max(MIN_SESSION_DURATION_MINUTES, round(estimate_session_minutes(exercises)))


def exercise_lookup(conn) -> dict[str, int]:
    """{catalogue name lowercased: exercise id} — what name resolution matches against."""
    return {r["name"].lower(): r["id"] for r in fetch_all(conn, "SELECT id, name FROM exercises")}


def insert_program(conn, *, athlete_id: int, name: str, status: str, phase: str,
                   duration_weeks: int, sessions_per_week: int, start_date: date,
                   athlete: dict, maxes_snapshot: dict, generation_params: dict,
                   end_date: date | None = None, rationale: str | None = None,
                   macrocycle_id: int | None = None, macrocycle_block_index: int | None = None) -> int:
    """INSERT one generated_programs row; returns its id. `athlete` is the
    athlete row (snapshotted here)."""
    return execute_returning(conn, """
        INSERT INTO generated_programs
            (athlete_id, name, status, phase, duration_weeks, sessions_per_week,
             start_date, end_date, athlete_snapshot, maxes_snapshot, generation_params, rationale,
             macrocycle_id, macrocycle_block_index)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        athlete_id, name, status, phase, duration_weeks, sessions_per_week,
        start_date, end_date,
        json.dumps(athlete_snapshot(athlete), default=str),
        json.dumps(maxes_snapshot),
        json.dumps(generation_params, default=str),
        rationale, macrocycle_id, macrocycle_block_index,
    ))


def session_exercise_values(session_id: int, ex: dict) -> tuple:
    """One exercise dict as the INSERT's parameters (missing keys → defaults)."""
    values = []
    for column, default in SESSION_EXERCISE_COLUMNS:
        value = ex.get(column, default)
        if column in _LIST_COLUMNS:
            value = value or []
        elif value is None and default is not None:
            value = default
        values.append(value)
    return (session_id, *values)


def insert_session(conn, program_id: int, week_number: int, day_number: int, *,
                   label: str, focus_area: str | None, exercises: list[dict],
                   notes: str | None = None) -> tuple[int, list[int]]:
    """INSERT a program_sessions row and its session_exercises rows (in list
    order); returns (session_id, [session_exercise ids])."""
    session_id = execute_returning(conn, """
        INSERT INTO program_sessions
            (program_id, week_number, day_number, session_label,
             estimated_duration_minutes, focus_area, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (program_id, week_number, day_number, label, estimate_duration(exercises), focus_area, notes))
    exercise_ids = [execute_returning(conn, _INSERT_SESSION_EXERCISE, session_exercise_values(session_id, ex))
                    for ex in exercises]
    return session_id, exercise_ids
