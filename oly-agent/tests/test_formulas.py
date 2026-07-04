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
