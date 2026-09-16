#!/usr/bin/env python3
"""
Import a coach-written program spreadsheet (CSV export) as a completed program
for an athlete, so the generator's "Previous Program" / "Recent Training" prompt
blocks and the phase-progression logic see real history (TODO DOG-1).

    cd oly-agent
    PYTHONUTF8=1 uv run python import_program_csv.py --file "<program>.csv" --athlete-id 1 --dry-run
    PYTHONUTF8=1 uv run python import_program_csv.py --file "<program>.csv" --athlete-id 1 --log-as-completed
    PYTHONUTF8=1 uv run python import_program_csv.py --file "<program>.csv" --athlete-id 1 --replace --log-as-completed

Sheet layout the parser understands (the "General Strength — 3 Block / 11W" export):

    GENERAL STRENGTH  BLOCK n                      block header
    Week N - <CYCLE NAME>                          week header (numbers restart per block —
                                                   weeks are renumbered 1..n by occurrence)
    Day n,%,Reps,%,Reps,…,Total Reps,Avg Intensity %,Volume
    <exercise>,<pct>,<reps>,<pct>,<reps>,…          one row per exercise, (pct, reps) pairs
    ,,,…,<total reps>,,<volume>                    day totals (first cell empty)

Reps cells: ``3``, ``3x3`` (sets×reps), ``3+3`` (complex — one set, reps of the
first movement, full notation kept in notes), ``4x3+3``, ``10x1``, ``1RM`` (a max
attempt), ``3x12-15`` (a range — the low end). Unloaded rows (jumps, rows, split
squats) carry ``5x5`` / ``x10`` in the *pct* column and get no intensity.
``85%`` and ``1RM`` are accepted in the pct column too.

Rows are written pipeline-style: one ``session_exercises`` row per (exercise,
pct/reps pair) with ``intensity_reference`` = the max the percentage refers to
(``snatch`` / ``clean`` / ``clean_and_jerk`` / ``jerk`` / ``push_press`` /
``back_squat`` / ``front_squat``), ``absolute_weight_kg`` from the athlete's
current maxes (``weight_resolver.resolve_weights``), ``exercise_id`` from an
alias map onto the catalogue (NULL when there is no sensible match — the
generator keys its history blocks by ``exercise_name``). ``intensity_pct`` is
clamped to the table's CHECK (≤ 120) with the true value kept in notes.

``--log-as-completed`` additionally writes one ``training_logs`` row per session
(dated from ``--start-date``) and one ``training_log_exercises`` row per
prescription (as performed: same sets/reps/weight, ``make_rate`` =
``--make-rate`` on competition lifts, no RPE), then runs
``feedback.compute_outcome`` + ``save_outcome`` so the program carries an
``outcome_summary`` and a phase verdict like a program completed in the UI.
"""

import argparse
import csv
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared.config import Settings
from shared.db import execute, execute_returning, fetch_all, fetch_one, get_connection
from shared.exercise_mapping import COMP_LIFT_REFS

logger = logging.getLogger(__name__)

INTENSITY_PCT_CEILING = 120.0      # session_exercises CHECK (intensity_pct <= 120)
DEFAULT_PHASE = "general_prep"
DEFAULT_NAME = "General Strength — 3 Block / 11W (imported)"

_BLOCK_RE = re.compile(r"^GENERAL STRENGTH\s+BLOCK\s+(\d+)", re.I)
_WEEK_RE = re.compile(r"^Week\s+(\d+)\s*-\s*(.+?)\s*$", re.I)
_DAY_RE = re.compile(r"^Day\s+(\d+)\s*$", re.I)
_REPS_RE = re.compile(r"^(?:(\d+)x)?(\d+(?:\+\d+)*)(?:-(\d+))?(?:/s)?$", re.I)
_LEADING_X_RE = re.compile(r"^x(\d+)$", re.I)


# ── Parsed structure ─────────────────────────────────────────────────────────

@dataclass
class Prescription:
    exercise_name: str
    sets: int
    reps: int
    intensity_pct: float | None = None
    intensity_reference: str | None = None
    is_max_attempt: bool = False
    notes: str | None = None
    exercise_id: int | None = None
    absolute_weight_kg: float | None = None


@dataclass
class Day:
    number: int
    exercises: list[Prescription] = field(default_factory=list)
    total_reps: int | None = None
    avg_intensity: float | None = None
    volume: int | None = None


@dataclass
class Week:
    number: int          # sequential, 1-based, by occurrence
    file_week: int       # the sheet's own (non-sequential) number
    block: int
    cycle: str           # e.g. "DEFICIT CYCLE"
    days: list[Day] = field(default_factory=list)


@dataclass
class ParsedProgram:
    weeks: list[Week]

    @property
    def sessions(self) -> list[tuple[Week, Day]]:
        return [(w, d) for w in self.weeks for d in w.days]

    @property
    def sessions_per_week(self) -> int:
        return max((len(w.days) for w in self.weeks), default=0)


# ── Cell parsers ─────────────────────────────────────────────────────────────

def parse_reps_cell(cell: str) -> tuple[int, int, str | None, bool]:
    """``"4x3+3"`` → (sets, reps, notes, is_max_attempt).

    reps = the first ``+`` part (the primary movement of a complex); the full
    notation goes to notes whenever it says more than sets×reps.
    """
    raw = cell.strip()
    if not raw:
        raise ValueError("empty reps cell")
    if raw.upper() == "1RM":
        return 1, 1, "1RM", True
    m = _LEADING_X_RE.match(raw)
    if m:
        return 1, int(m.group(1)), None, False
    m = _REPS_RE.match(raw)
    if not m:
        raise ValueError(f"unrecognised reps cell {raw!r}")
    sets = int(m.group(1)) if m.group(1) else 1
    parts = [int(p) for p in m.group(2).split("+")]
    reps = parts[0]
    notes = None
    if len(parts) > 1:
        notes = f"complex {raw}"
    elif m.group(3):
        notes = f"{parts[0]}-{m.group(3)} reps"
    elif raw.lower().endswith("/s"):
        notes = f"{raw} (per side)"
    return max(sets, 1), max(reps, 1), notes, False


def parse_pct_cell(cell: str) -> tuple[float | None, bool]:
    """``"85"`` / ``"85%"`` → (85.0, False); ``"1RM"`` → (100.0, True);
    anything else (``"5x5"``, ``"x10"``) → (None, False) — a reps spec in the pct column."""
    raw = cell.strip().rstrip("%").strip()
    if not raw:
        return None, False
    if raw.upper() == "1RM":
        return 100.0, True
    try:
        return float(raw), False
    except ValueError:
        return None, False


def clamp_intensity(pct: float | None) -> tuple[float | None, str | None]:
    """Keep intensity within the session_exercises CHECK; report the true value."""
    if pct is not None and pct > INTENSITY_PCT_CEILING:
        return INTENSITY_PCT_CEILING, f"prescribed at {pct:g}% (stored as {INTENSITY_PCT_CEILING:g}%)"
    return pct, None


# ── Exercise name → intensity reference / catalogue id ───────────────────────

def intensity_reference_for(name: str) -> str | None:
    """Which athlete max a percentage on this row refers to (pipeline keys)."""
    n = name.lower()
    if "% of cl" in n:
        return "clean"
    if "% of msn" in n or ("muscle snatch" in n and ("1rm" in n or "heavy single" in n)):
        return "muscle_snatch"
    if "clean" in n or n.startswith("cl "):
        return "clean_and_jerk" if "jerk" in n else "clean"
    if "jerk" in n:
        return "jerk"
    if "push press" in n:
        return "push_press"
    if "back squat" in n:
        return "back_squat"
    if "front squat" in n:
        return "front_squat"
    if "snatch" in n or n.startswith("sn ") or n.startswith("power sn"):
        return "snatch"
    if "mid grip deadlift" in n:
        return "clean"
    return None


# Sheet name (lowercased) → catalogue name. Complexes take the primary lift.
# Substring rules run after exact matches; unmatched → exercise_id NULL.
EXACT_ALIASES: dict[str, str] = {
    "hang snatch": "Hang Snatch (above knee)",
    "no foot snatch": "No Feet Snatch",
    "sn balance": "Snatch Balance",
    "snatch extension from deficit": "Snatch Pull from Deficit",
    "snatch grip deadlift": "Snatch Deadlift",
    "sn grip rdl": "Snatch Grip Romanian Deadlift",
    "block power snatch + oh sq": "Power Snatch from Blocks (knee)",
    "block snatch": "Block Snatch (knee)",
    "block snatch (above knee)": "Block Snatch (above knee)",
    "block clean (at knee)": "Block Clean (knee)",
    "block clean (above knee)": "Block Clean (above knee)",
    "mid grip deadlift": "Deadlift",
    "stiff leg deadlift (% of cl)": "Stiff-Leg Deadlift",
    "jerk from rack": "Jerk",
    "jerk dips": "Jerk Dip",
    "btn push press": "Push Press Behind the Neck",
    "block power clean + push press": "Power Clean",
    "clean & jerk": "Clean & Jerk",
    "close stance back squat": "Close-Stance Back Squat",
    "partial back squat (1/4 squat)": "Quarter Squat",
    "1+1/4 front squat": "1¼ Front Squat",
    "box jumps": "Box Jump",
    "broad jumps": "Broad Jump",
    "vertical jumps": "Vertical Jump",
    "jumping squats": "Jumping Squat",
    "bb rows": "Barbell Row",
    "pendlay rows": "Pendlay Row",
    "back extensions": "Back Extension",
    "bulgarian split squats": "Bulgarian Split Squat",
}
SUBSTRING_ALIASES: list[tuple[str, str]] = [
    # (needle in lowercased name, catalogue name) — first hit wins
    ("clean deadlift + clean + jerk", "Clean & Jerk"),
    ("power clean + jerk", "Clean & Jerk"),
    ("power clean + fsq + jerk", "Clean & Jerk"),
    ("power clean + power jerk", "Clean & Jerk"),
    ("clean + jerk", "Clean & Jerk"),
    ("clean + fsq + jerk", "Clean & Jerk"),
    ("muscle snatch", "Muscle Snatch"),
    ("power snatch from deficit", "Power Snatch from Deficit"),
    ("power snatch", "Power Snatch"),
    ("power sn ", "Power Snatch"),
    ("snatch extension + snatch", "Snatch"),
    ("snatch pull to hip + snatch", "Snatch"),
    ("snatch pull to hip", "Snatch Pull to Hip"),      # before "extension": the first movement names a complex
    ("sn pull to hip", "Snatch Pull to Hip"),
    ("snatch extension", "Snatch Extension"),
    ("sn extension", "Snatch Extension"),
    ("snatch deadlift", "Snatch Deadlift"),
    ("snatch from deficit", "Snatch from Deficit"),
    ("snatch", "Snatch"),
    ("power clean from deficit", "Power Clean from Deficit"),
    ("power clean", "Power Clean"),
    ("clean extension", "Clean Extension"),
    ("cl extension", "Clean Extension"),
    ("clean pull to hip", "Clean Pull to Hip"),
    ("clean deadlift", "Clean Deadlift"),
    ("clean from deficit", "Clean from Deficit"),
    ("clean", "Clean"),
    ("power jerk", "Power Jerk"),
    ("jerk", "Jerk"),
    ("push press", "Push Press"),
    ("back squat", "Back Squat"),
    ("front squat", "Front Squat"),
]


def catalogue_name_for(name: str) -> str | None:
    n = name.lower().strip()
    if n in EXACT_ALIASES:
        return EXACT_ALIASES[n]
    for needle, target in SUBSTRING_ALIASES:
        if needle in n:
            return target
    return None


def resolve_catalogue_ids(program: ParsedProgram, exercise_lookup: dict[str, int]) -> list[str]:
    """Fill exercise_id via the alias map; return the sheet names left unmapped."""
    unmapped: list[str] = []
    for _, day in program.sessions:
        for ex in day.exercises:
            target = catalogue_name_for(ex.exercise_name)
            ex.exercise_id = exercise_lookup.get(target.lower()) if target else None
            if ex.exercise_id is None and ex.exercise_name not in unmapped:
                unmapped.append(ex.exercise_name)
    return unmapped


# ── Row / sheet parsing ──────────────────────────────────────────────────────

def parse_exercise_row(row: list[str], pair_end: int) -> list[Prescription]:
    """One sheet row → one Prescription per (pct, reps) pair."""
    name = row[0].strip()
    out: list[Prescription] = []
    for i in range(1, pair_end - 1, 2):     # (i, i+1) must both sit left of Total Reps
        pct_cell = row[i].strip() if i < len(row) else ""
        reps_cell = row[i + 1].strip() if i + 1 < len(row) else ""
        if not pct_cell and not reps_cell:
            continue
        pct, pct_is_max = parse_pct_cell(pct_cell)
        notes: list[str] = []
        if pct is None and pct_cell:
            # unloaded: the reps spec sits in the pct column (Box Jumps,5x5)
            sets, reps, n, is_max = parse_reps_cell(pct_cell)
            if reps_cell:                       # e.g. "3x10,8" — keep whatever else was there
                notes.append(f"reps column: {reps_cell}")
        elif reps_cell:
            sets, reps, n, is_max = parse_reps_cell(reps_cell)
        else:                                   # pct with no reps — record it, don't guess
            sets, reps, n, is_max = 1, 1, "reps unspecified", False
        if n:
            notes.append(n)
        is_max = is_max or pct_is_max or (
            pct is not None and pct >= 100 and ("heavy single" in name.lower() or "1rm" in name.lower())
        )
        pct, clamp_note = clamp_intensity(pct)
        if clamp_note:
            notes.append(clamp_note)
        out.append(Prescription(
            exercise_name=name, sets=sets, reps=reps, intensity_pct=pct,
            intensity_reference=intensity_reference_for(name) if pct is not None else None,
            is_max_attempt=is_max, notes="; ".join(notes) or None,
        ))
    if not out:                                 # "Core,,,," — present but unspecified
        out.append(Prescription(exercise_name=name, sets=1, reps=1, notes="unspecified"))
    return out


def _to_int(cell: str) -> int | None:
    try:
        return int(float(cell.strip()))
    except (ValueError, AttributeError):
        return None


def _has_totals(row: list[str], pair_end: int) -> bool:
    return pair_end < len(row) and bool(row[pair_end].strip())


def _set_totals(day: Day, row: list[str], pair_end: int) -> None:
    day.total_reps = _to_int(row[pair_end])
    avg = row[pair_end + 1].strip() if pair_end + 1 < len(row) else ""
    day.avg_intensity = float(avg) if avg else None
    day.volume = _to_int(row[pair_end + 2]) if pair_end + 2 < len(row) else None


def parse_program_csv(rows: list[list[str]]) -> ParsedProgram:
    weeks: list[Week] = []
    block = 0
    week: Week | None = None
    day: Day | None = None
    pair_end = 1
    for row in rows:
        if not row or not any(c.strip() for c in row):
            continue
        first = row[0].strip()
        m = _BLOCK_RE.match(first)
        if m:
            block = int(m.group(1))
            continue
        m = _WEEK_RE.match(first)
        if m:
            week = Week(number=len(weeks) + 1, file_week=int(m.group(1)), block=block,
                        cycle=re.sub(r"\s+", " ", m.group(2)).strip())
            weeks.append(week)
            day = None
            continue
        m = _DAY_RE.match(first)
        if m:
            if week is None:
                raise ValueError(f"Day header before any week header: {row[:2]}")
            day = Day(number=int(m.group(1)))
            week.days.append(day)
            try:
                pair_end = row.index("Total Reps")
            except ValueError:
                pair_end = len(row)
            continue
        if any("Week Intensity" in c for c in row):
            day = None                      # the week summary follows; never a day's totals
            continue
        if not first:
            # totals row: first cell empty, Total Reps column filled — closes the day
            if day is not None and _has_totals(row, pair_end):
                _set_totals(day, row, pair_end)
                day = None
            continue
        if day is None:
            continue
        prescriptions = parse_exercise_row(row, pair_end)
        day.exercises.extend(prescriptions)
        if all(p.intensity_pct is None for p in prescriptions) and _has_totals(row, pair_end):
            # sheet quirk: an unloaded last row (BB Rows,3x10,…,62,,4440) carries the day totals
            _set_totals(day, row, pair_end)
            day = None
    if not weeks:
        raise ValueError("no 'Week N - …' headers found")
    return ParsedProgram(weeks=weeks)


def load_program_csv(path: Path) -> ParsedProgram:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return parse_program_csv(list(csv.reader(f)))


# ── Date helpers ─────────────────────────────────────────────────────────────

def session_log_date(start: date, week_number: int, day_number: int, days_in_week: int) -> date:
    """Spread a week's sessions over Mon–Sat of that week: 4/wk → Mon Tue Thu Fri."""
    spread = {1: [0], 2: [0, 3], 3: [0, 2, 4], 4: [0, 1, 3, 4], 5: [0, 1, 2, 3, 4], 6: [0, 1, 2, 3, 4, 5]}
    offsets = spread.get(days_in_week, list(range(days_in_week)))
    offset = offsets[day_number - 1] if 0 < day_number <= len(offsets) else min(day_number - 1, 6)
    return start + timedelta(days=7 * (week_number - 1) + offset)


# ── DB writes ────────────────────────────────────────────────────────────────

def session_label(week: Week, day: Day) -> str:
    return f"{week.cycle.title()} · Day {day.number}"


def focus_area(day: Day) -> str | None:
    for ex in day.exercises:
        if ex.intensity_reference in COMP_LIFT_REFS:
            return "snatch" if ex.intensity_reference == "snatch" else "clean"
    for ex in day.exercises:
        if ex.intensity_reference:
            return ex.intensity_reference.split("_")[0]
    return None


def rationale_text(program: ParsedProgram, source_name: str) -> str:
    lines = [f"# Imported Program\nCoach-written program imported from `{source_name}`; "
             f"{len(program.weeks)} weeks, {len(program.sessions)} sessions.\n", "## Blocks"]
    by_block: dict[int, list[Week]] = {}
    for w in program.weeks:
        by_block.setdefault(w.block, []).append(w)
    for b, ws in sorted(by_block.items()):
        cycles = sorted({w.cycle.title() for w in ws}, key=lambda c: [w.cycle.title() for w in ws].index(c))
        lines.append(f"- Block {b}: weeks {ws[0].number}–{ws[-1].number} — {', '.join(cycles)}")
    return "\n".join(lines)


def find_previous_import(conn, athlete_id: int, source_name: str) -> list[int]:
    rows = fetch_all(conn, """
        SELECT id FROM generated_programs
        WHERE athlete_id = %s AND generation_params->>'source' = 'csv_import'
          AND generation_params->>'file' = %s
        ORDER BY id
    """, (athlete_id, source_name))
    return [r["id"] for r in rows]


def delete_import(conn, program_ids: list[int]) -> None:
    """Remove an earlier import including the training logs it wrote (unlike UI
    deletion, which keeps logs — these logs exist only because of the import)."""
    if not program_ids:
        return
    execute(conn, """
        DELETE FROM training_log_exercises WHERE log_id IN (
            SELECT tl.id FROM training_logs tl
            JOIN program_sessions ps ON ps.id = tl.session_id
            WHERE ps.program_id = ANY(%s))
    """, (program_ids,))
    execute(conn, """
        DELETE FROM training_logs WHERE session_id IN (
            SELECT id FROM program_sessions WHERE program_id = ANY(%s))
    """, (program_ids,))
    execute(conn, "DELETE FROM generated_programs WHERE id = ANY(%s)", (program_ids,))


def write_program(conn, athlete_id: int, program: ParsedProgram, *, name: str, phase: str,
                  start_date: date, source_name: str, log_as_completed: bool,
                  make_rate: float) -> int:
    from orchestrator import _build_athlete_snapshot, _estimate_duration
    from weight_resolver import build_maxes_dict, resolve_weights

    athlete = fetch_one(conn, "SELECT * FROM athletes WHERE id = %s", (athlete_id,))
    if not athlete:
        raise ValueError(f"athlete {athlete_id} not found")
    max_rows = fetch_all(conn, """
        SELECT e.name, am.weight_kg FROM athlete_maxes am
        JOIN exercises e ON am.exercise_id = e.id
        WHERE am.athlete_id = %s AND am.max_type = 'current'
    """, (athlete_id,))
    maxes = build_maxes_dict(max_rows)
    exercise_lookup = {r["name"].lower(): r["id"] for r in fetch_all(conn, "SELECT id, name FROM exercises")}
    unmapped = resolve_catalogue_ids(program, exercise_lookup)
    if unmapped:
        logger.info(f"{len(unmapped)} sheet name(s) have no catalogue match (stored with NULL exercise_id): {unmapped}")

    weeks = len(program.weeks)
    end_date = start_date + timedelta(days=7 * weeks - 1)
    program_id = execute_returning(conn, """
        INSERT INTO generated_programs
            (athlete_id, name, status, phase, duration_weeks, sessions_per_week,
             start_date, end_date, athlete_snapshot, maxes_snapshot, generation_params, rationale)
        VALUES (%s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        athlete_id, name, phase, weeks, program.sessions_per_week, start_date, end_date,
        json.dumps(_build_athlete_snapshot(athlete), default=str),
        json.dumps(maxes),
        json.dumps({"source": "csv_import", "file": source_name, "model": None,
                    "imported_on": date.today().isoformat()}),
        rationale_text(program, source_name),
    ))

    for week, day in program.sessions:
        exercises = [{
            "exercise_order": i, "exercise_id": ex.exercise_id, "exercise_name": ex.exercise_name,
            "sets": ex.sets, "reps": ex.reps, "intensity_pct": ex.intensity_pct,
            "intensity_reference": ex.intensity_reference, "is_max_attempt": ex.is_max_attempt,
            "notes": ex.notes,
        } for i, ex in enumerate(day.exercises, 1)]
        resolve_weights(exercises, maxes)
        notes = None
        if day.total_reps is not None:
            notes = f"Sheet totals: {day.total_reps} reps"
            if day.avg_intensity is not None:
                notes += f", avg {day.avg_intensity:g}%"
            if day.volume is not None:
                notes += f", volume {day.volume}"
        session_id = execute_returning(conn, """
            INSERT INTO program_sessions
                (program_id, week_number, day_number, session_label, estimated_duration_minutes, focus_area, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (program_id, week.number, day.number, session_label(week, day),
              _estimate_duration(exercises), focus_area(day), notes))
        se_ids = []
        for ex in exercises:
            se_ids.append(execute_returning(conn, """
                INSERT INTO session_exercises
                    (session_id, exercise_order, exercise_id, exercise_name, sets, reps,
                     intensity_pct, intensity_reference, absolute_weight_kg, is_max_attempt, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (session_id, ex["exercise_order"], ex["exercise_id"], ex["exercise_name"], ex["sets"],
                  ex["reps"], ex["intensity_pct"], ex["intensity_reference"], ex["absolute_weight_kg"],
                  ex["is_max_attempt"], ex["notes"])))
        if log_as_completed:
            log_id = execute_returning(conn, """
                INSERT INTO training_logs (athlete_id, session_id, log_date)
                VALUES (%s, %s, %s) RETURNING id
            """, (athlete_id, session_id, session_log_date(start_date, week.number, day.number, len(week.days))))
            for ex, se_id in zip(exercises, se_ids, strict=True):
                weight = ex["absolute_weight_kg"]
                execute(conn, """
                    INSERT INTO training_log_exercises
                        (log_id, session_exercise_id, exercise_id, exercise_name, sets_completed,
                         reps_per_set, weight_kg, prescribed_weight_kg, weight_deviation_kg, make_rate)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (log_id, se_id, ex["exercise_id"], ex["exercise_name"], ex["sets"], [ex["reps"]],
                      weight if weight is not None else 0, weight, 0 if weight is not None else None,
                      make_rate if ex["intensity_reference"] in COMP_LIFT_REFS else None))
    conn.commit()
    logger.info(f"Imported program {program_id}: {weeks} weeks, {len(program.sessions)} sessions"
                + (" + training logs" if log_as_completed else ""))

    if log_as_completed:
        from feedback import compute_outcome, save_outcome
        outcome = compute_outcome(program_id, athlete_id, conn)
        save_outcome(outcome, conn)           # sets status='completed', end_date=today, outcome_summary
        execute(conn, "UPDATE generated_programs SET end_date = %s WHERE id = %s", (end_date, program_id))
        conn.commit()
    return program_id


# ── CLI ──────────────────────────────────────────────────────────────────────

def print_dry_run(program: ParsedProgram, exercise_lookup: dict[str, int] | None) -> None:
    unmapped = resolve_catalogue_ids(program, exercise_lookup) if exercise_lookup is not None else []
    print(f"{len(program.weeks)} weeks, {len(program.sessions)} sessions, "
          f"{program.sessions_per_week}/week, {sum(len(d.exercises) for _, d in program.sessions)} prescriptions")
    for week in program.weeks:
        print(f"\nWeek {week.number} (sheet week {week.file_week}, block {week.block}) — {week.cycle}")
        for day in week.days:
            totals = f"  [sheet: {day.total_reps} reps, avg {day.avg_intensity}%]" if day.total_reps else ""
            print(f"  Day {day.number}{totals}")
            for ex in day.exercises:
                pct = f"{ex.intensity_pct:g}% of {ex.intensity_reference}" if ex.intensity_pct is not None else "unloaded"
                eid = f"#{ex.exercise_id}" if ex.exercise_id else ("?" if exercise_lookup is not None else "")
                flags = " MAX" if ex.is_max_attempt else ""
                notes = f"  ({ex.notes})" if ex.notes else ""
                print(f"    {ex.exercise_name:<40} {ex.sets}x{ex.reps:<3} {pct:<22} {eid}{flags}{notes}")
    if exercise_lookup is not None:
        print(f"\nunmapped sheet names ({len(unmapped)}): {unmapped}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--athlete-id", type=int, required=True)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--phase", default=DEFAULT_PHASE)
    ap.add_argument("--start-date", type=date.fromisoformat, default=None,
                    help="first session date (default: today − 7×weeks so the last week is 'recent')")
    ap.add_argument("--make-rate", type=float, default=1.0, help="logged make rate on competition lifts")
    ap.add_argument("--log-as-completed", action="store_true",
                    help="also write training logs and compute the outcome summary")
    ap.add_argument("--replace", action="store_true", help="delete a previous import of the same file first")
    ap.add_argument("--dry-run", action="store_true", help="parse and print; no writes")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    program = load_program_csv(args.file)
    source_name = args.file.name
    settings = Settings()
    conn = get_connection(settings.database_url)
    try:
        if args.dry_run:
            lookup = {r["name"].lower(): r["id"] for r in fetch_all(conn, "SELECT id, name FROM exercises")}
            print_dry_run(program, lookup)
            previous = find_previous_import(conn, args.athlete_id, source_name)
            if previous:
                print(f"\nprevious import(s) of this file for athlete {args.athlete_id}: {previous} (use --replace)")
            return 0
        previous = find_previous_import(conn, args.athlete_id, source_name)
        if previous and not args.replace:
            print(f"already imported as program(s) {previous}; pass --replace to redo", file=sys.stderr)
            return 1
        if previous:
            delete_import(conn, previous)
            logger.info(f"Deleted previous import(s) {previous}")
        start = args.start_date or (date.today() - timedelta(days=7 * len(program.weeks)))
        program_id = write_program(
            conn, args.athlete_id, program, name=args.name, phase=args.phase, start_date=start,
            source_name=source_name, log_as_completed=args.log_as_completed, make_rate=args.make_rate,
        )
        print(f"imported program {program_id} for athlete {args.athlete_id} "
              f"({len(program.weeks)} weeks from {start})")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
