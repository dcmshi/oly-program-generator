# oly-agent/macrocycle.py
"""
Macrocycles (PLAN-3e): the whole block sequence to a meet date (or a chosen
length) planned up front, then generated one block at a time.

The plan is laid out *backward* from the end: realization (the peak or a
max-test block), intensification, then accumulation blocks of the level's
default length; a plan of MACROCYCLE_GENERAL_PREP_MIN_WEEKS or more opens with
general prep. Leftover weeks too short for a block are folded into a
neighbour (up to the level's maximum), otherwise that block is shortened.

When a block completes, `reflow` re-fits what remains:
  * competition date → the tail is re-planned from the weeks actually left, so
    a late or early finish never pushes the peak off the meet;
  * a held phase (the outcome verdict repeats it) becomes the next block —
    without a meet it is inserted and the plan grows; with a meet it replaces
    the next preparatory block, and is skipped when only intensification /
    realization remain (the date wins).

Block status is not stored: a block with a generated_programs row
(`macrocycle_block_index`) takes that program's status, the rest are planned.
"""

import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_profiles import PHASE_PROFILES

from shared.constants import (
    BLOCK_WEEKS_DEFAULT_BY_LEVEL,
    BLOCK_WEEKS_MAX_BY_LEVEL,
    BLOCK_WEEKS_MIN,
    MACROCYCLE_GENERAL_PREP_MIN_WEEKS,
    MACROCYCLE_WEEKS_DEFAULT,
    MACROCYCLE_WEEKS_MAX,
    MACROCYCLE_WEEKS_MIN,
)
from shared.db import execute, execute_returning, fetch_one

logger = logging.getLogger(__name__)

# The date fixes these: a hold never displaces them.
_PROTECTED_PHASES = frozenset({"intensification", "realization"})


def _level(level: str) -> str:
    return level if level in BLOCK_WEEKS_MAX_BY_LEVEL else "intermediate"


def block_weeks(level: str, phase: str) -> int:
    """The level's default length for a phase (same rule as plan.resolve_block_length)."""
    default = PHASE_PROFILES[phase]["default_weeks"]
    return min(BLOCK_WEEKS_DEFAULT_BY_LEVEL.get(_level(level), {}).get(phase, default),
               BLOCK_WEEKS_MAX_BY_LEVEL[_level(level)])


def macrocycle_weeks(weeks_to_competition: int | None, requested: int | None) -> int:
    """Total weeks: the meet decides when there is one, else the request (clamped).
    Raises ValueError when the meet is too close for more than one block."""
    if weeks_to_competition is not None:
        if weeks_to_competition < MACROCYCLE_WEEKS_MIN:
            raise ValueError(f"The competition is {weeks_to_competition} weeks out — too close for a "
                             f"macrocycle (minimum {MACROCYCLE_WEEKS_MIN}); generate a single block instead.")
        return min(weeks_to_competition, MACROCYCLE_WEEKS_MAX)
    weeks = MACROCYCLE_WEEKS_DEFAULT if requested is None else int(requested)
    return max(MACROCYCLE_WEEKS_MIN, min(MACROCYCLE_WEEKS_MAX, weeks))


def plan_blocks(level: str, total_weeks: int, *, competition: bool) -> list[dict]:
    """Lay `total_weeks` out backward from the end. Returns [{phase, weeks, note}]."""
    top = BLOCK_WEEKS_MAX_BY_LEVEL[_level(level)]
    real = {"phase": "realization", "weeks": min(block_weeks(level, "realization"), total_weeks),
            "note": "peak for the meet" if competition else "max-test block"}
    rest = total_weeks - real["weeks"]
    if rest < BLOCK_WEEKS_MIN:
        real["weeks"] = min(top, real["weeks"] + rest)
        return [real]
    inten = {"phase": "intensification", "weeks": min(block_weeks(level, "intensification"), rest), "note": ""}
    prep_weeks = rest - inten["weeks"]
    if prep_weeks < BLOCK_WEEKS_MIN:
        # a week or so short of a block: lengthen the later blocks, never a 1-week block
        for b in (inten, real):
            extra = min(prep_weeks, top - b["weeks"])
            b["weeks"] += extra
            prep_weeks -= extra
        return [inten, real]

    # Split the preparatory weeks into near-equal blocks of about the level's
    # accumulation length (≤ the level maximum), longest first.
    default = block_weeks(level, "accumulation")
    n = max(1, round(prep_weeks / default))
    while -(-prep_weeks // n) > top:
        n += 1
    sizes = [prep_weeks // n + (1 if i < prep_weeks % n else 0) for i in range(n)]
    prep = [{"phase": "accumulation", "weeks": w, "note": ""} for w in sizes]
    if total_weeks >= MACROCYCLE_GENERAL_PREP_MIN_WEEKS and n >= 2:
        prep[0]["phase"] = "general_prep"
    return prep + [inten, real]


def reflow(blocks: list[dict], completed_index: int, verdict: dict | None, level: str,
           weeks_to_competition: int | None) -> tuple[list[dict], str]:
    """The block list after block `completed_index` finished, plus a one-line
    explanation for the athlete (empty when the plan simply continues)."""
    done = blocks[:completed_index + 1]
    held_phase = done[-1]["phase"]
    verdict = verdict or {}
    hold = (held_phase != "realization"
            and verdict.get("next_phase") == held_phase
            and not verdict.get("advanced", False))
    reason = verdict.get("reason") or "thresholds not met"

    if weeks_to_competition is not None:
        if held_phase == "realization":
            return done, ""                  # the peak is done — never plan a second one
        tail = (plan_blocks(level, weeks_to_competition, competition=True)
                if weeks_to_competition >= BLOCK_WEEKS_MIN else [])
        if not hold:
            return done + tail, ""
        if tail and tail[0]["phase"] not in _PROTECTED_PHASES:
            tail[0] = {**tail[0], "phase": held_phase, "note": f"repeat — {reason}"}
            return done + tail, f"{held_phase.replace('_', ' ').title()} repeated: {reason}."
        return done + tail, (f"{held_phase.replace('_', ' ').title()} would repeat ({reason}), but the meet "
                             f"is {weeks_to_competition} weeks out — the plan keeps its peak.")

    tail = [dict(b) for b in blocks[completed_index + 1:]]
    if hold:
        repeat = {"phase": held_phase, "weeks": done[-1]["weeks"], "note": f"repeat — {reason}"}
        return done + [repeat] + tail, f"{held_phase.replace('_', ' ').title()} repeated: {reason}."
    return done + tail, ""


def block_rows(blocks: list[dict], programs: list[dict]) -> list[dict]:
    """Blocks joined with their programs for display: each gets `index`,
    `status` (planned or the program's status), `program_id` and `start_week`."""
    by_index = {p["macrocycle_block_index"]: p for p in programs if p.get("macrocycle_block_index") is not None}
    rows, week = [], 1
    for i, b in enumerate(blocks):
        prog = by_index.get(i)
        rows.append({**b, "index": i, "start_week": week,
                     "status": prog["status"] if prog else "planned",
                     "program_id": prog["id"] if prog else None})
        week += b["weeks"]
    return rows


# ── DB (psycopg2; the pipeline and complete_program's sync connection) ──

def create_macrocycle(conn, athlete_id: int, blocks: list[dict], competition_date: date | None) -> int:
    """Store a new plan; any macrocycle still active for the athlete is abandoned
    (one plan at a time). The caller commits."""
    execute(conn, "UPDATE macrocycles SET status = 'abandoned', updated_at = now() "
                  "WHERE athlete_id = %s AND status = 'active'", (athlete_id,))
    return execute_returning(
        conn,
        "INSERT INTO macrocycles (athlete_id, competition_date, blocks) VALUES (%s, %s, %s) RETURNING id",
        (athlete_id, competition_date, json.dumps(blocks)),
    )


def load_macrocycle(conn, macrocycle_id: int, athlete_id: int) -> dict | None:
    return fetch_one(conn, "SELECT * FROM macrocycles WHERE id = %s AND athlete_id = %s",
                     (macrocycle_id, athlete_id))


def next_block_index(conn, macrocycle_id: int) -> int:
    row = fetch_one(conn, "SELECT COALESCE(MAX(macrocycle_block_index), -1) + 1 AS nxt "
                          "FROM generated_programs WHERE macrocycle_id = %s", (macrocycle_id,))
    return int(row["nxt"]) if row else 0


def reflow_after_completion(conn, program_id: int, outcome, today: date | None = None) -> dict | None:
    """Re-fit the program's macrocycle after it completed. Returns
    {"macrocycle_id", "next_index", "message", "finished"} or None when the
    program is not part of an active macrocycle. The caller commits."""
    prog = fetch_one(conn, "SELECT athlete_id, macrocycle_id, macrocycle_block_index, "
                           "athlete_snapshot->>'level' AS level FROM generated_programs WHERE id = %s",
                     (program_id,))
    if not prog or prog["macrocycle_id"] is None or prog["macrocycle_block_index"] is None:
        return None
    mc = load_macrocycle(conn, prog["macrocycle_id"], prog["athlete_id"])
    if not mc or mc["status"] != "active":
        return None
    today = today or date.today()
    weeks_left = (mc["competition_date"] - today).days // 7 if mc["competition_date"] else None
    verdict = getattr(outcome, "phase_verdict", None)
    if verdict is not None and not isinstance(verdict, dict):
        verdict = verdict.model_dump()
    idx = prog["macrocycle_block_index"]
    blocks, message = reflow(mc["blocks"], idx, verdict, prog["level"] or "intermediate", weeks_left)
    finished = idx + 1 >= len(blocks)
    execute(conn, "UPDATE macrocycles SET blocks = %s, status = %s, updated_at = now() WHERE id = %s",
            (json.dumps(blocks), "completed" if finished else "active", mc["id"]))
    if message:
        logger.info(f"Macrocycle {mc['id']} re-flowed after block {idx}: {message}")
    return {"macrocycle_id": mc["id"], "next_index": idx + 1, "message": message, "finished": finished}

