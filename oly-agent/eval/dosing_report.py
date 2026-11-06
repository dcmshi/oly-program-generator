#!/usr/bin/env python3
"""
Dosing report (DOSE-1c): what the code-sets-the-numbers pass did to generated
programs, read back from `generation_log` and the saved sessions. No LLM calls.

    cd oly-agent
    PYTHONUTF8=1 uv run python -m eval.dosing_report 57 58

Per program:
  - reliability: sessions, first-try OK, attempts per session (the same
    counting as eval/model_baseline), flagged sessions (RETRY-1);
  - dosing edits per session, by kind (ceiling, classic / variant / week rep
    range, strength band, sets toward the session target);
  - competition-lift reps vs the session target on non-deload sessions: the
    model's proposal (raw reply) and the dosed result, as mean |Δ| and the
    share within DOSING_VOLUME_TOLERANCE; deload sessions are only trimmed
    (DOSE-1b), so they count as a miss only when over the target;
  - deload days: the largest set count on one competition-lift row (DOSE-1b);
  - accessories: how many rows, how many distinct, which (DOG-2p);
  - prompt length of first attempts (mean / max chars).

Complexes are counted by their components only in the dosed result; the raw
proposal is counted per row, so a complex in the raw reply reads as its total
reps. Use the numbers to compare programs, not as exact volumes.
"""

import argparse
import re
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT = _HERE.parent
_REPO = _AGENT.parent
for p in (str(_REPO), str(_AGENT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from complexes import comp_reps_per_set  # noqa: E402
from dosing import _counts_for_volume  # noqa: E402
from eval.model_baseline import summarize_log_rows  # noqa: E402

from shared.config import Settings  # noqa: E402
from shared.constants import DOSING_VOLUME_TOLERANCE  # noqa: E402
from shared.db import fetch_all, get_connection  # noqa: E402
from shared.llm import parse_llm_json  # noqa: E402

_TARGET_RE = re.compile(r"Target competition lift reps this session: (\d+)")
_DELOAD_RE = re.compile(r"Deload week: (yes|true)", re.I)
EDIT_KINDS = (
    ("ceiling", re.compile(r"week ceiling")),
    ("classic reps", re.compile(r"classic-lift range")),
    ("variant reps", re.compile(r"variant range")),
    ("week reps", re.compile(r"\breps range")),
    ("strength band", re.compile(r"(squat|pull) (band|reps)")),
    ("sets", re.compile(r"sets \(session target")),
)


def comp_reps(exercises: list[dict]) -> int:
    return sum(int(ex.get("sets") or 1) * comp_reps_per_set(ex)
               for ex in exercises if _counts_for_volume(ex))


def edit_kinds(edits: list[str]) -> dict[str, int]:
    counts = {k: 0 for k, _ in EDIT_KINDS}
    for e in edits:
        for kind, rx in EDIT_KINDS:
            if rx.search(e):
                counts[kind] += 1
    return counts


def _raw_exercises(raw: str | None) -> list[dict]:
    try:
        data = parse_llm_json(raw or "")
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("exercises") or []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def program_report(conn, program_id: int) -> dict:
    rows = fetch_all(conn, """
        SELECT week_number, day_number, attempt_number, status, validation_errors,
               input_tokens, output_tokens, estimated_cost_usd, prompt_text, raw_response, parsed_response
        FROM generation_log WHERE program_id = %s AND week_number IS NOT NULL
        ORDER BY week_number, day_number, attempt_number
    """, (program_id,))
    rel = summarize_log_rows(rows)
    by_session: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        by_session.setdefault((r["week_number"], r["day_number"]), []).append(r)

    edits_per_session, kinds = [], {k: 0 for k, _ in EDIT_KINDS}
    raw_delta, dosed_delta, raw_ok, dosed_ok = [], [], 0, 0
    deload_n, deload_over = 0, 0
    deload_max_sets, flagged, prompt_lens = [], 0, []
    for attempts in by_session.values():
        first = attempts[0]
        prompt_lens.append(len(first["prompt_text"] or ""))
        final = next((a for a in reversed(attempts) if a["status"] in ("success", "flagged")), None)
        if final is None:
            continue
        flagged += final["status"] == "flagged"
        parsed = final["parsed_response"] or {}
        edits = parsed.get("_dosing_edits") or []
        edits_per_session.append(len(edits))
        for k, v in edit_kinds(edits).items():
            kinds[k] += v
        m = _TARGET_RE.search(final["prompt_text"] or "")
        dosed = parsed.get("exercises") or []
        is_deload = bool(_DELOAD_RE.search(final["prompt_text"] or ""))
        if m and is_deload:
            # deloads are only trimmed (DOSE-1b): judge them by staying under the target
            target = int(m.group(1))
            deload_n += 1
            deload_over += comp_reps(dosed) > target * (1 + DOSING_VOLUME_TOLERANCE)
        elif m:
            target = int(m.group(1))
            tol = target * DOSING_VOLUME_TOLERANCE
            raw = comp_reps(_raw_exercises(final["raw_response"]))
            done = comp_reps(dosed)
            raw_delta.append(abs(raw - target))
            dosed_delta.append(abs(done - target))
            raw_ok += abs(raw - target) <= tol
            dosed_ok += abs(done - target) <= tol
        if is_deload:
            comp_sets = [int(e.get("sets") or 1) for e in dosed if _counts_for_volume(e)]
            deload_max_sets.append(max(comp_sets, default=0))

    acc = fetch_all(conn, """
        SELECT e.name, e.movement_family::text AS family
        FROM session_exercises se
        JOIN program_sessions ps ON ps.id = se.session_id
        JOIN exercises e ON e.id = se.exercise_id
        WHERE ps.program_id = %s
          AND (e.category::text = 'accessory'
               OR e.movement_family::text IN ('hinge', 'row', 'core', 'carry', 'plyometric', 'press')
               OR e.name ~* '(lunge|split squat|step-?up)')
    """, (program_id,))
    names: dict[str, int] = {}
    for a in acc:
        names[a["name"]] = names.get(a["name"], 0) + 1
    n_dosed = len(edits_per_session)
    return {
        "program_id": program_id,
        **{k: rel[k] for k in ("sessions", "first_try_ok", "attempts_per_session", "validation_errors")},
        "flagged": flagged,
        "cost_usd": round(sum(float(r["estimated_cost_usd"] or 0) for r in rows), 3),
        "dosing_edits_per_session": round(sum(edits_per_session) / n_dosed, 2) if n_dosed else None,
        "edit_kinds": kinds,
        "raw_comp_rep_abs_delta": round(statistics.mean(raw_delta), 1) if raw_delta else None,
        "dosed_comp_rep_abs_delta": round(statistics.mean(dosed_delta), 1) if dosed_delta else None,
        "raw_within_tolerance": f"{raw_ok}/{len(raw_delta)}",
        "dosed_within_tolerance": f"{dosed_ok}/{len(dosed_delta)}",
        "deload_sessions_over_target": f"{deload_over}/{deload_n}",
        "deload_max_sets_per_row": deload_max_sets,
        "accessory_rows": sum(names.values()),
        "accessories": dict(sorted(names.items(), key=lambda kv: -kv[1])),
        "prompt_chars_mean": round(statistics.mean(prompt_lens)) if prompt_lens else None,
        "prompt_chars_max": max(prompt_lens, default=None),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What DOSE-1 dosing did to generated programs")
    parser.add_argument("program_ids", type=int, nargs="+")
    args = parser.parse_args(argv)
    conn = get_connection(Settings().database_url)
    try:
        for pid in args.program_ids:
            rep = program_report(conn, pid)
            print(f"\n## Program {pid}")
            for k, v in rep.items():
                if k != "program_id":
                    print(f"  {k}: {v}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
