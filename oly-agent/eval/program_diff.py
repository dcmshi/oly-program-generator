#!/usr/bin/env python3
"""
Program diff: put two or more generated programs side by side, week by week
and day by day, and summarise what varies — for reading model baselines
against each other (MODEL-2) or a regenerated program against its predecessor.

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.program_diff 12 20 21 [--week 1] [--md out.md]

For every (week, day) present in any program it prints one table with a
column per program (`Exercise sets×reps @pct%`, warm-ups marked `w`), then a
summary per program: rows per session, distinct exercises, warm-up rows,
mean working intensity, exercises shared with / unique to the first program,
and the per-session Jaccard overlap of exercise sets with the first program.
Read-only; needs the DB.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.constants import WARMUP_VOLUME_EXCLUSION_PCT

Row = dict  # exercise_name, exercise_order, sets, reps, intensity_pct, rpe_target


def load_programs(conn, program_ids: list[int]) -> dict[int, dict]:
    """{program_id: {"name": str, "sessions": {(week, day): [rows…]}}}"""
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM generated_programs WHERE id = ANY(%s)", (program_ids,))
    names = dict(cur.fetchall())
    missing = [p for p in program_ids if p not in names]
    if missing:
        raise SystemExit(f"no such program(s): {missing}")
    cur.execute(
        """
        SELECT s.program_id, s.week_number, s.day_number, s.focus_area,
               e.exercise_name, e.exercise_order, e.sets, e.reps, e.intensity_pct, e.rpe_target, e.intensity_reference
          FROM program_sessions s JOIN session_exercises e ON e.session_id = s.id
         WHERE s.program_id = ANY(%s)
         ORDER BY s.program_id, s.week_number, s.day_number, e.exercise_order
        """,
        (program_ids,),
    )
    out = {pid: {"name": names[pid], "sessions": defaultdict(list), "focus": {}} for pid in program_ids}
    for pid, week, day, focus, name, order, sets, reps, pct, rpe, ref in cur.fetchall():
        out[pid]["sessions"][(week, day)].append(
            {"exercise_name": name, "exercise_order": order, "sets": sets, "reps": reps,
             "intensity_pct": float(pct) if pct is not None else None,
             "rpe_target": float(rpe) if rpe is not None else None, "intensity_reference": ref}
        )
        out[pid]["focus"][(week, day)] = focus
    return out


def is_warmup(row: Row) -> bool:
    return row["intensity_pct"] is not None and row["intensity_pct"] <= WARMUP_VOLUME_EXCLUSION_PCT


def fmt_row(row: Row) -> str:
    if row["intensity_pct"] is not None:
        pct = f"@{row['intensity_pct']:g}%"
    elif row.get("intensity_reference") == "bodyweight":
        pct = "@BW"
    else:
        pct = f"@RPE{row['rpe_target']:g}" if row.get("rpe_target") is not None else "@—"
    return f"{'w ' if is_warmup(row) else ''}{row['exercise_name']} {row['sets']}×{row['reps']} {pct}"


def session_table(programs: dict[int, dict], key: tuple[int, int]) -> list[str]:
    """Markdown table for one (week, day): one column per program, rows aligned by position."""
    pids = list(programs)
    columns = [programs[p]["sessions"].get(key, []) for p in pids]
    depth = max((len(c) for c in columns), default=0)
    focus = next((programs[p]["focus"].get(key) for p in pids if programs[p]["focus"].get(key)), "")
    lines = [f"### Week {key[0]} Day {key[1]}" + (f" — {focus}" if focus else ""),
             "| # | " + " | ".join(f"{p}" for p in pids) + " |",
             "|---|" + "---|" * len(pids)]
    for i in range(depth):
        cells = [fmt_row(c[i]) if i < len(c) else "—" for c in columns]
        lines.append(f"| {i + 1} | " + " | ".join(cells) + " |")
    return lines


def working_names(rows: list[Row]) -> set[str]:
    return {r["exercise_name"] for r in rows if not is_warmup(r)}


def summarise(programs: dict[int, dict]) -> list[dict]:
    """One summary dict per program; overlap fields are relative to the first program."""
    pids = list(programs)
    base = programs[pids[0]]
    base_all = set().union(*(working_names(r) for r in base["sessions"].values())) if base["sessions"] else set()
    out = []
    for pid in pids:
        prog = programs[pid]
        rows = [r for rs in prog["sessions"].values() for r in rs]
        work = [r for r in rows if not is_warmup(r) and r["intensity_pct"] is not None]
        names = set().union(*(working_names(r) for r in prog["sessions"].values())) if prog["sessions"] else set()
        jaccards = []
        for key, base_rows in base["sessions"].items():
            a, b = working_names(base_rows), working_names(prog["sessions"].get(key, []))
            if a or b:
                jaccards.append(len(a & b) / len(a | b))
        out.append({
            "program": pid, "name": prog["name"], "sessions": len(prog["sessions"]), "rows": len(rows),
            "rows_per_session": round(len(rows) / len(prog["sessions"]), 1) if prog["sessions"] else 0,
            "warmup_rows": sum(1 for r in rows if is_warmup(r)),
            "distinct_exercises": len(names),
            "mean_working_pct": round(sum(r["intensity_pct"] for r in work) / len(work), 1) if work else None,
            "mean_working_rpe": round(sum(r["rpe_target"] for r in work if r["rpe_target"] is not None)
                                      / max(1, sum(1 for r in work if r["rpe_target"] is not None)), 2),
            "shared_with_first": len(names & base_all), "unique_vs_first": sorted(names - base_all),
            "missing_vs_first": sorted(base_all - names),
            "session_jaccard_vs_first": round(sum(jaccards) / len(jaccards), 2) if jaccards else None,
        })
    return out


def render(programs: dict[int, dict], week: int | None = None) -> str:
    keys = sorted({k for p in programs.values() for k in p["sessions"]})
    if week is not None:
        keys = [k for k in keys if k[0] == week]
    lines = ["# Program diff", ""]
    for pid, prog in programs.items():
        lines.append(f"- **{pid}** — {prog['name']}")
    lines.append("")
    for key in keys:
        lines.extend(session_table(programs, key))
        lines.append("")
    lines.append("## Summary (overlap fields are vs the first program)")
    lines.append("")
    lines.append("| program | sessions | rows | rows/sess | warm-ups | distinct ex | mean work % | mean RPE | shared w/ first | session Jaccard |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    summary = summarise(programs)
    for s in summary:
        lines.append(f"| {s['program']} | {s['sessions']} | {s['rows']} | {s['rows_per_session']} | {s['warmup_rows']} | "
                     f"{s['distinct_exercises']} | {s['mean_working_pct']} | {s['mean_working_rpe']} | {s['shared_with_first']} | "
                     f"{s['session_jaccard_vs_first'] if s['session_jaccard_vs_first'] is not None else '—'} |")
    lines.append("")
    for s in summary[1:]:
        if s["unique_vs_first"]:
            lines.append(f"- **{s['program']}** adds: {', '.join(s['unique_vs_first'])}")
        if s["missing_vs_first"]:
            lines.append(f"- **{s['program']}** drops: {', '.join(s['missing_vs_first'])}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program_ids", type=int, nargs="+", help="two or more generated_programs.id; the first is the reference")
    ap.add_argument("--week", type=int, default=None, help="only this week")
    ap.add_argument("--md", type=Path, default=None, help="also write the markdown here")
    args = ap.parse_args()
    if len(args.program_ids) < 2:
        ap.error("give at least two program ids")

    import psycopg2

    from shared.config import Settings

    conn = psycopg2.connect(Settings().database_url)
    try:
        programs = load_programs(conn, args.program_ids)
    finally:
        conn.close()
    text = render(programs, args.week)
    print(text)
    if args.md:
        args.md.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
