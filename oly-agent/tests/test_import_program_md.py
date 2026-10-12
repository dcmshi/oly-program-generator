"""import_program_md.py: the Markdown program parser (Paul's notation)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from import_program_md import catalogue_name, md_session_label, parse_program_md, parse_scheme

DOC = r"""**Week 1**

**Day 1**
**Muscle SN:** 30% 4x5
**No Foot Clean \+ Jerk** 50% 2+2, 70% 2x2+2, 75% 4x1+2
**Back SQ:** 70% x4, 80-85% 3-4x2
Reverse Lunges: 3x10
DB Incline Press 3x10-15
**Core**

**Day 4 (optional)**
**RDL** 80% of CL 3x5
**Power CL \+ PP (% of PP)** 88%++ 4x1+1
Copenhagen Plank 2x30s/s
**Push Ups** 3 x max reps
**DB Seated Press** 2x7, 3x5

**Week 2**

**Day 1**
**Clean Deadlift** 115-120% 3x3
"""


def _days():
    program = parse_program_md(DOC)
    return program, [(w.number, d) for w, d in program.sessions]


def test_weeks_days_and_labels():
    program, days = _days()
    assert [w.number for w in program.weeks] == [1, 2]
    assert [(n, d.number) for n, d in days] == [(1, 1), (1, 4), (2, 1)]
    assert md_session_label(program.weeks[0], days[1][1]) == "Week 1 · Day 4 (optional)"


def test_bold_and_plain_lines_and_abbreviations():
    _, days = _days()
    d1 = days[0][1].exercises
    names = [e.exercise_name for e in d1]
    assert names[0] == "Muscle Snatch" and "Back Squat" in names and "Reverse Lunges" in names
    assert "DB Incline Press" in names and names[-1] == "Core" and d1[-1].notes == "unspecified"
    ms = d1[0]
    assert (ms.sets, ms.reps, ms.intensity_pct, ms.intensity_reference) == (4, 5, 30.0, "snatch")


def test_complexes_ranges_and_set_ranges():
    _, days = _days()
    cj = [e for e in days[0][1].exercises if e.exercise_name == "No Foot Clean + Jerk"]
    assert [(e.sets, e.reps, e.intensity_pct, e.intensity_reference) for e in cj] == [
        (1, 2, 50.0, "clean_and_jerk"), (2, 2, 70.0, "clean_and_jerk"), (4, 1, 75.0, "clean_and_jerk")]
    assert cj[2].notes == "complex 4x1+2"
    sq = [e for e in days[0][1].exercises if e.exercise_name == "Back Squat"]
    assert (sq[1].sets, sq[1].reps, sq[1].intensity_pct) == (3, 2, 80.0)
    assert sq[1].notes == "80-85%; 3-4 sets"


def test_percent_of_another_lift_and_plus_marks():
    _, days = _days()
    d4 = {e.exercise_name: e for e in days[1][1].exercises}
    assert d4["RDL"].intensity_reference == "clean" and d4["RDL"].intensity_pct == 80.0
    pc = d4["Power Clean + Push Press (% of Push Press)"]
    assert pc.intensity_reference == "push_press" and "88%++" in pc.notes


def test_unloaded_specials():
    _, days = _days()
    ex = days[1][1].exercises
    plank = next(e for e in ex if e.exercise_name == "Copenhagen Plank")
    assert (plank.sets, plank.reps, plank.notes) == (2, 30, "30 s per side")
    push = next(e for e in ex if e.exercise_name == "Push Ups")
    assert (push.sets, push.notes) == (3, "max reps")
    seated = [e for e in ex if e.exercise_name == "DB Seated Press"]
    assert [(e.sets, e.reps) for e in seated] == [(2, 7), (3, 5)]


def test_supramaximal_percentages_are_kept():
    _, days = _days()
    dl = days[2][1].exercises[0]
    assert (dl.intensity_pct, dl.intensity_reference, dl.notes) == (115.0, "clean", "115-120%")


def test_parse_scheme_edge_cases():
    assert parse_scheme("3x6 (light weight)") == (3, 6, ["light weight"])
    assert parse_scheme("2-3x45-60sec") == (2, 45, ["2-3 sets", "45-60 s"])
    assert parse_scheme("4x6 k") == (4, 6, [])
    assert parse_scheme("3-4x6 or Inverted Rows 3-4x12") == (3, 6, ["3-4 sets", "or Inverted Rows 3-4x12"])


def test_catalogue_names():
    assert catalogue_name("RDL (% of Clean)") is None                 # not "Clean"
    assert catalogue_name("Bent Over BB Rows") == "Barbell Row"
    assert catalogue_name("No Foot Snatch or No Touch Snatch") == "No Feet Snatch"
    assert catalogue_name("Power Snatch") == "Power Snatch"            # falls through to the CSV map
