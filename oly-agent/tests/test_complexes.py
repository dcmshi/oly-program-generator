# tests/test_complexes.py
"""
No-DB tests for first-class exercise complexes (PLAN-3a): the definition
helpers, annotation after parsing, component-level validation (Prilepin per
set, the week's ceiling, session volume), catalogue selection, the prompt
section, name acceptance, storage and the display label.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from complexes import (
    annotate_complexes,
    comp_component_reps,
    comp_reps_per_set,
    complex_line,
    complex_scheme,
    counts_as_competition_lift,
    prescription_label,
    reps_for_prilepin,
)
from validate import validate_session

SNATCH_OHS = {"id": 2, "name": "Snatch + Overhead Squat", "total_reps_per_set": 3, "intensity_reference": "snatch",
              "typical_intensity_low": 60, "typical_intensity_high": 80, "complexity_level": 2,
              "exercises_ordered": [{"exercise_name": "Snatch", "reps": 1}, {"exercise_name": "Overhead Squat", "reps": 2}]}
PULL_CLEAN = {"id": 3, "name": "Clean Pull + Clean", "total_reps_per_set": 2, "intensity_reference": "clean",
              "complexity_level": 2, "typical_intensity_low": 65, "typical_intensity_high": 82,
              "exercises_ordered": [{"exercise_name": "Clean Pull", "reps": 1}, {"exercise_name": "Clean", "reps": 1}]}
PULL_SQUAT = {"id": 9, "name": "Clean Pull + Front Squat", "total_reps_per_set": 2, "intensity_reference": "clean",
              "complexity_level": 1,
              "exercises_ordered": [{"exercise_name": "Clean Pull", "reps": 1}, {"exercise_name": "Front Squat", "reps": 1}]}
CATALOGUE = [SNATCH_OHS, PULL_CLEAN, PULL_SQUAT]


def _rx(name, sets=4, reps=1, pct=75.0, ref="snatch", order=1):
    return {"exercise_name": name, "exercise_order": order, "sets": sets, "reps": reps, "intensity_pct": pct,
            "intensity_reference": ref, "rest_seconds": 120, "rpe_target": 8.0, "selection_rationale": "r",
            "source_principle_ids": []}


def test_scheme_and_prompt_line():
    assert complex_scheme(SNATCH_OHS["exercises_ordered"]) == "1+2"
    line = complex_line(SNATCH_OHS)
    assert line == "  Snatch + Overhead Squat = Snatch ×1 + Overhead Squat ×2 [snatch, c2] 60–80% | reps = 3"
    assert "%" not in complex_line({**SNATCH_OHS, "typical_intensity_low": None})


def test_annotate_sets_the_definition_and_leaves_plain_exercises():
    exs = annotate_complexes([_rx("snatch + overhead squat", reps=1, ref=""), _rx("Snatch")], CATALOGUE)
    cx, plain = exs
    assert cx["complex_id"] == 2 and cx["reps"] == 3 and cx["notes"] == "complex 1+2"
    assert cx["exercise_id"] is None and cx["intensity_reference"] == "snatch"      # blank ref filled
    assert "complex" not in plain and plain["reps"] == 1
    assert annotate_complexes([_rx("Snatch")], None)[0].get("complex_id") is None


def test_component_logic_ignores_the_name_markers():
    """'squat' / 'pull' in a complex's name must not make it a non-competition lift."""
    ohs, pc, ps = annotate_complexes([_rx("Snatch + Overhead Squat"), _rx("Clean Pull + Clean", ref="clean"),
                                      _rx("Clean Pull + Front Squat", ref="clean")], CATALOGUE)
    assert counts_as_competition_lift(ohs) and counts_as_competition_lift(pc)
    assert not counts_as_competition_lift(ps) and comp_component_reps(ps) == []
    assert comp_component_reps(ohs) == [1] and reps_for_prilepin(ohs) == 1 and comp_reps_per_set(ohs) == 1
    assert reps_for_prilepin(ps) == 0
    plain = _rx("Snatch", reps=2)
    assert reps_for_prilepin(plain) == 2 and comp_reps_per_set(plain) == 2 and counts_as_competition_lift(plain)


WEEK = {"intensity_floor": 70, "intensity_ceiling": 92, "total_competition_lift_reps": 60,
        "reps_per_set_range": [1, 3], "volume_modifier": 1.0}


def _validate(exs):
    return validate_session(annotate_complexes(exs, CATALOGUE), WEEK, [], {"exercise_preferences": {}})


def test_heavy_complex_is_judged_by_its_competition_components():
    # 3 total reps at 90 % would break Prilepin's 2-rep limit; the snatch component is 1 rep
    res = _validate([_rx("Snatch + Overhead Squat", sets=3, pct=90.0)])
    assert not any("Prilepin allows max 2 reps" in e for e in res.errors), res.errors
    # …but the week ceiling still applies although the name contains "squat"
    res = _validate([_rx("Snatch + Overhead Squat", sets=3, pct=95.0)])
    assert any("exceeds week ceiling" in e for e in res.errors), res.errors
    # a plain 3-rep snatch at 90 % is still an error
    res = _validate([_rx("Snatch", sets=3, reps=3, pct=90.0)])
    assert any("Prilepin allows max 2 reps" in e for e in res.errors)


def test_complex_volume_counts_only_competition_reps():
    from validate import _session_comp_lift_reps
    exs = annotate_complexes([_rx("Snatch + Overhead Squat", sets=5, pct=78.0),
                              _rx("Clean Pull + Front Squat", sets=5, pct=85.0, ref="clean")], CATALOGUE)
    reps = _session_comp_lift_reps(exs)
    assert sum(reps.values()) == 5                                  # 5 sets × 1 snatch; the pull+squat adds 0


def test_select_available_complexes_respects_catalogue_and_avoid_list():
    from retrieve import select_available_complexes
    available = [{"name": n} for n in ("Snatch", "Overhead Squat", "Clean Pull", "Clean")]
    got = select_available_complexes(CATALOGUE, available, avoid=[])
    assert [c["id"] for c in got] == [2, 3]                          # Front Squat not in this catalogue
    assert [c["id"] for c in select_available_complexes(CATALOGUE, available, avoid=["overhead_squat"])] == [3]
    assert select_available_complexes([{"name": "x", "exercises_ordered": []}], available, None) == []


def test_fetch_complexes_degrades_to_none_on_an_old_schema():
    from retrieve import _fetch_complexes
    conn = MagicMock()
    with patch("retrieve.fetch_all", side_effect=RuntimeError("column complexity_level does not exist")):
        assert _fetch_complexes(conn, 3) == []
    conn.rollback.assert_called_once()
    with patch("retrieve.fetch_all", return_value=[SNATCH_OHS]) as fa:
        assert _fetch_complexes(conn, 3) == [SNATCH_OHS]
    assert fa.call_args.args[2] == (3,)


def test_prompt_section_only_when_complexes_are_offered():
    from generate import _available_complexes_section
    assert _available_complexes_section([]) == "" and _available_complexes_section(None) == ""
    text = _available_complexes_section([SNATCH_OHS])
    assert text.startswith("## Available Complexes") and "Snatch + Overhead Squat = Snatch ×1" in text
    assert "sets = number of complexes" in text


def test_complex_names_pass_name_validation():
    from generate import validate_exercise_names
    names = ["Snatch", "Overhead Squat"] + [c["name"] for c in CATALOGUE]
    assert validate_exercise_names([_rx("Snatch + Overhead Squat")], names) == []
    assert validate_exercise_names([_rx("Snatch + Overhead Squat")], ["Snatch"])


def test_save_session_stores_complex_id_and_scheme():
    from orchestrator import _save_session
    ex = annotate_complexes([_rx("Snatch + Overhead Squat", sets=4)], CATALOGUE)[0]
    tmpl = SimpleNamespace(label="Snatch day", primary_movement="snatch")
    with patch("orchestrator.execute_returning", return_value=77), patch("orchestrator.execute") as ins:
        assert _save_session(MagicMock(), 1, 1, 1, tmpl, [ex]) == 77
    sql, params = ins.call_args.args[1], ins.call_args.args[2]
    assert "complex_id, notes" in sql and params[-2:] == (2, "complex 1+2") and params[5] == 3


def test_resolve_exercise_ids_skips_complexes():
    from weight_resolver import resolve_exercise_ids
    ex = annotate_complexes([_rx("Snatch + Overhead Squat")], CATALOGUE)[0]
    out = resolve_exercise_ids([ex, _rx("Snatch")], {"snatch": 5})
    assert out[0]["exercise_id"] is None and out[1]["exercise_id"] == 5


def test_prescription_label():
    assert prescription_label({"sets": 4, "reps": 3, "complex_id": 2, "notes": "complex 1+2"}) == "4×(1+2)"
    assert prescription_label({"sets": 5, "reps": 2, "complex_id": None, "notes": None}) == "5×2"
    assert prescription_label(SimpleNamespace(sets=3, reps=1, complex_id=None, notes="")) == "3×1"
