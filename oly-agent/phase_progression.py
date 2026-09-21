# oly-agent/phase_progression.py
"""Single source of truth for phase-advancement decisions.

Both plan._advance_phase (which builds the *next* program) and
feedback._compute_phase_verdict (which reports what will happen at completion)
must agree on where an athlete goes next. They used to hand-mirror each other,
which is exactly how the realization→accumulation logic drifted (A-H3). Both now
call decide_next_phase() / compute_load_adjustments() so they cannot diverge.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.constants import (
    ADJUST_RPE_DEVIATION,
    ADVANCE_MAX_RPE_DEVIATION,
    ADVANCE_MIN_ADHERENCE_PCT,
    ADVANCE_MIN_MAKE_RATE,
    EXCELLENT_ADHERENCE_PCT,
    EXCELLENT_MAKE_RATE,
    OUTCOME_ADHERENCE_FULL_MISS_PCT,
    OUTCOME_INTENSITY_BOOST_EXCELLENT,
    OUTCOME_INTENSITY_STEP_MAKE_RATE,
    OUTCOME_MAKE_RATE_FULL_MISS,
    OUTCOME_RPE_FULL_MISS,
    OUTCOME_VOLUME_STEP_ADHERENCE,
    OUTCOME_VOLUME_STEP_RPE,
)

# Standard periodization progression (loops back after realization).
PHASE_SEQUENCE = ["general_prep", "accumulation", "intensification", "realization"]


def decide_next_phase(
    prev_phase: str | None,
    adherence_pct: float,
    avg_make_rate: float,
    avg_rpe_deviation: float,
) -> tuple[str, bool, str]:
    """Decide the next training phase.

    Returns (next_phase, advanced, status) where status is a machine-readable
    reason code the callers map to their own display text:
      cold_start | realization_rebuild | advanced | at_top | rpe_held | repeated

    Rules: advance only when adherence ≥ threshold AND make rate ≥ threshold and
    RPE deviation is not excessive; realization always rebuilds with accumulation
    (never repeats peaking, even when overreached — that's the A-H3 fix).
    """
    ready = adherence_pct >= ADVANCE_MIN_ADHERENCE_PCT and avg_make_rate >= ADVANCE_MIN_MAKE_RATE
    rpe_blocked = avg_rpe_deviation > ADVANCE_MAX_RPE_DEVIATION

    if prev_phase not in PHASE_SEQUENCE:
        return "accumulation", False, "cold_start"
    if prev_phase == "realization":
        return "accumulation", False, "realization_rebuild"
    if ready and not rpe_blocked:
        idx = PHASE_SEQUENCE.index(prev_phase)
        nxt = PHASE_SEQUENCE[min(idx + 1, len(PHASE_SEQUENCE) - 1)]
        advanced = nxt != prev_phase
        return nxt, advanced, "advanced" if advanced else "at_top"
    if ready and rpe_blocked:
        return prev_phase, False, "rpe_held"
    return prev_phase, False, "repeated"


def compute_load_deltas(
    adherence_pct: float,
    avg_make_rate: float,
    avg_rpe_deviation: float,
) -> tuple[float, float, list[str]]:
    """Next-program load nudges implied by the previous outcome (PLAN-2 §1.8):
    `(volume_delta, intensity_ceiling_delta, labels)`.

    Each nudge is proportional to the size of the miss, up to the step that used
    to be applied flat: adherence 69 % is −1 %, 40 % is −10 %; make rate 0.74 is
    −0.4 pts, 0.50 is −3; RPE deviation 1.1 is −0.4 %, 2.0+ is −5 %. The
    "excellent" boost stays a flat +2 %. The single source for plan (numbers)
    and feedback (labels) — they can't drift.
    """
    vol_delta = 0.0
    int_delta = 0.0
    labels: list[str] = []
    if adherence_pct < ADVANCE_MIN_ADHERENCE_PCT:
        miss = min(1.0, (ADVANCE_MIN_ADHERENCE_PCT - adherence_pct) / OUTCOME_ADHERENCE_FULL_MISS_PCT)
        d = -round(OUTCOME_VOLUME_STEP_ADHERENCE * miss, 3)
        vol_delta += d
        labels.append(f"Volume {d * 100:+.0f}% (low adherence)")
    if avg_make_rate < ADVANCE_MIN_MAKE_RATE:
        miss = min(1.0, (ADVANCE_MIN_MAKE_RATE - avg_make_rate) / OUTCOME_MAKE_RATE_FULL_MISS)
        d = -round(OUTCOME_INTENSITY_STEP_MAKE_RATE * miss, 1)
        int_delta += d
        labels.append(f"Intensity ceiling {d:+.1f}% (low make rate)")
    if avg_rpe_deviation > ADJUST_RPE_DEVIATION:
        miss = min(1.0, (avg_rpe_deviation - ADJUST_RPE_DEVIATION) / OUTCOME_RPE_FULL_MISS)
        d = -round(OUTCOME_VOLUME_STEP_RPE * miss, 3)
        vol_delta += d
        labels.append(f"Volume {d * 100:+.0f}% (high RPE deviation)")
    if adherence_pct >= EXCELLENT_ADHERENCE_PCT and avg_make_rate >= EXCELLENT_MAKE_RATE:
        int_delta += OUTCOME_INTENSITY_BOOST_EXCELLENT
        labels.append(f"Intensity ceiling +{OUTCOME_INTENSITY_BOOST_EXCELLENT:.0f}% (excellent performance)")
    return round(vol_delta, 3), round(int_delta, 1), labels


def compute_load_adjustments(
    adherence_pct: float,
    avg_make_rate: float,
    avg_rpe_deviation: float,
) -> list[str]:
    """Human-readable nudges for the verdict (see compute_load_deltas)."""
    return compute_load_deltas(adherence_pct, avg_make_rate, avg_rpe_deviation)[2]
