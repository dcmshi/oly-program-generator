# tests/test_feedback.py
"""
No-DB unit tests for feedback.py — compute_outcome(), save_outcome(),
_compute_phase_verdict() and _compute_trend().

The previous version ran against a live DB pinned to program 4, which has
since been deleted — every test errored, and the file was never in the
no-key suite, so feedback.py sat at 27 % coverage. The queries are now
answered by a fake that routes on the SQL text.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root → shared
sys.path.insert(0, str(Path(__file__).parent.parent))          # oly-agent  → feedback

import feedback
from feedback import _compute_phase_verdict, _compute_trend, compute_outcome, save_outcome
from models import ProgramOutcome

from shared.constants import ADVANCE_MIN_ADHERENCE_PCT


def _fake_db(**answers):
    """fetch_one / fetch_all replacements keyed by a distinctive SQL fragment."""
    routes = {
        "FROM program_sessions WHERE program_id": ("one", {"cnt": answers.get("prescribed", 16)}),
        "COUNT(DISTINCT tl.session_id)": ("one", {"cnt": answers.get("completed", 12)}),
        "tle.rpe - se.rpe_target": ("all", answers.get("rpe_rows", [])),
        "tle.make_rate, se.intensity_reference": ("all", answers.get("make_rows", [])),
        "AS weekly_reps": ("all", answers.get("weekly_rows", [])),
        "maxes_snapshot, created_at": ("one", answers.get("program_row", {"maxes_snapshot": {}})),
        "FROM athlete_maxes": ("all", answers.get("max_rows", [])),
        "tl.athlete_notes": ("all", answers.get("notes_rows", [])),
        "SELECT phase FROM generated_programs": ("one", answers.get("phase_row", {"phase": "accumulation"})),
    }

    def route(kind):
        def fn(conn, sql, params=None):
            for frag, (k, value) in routes.items():
                if frag in sql:
                    assert k == kind, f"{frag!r} fetched with fetch_{kind}"
                    return value
            raise AssertionError(f"unexpected query: {sql[:80]}")
        return fn
    return patch.multiple(feedback, fetch_one=route("one"), fetch_all=route("all"))


def test_compute_outcome_aggregates_every_signal():
    with _fake_db(
        prescribed=16, completed=12,
        rpe_rows=[{"deviation": 0.5}, {"deviation": 1.5}, {"deviation": 1.0}],
        make_rows=[{"make_rate": 1.0, "intensity_reference": "snatch"},
                   {"make_rate": 0.5, "intensity_reference": "snatch"},
                   {"make_rate": 0.9, "intensity_reference": "clean_and_jerk"}],
        weekly_rows=[{"week_number": 1, "weekly_reps": 60}, {"week_number": 2, "weekly_reps": None},
                     {"week_number": 3, "weekly_reps": 90}],
        program_row={"maxes_snapshot": {"snatch": 100.0, "clean_and_jerk": 125.0}},
        max_rows=[{"name": "Snatch", "weight_kg": 102.5}, {"name": "Clean & Jerk", "weight_kg": 125.0},
                  {"name": "Back Squat", "weight_kg": 180.0}],
        notes_rows=[{"athlete_notes": "knees sore"}, {"athlete_notes": "felt fast"}],
    ):
        out = compute_outcome(7, 1, MagicMock())
    assert out.program_id == 7 and out.athlete_id == 1
    assert (out.sessions_prescribed, out.sessions_completed, out.adherence_pct) == (16, 12, 75.0)
    assert out.avg_rpe_deviation == 1.0
    assert out.avg_make_rate == round((1.0 + 0.5 + 0.9) / 3, 2)
    assert out.make_rate_by_lift == {"clean_and_jerk": 0.9, "snatch": 0.75}
    assert out.avg_weekly_reps == 50.0                     # a NULL week counts as 0
    # only lifts present in the snapshot, only non-zero deltas, keyed by display name
    assert out.maxes_delta == {"Snatch": 2.5}
    assert out.athlete_feedback == "knees sore | felt fast"
    assert out.phase_verdict["prev_phase"] == "accumulation"


def test_compute_outcome_with_no_logs_is_all_zero_and_stable():
    with _fake_db(prescribed=0, completed=0, program_row=None, phase_row=None):
        out = compute_outcome(7, 1, MagicMock())
    assert out.adherence_pct == 0.0 and out.avg_rpe_deviation == 0.0 and out.avg_make_rate == 0.0
    assert out.make_rate_by_lift == {} and out.maxes_delta == {} and out.athlete_feedback is None
    assert out.rpe_trend == out.make_rate_trend == "stable"
    assert out.phase_verdict["prev_phase"] is None and out.phase_verdict["next_phase"] == "accumulation"


def test_compute_outcome_trends_use_the_last_six_entries():
    rising_rpe = [{"deviation": d} for d in (0, 0, 0, 0, 0, 0, 3, 3, 3)]      # last 6: 0,0,0,3,3,3
    with _fake_db(rpe_rows=rising_rpe):
        out = compute_outcome(7, 1, MagicMock())
    assert out.rpe_trend == "ascending"


def test_save_outcome_writes_a_validated_summary_and_commits():
    outcome = ProgramOutcome(
        program_id=7, athlete_id=1, maxes_delta={"Snatch": 2.5}, sessions_prescribed=16,
        sessions_completed=12, adherence_pct=75.0, avg_rpe_deviation=1.0, avg_make_rate=0.8,
        make_rate_by_lift={"snatch": 0.75}, phase_verdict=_compute_phase_verdict("accumulation", 75.0, 0.8, 1.0),
        avg_weekly_reps=50.0, rpe_trend="stable", make_rate_trend="stable", athlete_feedback="ok",
    )
    conn = MagicMock()
    with patch.object(feedback, "execute") as execute:
        save_outcome(outcome, conn)
    sql, params = execute.call_args.args[1], execute.call_args.args[2]
    assert "SET outcome_summary" in sql and "status = 'completed'" in sql and params[1] == 7
    summary = json.loads(params[0])
    assert summary["adherence_pct"] == 75.0 and summary["maxes_delta"] == {"Snatch": 2.5}
    assert summary["phase_verdict"]["next_phase"] == "intensification"
    conn.commit.assert_called_once()


def test_save_outcome_without_a_verdict():
    outcome = ProgramOutcome(
        program_id=7, athlete_id=1, maxes_delta={}, sessions_prescribed=0, sessions_completed=0,
        adherence_pct=0.0, avg_rpe_deviation=0.0, avg_make_rate=0.0, make_rate_by_lift={},
        phase_verdict=None, avg_weekly_reps=0.0, rpe_trend="stable", make_rate_trend="stable",
        athlete_feedback=None,
    )
    with patch.object(feedback, "execute") as execute:
        save_outcome(outcome, MagicMock())
    assert json.loads(execute.call_args.args[2][0])["phase_verdict"] is None


def test_phase_verdict_statuses_and_reasons():
    adv = _compute_phase_verdict("accumulation", 90.0, 0.9, 0.2)
    assert adv["advanced"] and adv["next_phase"] == "intensification" and "advanced" in adv["reason"]
    assert all(c["passed"] for c in adv["checks"]) and adv["prev_label"] == "Accumulation"

    low = _compute_phase_verdict("accumulation", ADVANCE_MIN_ADHERENCE_PCT - 10, 0.9, 0.2)
    assert not low["advanced"] and low["next_phase"] == "accumulation"
    assert low["reason"].startswith("Phase repeated") and "Adherence" in low["reason"]

    rpe = _compute_phase_verdict("accumulation", 90.0, 0.9, 3.0)
    assert not rpe["advanced"] and "RPE deviation too high" in rpe["reason"]

    cold = _compute_phase_verdict(None, 0.0, 0.0, 0.0)
    assert cold["next_phase"] == "accumulation" and cold["prev_label"] == "—"

    rebuild = _compute_phase_verdict("realization", 90.0, 0.9, 0.2)
    assert rebuild["next_phase"] == "accumulation" and "rebuilding" in rebuild["reason"]
    assert rebuild["adjustments"] == ["Intensity ceiling +2% (excellent performance)"]   # ≥ 90 % adherence, ≥ 0.85 make rate
    assert _compute_phase_verdict("accumulation", 80.0, 0.8, 0.2)["adjustments"] == []  # passed, not excellent
    assert low["adjustments"] and all(isinstance(x, str) for x in low["adjustments"])   # a miss → labelled nudge(s)


def test_compute_trend():
    assert _compute_trend([1.0, 2.0]) == "stable"                          # too short
    assert _compute_trend([0.0, 0.0, 2.0, 2.0]) == "ascending"
    assert _compute_trend([2.0, 2.0, 0.0, 0.0]) == "descending"
    assert _compute_trend([0.0, 0.0, 2.0, 2.0], invert=True) == "descending"
    assert _compute_trend([0.8, 0.8, 0.7, 0.7], threshold=0.07) == "descending"   # make-rate scale
    assert _compute_trend([0.8, 0.8, 0.79, 0.79], threshold=0.07) == "stable"
