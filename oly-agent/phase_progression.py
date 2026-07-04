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


def compute_load_adjustments(
    adherence_pct: float,
    avg_make_rate: float,
    avg_rpe_deviation: float,
) -> list[str]:
    """Next-program load nudges implied by the previous outcome.

    Returns human-readable strings; feedback surfaces them in the verdict and
    plan._apply_outcome_adjustments applies the matching numeric deltas.
    """
    adjustments: list[str] = []
    if adherence_pct < ADVANCE_MIN_ADHERENCE_PCT:
        adjustments.append("Volume −10% (low adherence)")
    if avg_make_rate < ADVANCE_MIN_MAKE_RATE:
        adjustments.append("Intensity ceiling −3% (low make rate)")
    if avg_rpe_deviation > ADJUST_RPE_DEVIATION:
        adjustments.append("Volume −5% (high RPE deviation)")
    if adherence_pct >= EXCELLENT_ADHERENCE_PCT and avg_make_rate >= EXCELLENT_MAKE_RATE:
        adjustments.append("Intensity ceiling +2% (excellent performance)")
    return adjustments
