# oly-agent/program_audit.py
"""
Whole-program audit (AGENT-0a) — deterministic checks over a finished program.

`validate.py` judges one session at a time, against that session's prompt. The
problems it cannot see are the ones that only show across sessions: a week
whose comp-lift volume drifts from the plan although every session passed, a
deload that ends up heavier than the week before it, the jerk of a day
changing every week (program 29: Jerk → Push Jerk → Jerk from Behind Neck), a
citation to a chunk the prompt never showed. This module checks the realised
sessions against the plan after the last session and before EXPLAIN.

Checks (each finding is `error`, `warning` or `info`, with week / day /
exercise and a reason):

  slots           every planned (week, day) has a session with exercises     error
  weekly_volume   week's comp-lift reps vs the summed session targets         warning > DOSING_VOLUME_TOLERANCE,
                  (a deload is judged only on going over)                     error > AUDIT_VOLUME_ERROR_FRACTION
  intensity       comp-lift working set above the week's ceiling              error
                  a non-deload week whose top comp-lift set stays below
                  the week's floor                                            warning
  block_shape     deload not lighter (volume) than the week before            error
                  deload top set heavier than the week before                 warning
                  planned ceiling up but realised top set down (or planned
                  volume down but realised up), within one wave               warning
  lift_drift      a day's snatch / clean / jerk / squat / pull exercises
                  change from week to week within a wave (accessories
                  rotate on purpose, DOG-2m, and are not compared)            warning
  coach           intensity cap, avoid list, sessions per week                error
  citations       a cited [Cn] label or chunk id the session's prompt
                  didn't have                                                 error
                  the cited chunk mentions neither the exercise, its
                  family nor a targeted fault (lexical support, no model)     warning

It never fails a program and never triggers a paid call: the report is stored
in `generation_params.audit` and summarised for EXPLAIN. AGENT-2's repair
round acts on `error` findings only.

`python -m eval.program_audit <program_id> …` runs it over stored programs;
there the plan is read back from each session's logged prompt (week ceiling,
floor, deload flag, session target, primary movement), so programs from before
the plan snapshot (RESUME-1) can be audited too.
"""

import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from complexes import comp_reps_per_set, counts_as_competition_lift
from dosing import _counts_for_volume
from phase_profiles import wave_anchor_weeks
from weight_resolver import cited_context_labels

from shared.constants import (
    AUDIT_CEILING_SLACK_PCT,
    AUDIT_INTENSITY_SLACK_PCT,
    AUDIT_SUMMARY_MAX_FINDINGS,
    AUDIT_VOLUME_ERROR_FRACTION,
    DOSING_VOLUME_TOLERANCE,
)
from shared.exercise_mapping import MOVEMENT_FAMILY_PATTERNS, infer_movement_family, is_warmup_set

AUDIT_VERSION = 1
SEVERITIES = ("error", "warning", "info")

_FAMILY_PATTERN = {family: pattern for pattern, family in MOVEMENT_FAMILY_PATTERNS}


@dataclass
class SessionPlan:
    """What the plan asked of one (week, day)."""
    week: int
    day: int
    floor: float | None
    ceiling: float | None
    is_deload: bool
    rep_target: int | None
    label: str = ""
    primary_movement: str | None = None


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    week: int | None = None
    day: int | None = None
    exercise: str | None = None


# ── Plan adapters ────────────────────────────────────────────────────────────

def session_plans_from_plan(plan, level: str = "intermediate") -> list[SessionPlan]:
    """The plan the orchestrator generated from, one entry per (week, day),
    with the same session rep target `_generate_session` put in the prompt."""
    from shared.volume_tables import session_rep_target

    days = len(plan.session_templates)
    out = []
    for wt in plan.weekly_targets:
        for st in plan.session_templates:
            out.append(SessionPlan(
                week=wt.week_number, day=st.day_number,
                floor=wt.intensity_floor, ceiling=wt.intensity_ceiling, is_deload=bool(wt.is_deload),
                rep_target=session_rep_target(
                    getattr(wt, "volume_table", "prilepin"),
                    intensity_floor=wt.intensity_floor, intensity_ceiling=wt.intensity_ceiling,
                    session_volume_share=st.session_volume_share, volume_modifier=wt.volume_modifier,
                    sessions_per_week=days, level=level, phase=plan.phase,
                ),
                label=st.label, primary_movement=st.primary_movement,
            ))
    return out


_PROMPT_FIELDS = {
    "range": re.compile(r"^Intensity range: ([\d.]+)% – ([\d.]+)%", re.M),
    "ceiling": re.compile(r"^Intensity ceiling \(hard limit for competition lifts\): ([\d.]+)%", re.M),
    "deload": re.compile(r"^Deload week: (\w+)", re.M),
    "target": re.compile(r"(?:Target competition lift reps this session:|"
                         r"Competition lift reps this session: at most) (\d+)"),
    "day": re.compile(r"^Day \d+: (.+)$", re.M),
    "primary": re.compile(r"^Primary movement: (\S+)", re.M),
}


def session_plan_from_prompt(week: int, day: int, prompt: str) -> SessionPlan:
    """A SessionPlan read back from a logged session prompt (the CLI path).
    A field the prompt doesn't carry is None and its check is skipped."""
    text = prompt or ""
    plan_part = text[text.find("## Program Plan"):] if "## Program Plan" in text else text

    def grab(key: str):
        return _PROMPT_FIELDS[key].search(plan_part)

    rng, ceiling = grab("range"), grab("ceiling")
    deload, target, label, primary = grab("deload"), grab("target"), grab("day"), grab("primary")
    return SessionPlan(
        week=week, day=day,
        floor=float(rng.group(1)) if rng else None,
        ceiling=float(ceiling.group(1)) if ceiling else (float(rng.group(2)) if rng else None),
        is_deload=bool(deload and deload.group(1).lower() in ("yes", "true")),
        rep_target=int(target.group(1)) if target else None,
        label=label.group(1).strip() if label else "",
        primary_movement=primary.group(1) if primary else None,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pct(ex: dict) -> float | None:
    try:
        return float(ex["intensity_pct"]) if ex.get("intensity_pct") is not None else None
    except (TypeError, ValueError):
        return None


def _working(exercises: list[dict]) -> list[dict]:
    return [ex for ex in exercises or []
            if not is_warmup_set(ex.get("intensity_reference"), ex.get("intensity_pct"))]


def comp_reps(exercises: list[dict]) -> int:
    """Competition-lift reps the validator counts: above the warm-up band."""
    return sum(int(ex.get("sets") or 1) * comp_reps_per_set(ex) for ex in exercises or [] if _counts_for_volume(ex))


def top_comp_pct(exercises: list[dict]) -> float | None:
    pcts = [p for ex in exercises or [] if counts_as_competition_lift(ex) and (p := _pct(ex)) is not None]
    return max(pcts) if pcts else None


MAIN_LIFT_FAMILIES: tuple[str, ...] = ("snatch", "clean", "jerk", "squat", "pull")


def main_lifts_by_family(exercises: list[dict]) -> dict[str, tuple[str, ...]]:
    """The session's working main-lift exercises grouped by movement family,
    names in order ("Clean & Jerk" is clean family, "Push Jerk" jerk). Accessory
    families are left out: DOG-2m rotates them across the week on purpose."""
    out: dict[str, list[str]] = {}
    for ex in _working(exercises):
        name = str(ex.get("exercise_name") or "")
        family = infer_movement_family(name)
        if family in MAIN_LIFT_FAMILIES and name not in out.get(family, []):
            out.setdefault(family, []).append(name)
    return {f: tuple(names) for f, names in out.items()}


def _norm_name(name) -> str:
    return str(name or "").lower().replace(" ", "_")


def _phrase_re(phrase: str) -> re.Pattern:
    words = [re.escape(w) for w in re.findall(r"[a-z0-9]+", phrase.lower())]
    return re.compile(r"\b" + r"[\s\-_]+".join(words) + r"(?:s|es)?\b", re.I) if words else re.compile(r"(?!)")


def citation_supported(chunk_text: str, exercise_name: str, faults: list[str]) -> bool:
    """ALCE-style lexical support: the chunk names the exercise, its movement
    family, or one of the athlete's targeted faults."""
    text = chunk_text or ""
    if _phrase_re(exercise_name).search(text):
        return True
    family = infer_movement_family(exercise_name)
    if family and family in _FAMILY_PATTERN and _FAMILY_PATTERN[family].search(text):
        return True
    for fault in faults or []:
        words = [w for w in re.findall(r"[a-z]+", str(fault).lower()) if len(w) >= 3 and w != "and"]
        if words and all(re.search(rf"\b{re.escape(w)}", text, re.I) for w in words):
            return True
    return False


# ── The audit ────────────────────────────────────────────────────────────────

def audit_program(
    sessions: list[dict],
    plans: list[SessionPlan],
    *,
    coach: dict | None = None,
    avoid: list[str] | None = None,
    faults: list[str] | None = None,
    retrieval_sets: dict[tuple[int, int], list[dict]] | None = None,
    chunk_texts: dict[int, str] | None = None,
) -> dict:
    """Audit realised `sessions` ({week, day, label, exercises}) against `plans`.

    `retrieval_sets` maps (week, day) to the final attempt's logged retrieval
    set ([{label: "C1", id, …}]); `chunk_texts` the cited chunks' raw text.
    Either missing → the citation checks are skipped for those sessions.
    Returns the report stored in `generation_params.audit`."""
    findings: list[Finding] = []
    coach = coach or {}
    by_slot = {(s.get("week"), s.get("day")): s for s in sessions}
    plan_by_slot = {(p.week, p.day): p for p in plans}

    # slots
    for p in plans:
        s = by_slot.get((p.week, p.day))
        if s is None or not s.get("exercises"):
            findings.append(Finding("error", "slots", "planned session is missing or empty", p.week, p.day))
    extra = sorted(k for k in by_slot if k not in plan_by_slot)
    if extra:
        findings.append(Finding("info", "slots", "sessions outside the plan (max test or cap): "
                                + ", ".join(f"W{w}D{d}" for w, d in extra)))

    # weekly volume + intensity
    weeks: dict[int, list[SessionPlan]] = defaultdict(list)
    for p in plans:
        weeks[p.week].append(p)
    week_rows = []
    for week in sorted(weeks):
        wplans = weeks[week]
        wsessions = [by_slot[(p.week, p.day)] for p in wplans if (p.week, p.day) in by_slot]
        exercises = [ex for s in wsessions for ex in s.get("exercises") or []]
        complete = len(wsessions) == len(wplans) and all(s.get("exercises") for s in wsessions)
        targets = [p.rep_target for p in wplans]
        target = sum(targets) if all(t is not None for t in targets) else None
        row = {"week": week, "deload": any(p.is_deload for p in wplans), "comp_reps": comp_reps(exercises),
               "target": target, "top_pct": top_comp_pct(exercises),
               "ceiling": wplans[0].ceiling, "floor": wplans[0].floor, "complete": complete}
        week_rows.append(row)
        if complete and target:
            delta = (row["comp_reps"] - target) / target
            over_only = row["deload"]
            off = delta if over_only else abs(delta)
            if off > DOSING_VOLUME_TOLERANCE:
                sev = "error" if off > AUDIT_VOLUME_ERROR_FRACTION else "warning"
                findings.append(Finding(sev, "weekly_volume",
                                        f"{row['comp_reps']} comp-lift reps vs {target} planned "
                                        f"({delta:+.0%}{', deload' if over_only else ''})", week))
        for p in wplans:
            s = by_slot.get((p.week, p.day))
            for ex in (s or {}).get("exercises") or []:
                pct = _pct(ex)
                if pct is None or not counts_as_competition_lift(ex):
                    continue
                cap = coach.get("intensity_cap")
                if cap is not None and pct > float(cap) + AUDIT_CEILING_SLACK_PCT:
                    findings.append(Finding("error", "coach", f"{pct:g}% is over the coach's {cap}% cap",
                                            p.week, p.day, ex.get("exercise_name")))
                elif p.ceiling is not None and pct > p.ceiling + AUDIT_CEILING_SLACK_PCT:
                    findings.append(Finding("error", "intensity", f"{pct:g}% is over the week's {p.ceiling:g}% ceiling",
                                            p.week, p.day, ex.get("exercise_name")))
        if (complete and not row["deload"] and row["floor"] is not None and row["top_pct"] is not None
                and row["top_pct"] < row["floor"]):
            findings.append(Finding("warning", "intensity",
                                    f"top comp-lift set {row['top_pct']:g}% never reaches the week's "
                                    f"{row['floor']:g}% floor", week))

    # block shape
    anchors = wave_anchor_weeks([_WeekStub(r["week"], r["deload"]) for r in week_rows])
    for prev, cur in zip(week_rows, week_rows[1:], strict=False):
        if not (prev["complete"] and cur["complete"]):
            continue
        if cur["deload"] and not prev["deload"]:
            if cur["comp_reps"] >= prev["comp_reps"] and prev["comp_reps"]:
                findings.append(Finding("error", "block_shape",
                                        f"deload has {cur['comp_reps']} comp-lift reps, not fewer than "
                                        f"week {prev['week']}'s {prev['comp_reps']}", cur["week"]))
            if cur["top_pct"] is not None and prev["top_pct"] is not None and cur["top_pct"] > prev["top_pct"]:
                findings.append(Finding("warning", "block_shape",
                                        f"deload top set {cur['top_pct']:g}% is heavier than week "
                                        f"{prev['week']}'s {prev['top_pct']:g}%", cur["week"]))
            continue
        if prev["deload"] or cur["deload"] or anchors.get(prev["week"]) != anchors.get(cur["week"]):
            continue
        if (prev["ceiling"] is not None and cur["ceiling"] is not None and cur["ceiling"] > prev["ceiling"]
                and prev["top_pct"] is not None and cur["top_pct"] is not None
                and cur["top_pct"] < prev["top_pct"] - AUDIT_INTENSITY_SLACK_PCT):
            findings.append(Finding("warning", "block_shape",
                                    f"ceiling rises {prev['ceiling']:g} → {cur['ceiling']:g}% but the top set falls "
                                    f"{prev['top_pct']:g} → {cur['top_pct']:g}%", cur["week"]))
        if prev["target"] and cur["target"] and prev["comp_reps"]:
            planned = (cur["target"] - prev["target"]) / prev["target"]
            realised = (cur["comp_reps"] - prev["comp_reps"]) / prev["comp_reps"]
            if (planned < -DOSING_VOLUME_TOLERANCE < DOSING_VOLUME_TOLERANCE < realised
                    or planned > DOSING_VOLUME_TOLERANCE > -DOSING_VOLUME_TOLERANCE > realised):
                findings.append(Finding("warning", "block_shape",
                                        f"planned volume {planned:+.0%} on week {prev['week']}, "
                                        f"realised {realised:+.0%}", cur["week"]))

    # lift drift: per day, wave and main-lift family
    seen: dict[tuple[int, int], list[tuple[int, dict[str, tuple[str, ...]]]]] = defaultdict(list)
    for p in sorted(plans, key=lambda p: (p.week, p.day)):
        s = by_slot.get((p.week, p.day))
        if p.is_deload or not s or not s.get("exercises"):
            continue
        seen[(p.day, anchors.get(p.week, p.week))].append((p.week, main_lifts_by_family(s["exercises"])))
    for (day, anchor), weeks_seen in sorted(seen.items()):
        if len(weeks_seen) < 2:
            continue
        for family in MAIN_LIFT_FAMILIES:
            seq = [(w, fams.get(family, ())) for w, fams in weeks_seen]
            if len({names for _, names in seq}) > 1:
                findings.append(Finding(
                    "warning", "lift_drift",
                    f"{family} work changes within the wave from week {anchor}: "
                    + " → ".join(f"W{w} {' + '.join(names) or '—'}" for w, names in seq),
                    seq[0][0], day))

    # coach: avoid list, sessions per week
    avoid_norm = {_norm_name(a) for a in [*(avoid or []), *(coach.get("avoid_exercises") or [])] if a}
    for s in sessions:
        for ex in s.get("exercises") or []:
            if _norm_name(ex.get("exercise_name")) in avoid_norm:
                findings.append(Finding("error", "coach", "exercise is on the avoid list",
                                        s.get("week"), s.get("day"), ex.get("exercise_name")))
    spw = coach.get("sessions_per_week")
    if spw:
        for week in sorted(weeks):
            if len(weeks[week]) != int(spw):
                findings.append(Finding("error", "coach",
                                        f"{len(weeks[week])} sessions planned, coach asked for {spw}", week))

    # citations
    cite = {"cited_exercises": 0, "citations": 0, "outside_prompt": 0, "unsupported": 0, "checked": 0}
    if retrieval_sets is not None:
        for s in sessions:
            rset = retrieval_sets.get((s.get("week"), s.get("day")))
            if not rset:
                continue
            labels = {str(e.get("label")): e for e in rset}
            ids = {e.get("id") for e in rset}
            for ex in s.get("exercises") or []:
                cited = cited_context_labels(ex.get("selection_rationale"))
                if not cited:
                    continue
                name = ex.get("exercise_name")
                cite["cited_exercises"] += 1
                cite["citations"] += len(cited)
                for n in cited:
                    entry = labels.get(f"C{n}")
                    if entry is None:
                        cite["outside_prompt"] += 1
                        findings.append(Finding("error", "citations", f"cites C{n}, which the prompt didn't show",
                                                s.get("week"), s.get("day"), name))
                        continue
                    text = (chunk_texts or {}).get(entry.get("id"))
                    if text is None:
                        continue
                    cite["checked"] += 1
                    if not citation_supported(text, name, faults or []):
                        cite["unsupported"] += 1
                        findings.append(Finding("warning", "citations",
                                                f"C{n} (chunk {entry.get('id')}) names neither the exercise, "
                                                f"its family nor a targeted fault", s.get("week"), s.get("day"), name))
                stray = [i for i in ex.get("source_chunk_ids") or [] if i not in ids]
                if stray:
                    cite["outside_prompt"] += len(stray)
                    findings.append(Finding("error", "citations",
                                            f"source chunk(s) {stray} were not in the session's prompt",
                                            s.get("week"), s.get("day"), name))

    counts = {sev: sum(f.severity == sev for f in findings) for sev in SEVERITIES}
    return {
        "version": AUDIT_VERSION,
        "counts": counts,
        "weeks": week_rows,
        "citations": cite,
        "findings": [asdict(f) for f in findings],
    }


@dataclass
class _WeekStub:
    week_number: int
    is_deload: bool


def audit_summary(report: dict | None, max_findings: int = AUDIT_SUMMARY_MAX_FINDINGS) -> str:
    """The audit for the EXPLAIN prompt: counts, then errors and warnings, so
    the rationale doesn't describe a progression the program doesn't have."""
    if not report:
        return ""
    counts = report.get("counts") or {}
    head = f"{counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings"
    shown = [f for f in report.get("findings") or [] if f["severity"] in ("error", "warning")]
    shown.sort(key=lambda f: (f["severity"] != "error", f.get("week") or 0, f.get("day") or 0))
    lines = [head]
    for f in shown[:max_findings]:
        where = "".join([f"W{f['week']}" if f.get("week") is not None else "",
                         f"D{f['day']}" if f.get("day") is not None else ""])
        ex = f"{f['exercise']}: " if f.get("exercise") else ""
        lines.append(f"  {f['severity']} [{f['check']}] {where + ' ' if where else ''}{ex}{f['message']}")
    if len(shown) > max_findings:
        lines.append(f"  (… {len(shown) - max_findings} more)")
    return "\n".join(lines)


# ── Loading a stored program (CLI and orchestrator) ─────────────────────────

def _fetch(fetch):
    if fetch is not None:
        return fetch
    from shared.db import fetch_all
    return fetch_all


def load_program_sessions(conn, program_id: int, fetch=None) -> list[dict]:
    """Stored sessions as {week, day, label, exercises}, with each complex's
    definition attached as `complex` so its components are counted. `fetch`
    defaults to shared.db.fetch_all (the orchestrator passes its own)."""
    fetch_all = _fetch(fetch)
    rows = fetch_all(conn, """
        SELECT ps.week_number, ps.day_number, ps.session_label, se.*
        FROM program_sessions ps LEFT JOIN session_exercises se ON se.session_id = ps.id
        WHERE ps.program_id = %s ORDER BY ps.week_number, ps.day_number, se.exercise_order""", (program_id,))
    complex_ids = sorted({r["complex_id"] for r in rows if r.get("complex_id")})
    complexes = {}
    if complex_ids:
        complexes = {c["id"]: c for c in fetch_all(
            conn, "SELECT * FROM exercise_complexes WHERE id = ANY(%s)", (complex_ids,))}
    sessions: dict[tuple[int, int], dict] = {}
    for r in rows:
        key = (r["week_number"], r["day_number"])
        s = sessions.setdefault(key, {"week": key[0], "day": key[1], "label": r["session_label"], "exercises": []})
        if r.get("exercise_name") is None:
            continue
        ex = {k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items()
              if k not in ("week_number", "day_number", "session_label", "session_id", "created_at")}
        if ex.get("complex_id") in complexes:
            ex["complex"] = complexes[ex["complex_id"]]
        s["exercises"].append(ex)
    return list(sessions.values())


def load_citation_context(conn, program_id: int, fetch=None
                          ) -> tuple[dict[tuple[int, int], list[dict]], dict[int, str]]:
    """(retrieval set of each session's final attempt, raw text of every chunk
    in those sets) from generation_log and knowledge_chunks."""
    fetch_all = _fetch(fetch)
    rows = fetch_all(conn, """
        SELECT DISTINCT ON (week_number, day_number) week_number, day_number, retrieval_set
        FROM generation_log
        WHERE program_id = %s AND week_number IS NOT NULL AND status IN ('success', 'flagged')
        ORDER BY week_number, day_number, attempt_number DESC""", (program_id,))
    sets = {(r["week_number"], r["day_number"]): r["retrieval_set"] or [] for r in rows}
    ids = sorted({e["id"] for rset in sets.values() for e in rset if e.get("id") is not None})
    texts = {}
    if ids:
        texts = {r["id"]: r["raw_content"] for r in fetch_all(
            conn, "SELECT id, raw_content FROM knowledge_chunks WHERE id = ANY(%s)", (ids,))}
    return sets, texts


def session_plans_from_log(conn, program_id: int, fetch=None) -> list[SessionPlan]:
    """The plan read back from each session's first logged prompt."""
    fetch_all = _fetch(fetch)
    rows = fetch_all(conn, """
        SELECT DISTINCT ON (week_number, day_number) week_number, day_number, prompt_text
        FROM generation_log WHERE program_id = %s AND week_number IS NOT NULL
        ORDER BY week_number, day_number, attempt_number""", (program_id,))
    return [session_plan_from_prompt(r["week_number"], r["day_number"], r["prompt_text"]) for r in rows]
