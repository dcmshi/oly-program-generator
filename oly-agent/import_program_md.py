#!/usr/bin/env python3
"""
Import a coach-written program sent as Markdown / plain text (Google Docs
"Download as Markdown") as a program to train, optionally activating it.

    cd oly-agent
    PYTHONUTF8=1 uv run python import_program_md.py --file "<program>.md" --athlete-id 1 --dry-run
    PYTHONUTF8=1 uv run python import_program_md.py --file "<program>.md" --athlete-id 1 --activate

Layout the parser understands (Paul's "8-Week 4 or 5 Day" doc):

    **Week 1**                                         week header (bold optional)
    **Day 1**  /  **Day 4 (optional)**                 day header; "(optional)" goes in the label
    **Muscle SN:** 30% 4x5                             bold name, optional colon, then the spec
    **Power Snatch** 50% x3, 60% 2x3, 70% 3x2          comma-separated items, one row each
    **No Foot Clean \\+ Jerk** 50% 2+2, 75% 4x1+2       complexes ("+", Markdown-escaped or not)
    Reverse Lunges: 3x10                               plain name: spec
    DB Incline Press 3x10-15                           plain name, spec from the first number
    **RDL** 80% of CL 3x5 / **RDL (% of CL)** 60-70% 5x6   percentage of another lift
    **Core**                                           no spec — stored as "unspecified"

Items: ``70%`` / ``70-75%`` (range → low end, range kept in notes) / ``88%++``
then ``x3``, ``2x3``, ``3-4x2`` (sets range → low end), ``3x4-6``, ``2+2``,
``4x1+2``, ``3x60s``, ``3x9/s``, ``2xmax``; anything after the scheme ("or
Inverted Rows 3-4x12", "(light weight)") is kept in notes. Abbreviations are
expanded for the stored name (SN → Snatch, CL → Clean, SQ → Squat, FSQ → Front
Squat, Ext → Extension, OH → Overhead, PP → Push Press) so the percentages
resolve against the right max and the history blocks read cleanly.

Rows are written through ``import_program_csv.write_program`` (same
intensity references, catalogue aliases, kg resolution from current maxes,
complex notation in notes). ``--activate`` then does what the program page's
Activate button does: supersede the athlete's active program, mark this one
active.
"""

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_program_csv import (
    Day,
    ParsedProgram,
    Prescription,
    Week,
    catalogue_name_for,
    clamp_intensity,
    find_previous_import,
    intensity_reference_for,
    parse_reps_cell,
    print_dry_run,
    write_program,
)

from shared.config import Settings
from shared.db import execute, fetch_all, get_connection

logger = logging.getLogger(__name__)

SOURCE = "md_import"
DEFAULT_PHASE = "accumulation"

_WEEK_RE = re.compile(r"^\**\s*Week\s+(\d+)\s*\**\s*$", re.I)
_DAY_RE = re.compile(r"^\**\s*Day\s+(\d+)\s*(\([^)]*\))?\s*\**\s*$", re.I)
_PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*%(\+*)\s*(?:of\s+([A-Za-z]+)\b)?\s*(.*)$", re.I)
_SCHEME_RE = re.compile(
    r"^(?:(\d+)(?:\s*-\s*(\d+))?\s*x\s*|x\s*)?"          # sets (or a range), or a bare leading x
    r"(\d+(?:\+\d+)*)(?:\s*-\s*(\d+))?"                   # reps, a complex, or a reps range
    r"(sec|s|/s|/side)?\b\s*(.*)$", re.I)                 # unit, then whatever is left
_MAX_RE = re.compile(r"^(\d+)\s*x\s*(max|submaximal)\b\s*(.*)$", re.I)
_PLAIN_SPLIT_RE = re.compile(r"^(?P<name>.*?[A-Za-z)])\s+(?P<spec>\d.*)$")

# Whole-word abbreviations in Paul's notation → catalogue words.
_ABBREVIATIONS = [
    (r"\bFSQ\b", "Front Squat"), (r"\bSN\b", "Snatch"), (r"\bCL\b", "Clean"), (r"\bSQ\b", "Squat"),
    (r"\bSq\b", "Squat"), (r"\bExt\b", "Extension"), (r"\bOH\b", "Overhead"), (r"\bPP\b", "Push Press"),
    (r"\bRev\b", "Reverse"), (r"^Front \+", "Front Squat +"),
]
# Names the CSV alias map doesn't know, checked first (lowercased, expanded).
# None = deliberately no catalogue match (the CSV map's "clean" substring
# would otherwise file an RDL "(% of Clean)" under Clean).
_MD_ALIASES: list[tuple[str, str | None]] = [
    ("rdl", None),
    ("no foot snatch", "No Feet Snatch"),
    ("no foot clean", "Clean & Jerk"),
    ("deficit snatch extension", "Snatch Pull from Deficit"),
    ("deficit clean extension", None),
    ("deficit snatch", "Snatch from Deficit"),
    ("block snatch", "Block Snatch (knee)"),
    ("block clean extension", "Clean Extension"),
    ("block clean", "Block Clean (knee)"),
    ("hang snatch", "Hang Snatch (above knee)"),
    ("bulgarian split", "Bulgarian Split Squat"),
    ("box jumps", "Box Jump"),
    ("pendlay row", "Pendlay Row"),
    ("bent over bb row", "Barbell Row"),
    ("back extension", "Back Extension"),
]
_OF_REFS = {"cl": "clean", "clean": "clean", "sn": "snatch", "snatch": "snatch", "pp": "push_press",
            "jerk": "jerk", "fsq": "front_squat", "bsq": "back_squat"}


@dataclass
class MdDay(Day):
    label: str = ""


def expand_name(raw: str) -> str:
    name = raw.replace("\\", "").strip().rstrip(":").strip()
    for pattern, repl in _ABBREVIATIONS:
        name = re.sub(pattern, repl, name)
    return re.sub(r"\s+", " ", name)


def reference_for(name: str, of: str | None) -> str | None:
    """The max a percentage refers to: an explicit "of CL" / "(% of PP)" wins."""
    m = re.search(r"%\s*of\s+([A-Za-z ]+?)\)", name)
    explicit = (of or (m.group(1) if m else "") or "").strip().lower()
    if explicit:
        key = explicit.split()[0] if explicit not in _OF_REFS else explicit
        return _OF_REFS.get(explicit) or _OF_REFS.get(key) or intensity_reference_for(explicit)
    return intensity_reference_for(name)


def catalogue_name(name: str) -> str | None:
    n = name.lower()
    for needle, target in _MD_ALIASES:
        if re.search(rf"\b{re.escape(needle)}", n):          # leading boundary only: "row" matches "Rows"
            return target
    return catalogue_name_for(name)


def parse_scheme(text: str) -> tuple[int, int, list[str]]:
    """``"3-4x2"`` → (3, 2, ["3-4 sets"]). Unparseable → (1, 1, ["<text>"])."""
    raw = text.strip()
    m = _MAX_RE.match(raw)
    if m:
        rest = re.sub(r"^reps\b\s*", "", m.group(3).strip()).strip()
        return int(m.group(1)), 1, [f"{m.group(2).lower()} reps"] + ([rest] if rest else [])
    m = _SCHEME_RE.match(raw)
    if not m:
        return 1, 1, [raw] if raw else ["unspecified"]
    sets_lo, sets_hi, reps, reps_hi, unit, rest = m.groups()
    cell = (f"{sets_lo}x" if sets_lo else "") + reps + (f"-{reps_hi}" if reps_hi else "")
    sets, n_reps, note, _max = parse_reps_cell(cell)
    rest = rest.strip()
    per_side = bool(unit and unit.lower() in ("/s", "/side"))
    if rest.lower().startswith(("/s", "/side")):                 # "30s/s"
        per_side, rest = True, re.sub(r"^/s(ide)?", "", rest, flags=re.I).strip()
    seconds = bool(unit and unit.lower() in ("s", "sec"))
    span = f"{reps}-{reps_hi}" if reps_hi else reps
    notes = [note] if note and not seconds else []                # a timed range is said once, below
    if sets_hi:
        notes.append(f"{sets_lo}-{sets_hi} sets")
    if seconds:
        notes.append(f"{span} s" + (" per side" if per_side else ""))
    elif per_side:
        notes.append(f"{span} per side")
    rest = re.sub(r"^reps\b\s*", "", rest).strip().strip("()").strip()
    if len(rest) > 1:                                              # a lone "k" is a typo
        notes.append(rest)
    return sets, n_reps, notes


def parse_items(name: str, spec: str) -> list[Prescription]:
    """One Prescription per comma-separated item of the spec."""
    out: list[Prescription] = []
    for item in [i.strip() for i in spec.split(",") if i.strip()]:
        m = _PCT_RE.match(item)
        notes: list[str] = []
        pct = ref = None
        if m:
            lo, hi, plus, of, scheme = m.groups()
            pct = float(lo)
            if hi:
                notes.append(f"{lo}-{hi}%")
            if plus:
                notes.append(f"{lo}%{plus}")
            ref = reference_for(name, of)
            if of:
                notes.append(f"% of {of}")
        else:
            scheme = item
        sets, reps, scheme_notes = parse_scheme(scheme)
        notes += scheme_notes
        pct, clamp_note = clamp_intensity(pct)
        if clamp_note:
            notes.append(clamp_note)
        out.append(Prescription(exercise_name=name, sets=sets, reps=reps, intensity_pct=pct,
                                intensity_reference=ref if pct is not None else None,
                                notes="; ".join(notes) or None))
    if not out:
        out.append(Prescription(exercise_name=name, sets=1, reps=1, notes="unspecified"))
    return out


def split_line(line: str) -> tuple[str, str]:
    """(raw name, spec) of one exercise line."""
    if line.startswith("**"):
        end = line.find("**", 2)
        if end > 0:
            return line[2:end], line[end + 2:].strip()
    if ":" in line:
        name, spec = line.split(":", 1)
        return name, spec.strip()
    m = _PLAIN_SPLIT_RE.match(line)
    return (m.group("name"), m.group("spec")) if m else (line, "")


def parse_program_md(text: str) -> ParsedProgram:
    weeks: list[Week] = []
    week: Week | None = None
    day: MdDay | None = None
    for raw in text.splitlines():
        line = raw.replace("\\+", "+").strip()
        if not line:
            continue
        m = _WEEK_RE.match(line)
        if m:
            week = Week(number=len(weeks) + 1, file_week=int(m.group(1)), block=1, cycle="")
            weeks.append(week)
            day = None
            continue
        m = _DAY_RE.match(line)
        if m:
            if week is None:
                raise ValueError(f"Day header before any week header: {line!r}")
            suffix = f" {m.group(2).lower()}" if m.group(2) else ""
            day = MdDay(number=int(m.group(1)), label=f"Day {m.group(1)}{suffix}")
            week.days.append(day)
            continue
        if day is None:
            continue
        raw_name, spec = split_line(line)
        name = expand_name(raw_name)
        if name:
            day.exercises.extend(parse_items(name, spec))
    if not weeks:
        raise ValueError("no 'Week N' headers found")
    return ParsedProgram(weeks=weeks)


def md_session_label(week: Week, day: Day) -> str:
    return f"Week {week.number} · {getattr(day, 'label', '') or f'Day {day.number}'}"


def md_rationale(program: ParsedProgram, source_name: str, title_weeks: int | None) -> str:
    lines = [f"# Imported Program\nCoach-written program imported from `{source_name}`; "
             f"{len(program.weeks)} weeks, {len(program.sessions)} sessions "
             f"({min(len(w.days) for w in program.weeks)}–{program.sessions_per_week} per week)."]
    optional = sorted({d.label for _, d in program.sessions if "optional" in getattr(d, "label", "")})
    if optional:
        lines.append(f"Optional sessions: {', '.join(optional)}.")
    if title_weeks and title_weeks != len(program.weeks):
        lines.append(f"The file is titled {title_weeks} weeks; it holds weeks 1–{len(program.weeks)}.")
    return "\n".join(lines)


def resolve_ids(program: ParsedProgram, lookup: dict[str, int]) -> list[str]:
    unmapped: list[str] = []
    for _, day in program.sessions:
        for ex in day.exercises:
            target = catalogue_name(ex.exercise_name)
            ex.exercise_id = lookup.get(target.lower()) if target else None
            if ex.exercise_id is None and ex.exercise_name not in unmapped:
                unmapped.append(ex.exercise_name)
    return unmapped


def activate(conn, program_id: int, athlete_id: int) -> list[int]:
    """The program page's Activate: supersede the active program, activate this one."""
    superseded = [r["id"] for r in fetch_all(
        conn, "SELECT id FROM generated_programs WHERE athlete_id = %s AND status = 'active' AND id != %s",
        (athlete_id, program_id))]
    execute(conn, "UPDATE generated_programs SET status = 'superseded', updated_at = NOW() "
                  "WHERE athlete_id = %s AND status = 'active' AND id != %s", (athlete_id, program_id))
    execute(conn, "UPDATE generated_programs SET status = 'active', updated_at = NOW() "
                  "WHERE id = %s AND athlete_id = %s AND status = 'draft'", (program_id, athlete_id))
    conn.commit()
    return superseded


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--athlete-id", type=int, required=True)
    ap.add_argument("--name", default=None, help="program name (default: the file name)")
    ap.add_argument("--phase", default=DEFAULT_PHASE)
    ap.add_argument("--start-date", type=date.fromisoformat, default=None, help="first session (default: today)")
    ap.add_argument("--activate", action="store_true", help="make it the athlete's active program")
    ap.add_argument("--replace", action="store_true", help="delete a previous import of the same file first")
    ap.add_argument("--dry-run", action="store_true", help="parse and print; no writes")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    program = parse_program_md(args.file.read_text(encoding="utf-8-sig"))
    source_name = args.file.name
    m = re.match(r"^(\d+)-Week", args.file.stem, re.I)
    title_weeks = int(m.group(1)) if m else None
    name = args.name or f"{args.file.stem} (Paul, imported)"
    conn = get_connection(Settings().database_url)
    try:
        lookup = {r["name"].lower(): r["id"] for r in fetch_all(conn, "SELECT id, name FROM exercises")}
        if args.dry_run:
            unmapped = resolve_ids(program, lookup)
            print_dry_run(program, None)
            print(f"\nno catalogue match ({len(unmapped)}; stored with NULL exercise_id): {unmapped}")
            print("\n" + md_rationale(program, source_name, title_weeks))
            return 0
        previous = find_previous_import(conn, args.athlete_id, source_name, source=SOURCE)
        if previous and not args.replace:
            print(f"already imported as program(s) {previous}; pass --replace to redo", file=sys.stderr)
            return 1
        if previous:
            from import_program_csv import delete_import
            delete_import(conn, previous)
            logger.info(f"Deleted previous import(s) {previous}")
        program_id = write_program(
            conn, args.athlete_id, program, name=name, phase=args.phase,
            start_date=args.start_date or date.today(), source_name=source_name,
            log_as_completed=False, make_rate=1.0, status="draft", source=SOURCE,
            label_fn=md_session_label, rationale=md_rationale(program, source_name, title_weeks),
            resolve_fn=resolve_ids)
        msg = f"imported program {program_id} for athlete {args.athlete_id} ({len(program.weeks)} weeks)"
        if args.activate:
            superseded = activate(conn, program_id, args.athlete_id)
            msg += " — active" + (f"; superseded {superseded}" if superseded else "")
        print(msg)
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
