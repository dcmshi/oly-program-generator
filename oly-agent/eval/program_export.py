# oly-agent/eval/program_export.py
"""
Render one generated program as a standalone HTML document (and, with Chrome
installed, a PDF) for sharing outside the app — e.g. sending a block to a coach.

    PYTHONUTF8=1 uv run python -m eval.program_export 29                 # → eval/exports/program_29.html
    PYTHONUTF8=1 uv run python -m eval.program_export 29 --pdf           # + program_29.pdf via headless Chrome
    PYTHONUTF8=1 uv run python -m eval.program_export 29 --pdf --out ~/Desktop

The document is coach-facing: the plan and rationale up front, then every
session as a table (order, exercise, sets × reps, kg, % of which max, RPE,
rest, notes), warm-ups marked, maxes and the intensity waves per week at the
end. No app chrome, no login — it reads the database directly.
"""

import argparse
import html
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared.config import Settings  # noqa: E402
from shared.db import fetch_all, fetch_one, get_connection  # noqa: E402
from shared.exercise_mapping import is_competition_lift, is_warmup_set  # noqa: E402

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

CSS = """
:root { --ink:#1c1b19; --muted:#6b675f; --line:#d9d4c8; --paper:#fffdf8; --accent:#1f3a5f; }
* { box-sizing: border-box; }
body { font: 11pt/1.45 Georgia, "Times New Roman", serif; color: var(--ink); background: var(--paper); margin: 0; padding: 28px 36px; }
h1 { font-size: 22pt; margin: 0 0 4px; color: var(--accent); }
h2 { font-size: 15pt; margin: 26px 0 8px; color: var(--accent); border-bottom: 1px solid var(--line); padding-bottom: 3px; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 16px 0 6px; page-break-after: avoid; }
.meta { color: var(--muted); font-size: 10pt; margin-bottom: 14px; }
.meta b { color: var(--ink); }
.rationale { white-space: pre-wrap; font-size: 10.5pt; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; page-break-inside: avoid; margin-bottom: 10px; }
th, td { border-bottom: 1px solid var(--line); padding: 3px 6px; vertical-align: top; text-align: left; }
th { font-family: Helvetica, Arial, sans-serif; font-size: 8.5pt; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.warmup td { color: var(--muted); }
tr.warmup td.name::after { content: " · warm-up"; font-size: 8pt; }
td.note { color: var(--muted); font-size: 8.5pt; }
.session { page-break-inside: avoid; margin-bottom: 14px; }
.wave td, .wave th { text-align: right; } .wave th:first-child, .wave td:first-child { text-align: left; }
.deload { color: var(--accent); font-weight: bold; }
@page { size: A4; margin: 14mm 12mm; }
@media print { body { padding: 0; } h2 { page-break-before: always; } h2.first { page-break-before: auto; } }
"""


def fmt_kg(kg):
    return f"{float(kg):g} kg" if kg is not None else ""


def fmt_pct(pct, ref):
    if pct is None:
        return "BW" if ref == "bodyweight" else ""
    return f"{float(pct):g}% {ref.replace('_', ' ')}" if ref and ref not in ("none",) else f"{float(pct):g}%"


def render(program: dict, athlete: dict, sessions: list[dict], exercises: dict[int, list[dict]], maxes: list[dict]) -> str:
    e = html.escape
    name = program["name"]
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(name)}</title><style>{CSS}</style></head><body>"]
    out.append(f"<h1>{e(name)}</h1>")
    out.append(
        "<div class='meta'>"
        f"<b>{e(athlete.get('name') or '')}</b> · {e(athlete.get('level') or '')} · "
        f"{program['duration_weeks']} weeks × {program['sessions_per_week']} sessions · phase <b>{e(program['phase'])}</b> · "
        f"status {e(program['status'])} · generated {program['created_at']:%Y-%m-%d}"
        "</div>"
    )
    if maxes:
        out.append("<table><tr><th>Max on file</th><th class='num'>kg</th><th>date</th></tr>")
        for m in maxes:
            out.append(f"<tr><td>{e(m['exercise_name'])}</td><td class='num'>{fmt_kg(m['weight_kg'])}</td><td>{m['date_achieved']:%Y-%m-%d}</td></tr>")
        out.append("</table>")
    if program.get("rationale"):
        out.append("<h2 class='first'>Plan and rationale</h2>")
        out.append(f"<div class='rationale'>{e(program['rationale'])}</div>")

    by_week: dict[int, list[dict]] = defaultdict(list)
    for s in sessions:
        by_week[s["week_number"]].append(s)
    for week in sorted(by_week):
        out.append(f"<h2>Week {week}</h2>")
        for s in sorted(by_week[week], key=lambda x: x["day_number"]):
            label = s.get("session_label") or s.get("focus_area") or ""
            dur = f" · ~{s['estimated_duration_minutes']} min" if s.get("estimated_duration_minutes") else ""
            out.append(f"<div class='session'><h3>Day {s['day_number']} — {e(label)}{dur}</h3>")
            out.append("<table><tr><th>#</th><th>Exercise</th><th class='num'>Sets × reps</th><th class='num'>Load</th>"
                       "<th>Intensity</th><th class='num'>RPE</th><th class='num'>Rest</th><th>Notes</th></tr>")
            for ex in exercises.get(s["id"], []):
                warm = (is_competition_lift(ex["exercise_name"], ex["intensity_reference"])
                        and is_warmup_set(ex["intensity_reference"], ex["intensity_pct"]))
                note = ex.get("notes") or ""
                if ex.get("is_max_attempt"):
                    note = ("max attempt. " + note).strip()
                rpe = f"{float(ex['rpe_target']):g}" if ex["rpe_target"] is not None else ""
                rest = f"{ex['rest_seconds']}s" if ex["rest_seconds"] else ""
                out.append(
                    f"<tr class='{'warmup' if warm else ''}'><td class='num'>{ex['exercise_order']}</td>"
                    f"<td class='name'>{e(ex['exercise_name'])}</td>"
                    f"<td class='num'>{ex['sets']} × {ex['reps']}</td>"
                    f"<td class='num'>{fmt_kg(ex['absolute_weight_kg'])}</td>"
                    f"<td>{e(fmt_pct(ex['intensity_pct'], ex['intensity_reference']))}</td>"
                    f"<td class='num'>{rpe}</td><td class='num'>{rest}</td><td class='note'>{e(note)}</td></tr>"
                )
            if s.get("notes"):
                out.append(f"<div class='note' style='font-size:9pt;color:var(--muted)'>{e(s['notes'])}</div>")
            out.append("</table></div>")

    # weekly wave summary
    out.append("<h2>Weekly loading</h2><table class='wave'><tr><th>Week</th><th>Comp-lift reps ≥ 65%</th><th>Top comp-lift %</th><th>Sets</th></tr>")
    comp_refs = {"snatch", "clean_and_jerk", "clean", "jerk"}
    for week in sorted(by_week):
        rows = [ex for s in by_week[week] for ex in exercises.get(s["id"], [])]
        comp = [ex for ex in rows if ex["intensity_reference"] in comp_refs and ex["intensity_pct"] is not None
                and not any(m in ex["exercise_name"].lower() for m in ("pull", "deadlift", "extension"))]
        reps = sum(ex["sets"] * ex["reps"] for ex in comp if float(ex["intensity_pct"]) >= 65)
        top = max((float(ex["intensity_pct"]) for ex in comp), default=0)
        deload = " <span class='deload'>deload</span>" if any(s.get("session_label", "") and "deload" in (s.get("notes") or "").lower() for s in by_week[week]) else ""
        out.append(f"<tr><td>Week {week}{deload}</td><td>{reps}</td><td>{top:g}%</td><td>{sum(ex['sets'] for ex in rows)}</td></tr>")
    out.append("</table>")
    out.append("<div class='meta' style='margin-top:18px'>Loads are % of the max on file (estimated where none is recorded). "
               "Warm-up sets are the greyed rows. Generated by the oly-program-generator agent; review before use.</div>")
    out.append("</body></html>")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program_id", type=int)
    ap.add_argument("--pdf", action="store_true", help="also print to PDF with headless Chrome")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "exports")
    args = ap.parse_args(argv)

    conn = get_connection(Settings().database_url)
    program = fetch_one(conn, "SELECT * FROM generated_programs WHERE id = %s", (args.program_id,))
    if not program:
        sys.exit(f"no program {args.program_id}")
    athlete = fetch_one(conn, "SELECT name, level FROM athletes WHERE id = %s", (program["athlete_id"],)) or {}
    sessions = fetch_all(conn, "SELECT * FROM program_sessions WHERE program_id = %s ORDER BY week_number, day_number", (args.program_id,))
    rows = fetch_all(conn, """SELECT se.* FROM session_exercises se JOIN program_sessions ps ON ps.id = se.session_id
                              WHERE ps.program_id = %s ORDER BY se.session_id, se.exercise_order""", (args.program_id,))
    exercises: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        exercises[r["session_id"]].append(r)
    maxes = fetch_all(conn, """SELECT e.name AS exercise_name, am.weight_kg, am.date_achieved FROM athlete_maxes am
                               JOIN exercises e ON e.id = am.exercise_id WHERE am.athlete_id = %s AND am.max_type = 'current'
                               ORDER BY e.name""", (program["athlete_id"],))

    out_dir = args.out.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"program_{args.program_id}.html"
    html_path.write_text(render(program, athlete, sessions, exercises, maxes), encoding="utf-8")
    print(f"HTML: {html_path}")
    if args.pdf:
        chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
        if not chrome:
            sys.exit("Chrome not found — open the HTML and print to PDF from the browser")
        pdf_path = out_dir / f"program_{args.program_id}.pdf"
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True, timeout=120)
        print(f"PDF:  {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
