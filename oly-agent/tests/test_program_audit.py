# oly-agent/tests/test_program_audit.py
"""No-key tests for program_audit (AGENT-0a): each check on synthetic programs,
the prompt read-back the CLI uses, the summary EXPLAIN shows."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from program_audit import (
    SessionPlan,
    audit_program,
    audit_summary,
    citation_supported,
    main_lifts_by_family,
    session_plan_from_prompt,
)


def _ex(name, sets, reps, pct, ref=None, **kw):
    return {"exercise_name": name, "sets": sets, "reps": reps, "intensity_pct": pct,
            "intensity_reference": ref, **kw}


def _plans(weeks=3, days=2, deload=(), target=10, floor=70, ceilings=None):
    ceilings = ceilings or {}
    return [SessionPlan(week=w, day=d, floor=floor, ceiling=ceilings.get(w, 80), is_deload=w in deload,
                        rep_target=target, label=f"Day {d}", primary_movement="snatch")
            for w in range(1, weeks + 1) for d in range(1, days + 1)]


def _session(week, day, *exercises):
    return {"week": week, "day": day, "label": f"Day {day}", "exercises": list(exercises)}


def _clean_program(weeks=3, days=2, sets=5, pct=75):
    return [_session(w, d, _ex("Snatch", 2, 2, 55, "snatch"), _ex("Snatch", sets, 2, pct, "snatch"),
                     _ex("Back Squat", 4, 5, 75, "back_squat"))
            for w in range(1, weeks + 1) for d in range(1, days + 1)]


def _checks(report, severity=None):
    return [(f["check"], f["severity"]) for f in report["findings"] if severity in (None, f["severity"])]


def test_a_program_that_matches_its_plan_has_no_findings():
    report = audit_program(_clean_program(), _plans())
    assert report["counts"] == {"error": 0, "warning": 0, "info": 0}
    assert [w["comp_reps"] for w in report["weeks"]] == [20, 20, 20]        # warm-up ramp left out


def test_missing_and_empty_sessions_are_errors():
    sessions = _clean_program()
    sessions = [s for s in sessions if (s["week"], s["day"]) != (2, 1)]
    sessions[0]["exercises"] = []
    report = audit_program(sessions, _plans())
    slots = [(f["week"], f["day"]) for f in report["findings"] if f["check"] == "slots"]
    assert slots == [(1, 1), (2, 1)]


def test_weekly_volume_warns_past_tolerance_and_errors_far_off():
    sessions = _clean_program()
    for s in sessions:
        if s["week"] == 2:
            s["exercises"][1]["sets"] = 4           # 16 vs 20 planned: −20 %
        if s["week"] == 3:
            s["exercises"][1]["sets"] = 2           # 8 vs 20: −60 %
    found = {(f["week"], f["severity"]) for f in audit_program(sessions, _plans())["findings"]
             if f["check"] == "weekly_volume"}
    assert found == {(2, "warning"), (3, "error")}


def test_a_deload_is_judged_only_on_going_over():
    sessions = _clean_program()
    for s in sessions:
        if s["week"] == 3:
            s["exercises"][1]["sets"] = 1           # far under: fine on a deload
    report = audit_program(sessions, _plans(deload=(3,)))
    assert ("weekly_volume", "warning") not in _checks(report) and ("weekly_volume", "error") not in _checks(report)


def test_ceiling_coach_cap_and_floor():
    sessions = _clean_program()
    sessions[0]["exercises"][1]["intensity_pct"] = 85            # W1 ceiling 80
    report = audit_program(sessions, _plans(floor=78))
    assert ("intensity", "error") in _checks(report)
    assert ("intensity", "warning") in _checks(report)           # top set 75 < floor 78 in weeks 2-3
    capped = audit_program(_clean_program(pct=75), _plans(), coach={"intensity_cap": 70})
    assert {c for c in _checks(capped, "error")} == {("coach", "error")}


def test_deload_heavier_than_the_week_before_is_a_block_shape_error():
    report = audit_program(_clean_program(), _plans(deload=(3,)))
    assert ("block_shape", "error") in _checks(report)


def test_planned_rise_with_a_realised_fall_is_a_warning_within_a_wave_only():
    sessions = _clean_program()
    for s in sessions:
        if s["week"] == 2:
            s["exercises"][1]["intensity_pct"] = 70
    plans = _plans(ceilings={1: 78, 2: 82, 3: 84})
    assert ("block_shape", "warning") in _checks(audit_program(sessions, plans))


def test_lift_drift_within_a_wave_but_not_across_one():
    def jerk_day(week, name):
        return _session(week, 1, _ex("Clean & Jerk", 4, 1, 75, "clean_and_jerk"), _ex(name, 3, 2, 70, "jerk"))

    plans = [SessionPlan(w, 1, 70, 80, False, 7) for w in (1, 2, 3)]
    drift = audit_program([jerk_day(1, "Jerk"), jerk_day(2, "Push Jerk"), jerk_day(3, "Jerk from Behind Neck")], plans)
    msgs = [f["message"] for f in drift["findings"] if f["check"] == "lift_drift"]
    assert len(msgs) == 1 and "W1 Jerk → W2 Push Jerk → W3 Jerk from Behind Neck" in msgs[0]

    # weeks 1-2, deload 3, new wave from 4: a new jerk variant at week 4 is the rotation DOG-2e asks for
    plans = [SessionPlan(w, 1, 70, 80, w == 3, 7) for w in (1, 2, 3, 4, 5)]
    sessions = [jerk_day(1, "Jerk"), jerk_day(2, "Jerk"), jerk_day(3, "Jerk"), jerk_day(4, "Push Jerk"),
                jerk_day(5, "Push Jerk")]
    assert "lift_drift" not in {c for c, _ in _checks(audit_program(sessions, plans))}


def test_main_lifts_by_family_skips_warmups_and_accessories():
    exs = [_ex("Snatch", 2, 2, 55, "snatch"), _ex("Snatch", 5, 2, 75, "snatch"),
           _ex("Back Squat", 4, 5, 75, "back_squat"), _ex("Barbell Row", 3, 8, None)]
    assert main_lifts_by_family(exs) == {"snatch": ("Snatch",), "squat": ("Back Squat",)}


def test_avoid_list_and_sessions_per_week():
    sessions = _clean_program()
    sessions[2]["exercises"].append(_ex("Back Squat", 3, 3, 80, "back_squat"))
    report = audit_program(sessions, _plans(), avoid=["back squat"], coach={"sessions_per_week": 3})
    coach = [f for f in report["findings"] if f["check"] == "coach"]
    assert sum("avoid" in f["message"] for f in coach) == 7         # 6 sessions + the extra row
    assert sum("coach asked for 3" in f["message"] for f in coach) == 3


def test_citations_outside_the_prompt_are_errors_and_unsupported_ones_warnings():
    snatch = _ex("Snatch", 5, 2, 75, "snatch", selection_rationale="Main lift [C1].", source_chunk_ids=[11])
    row = _ex("Barbell Row", 3, 8, None, selection_rationale="Upper back [C2] [C5].", source_chunk_ids=[12, 99])
    sessions = [_session(1, 1, snatch, row)]
    rsets = {(1, 1): [{"label": "C1", "id": 11}, {"label": "C2", "id": 12}]}
    texts = {11: "The snatch is pulled from the floor.", 12: "Squat depth and ankle mobility."}
    report = audit_program(sessions, [SessionPlan(1, 1, 70, 80, False, None)],
                           retrieval_sets=rsets, chunk_texts=texts)
    cites = [(f["severity"], f["exercise"], f["message"]) for f in report["findings"] if f["check"] == "citations"]
    assert ("error", "Barbell Row", "cites C5, which the prompt didn't show") in cites
    assert any(s == "error" and "[99]" in m for s, _, m in cites)
    assert any(s == "warning" and e == "Barbell Row" and "C2" in m for s, e, m in cites)
    assert report["citations"]["cited_exercises"] == 2 and report["citations"]["unsupported"] == 1


def test_citation_support_by_name_family_or_fault():
    assert citation_supported("Do power snatches from the hang.", "Power Snatch", [])
    assert citation_supported("Snatch technique for beginners.", "Hang Power Snatch (above knee)", [])
    assert citation_supported("Romanian deadlifts build the hamstrings.", "Snatch Grip RDL", [])
    assert citation_supported("A slow turnover under the bar.", "Tall Snatch", ["slow_turnover"])
    assert not citation_supported("Squat depth and ankle mobility.", "Reverse Crunch", ["slow_turnover"])


def test_session_plan_read_back_from_a_logged_prompt():
    prompt = ("…static…\n## Program Plan\nPhase: accumulation — Week 2 of 6\n"
              "Intensity range: 72% – 82.0%\nIntensity ceiling (hard limit for competition lifts): 82.0%\n"
              "Deload week: No\n\n## Session Template\nDay 1: Snatch + Squat\nPrimary movement: snatch\n"
              "Target competition lift reps this session: 21\n")
    p = session_plan_from_prompt(2, 1, prompt)
    assert (p.floor, p.ceiling, p.is_deload, p.rep_target, p.label, p.primary_movement) == (
        72.0, 82.0, False, 21, "Snatch + Squat", "snatch")
    deload = prompt.replace("Deload week: No", "Deload week: Yes").replace(
        "Target competition lift reps this session: 21", "Competition lift reps this session: at most 12 (deload)")
    p = session_plan_from_prompt(2, 1, deload)
    assert p.is_deload and p.rep_target == 12
    assert session_plan_from_prompt(1, 1, "").ceiling is None


def test_audit_summary_lists_errors_first_and_caps_the_list():
    sessions = _clean_program()
    sessions[0]["exercises"][1]["intensity_pct"] = 85
    report = audit_program(sessions, _plans(floor=78))
    lines = audit_summary(report, max_findings=1).splitlines()
    assert lines[0].startswith("1 errors, ")
    assert lines[1].startswith("  error [intensity] W1D1 Snatch: 85% is over")
    assert lines[-1].startswith("  (… ")
    assert audit_summary(None) == ""
