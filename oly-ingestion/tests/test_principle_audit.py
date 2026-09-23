# tests/test_principle_audit.py
"""No-key unit tests for principle_audit.py (PRIN-AUDIT): which numbers a
principle claims, and when the source text supports them."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from principle_audit import (
    Claim,
    audit_principle,
    evaluate,
    match_source_files,
    numeric_claims,
    strip_unsupported,
    text_numbers,
)
from principle_model_compare import densest_window


def test_text_numbers_reads_digits_ranges_thousands_and_words():
    nums = text_numbers("Pulls at 80-105% of the best clean; 1,500 lifts a month, twice a week, 16.5% GPP.")
    assert {80, 105, 92.5, 1500, 2, 16.5} <= nums


def test_numeric_claims_skips_booleans_and_lists_and_reads_comparisons():
    claims = numeric_claims(
        {"intensity_ceiling": 90, "include_deload_week": True, "prefer_exercises": ["snatch"]},
        {"weeks_out_from_competition": {"lte": 2}, "training_age_years": {"between": [1, 3]}, "phase": "accumulation"},
    )
    assert Claim("recommendation", "intensity_ceiling", 90.0) in claims
    assert {c.key for c in claims} == {"intensity_ceiling", "weeks_out_from_competition", "training_age_years"}
    assert len(claims) == 4


def test_volume_modifier_supported_as_percent_or_reduction():
    nums = text_numbers("In the taper, reduce volume by 40%.")
    sup, unsup = audit_principle({"volume_modifier": 0.6}, {}, nums)
    assert sup and not unsup
    sup, unsup = audit_principle({"volume_modifier": 0.4}, {}, text_numbers("GPP is 40% of the time"))
    assert sup                                   # the audit checks numbers, not their meaning


def test_invented_numbers_are_flagged():
    """The source 808 cases: nothing in the text says 85 or 6."""
    nums = text_numbers("Usage of maximum weights by 12-15 year olds should be strictly regulated.")
    _, unsup = audit_principle({"intensity_ceiling": 85, "sessions_per_week_max": 6}, {}, nums)
    assert {c.key for c in unsup} == {"intensity_ceiling", "sessions_per_week_max"}


def test_rest_seconds_supported_by_minutes():
    _, unsup = audit_principle({"rest_between_sets_min": 180}, {}, text_numbers("rest 3 minutes between sets"))
    assert not unsup


def test_strip_unsupported_keeps_condition_and_other_keys():
    rec = {"intensity_ceiling": 85, "avoid_exercises": ["push jerk"], "total_reps_max": 20}
    out = strip_unsupported(rec, [Claim("recommendation", "intensity_ceiling", 85),
                                  Claim("condition", "week_of_block", 3)])
    assert out == {"avoid_exercises": ["push jerk"], "total_reps_max": 20}


def test_source_without_text_is_unverifiable_not_flagged():
    rows = [(1, 10, "Rule", {"intensity_ceiling": 85}, {}, "m", "website")]
    stats, changes, flagged, _ = evaluate(rows, {10: ""})
    s = stats["m · website"]
    assert s["unverifiable"] == 1 and s["unsupported_claims"] == 0
    assert not changes and not flagged


def test_source_files_map_is_explicit():
    files = [Path("Tudor Bompa, Carlo Buzzichelli - Periodization.epub"),
             Path("Hornsby 2017 - Strength RFD and power.txt")]
    assert match_source_files(802, files) == [files[0]]
    assert match_source_files(5, files) == []                     # the never-obtained book


def test_densest_window_picks_the_numeric_part():
    text = "prose " * 4000 + "70% 75% 80% 85% " * 50 + "prose " * 4000
    w = densest_window(text, size=2000)
    assert w.count("%") >= 100 and len(w) == 2000


def test_competition_day_condition_is_not_a_numeric_claim_to_check():
    _, unsup = audit_principle({}, {"weeks_out_from_competition": {"eq": 0}}, text_numbers("the warm-up room"))
    assert not unsup


def test_audit_source_strips_unsupported_keys_and_backs_them_up(tmp_path):
    """The end-of-ingest pass: numbers checked against chunks + the document
    text the ingest classified; unsupported keys stripped, previous values
    appended to the JSON-lines backup."""
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from principle_audit import audit_source

    cur = MagicMock()
    cur.fetchall.side_effect = [
        [(9999, "Pulls at 80-90% of the best clean.")],                        # chunk text
        [(1, 9999, "Pull range", {"intensity_floor": 80, "intensity_ceiling": 90}, {}, "m", "book"),
         (2, 9999, "Juvenile cap", {"intensity_ceiling": 75, "avoid_exercises": ["x"]}, {}, "m", "book"),
         (3, 9999, "Taper", {"volume_modifier": 0.6}, {}, "m", "book")],       # 0.6 ← "reduce by 40%" in the document
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    backup = tmp_path / "b.jsonl"
    with patch("psycopg2.connect", return_value=conn):
        out = audit_source(9999, SimpleNamespace(database_url="db"), "In the taper reduce volume by 40%.",
                           backup_path=backup)
    assert out == {"claims": 4, "unsupported": 1, "rows_changed": 1}
    updates = [c for c in cur.execute.call_args_list if "UPDATE" in c.args[0]]
    assert len(updates) == 1 and json.loads(updates[0].args[1][0]) == {"avoid_exercises": ["x"]}
    assert json.loads(backup.read_text().splitlines()[0])["before"] == {"intensity_ceiling": 75, "avoid_exercises": ["x"]}
    conn.commit.assert_called_once()


def test_run_audit_pass_never_raises():
    from types import SimpleNamespace
    from unittest.mock import patch

    from principle_audit import run_audit_pass

    with patch("psycopg2.connect", side_effect=RuntimeError("no db")):
        assert run_audit_pass(1, SimpleNamespace(database_url="db")) is None
