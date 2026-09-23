# tests/test_log_commands.py
"""
No-DB tests for the training-log CLI commands in log.py (show / session /
exercise / status / history / main). test_log.py covers the pure helpers;
these drive each command end to end with a fake DB that answers by SQL
fragment and scripted input(), and check what is printed and written.
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import log

TODAY = date.today()
PROGRAM = {"id": 5, "name": "Block A", "phase": "accumulation", "start_date": TODAY - timedelta(days=8),
           "duration_weeks": 4, "sessions_per_week": 3}


def fake_db(routes: dict):
    """Patch log.fetch_one / fetch_all: `routes` maps an SQL fragment to a value
    or to a callable(params) → value. Unmatched queries fail loudly."""
    def answer(sql, params):
        for frag, value in routes.items():
            if frag in sql:
                return value(params) if callable(value) else value
        raise AssertionError(f"unexpected query: {' '.join(sql.split())[:90]}")

    return patch.multiple(log, fetch_one=lambda c, s, p=None: answer(s, p),
                          fetch_all=lambda c, s, p=None: answer(s, p))


def scripted(*answers):
    """builtins.input replacement returning the answers in order."""
    it = iter(answers)
    return patch("builtins.input", lambda *_a, **_k: next(it))


# ── show ─────────────────────────────────────────────────────────

def test_show_prints_current_week_sessions_exercises_and_logged_tag(capsys):
    routes = {
        "status = 'active'": PROGRAM,
        "FROM program_sessions ps": [
            {"id": 11, "week_number": 2, "day_number": 1, "session_label": "Snatch day",
             "estimated_duration_minutes": 75, "focus_area": "snatch"},
            {"id": 12, "week_number": 2, "day_number": 2, "session_label": "Clean day",
             "estimated_duration_minutes": 80, "focus_area": "clean"},
        ],
        "FROM training_logs WHERE session_id": lambda p: {"id": 99, "overall_rpe": 8} if p == (11,) else None,
        "FROM session_exercises": [
            {"exercise_order": 1, "exercise_name": "Snatch", "sets": 5, "reps": 2, "intensity_pct": 78,
             "absolute_weight_kg": 78.0, "rest_seconds": 120, "rpe_target": 8},
            {"exercise_order": 2, "exercise_name": "Back Extension", "sets": 3, "reps": 10, "intensity_pct": None,
             "absolute_weight_kg": None, "rest_seconds": None, "rpe_target": None},
        ],
    }
    with fake_db(routes):
        log.cmd_show(1, MagicMock())
    out = capsys.readouterr().out
    assert "Week 2 of 4" in out and "Block A" in out
    assert "✓ logged (log_id=99, RPE=8)" in out and out.count("✓ logged") == 1
    assert "1. Snatch  5×2  @78.0kg  RPE 8  rest 120s" in out
    assert "2. Back Extension  3×10" in out


def test_show_without_program_or_sessions(capsys):
    with fake_db({"FROM generated_programs": None}):
        log.cmd_show(1, MagicMock())
    assert "No program found" in capsys.readouterr().out
    with fake_db({"status = 'active'": {**PROGRAM, "start_date": str(PROGRAM["start_date"])},
                  "FROM program_sessions ps": []}):
        log.cmd_show(1, MagicMock())
    assert "No sessions found for week 2" in capsys.readouterr().out


# ── session ──────────────────────────────────────────────────────

def _session_routes():
    return {"status = 'active'": PROGRAM,
            "LEFT JOIN training_logs tl": [{"id": 11, "day_number": 1, "session_label": "Snatch day"}]}


def test_session_links_a_listed_session_and_saves_the_log(capsys):
    conn = MagicMock()
    prompts = iter([str(TODAY), 8.0, 75, 88.5, 4, 2, "felt good"])
    with fake_db(_session_routes()), scripted("11", "n"), \
         patch.object(log, "_prompt", lambda *a, **k: next(prompts)), \
         patch.object(log, "execute_returning", return_value=42) as ins:
        assert log.cmd_session(1, conn) == 42
    params = ins.call_args.args[2]
    assert params[:3] == (1, 11, TODAY) and params[3:] == (8.0, 75, 88.5, 4, 2, "felt good")
    conn.commit.assert_called_once()
    assert "log_id=42" in capsys.readouterr().out


def test_session_rejects_an_unlisted_id_and_bad_date_falls_back_to_today(capsys):
    prompts = iter(["not-a-date", None, None, None, None, None, ""])
    with fake_db(_session_routes()), scripted("999", "n"), \
         patch.object(log, "_prompt", lambda *a, **k: next(prompts)), \
         patch.object(log, "execute_returning", return_value=7) as ins:
        log.cmd_session(1, MagicMock())
    params = ins.call_args.args[2]
    assert params[1] is None and params[2] == TODAY and params[-1] is None     # no link, today, notes None
    assert "isn't one of the listed sessions" in capsys.readouterr().out


def test_session_save_failure_rolls_back_and_returns_none(capsys):
    conn = MagicMock()
    prompts = iter([str(TODAY), None, None, None, None, None, None])
    with fake_db(_session_routes()), scripted(""), \
         patch.object(log, "_prompt", lambda *a, **k: next(prompts)), \
         patch.object(log, "execute_returning", side_effect=RuntimeError("db down")):
        assert log.cmd_session(1, conn, session_id=11) is None
    conn.rollback.assert_called_once()
    assert "Could not save the session log: db down" in capsys.readouterr().out


def test_session_can_continue_into_exercise_entry():
    prompts = iter([str(TODAY), None, None, None, None, None, None])
    with fake_db(_session_routes()), scripted("y"), \
         patch.object(log, "_prompt", lambda *a, **k: next(prompts)), \
         patch.object(log, "execute_returning", return_value=42), \
         patch.object(log, "cmd_exercise") as ex:
        log.cmd_session(1, MagicMock(), session_id=11)
    ex.assert_called_once()
    assert ex.call_args.args[0] == 42 and ex.call_args.kwargs["session_id"] == 11


# ── exercise ─────────────────────────────────────────────────────

def test_exercise_linked_and_unlinked_entries_are_written_with_deviations(capsys):
    prescribed = [{"id": 301, "exercise_order": 1, "exercise_name": "Snatch", "sets": 5, "reps": 2,
                   "absolute_weight_kg": 80.0, "intensity_pct": 80, "rpe_target": 8.0}]
    # entry 1: linked 301; entry 2: unlinked "Back Squat" with bad reps; entry 3: finish
    inputs = ["301", "3,3,2", "fast", "", "Back Squat", "5,x", "", "", ""]
    prompts = iter([5, 82.5, 9.0, 80.0,            # linked: sets, weight, rpe, make %
                    None, None, None, None])         # unlinked: all blank
    conn = MagicMock()
    with fake_db({"FROM session_exercises": prescribed}), scripted(*inputs), \
         patch.object(log, "_prompt", lambda *a, **k: next(prompts)), \
         patch.object(log, "execute") as ins:
        log.cmd_exercise(42, conn, session_id=11)
    first, second = (c.args[2] for c in ins.call_args_list)
    assert first[:6] == (42, 301, "Snatch", 5, [3, 3, 2], 82.5)
    assert first[6:] == (9.0, 0.8, "fast", 80.0, 2.5, 1.0)      # make rate stored as a fraction
    assert second[1:4] == (None, "Back Squat", 1)                # blank sets → 1 (no valid reps)
    assert second[4] is None and second[5] == 0.0                 # bad reps dropped, weight defaulted
    assert second[-2:] == (None, None)                            # no deviation without a prescription
    assert conn.commit.call_count == 2
    assert "2 exercise(s) logged" in capsys.readouterr().out


def test_exercise_rejects_out_of_range_reps():
    prompts = iter([None, 50.0, None, None])
    with fake_db({}), scripted("Front Squat", "0,3", "", ""), \
         patch.object(log, "_prompt", lambda *a, **k: next(prompts)), \
         patch.object(log, "execute") as ins:
        log.cmd_exercise(42, MagicMock())
    assert ins.call_args.args[2][4] is None                       # [0, 3] contains an out-of-range entry


# ── status ───────────────────────────────────────────────────────

def test_status_reports_session_and_exercise_warnings_and_adherence(capsys):
    logs = [{"id": 1, "log_date": TODAY, "overall_rpe": 9.5, "session_duration_minutes": 80, "sleep_quality": 2,
             "stress_level": 4, "athlete_notes": "tired", "session_label": "Snatch day", "week_number": 2,
             "day_number": 1},
            {"id": 2, "log_date": TODAY - timedelta(days=2), "overall_rpe": None, "session_duration_minutes": None,
             "sleep_quality": None, "stress_level": None, "athlete_notes": None, "session_label": None,
             "week_number": None, "day_number": None}]
    ex_stats = [{"exercise_name": "Snatch", "avg_rpe": 9.0, "avg_rpe_dev": 2.0, "avg_make_rate": 0.6,
                 "sessions": 3, "rpe_samples": 3, "make_rate_samples": 3}]
    routes = {"status = 'active'": PROGRAM, "LEFT JOIN program_sessions ps": logs,
              "AVG(tle.rpe)": ex_stats, "SELECT COUNT(*) as cnt FROM program_sessions": {"cnt": 6},
              "SELECT COUNT(*) as cnt FROM training_logs": {"cnt": 3}}
    with fake_db(routes):
        log.cmd_status(1, MagicMock())
    out = capsys.readouterr().out
    for expected in ("High session RPE (9.5)", "Poor sleep (quality=2)", "High stress (level=4)",
                     "Snatch: avg RPE deviation +2.0", "Snatch: make rate 60%", "Unlinked session",
                     "Adherence through week 2: 3/6 sessions logged (50%)", "Notes: tired"):
        assert expected in out, expected


def test_status_clean_week_and_no_logs(capsys):
    ok_log = [{"id": 1, "log_date": TODAY, "overall_rpe": 7, "session_duration_minutes": 60, "sleep_quality": 4,
               "stress_level": 2, "athlete_notes": None, "session_label": "Snatch day", "week_number": 2,
               "day_number": 1}]
    base = {"status = 'active'": PROGRAM, "AVG(tle.rpe)": [],
            "SELECT COUNT(*) as cnt FROM program_sessions": {"cnt": 0},
            "SELECT COUNT(*) as cnt FROM training_logs": {"cnt": 0}}
    with fake_db({**base, "LEFT JOIN program_sessions ps": ok_log}):
        log.cmd_status(1, MagicMock())
    out = capsys.readouterr().out
    assert "All metrics within normal range" in out and "Adherence" not in out
    with fake_db({**base, "LEFT JOIN program_sessions ps": []}):
        log.cmd_status(1, MagicMock())
    assert "No logs in the past 14 days" in capsys.readouterr().out
    with fake_db({"FROM generated_programs": None}):
        log.cmd_status(1, MagicMock())
    assert "No program found" in capsys.readouterr().out


# ── history ──────────────────────────────────────────────────────

def test_history_lists_logs_and_their_exercises(capsys):
    logs = [{"id": 1, "log_date": TODAY, "overall_rpe": 8, "session_duration_minutes": 70, "athlete_notes": "ok",
             "session_label": "Snatch day", "week_number": 2, "day_number": 1}]
    exercises = [{"exercise_name": "Snatch", "sets_completed": 5, "reps_per_set": [2, 2, 2, 2, 2], "weight_kg": 80,
                  "rpe": 8, "make_rate": 0.8, "technical_notes": "bar drifted"}]
    with fake_db({"LEFT JOIN program_sessions ps": logs, "FROM training_log_exercises": exercises}):
        log.cmd_history(1, MagicMock(), weeks=1)
    out = capsys.readouterr().out
    assert "last 1 week(s)" in out and "W2D1" in out and "5×[2,2,2,2,2]  @80kg  RPE 8  make 80%" in out
    assert "↳ bar drifted" in out
    with fake_db({"LEFT JOIN program_sessions ps": []}):
        log.cmd_history(1, MagicMock())
    assert "No logs in the past 2 week(s)" in capsys.readouterr().out


# ── main ─────────────────────────────────────────────────────────

def test_main_dispatches_each_command_and_closes_the_connection():
    conn = MagicMock()
    for argv, target, expected in (
        (["show", "--athlete-id", "1"], "cmd_show", (1, conn)),
        (["status", "--athlete-id", "1"], "cmd_status", (1, conn)),
        (["history", "--athlete-id", "1", "--weeks", "3"], "cmd_history", (1, conn)),
    ):
        with patch.object(sys, "argv", ["log.py", *argv]), \
             patch.object(log, "get_connection", return_value=conn), \
             patch.object(log, "Settings"), patch.object(log, target) as cmd:
            log.main()
        assert cmd.call_args.args[:2] == expected
    assert conn.close.called
