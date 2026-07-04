# oly-agent/tests/test_phase_progression.py
"""
Tests for phase_progression.py — the single source of truth shared by
plan._advance_phase and feedback._compute_phase_verdict (R5).

Run: python tests/test_phase_progression.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from phase_progression import PHASE_SEQUENCE, compute_load_adjustments, decide_next_phase

RESULTS = []


def _test(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name))
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
    except Exception as e:
        RESULTS.append(("ERROR", name, f"{type(e).__name__}: {e}"))


# ── decide_next_phase ─────────────────────────────────────────────────────────

def test_cold_start_none():
    assert decide_next_phase(None, 90, 0.85, 0.2) == ("accumulation", False, "cold_start")


def test_cold_start_unknown_phase():
    assert decide_next_phase("mystery", 90, 0.85, 0.2) == ("accumulation", False, "cold_start")


def test_realization_rebuilds_with_accumulation():
    assert decide_next_phase("realization", 90, 0.85, 0.2) == ("accumulation", False, "realization_rebuild")


def test_realization_overreached_still_rebuilds():
    # A-H3: high RPE deviation after realization must NOT force a second peaking
    # block — realization always rebuilds with accumulation.
    assert decide_next_phase("realization", 90, 0.85, 2.0) == ("accumulation", False, "realization_rebuild")


def test_accumulation_advances_to_intensification():
    assert decide_next_phase("accumulation", 75, 0.80, 0.5) == ("intensification", True, "advanced")


def test_general_prep_advances_to_accumulation():
    assert decide_next_phase("general_prep", 75, 0.80, 0.5) == ("accumulation", True, "advanced")


def test_intensification_advances_to_realization():
    assert decide_next_phase("intensification", 75, 0.80, 0.5) == ("realization", True, "advanced")


def test_ready_but_rpe_blocked_holds_phase():
    assert decide_next_phase("accumulation", 90, 0.85, 2.0) == ("accumulation", False, "rpe_held")


def test_not_ready_repeats_phase():
    assert decide_next_phase("accumulation", 50, 0.60, 0.3) == ("accumulation", False, "repeated")


def test_sequence_order():
    assert PHASE_SEQUENCE == ["general_prep", "accumulation", "intensification", "realization"]


# ── compute_load_adjustments ────────────────────────────────────────────────

def test_no_adjustments_when_good():
    assert compute_load_adjustments(80, 0.80, 0.3) == []


def test_low_adherence_reduces_volume():
    assert any("low adherence" in a for a in compute_load_adjustments(50, 0.80, 0.3))


def test_low_make_rate_reduces_intensity():
    assert any("low make rate" in a for a in compute_load_adjustments(80, 0.60, 0.3))


def test_high_rpe_reduces_volume():
    assert any("high RPE deviation" in a for a in compute_load_adjustments(80, 0.80, 1.5))


def test_excellent_boosts_intensity():
    assert any("excellent performance" in a for a in compute_load_adjustments(95, 0.90, 0.2))


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
