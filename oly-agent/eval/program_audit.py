#!/usr/bin/env python3
"""
Whole-program audit (AGENT-0a) over stored programs. No LLM calls.

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.program_audit 29 37 43 58
    PYTHONUTF8=1 uv run python -m eval.program_audit 58 --all          # every finding
    PYTHONUTF8=1 uv run python -m eval.program_audit 58 --store        # write generation_params.audit

The plan comes back from each session's first logged prompt (week ceiling /
floor, deload flag, session rep target, primary movement — see
`program_audit.session_plan_from_prompt`), so programs generated before the
plan snapshot (RESUME-1) are audited the same way as new ones. The coach
record is `generation_params.coach`; the avoid list and the faults come from
`athlete_snapshot`, with the coach's overrides on top. Imported programs have
no generation log and are skipped.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT = _HERE.parent
_REPO = _AGENT.parent
for p in (str(_REPO), str(_AGENT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from program_audit import (  # noqa: E402
    audit_program,
    audit_summary,
    load_citation_context,
    load_program_sessions,
    session_plans_from_log,
)

from shared.config import Settings  # noqa: E402
from shared.db import execute, fetch_all, get_connection  # noqa: E402


def audit_stored_program(conn, program_id: int) -> dict | None:
    """The audit report for one stored program, or None without a generation log."""
    rows = fetch_all(conn, "SELECT generation_params, athlete_snapshot FROM generated_programs WHERE id = %s",
                     (program_id,))
    if not rows:
        raise SystemExit(f"program {program_id} not found")
    plans = session_plans_from_log(conn, program_id)
    if not plans:
        return None
    params = rows[0]["generation_params"] or {}
    snapshot = rows[0]["athlete_snapshot"] or {}
    coach = params.get("coach") or {}
    avoid = ((snapshot.get("exercise_preferences") or {}).get("avoid")) or []
    faults = coach.get("technical_faults") if coach.get("technical_faults") is not None \
        else (snapshot.get("technical_faults") or [])
    sessions = load_program_sessions(conn, program_id)
    retrieval_sets, chunk_texts = load_citation_context(conn, program_id)
    return audit_program(sessions, plans, coach=coach, avoid=avoid, faults=faults,
                         retrieval_sets=retrieval_sets, chunk_texts=chunk_texts)


def _print_report(program_id: int, report: dict, show_all: bool) -> None:
    print(f"\n## Program {program_id}")
    print(f"  counts: {report['counts']}")
    by_check = Counter((f["check"], f["severity"]) for f in report["findings"])
    print("  by check: " + ", ".join(f"{c}/{s} {n}" for (c, s), n in sorted(by_check.items())))
    print(f"  citations: {report['citations']}")
    print("  week  deload  comp reps / target  top %  floor–ceiling")
    for w in report["weeks"]:
        target = w["target"] if w["target"] is not None else "?"
        top = f"{w['top_pct']:g}" if w["top_pct"] is not None else "-"
        band = f"{w['floor']:g}–{w['ceiling']:g}" if w["floor"] is not None and w["ceiling"] is not None else "?"
        print(f"  {w['week']:>4}  {'yes' if w['deload'] else '':>6}  {w['comp_reps']:>9} / {target:<7}  "
              f"{top:>5}  {band}")
    if show_all:
        for f in report["findings"]:
            where = "".join([f"W{f['week']}" if f.get("week") is not None else "",
                             f"D{f['day']}" if f.get("day") is not None else ""])
            ex = f" {f['exercise']}:" if f.get("exercise") else ""
            print(f"  - {f['severity']} [{f['check']}] {where}{ex} {f['message']}")
    else:
        print(audit_summary(report).replace("\n", "\n  ").join(["  ", ""]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic whole-program audit (AGENT-0a)")
    parser.add_argument("program_ids", type=int, nargs="+")
    parser.add_argument("--all", action="store_true", help="print every finding, not the summary")
    parser.add_argument("--json", type=Path, help="write {program_id: report} here")
    parser.add_argument("--store", action="store_true", help="write each report to generation_params.audit")
    args = parser.parse_args(argv)
    conn = get_connection(Settings().database_url)
    out = {}
    try:
        for pid in args.program_ids:
            report = audit_stored_program(conn, pid)
            if report is None:
                print(f"\n## Program {pid}: no generation log (imported?) — skipped")
                continue
            out[pid] = report
            _print_report(pid, report, args.all)
            if args.store:
                execute(conn, "UPDATE generated_programs SET generation_params = "
                              "COALESCE(generation_params, '{}'::jsonb) || jsonb_build_object('audit', %s::jsonb) "
                              "WHERE id = %s", (json.dumps(report), pid))
                conn.commit()
    finally:
        conn.close()
    if args.json:
        args.json.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
