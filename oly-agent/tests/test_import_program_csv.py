# oly-agent/tests/test_import_program_csv.py
"""
No-DB tests for import_program_csv.py (DOG-1): cell parsers, the sheet →
weeks/days/prescriptions parse (non-sequential week numbers, totals rows,
sheet quirks), intensity-reference and catalogue resolution, log-date
arithmetic, and the write path against mocked DB helpers.

Run: PYTHONUTF8=1 uv run pytest tests/test_import_program_csv.py -q
"""

import csv
import io
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import import_program_csv as ipc
from import_program_csv import (
    INTENSITY_PCT_CEILING,
    catalogue_name_for,
    clamp_intensity,
    focus_area,
    intensity_reference_for,
    parse_exercise_row,
    parse_pct_cell,
    parse_program_csv,
    parse_reps_cell,
    resolve_catalogue_ids,
    session_log_date,
    write_program,
)

# ── cell parsers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cell, expected", [
    ("3", (1, 3, None, False)),
    ("3x3", (3, 3, None, False)),
    ("10x1", (10, 1, None, False)),
    ("3+3", (1, 3, "complex 3+3", False)),
    ("4x3+3", (4, 3, "complex 4x3+3", False)),
    ("2x2+2+2", (2, 2, "complex 2x2+2+2", False)),
    ("5x2+2+1", (5, 2, "complex 5x2+2+1", False)),
    ("1RM", (1, 1, "1RM", True)),
    ("3x12-15", (3, 12, "12-15 reps", False)),
    ("4x8/s", (4, 8, "4x8/s (per side)", False)),
    ("x10", (1, 10, None, False)),
    (" 3x5 ", (3, 5, None, False)),
])
def test_parse_reps_cell(cell, expected):
    assert parse_reps_cell(cell) == expected


@pytest.mark.parametrize("cell", ["", "abc", "3x", "x", "3-", "3xx3"])
def test_parse_reps_cell_rejects(cell):
    with pytest.raises(ValueError):
        parse_reps_cell(cell)


def test_parse_pct_cell_and_clamp():
    assert parse_pct_cell("85") == (85.0, False)
    assert parse_pct_cell("85%") == (85.0, False)
    assert parse_pct_cell("1RM") == (100.0, True)
    assert parse_pct_cell("") == (None, False)
    assert parse_pct_cell("5x5") == (None, False)          # reps spec in the pct column
    assert clamp_intensity(80.0) == (80.0, None)
    assert clamp_intensity(None) == (None, None)
    pct, note = clamp_intensity(125.0)
    assert pct == INTENSITY_PCT_CEILING == 120.0 and "125%" in note


# ── exercise row ─────────────────────────────────────────────────────────────

def _row(text: str) -> list[str]:
    return next(csv.reader(io.StringIO(text)))


def test_parse_exercise_row_one_prescription_per_pair():
    row = _row("Power Snatch,50,3,60,3,70,3,75,4x2,,,,,,,,17,67,1140")
    out = parse_exercise_row(row, pair_end=16)
    assert [(p.sets, p.reps, p.intensity_pct) for p in out] == [(1, 3, 50.0), (1, 3, 60.0), (1, 3, 70.0), (4, 2, 75.0)]
    assert {p.intensity_reference for p in out} == {"snatch"}
    assert all(p.exercise_name == "Power Snatch" and not p.is_max_attempt for p in out)


def test_parse_exercise_row_never_reads_the_totals_columns():
    """Pairs stop before Total Reps even when the header leaves an odd column count."""
    row = _row("Muscle Snatch,50,3,60,3x3,,,,,,,,,,,,12,58,690")
    out = parse_exercise_row(row, pair_end=16)
    assert len(out) == 2 and out[-1].reps == 3 and out[-1].sets == 3


def test_parse_exercise_row_unloaded_max_and_clamp_cases():
    box = parse_exercise_row(_row("Box Jumps,5x5,,,,,,,,,,,,,,,,,"), 16)
    assert [(p.sets, p.reps, p.intensity_pct, p.intensity_reference) for p in box] == [(5, 5, None, None)]
    assert box[0].notes is None
    core = parse_exercise_row(_row("Core,,,,,,,,,,,,,,,,,,"), 16)
    assert (core[0].sets, core[0].reps, core[0].notes) == (1, 1, "unspecified")
    ext = parse_exercise_row(_row("Back Extensions,3x12-15,,,,,,,,,,,,,,,,,"), 16)
    assert (ext[0].sets, ext[0].reps, ext[0].notes) == (3, 12, "12-15 reps")

    heavy = parse_exercise_row(_row("Muscle Snatch - Heavy Single,50,3,60,3,70,2,75,1,80,1,1RM,1,,,,11,83,915"), 16)
    assert heavy[-1].intensity_pct == 100.0 and heavy[-1].is_max_attempt and heavy[-1].notes is None
    assert heavy[-1].intensity_reference == "muscle_snatch" and not heavy[0].is_max_attempt
    bs = parse_exercise_row(_row("Back Squat - Heavy Single,60,3,70,3,75,2,80,2,85,1,88,1,100,1RM,,13,75,975"), 16)
    assert bs[-1].is_max_attempt and bs[-1].notes == "1RM" and bs[-1].reps == 1
    sn = parse_exercise_row(_row("Snatch - Heavy Single,60,3,70,3,75,2,80,2,85,1,90,1,100,1,,13,75,975"), 16)
    assert sn[-1].is_max_attempt and sn[-1].intensity_pct == 100.0   # name says heavy single, pct 100

    cdl = parse_exercise_row(_row('Clean Deadlift 6" Block,125,3x3,,,,,,,,,,,,,,9,125,1125'), 16)
    assert cdl[0].intensity_pct == 120.0 and "125%" in cdl[0].notes and cdl[0].intensity_reference == "clean"
    cj = parse_exercise_row(_row("Clean + Jerk,50,2+2,60,2+2,70,2+2,75,2+2,80,2+2,85%,3x2+1,,,,29,73,2105"), 16)
    assert cj[-1].intensity_pct == 85.0 and (cj[-1].sets, cj[-1].reps) == (3, 2) and cj[-1].notes == "complex 3x2+1"
    assert {p.intensity_reference for p in cj} == {"clean_and_jerk"}


# ── intensity reference / catalogue ──────────────────────────────────────────

@pytest.mark.parametrize("name, ref", [
    ("Snatch", "snatch"), ("Power Snatch from Deficit", "snatch"), ("Muscle Snatch", "snatch"),
    ("SN Balance", "snatch"), ("SN Grip RDL", "snatch"), ("Power SN + OH SQ", "snatch"),
    ("Snatch Extension + Snatch", "snatch"), ("No Foot Snatch", "snatch"),
    ("Muscle Snatch (% of MSN 1rm)", "muscle_snatch"), ("Muscle Snatch (1RM)", "muscle_snatch"),
    ("Clean", "clean"), ("Clean from Deficit", "clean"), ("Clean Extension from Deficit", "clean"),
    ("CL Extension (pause at power pos)", "clean"), ("Clean Deadlift 6\" Block", "clean"),
    ("Mid Grip Deadlift", "clean"), ("Stiff Leg Deadlift (% of CL)", "clean"),
    ("Block Power Clean + Push Press", "clean"),
    ("Clean + Jerk", "clean_and_jerk"), ("Clean & Jerk", "clean_and_jerk"),
    ("Power Clean + Jerk", "clean_and_jerk"), ("Clean Deadlift + Clean + Jerk", "clean_and_jerk"),
    ("Power Clean + FSQ + Jerk", "clean_and_jerk"), ("Power Clean + Power Jerk", "clean_and_jerk"),
    ("Jerk from Rack", "jerk"), ("Power Jerk", "jerk"), ("Jerk Dips", "jerk"),
    ("Push Press", "push_press"), ("BTN Push Press", "push_press"),
    ("Back Squat", "back_squat"), ("Close Stance Back Squat", "back_squat"),
    ("Partial Back Squat (1/4 squat)", "back_squat"), ("Back Squat (60s rest between sets)", "back_squat"),
    ("Front Squat", "front_squat"), ("1+1/4 Front Squat", "front_squat"),
    ("Box Jumps", None), ("BB Rows", None), ("Core", None),
])
def test_intensity_reference_for(name, ref):
    assert intensity_reference_for(name) == ref


@pytest.mark.parametrize("name, target", [
    ("Snatch", "Snatch"), ("Snatch from Deficit", "Snatch from Deficit"),
    ("Block Snatch", "Block Snatch (knee)"), ("Block Snatch (above knee)", "Block Snatch (above knee)"),
    ("Snatch - Heavy Single", "Snatch"), ("Snatch + OH SQ", "Snatch"), ("Snatch Extension + Snatch", "Snatch"),
    ("Hang Snatch", "Hang Snatch (above knee)"), ("No Foot Snatch", "No Feet Snatch"),
    ("Power Snatch (pause at knee)", "Power Snatch"), ("Power Snatch from Deficit", "Power Snatch from Deficit"),
    ("Block Power Snatch + OH SQ", "Power Snatch from Blocks (knee)"),
    ("Muscle Snatch (% of MSN 1rm)", "Muscle Snatch"), ("SN Balance", "Snatch Balance"),
    ("Snatch Extension", "Snatch Extension"), ("SN Pull to Hip + SN Extension", "Snatch Pull to Hip"),
    ("Snatch Extension from Deficit", "Snatch Pull from Deficit"), ("Snatch Grip Deadlift", "Snatch Deadlift"),
    ("SN Grip RDL", "Snatch Grip Romanian Deadlift"),
    ("Clean from Deficit", "Clean from Deficit"), ("Block Clean (at knee)", "Block Clean (knee)"),
    ("Block Clean (above knee)", "Block Clean (above knee)"), ("Power Clean from Deficit", "Power Clean from Deficit"),
    ("Clean + Jerk - Heavy Single", "Clean & Jerk"), ("Clean & Jerk", "Clean & Jerk"),
    ("Power Clean + FSQ + Jerk", "Clean & Jerk"), ("Clean Deadlift + Clean + Jerk", "Clean & Jerk"),
    ("Block Power Clean + Push Press", "Power Clean"), ("Clean Pull to Hip + Clean Ext", "Clean Pull to Hip"),
    ("CL Extension (pause at power pos)", "Clean Extension"), ("Clean Deadlift 6\" Block", "Clean Deadlift"),
    ("Mid Grip Deadlift", "Deadlift"), ("Stiff Leg Deadlift (% of CL)", "Stiff-Leg Deadlift"),
    ("Jerk from Rack", "Jerk"), ("Power Jerk", "Power Jerk"), ("Jerk Dips", "Jerk Dip"),
    ("BTN Push Press", "Push Press Behind the Neck"), ("Partial Back Squat (1/4 squat)", "Quarter Squat"),
    ("Close Stance Back Squat", "Close-Stance Back Squat"), ("1+1/4 Front Squat", "1¼ Front Squat"),
    ("Box Jumps", "Box Jump"), ("Vertical Jumps", "Vertical Jump"), ("BB Rows", "Barbell Row"),
    ("Bulgarian Split Squats", "Bulgarian Split Squat"), ("Core", None),
])
def test_catalogue_name_for(name, target):
    assert catalogue_name_for(name) == target


# ── sheet parse ──────────────────────────────────────────────────────────────

SHEET = """\
GENERAL STRENGTH  BLOCK 1,,,,,,,,,,,,,,,,,,
,,,,,,,,,,,,,,,,,,
Week 1 - DEFICIT CYCLE,,,,,,,,,,,,,,,,,,
Day 1,%,Reps,%,Reps,%,Reps,%,Reps,,,,,,,,Total Reps,Avg Intensity %,Volume
Muscle Snatch,50,3,60,3x3,,,,,,,,,,,,12,58,690
Back Squat,50,5,60,4,70,4,80,4x3,,,,,,,,24,74,1770
Box Jumps,5x5,,,,,,,,,,,,,,,,,
,,,,,,,,,,,,,,,,36,,2460
,,,,,,,,,,,,,,,,,,
Day 3 ,%,Reps,%,Reps,%,Reps,%,Reps,%,Reps,,,,,,Total Reps,Avg Intensity %,Volume
Snatch,60,3,70,3,75,2,80,2,85,1,,,,,,11,74,800
Clean + Jerk,60,3,70,3,75,2,80,2x2,,,,,,,,12,72,860
BB Rows,3x10,,,,,,,,,,,,,,,23,,1660
,,,,,,,,,,,,,,,,,,
,,,,,,,,,,,,,,,,,Week Intensity Avg,
,,,,,,,,,,,,,,,,59,73,4120
,,,,,,,,,,,,,,,,,,
GENERAL STRENGTH  BLOCK 2,,,,,,,,,,,,,,,,,,
Week 8 -  GENERAL CYCLE,,,,,,,,,,,,,,,,,,
Day 1,%,Reps,%,Reps,%,Reps,%,Reps,%,Reps,%,Reps,,,,Total Reps,Avg Intensity %,Volume
"Clean Deadlift 6"" Block",125,3x3,,,,,,,,,,,,,,9,125,1125
Core,,,,,,,,,,,,,,,,,,
,,,,,,,,,,,,,,,,9,,1125
"""


def _parse(text=SHEET):
    return parse_program_csv(list(csv.reader(io.StringIO(text))))


def test_parse_program_structure_and_week_renumbering():
    prog = _parse()
    assert [(w.number, w.file_week, w.block, w.cycle) for w in prog.weeks] == [
        (1, 1, 1, "DEFICIT CYCLE"), (2, 8, 2, "GENERAL CYCLE")]
    assert [[d.number for d in w.days] for w in prog.weeks] == [[1, 3], [1]]
    assert prog.sessions_per_week == 2 and len(prog.sessions) == 3
    w1d1, w1d3, w2d1 = (d for _, d in prog.sessions)
    # totals row closes the day; the week summary row is never read as a day's totals
    assert (w1d1.total_reps, w1d1.volume) == (36, 2460)
    assert (w1d3.total_reps, w1d3.volume) == (23, 1660)       # carried on the unloaded BB Rows row
    assert (w2d1.total_reps, w2d1.volume) == (9, 1125)
    assert [p.exercise_name for p in w1d1.exercises] == ["Muscle Snatch", "Muscle Snatch", "Back Squat"] * 1 + ["Back Squat"] * 3 + ["Box Jumps"]
    assert [p.exercise_name for p in w1d3.exercises][-1] == "BB Rows" and len(w1d3.exercises) == 5 + 4 + 1
    assert [(p.exercise_name, p.intensity_pct, p.notes) for p in w2d1.exercises] == [
        ('Clean Deadlift 6" Block', 120.0, "prescribed at 125% (stored as 120%)"),
        ("Core", None, "unspecified"),
    ]


def test_parse_program_requires_week_headers():
    with pytest.raises(ValueError):
        _parse("Day 1,%,Reps\nSnatch,70,3\n")
    with pytest.raises(ValueError):
        _parse("nothing,here\n")


def test_resolve_catalogue_ids_and_focus_area():
    prog = _parse()
    lookup = {"muscle snatch": 7, "back squat": 39, "snatch": 1, "clean & jerk": 44, "clean deadlift": 31,
              "box jump": 90}
    unmapped = resolve_catalogue_ids(prog, lookup)
    assert unmapped == ["BB Rows", "Core"]          # Barbell Row is not in this test lookup
    w1d1, w1d3, w2d1 = (d for _, d in prog.sessions)
    assert [p.exercise_id for p in w1d1.exercises] == [7, 7, 39, 39, 39, 39, 90]
    assert {p.exercise_id for p in w1d3.exercises} == {1, 44, None}
    assert w2d1.exercises[0].exercise_id == 31
    assert focus_area(w1d1) == "snatch" and focus_area(w1d3) == "snatch" and focus_area(w2d1) == "clean"


# ── dates ────────────────────────────────────────────────────────────────────

def test_session_log_date_spreads_sessions_over_the_week():
    start = date(2026, 6, 29)                                    # a Monday
    assert session_log_date(start, 1, 1, 4) == date(2026, 6, 29)
    assert session_log_date(start, 1, 2, 4) == date(2026, 6, 30)
    assert session_log_date(start, 1, 3, 4) == date(2026, 7, 2)     # Thursday
    assert session_log_date(start, 1, 4, 4) == date(2026, 7, 3)
    assert session_log_date(start, 3, 1, 4) == date(2026, 7, 13)
    assert session_log_date(start, 1, 3, 3) == date(2026, 7, 3)     # 3/wk → Mon Wed Fri
    assert session_log_date(start, 1, 7, 4) == date(2026, 7, 5)     # out-of-range day clamps to Sunday


# ── write path ───────────────────────────────────────────────────────────────

def test_write_program_inserts_sessions_exercises_logs_and_outcome():
    prog = _parse()
    ids = iter(range(100, 1000))
    athlete = {"id": 1, "username": "dshi", "password_hash": "x", "level": "intermediate", "name": "David"}
    max_rows = [{"name": "Snatch", "weight_kg": 70}, {"name": "Clean & Jerk", "weight_kg": 92},
                {"name": "Back Squat", "weight_kg": 150}]
    exercise_rows = [{"id": 7, "name": "Muscle Snatch"}, {"id": 39, "name": "Back Squat"}, {"id": 1, "name": "Snatch"},
                     {"id": 44, "name": "Clean & Jerk"}]
    conn = MagicMock()
    with patch.object(ipc, "fetch_one", return_value=athlete), \
         patch.object(ipc, "fetch_all", side_effect=[max_rows, exercise_rows]), \
         patch.object(ipc, "execute_returning", side_effect=lambda *a, **k: next(ids)) as ret, \
         patch.object(ipc, "execute") as execute, \
         patch("feedback.compute_outcome") as compute_outcome, \
         patch("feedback.save_outcome") as save_outcome:
        program_id = write_program(
            conn, 1, prog, name="Imported", phase="general_prep", start_date=date(2026, 6, 29),
            source_name="sheet.csv", log_as_completed=True, make_rate=0.9,
        )

    assert program_id == 100
    sql_calls = [c.args[1] for c in ret.call_args_list]
    n_sessions, n_exercises = 3, sum(len(d.exercises) for _, d in prog.sessions)
    assert sum("INSERT INTO generated_programs" in s for s in sql_calls) == 1
    assert sum("INSERT INTO program_sessions" in s for s in sql_calls) == n_sessions
    assert sum("INSERT INTO session_exercises" in s for s in sql_calls) == n_exercises
    assert sum("INSERT INTO training_logs" in s for s in sql_calls) == n_sessions
    log_ex = [c.args[2] for c in execute.call_args_list if "INSERT INTO training_log_exercises" in c.args[1]]
    assert len(log_ex) == n_exercises

    program_params = ret.call_args_list[0].args[2]
    assert program_params[0] == 1 and program_params[1] == "Imported" and program_params[3] == 2   # duration_weeks
    assert program_params[4] == 2                                                                 # sessions_per_week
    assert program_params[6] == date(2026, 7, 12)                                                 # end_date
    assert '"password_hash"' not in program_params[7] and '"level": "intermediate"' in program_params[7]
    assert '"snatch": 70.0' in program_params[8]
    assert '"source": "csv_import"' in program_params[9] and '"file": "sheet.csv"' in program_params[9]

    # session_exercises: Back Squat 4x3 @80% of 150 → 120 kg; Box Jumps unloaded → no weight
    se = [c.args[2] for c in ret.call_args_list if "INSERT INTO session_exercises" in c.args[1]]
    bs = next(p for p in se if p[3] == "Back Squat" and p[4] == 4)
    assert (bs[5], bs[6], bs[7], bs[8]) == (3, 80.0, "back_squat", 120.0)
    box = next(p for p in se if p[3] == "Box Jumps")
    assert (box[4], box[5], box[6], box[7], box[8]) == (5, 5, None, None, None)
    cdl = next(p for p in se if p[3].startswith("Clean Deadlift"))
    assert cdl[6] == 120.0 and cdl[7] == "clean" and cdl[8] is None      # no clean max → NULL weight, note kept

    # training logs: dated per week/day, make_rate only on competition lifts, weight 0 when unknown
    logs = [c.args[2] for c in ret.call_args_list if "INSERT INTO training_logs" in c.args[1]]
    # a 2-day week: day label 3 exceeds the spread → clamps to +2 days (Wed)
    assert [p[2] for p in logs] == [date(2026, 6, 29), date(2026, 7, 1), date(2026, 7, 6)]
    snatch_log = next(p for p in log_ex if p[3] == "Snatch" and p[4] == 1)
    assert snatch_log[5] == [3] and snatch_log[9] == 0.9
    bs_log = next(p for p in log_ex if p[3] == "Back Squat" and p[4] == 4)
    assert bs_log[6] == 120.0 and bs_log[7] == 120.0 and bs_log[8] == 0 and bs_log[9] is None
    box_log = next(p for p in log_ex if p[3] == "Box Jumps")
    assert box_log[6] == 0 and box_log[7] is None and box_log[8] is None

    compute_outcome.assert_called_once_with(100, 1, conn)
    save_outcome.assert_called_once_with(compute_outcome.return_value, conn)
    assert conn.commit.call_count >= 2


def test_write_program_without_logs_skips_outcome():
    prog = _parse()
    ids = iter(range(1, 1000))
    with patch.object(ipc, "fetch_one", return_value={"id": 1}), \
         patch.object(ipc, "fetch_all", side_effect=[[], []]), \
         patch.object(ipc, "execute_returning", side_effect=lambda *a, **k: next(ids)) as ret, \
         patch.object(ipc, "execute") as execute, \
         patch("feedback.compute_outcome") as compute_outcome:
        write_program(MagicMock(), 1, prog, name="n", phase="general_prep", start_date=date(2026, 6, 29),
                      source_name="s.csv", log_as_completed=False, make_rate=1.0)
    assert not any("training_logs" in c.args[1] for c in ret.call_args_list)
    assert not any("training_log_exercises" in c.args[1] for c in execute.call_args_list)
    compute_outcome.assert_not_called()
