# oly-agent/tests/test_formulas.py
"""
Tests for shared/formulas.py — round_kg() and estimate_session_minutes().
These back the R1/R2 refactor that removed three duplicate rounding copies and
two duplicate duration formulas.

Run: python tests/test_formulas.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.constants import SECONDS_PER_SET
from shared.formulas import estimate_session_minutes, round_kg

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


# ── round_kg ──────────────────────────────────────────────────────────────────

def test_round_kg_rounds_to_half():
    assert round_kg(72.3) == 72.5
    assert round_kg(72.24) == 72.0
    assert round_kg(100.0) == 100.0
    assert round_kg(0) == 0.0


def test_round_kg_matches_legacy_formula():
    # Legacy implementations used round(raw * 2) / 2 — must stay identical.
    for raw in (0.1, 47.6, 88.25, 123.74, 160.0):
        assert round_kg(raw) == round(raw * 2) / 2, raw


# ── estimate_session_minutes ────────────────────────────────────────────────

def test_estimate_session_minutes_basic():
    # 3 sets @120s rest + 4 sets @90s rest
    exercises = [
        {"sets": 3, "rest_seconds": 120},
        {"sets": 4, "rest_seconds": 90},
    ]
    expected = (3 * (SECONDS_PER_SET + 120) + 4 * (SECONDS_PER_SET + 90)) / 60
    assert estimate_session_minutes(exercises) == expected


def test_estimate_session_minutes_default_rest():
    # Missing rest_seconds falls back to the default (90s).
    exercises = [{"sets": 2}]
    expected = (2 * (SECONDS_PER_SET + 90)) / 60
    assert estimate_session_minutes(exercises) == expected


def test_estimate_session_minutes_empty():
    assert estimate_session_minutes([]) == 0


# ── DOG-1g: the weekly comp-lift budget is one Prilepin optimal per session ──

def test_session_rep_target_is_one_prilepin_optimal_per_session():
    from shared.prilepin import compute_session_rep_target

    shares = (0.30, 0.30, 0.20, 0.20)                      # a 4-day template set
    targets = [compute_session_rep_target(70, 80, s, 1.0, sessions_per_week=4) for s in shares]
    assert targets == [22, 22, 14, 14]                     # 18 optimal x share x 4
    assert sum(targets) == 72                              # the real block did 87-96, the generator 63-94
    deload = [compute_session_rep_target(65, 73, s, 0.6, sessions_per_week=4) for s in shares]
    assert sum(deload) < sum(targets) and all(t >= 3 for t in deload)
    # the old single-session semantics remain the default for lone callers
    assert compute_session_rep_target(70, 80, 0.30, 1.0) == 5
    return True, ""


def test_plan_weekly_budget_scales_with_frequency():
    from unittest.mock import patch

    from plan import plan
    from tests.test_plan import _FakeSettings, _ctx

    with patch("plan.fetch_all", return_value=[]):
        four = plan(_ctx(previous_program={"phase": "x"}, sessions_per_week=4), None, _FakeSettings())
        three = plan(_ctx(previous_program={"phase": "x"}, sessions_per_week=3), None, _FakeSettings())
    w4 = next(w for w in four.weekly_targets if not w.is_deload)
    w3 = next(w for w in three.weekly_targets if not w.is_deload)
    assert 50 <= w4.total_competition_lift_reps <= 100, w4
    assert w3.total_competition_lift_reps < w4.total_competition_lift_reps
    return True, ""


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        _test(name, fn)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    failed = sum(1 for r in RESULTS if r[0] in ("FAIL", "ERROR"))
    for r in RESULTS:
        detail = f"  → {r[2]}" if len(r) > 2 else ""
        print(f"  {r[0]}  {r[1]}{detail}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
