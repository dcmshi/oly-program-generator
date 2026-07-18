# oly-agent/validate.py
"""
Step 5: VALIDATE — Check the generated session against programming constraints.

Checks:
  1. Prilepin's volume compliance (weekly cumulative reps per zone)
  2. Intensity envelope (no exercise exceeds the week's ceiling)
  3. Reps-per-set compliance per Prilepin's zone
  4. Athlete avoid list (exercise_preferences.avoid)
  5. Programming principle recommendations
  6. Session duration estimate vs athlete's available time
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import ValidationResult

from shared.constants import (
    DEFAULT_SESSION_DURATION_MINUTES,
    PRILEPIN_HARD_CAP_MULTIPLIER,
    SESSION_DURATION_TOLERANCE,
    SUPRAMAX_INTENSITY_WARN_PCT,
    WARMUP_INTENSITY_CUTOFF_PCT,
    WARMUP_VOLUME_EXCLUSION_PCT,
    WEEKLY_REP_BUDGET_TOLERANCE,
)
from shared.exercise_mapping import COMP_LIFT_REFS
from shared.formulas import estimate_session_minutes
from shared.prilepin import get_prilepin_data, get_prilepin_zone

# Keywords used to detect whether a prescribed exercise addresses a strength limiter.
# Checked against the exercise name (lowercase). First keyword is used in warning text.
_LIMITER_KEYWORDS: dict[str, list[str]] = {
    "squat_limited":        ["squat"],
    "pull_limited":         ["pull", "deadlift", "row"],
    "overhead_limited":     ["press", "overhead", "jerk"],
    "jerk_limited":         ["jerk"],
    "clean_limited":        ["clean"],
    "positional_strength":  ["pause", "tempo", "slow"],
}


def _numeric_pct(ex: dict) -> float | None:
    """intensity_pct as float, or None when absent/non-numeric.

    Check 0 files the "not numeric" error; the later checks must not crash on
    the same value before the ValidationResult is returned (audit2-L3).
    """
    try:
        pct = ex.get("intensity_pct")
        return float(pct) if pct is not None else None
    except (TypeError, ValueError):
        return None


def validate_session(
    session_exercises: list[dict],
    week_target: dict,
    active_principles: list[dict],
    athlete: dict,
    week_cumulative_reps: dict | None = None,
    fault_exercise_names: list[str] | None = None,
) -> ValidationResult:
    """Validate a generated session against all programming constraints.

    Args:
        session_exercises: LLM-generated exercise list for this session
        week_target: WeekTarget dict (intensity_floor, intensity_ceiling, etc.)
        active_principles: programming_principles rows
        athlete: athletes row (for session_duration_minutes, exercise_preferences)
        week_cumulative_reps: {zone_key: total_reps} already prescribed earlier
                               in the same week; None on the first session.
        fault_exercise_names: flat list of exercise names known to address the athlete's
                               technical faults (from retrieval_context.fault_exercises).
                               If None, fault-coverage check is skipped.

    Returns:
        ValidationResult with is_valid, errors, warnings, session_comp_reps.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Guard: empty session is always invalid
    if not session_exercises:
        return ValidationResult(
            is_valid=False,
            errors=["Session has no exercises — LLM returned an empty list"],
            warnings=[],
            session_comp_reps={},
        )

    # Normalize week_target to plain dict if it's a WeekTarget dataclass
    if hasattr(week_target, "__dataclass_fields__"):
        from dataclasses import asdict
        week_target = asdict(week_target)

    intensity_ceiling = week_target.get("intensity_ceiling", 100)
    intensity_floor = week_target.get("intensity_floor", 0)

    # ── Check 0: DB-constraint mirror ─────────────────────────
    # session_exercises enforces NOT NULL sets/reps (CHECK >= 1),
    # NOT NULL exercise_order (UNIQUE per session), and intensity_pct in
    # (0, 120]. Violations must fail HERE so generate's retry loop can fix
    # them — otherwise the IntegrityError fires at save time, after every
    # session was already paid for (AGT-M4, audit2-M1).
    seen_orders: set = set()
    for ex in session_exercises:
        name = ex.get("exercise_name") or "exercise"
        for field in ("sets", "reps"):
            try:
                value = int(ex.get(field) or 0)
            except (TypeError, ValueError):
                value = 0
            if value < 1:
                errors.append(f"{name}: {field} must be an integer >= 1 (got {ex.get(field)!r})")
        pct_raw = ex.get("intensity_pct")
        if pct_raw is not None:
            try:
                pct_val = float(pct_raw)
                if pct_val <= 0 or pct_val > 120:
                    errors.append(
                        f"{name}: intensity_pct {pct_val:g} outside the storable range "
                        f"(0, 120] — use null for unloaded work"
                    )
            except (TypeError, ValueError):
                errors.append(f"{name}: intensity_pct {pct_raw!r} is not numeric")
        # exercise_order is NOT NULL — the parse-time coercion can produce
        # None ("one" → None) and an absent key slipped through (audit2-M1)
        order = ex.get("exercise_order")
        try:
            order_val = int(order) if order is not None and not isinstance(order, bool) else None
        except (TypeError, ValueError):
            order_val = None
        if order_val is None or order_val < 1:
            errors.append(f"{name}: exercise_order must be an integer >= 1 (got {order!r})")
        elif order_val in seen_orders:
            errors.append(
                f"duplicate exercise_order {order_val} — orders must be unique within the session"
            )
        else:
            seen_orders.add(order_val)

    # ── Check 1: Prilepin's per-session volume compliance ─────
    # Prilepin's chart gives rep targets PER SESSION, not per week.
    # 70-80% zone: 12-24 reps per session (optimal 18).
    # The weekly total is managed via session_volume_share and volume_modifier
    # in the plan step; here we only enforce the per-session ceiling.
    comp_lift_reps: dict[str, int] = {}

    for ex in session_exercises:
        if ex.get("intensity_reference") not in COMP_LIFT_REFS:
            continue
        pct = _numeric_pct(ex)
        if not pct:
            continue
        # Warmup ramping isn't training volume: the prompt MANDATES 2-3 warmup
        # sets at 50-60% before each comp lift, and counting the 55-60 ones
        # against Prilepin/weekly working budgets made well-formed sessions
        # warn routinely (audit2-L2). Only the mandated band is excluded —
        # 61-65% working sets still count toward the 55-65 zone (audit3-M1).
        if pct <= WARMUP_VOLUME_EXCLUSION_PCT:
            continue
        zone = get_prilepin_zone(pct)
        if zone is None:
            continue
        try:
            total = int(ex.get("sets") or 0) * int(ex.get("reps") or 0)
        except (TypeError, ValueError):
            continue  # Check 0 already errored on the malformed field
        comp_lift_reps[zone] = comp_lift_reps.get(zone, 0) + total

    for zone, session_total in comp_lift_reps.items():
        zone_data = get_prilepin_data(zone)
        if not zone_data:
            continue

        # Hard cap at 1.5× range_high to account for snatch variations that
        # all reference the same max (pause snatch, hang snatch, etc. each contribute
        # to the zone total). Prilepin's original chart counts the main competition
        # lift only; variations shift the effective ceiling upward.
        hard_cap = round(zone_data["total_reps_range_high"] * PRILEPIN_HARD_CAP_MULTIPLIER)
        if session_total > hard_cap:
            errors.append(
                f"Prilepin session volume excessive: {session_total} reps "
                f"in {zone}% zone (hard cap {hard_cap})"
            )
        elif session_total > zone_data["total_reps_range_high"]:
            warnings.append(
                f"Prilepin: {session_total} reps in {zone}% zone exceeds "
                f"Prilepin range max {zone_data['total_reps_range_high']} "
                f"(optimal {zone_data['optimal_total_reps']})"
            )

    # ── Check 1b: Weekly cumulative rep budget ────────────────
    # The module header promised a weekly check but week_cumulative_reps was
    # never read (AGT-L3). Warn when the week's running comp-lift total blows
    # past the plan's weekly budget by more than the tolerance — the LLM was
    # told the remaining budget, so overshoot is a quality signal, not an error.
    weekly_budget = week_target.get("total_competition_lift_reps") or 0
    if weekly_budget:
        prior_reps = sum((week_cumulative_reps or {}).values())
        week_total = prior_reps + sum(comp_lift_reps.values())
        if week_total > weekly_budget * WEEKLY_REP_BUDGET_TOLERANCE:
            warnings.append(
                f"Weekly comp-lift volume {week_total} reps exceeds the week's "
                f"budget of {weekly_budget} by more than "
                f"{WEEKLY_REP_BUDGET_TOLERANCE - 1:.0%}"
            )

    # ── Check 2: Intensity envelope ───────────────────────────
    for ex in session_exercises:
        pct = _numeric_pct(ex)
        if pct is None:
            continue
        # The week ceiling is a competition-lift ceiling. Pulls/squats reference
        # their own max and are routinely programmed above it (supramaximal
        # pulls), so only comp lifts hard-error here; non-comp lifts merely warn
        # if implausibly high (A-L4).
        if pct > intensity_ceiling:
            if ex.get("intensity_reference") in COMP_LIFT_REFS:
                errors.append(
                    f"{ex.get('exercise_name')} at {pct}% exceeds week ceiling {intensity_ceiling}%"
                )
            elif pct > SUPRAMAX_INTENSITY_WARN_PCT:
                warnings.append(
                    f"{ex.get('exercise_name')} at {pct}% is unusually high for a "
                    f"non-competition lift (supramaximal work expected up to "
                    f"~{SUPRAMAX_INTENSITY_WARN_PCT:.0f}%)"
                )
        # Below floor is only a warning for competition lifts.
        # Skip warm-up sets (≤ cutoff) — they are intentionally sub-floor.
        if (pct < intensity_floor and pct > WARMUP_INTENSITY_CUTOFF_PCT
                and ex.get("intensity_reference") in COMP_LIFT_REFS):
            warnings.append(
                f"{ex.get('exercise_name')} at {pct}% is below week floor {intensity_floor}% "
                f"for competition lifts"
            )

    # ── Check 3: Reps-per-set compliance ─────────────────────
    for ex in session_exercises:
        pct = _numeric_pct(ex) or 0
        reps = ex.get("reps") or 0
        if pct >= 90 and reps > 2:
            errors.append(
                f"{ex.get('exercise_name')}: {reps} reps at {pct}% — "
                f"Prilepin allows max 2 reps/set above 90%"
            )
        elif pct >= 80 and reps > 4:
            warnings.append(
                f"{ex.get('exercise_name')}: {reps} reps at {pct}% — "
                f"Prilepin suggests max 4 reps/set in 80-90% zone"
            )

    # ── Check 4: Athlete avoid list ───────────────────────────
    avoid_list = [
        name.lower().replace(" ", "_")
        for name in (athlete.get("exercise_preferences") or {}).get("avoid", [])
    ]
    for ex in session_exercises:
        name_norm = (ex.get("exercise_name") or "").lower().replace(" ", "_")
        if name_norm in avoid_list:
            errors.append(f"{ex.get('exercise_name')} is in athlete's avoid list")

    # ── Check 5: Principle compliance ─────────────────────────
    for principle in active_principles:
        rec = principle.get("recommendation") or {}
        if isinstance(rec, str):
            continue  # skip text-only recommendations

        max_ex = rec.get("max_exercises_per_session")
        if max_ex and len(session_exercises) > max_ex:
            warnings.append(
                f"Session has {len(session_exercises)} exercises; "
                f"principle '{principle['principle_name']}' recommends max {max_ex}"
            )

        if rec.get("competition_lifts_first") and session_exercises:
            first = session_exercises[0]
            if first.get("intensity_reference") not in COMP_LIFT_REFS:
                warnings.append(
                    f"First exercise is '{first.get('exercise_name')}', "
                    f"but principle requires competition lifts first"
                )

    # ── Check 6: Estimated session duration ───────────────────
    available_minutes = athlete.get("session_duration_minutes") or DEFAULT_SESSION_DURATION_MINUTES
    estimated_minutes = estimate_session_minutes(session_exercises)
    if estimated_minutes > available_minutes * SESSION_DURATION_TOLERANCE:
        warnings.append(
            f"Estimated duration {estimated_minutes:.0f} min exceeds "
            f"available {available_minutes} min"
        )

    # ── Check 7: RPE target vs intensity appropriateness ──────
    # High-intensity sets should carry a high RPE target — if the LLM assigns
    # a low RPE to a heavy set it likely miscalibrated the prescription.
    for ex in session_exercises:
        pct = _numeric_pct(ex) or 0
        rpe = ex.get("rpe_target")
        if rpe is None or pct == 0:
            continue
        try:
            rpe = float(rpe)
        except (TypeError, ValueError):
            continue
        if pct >= 90 and rpe < 8.0:
            warnings.append(
                f"{ex.get('exercise_name')} at {pct}% has RPE target {rpe:.1f} — "
                f"intensity ≥90% typically warrants RPE 8.0+"
            )
        elif pct >= 80 and rpe < 7.0:
            warnings.append(
                f"{ex.get('exercise_name')} at {pct}% has RPE target {rpe:.1f} — "
                f"intensity 80–90% typically warrants RPE 7.0+"
            )

    # ── Check 8: Fault-correction exercise coverage ───────────
    # If the athlete has identified technical faults and fault-correction exercises
    # were retrieved, warn when none of those exercises appear in this session.
    # Skipped when fault_exercise_names is None (not provided by caller).
    technical_faults = athlete.get("technical_faults") or []
    if technical_faults and fault_exercise_names is not None:
        prescribed_lower = {(ex.get("exercise_name") or "").lower() for ex in session_exercises}
        fault_lower = {name.lower() for name in fault_exercise_names}
        if not prescribed_lower & fault_lower:
            warnings.append(
                f"Athlete has technical faults ({', '.join(technical_faults)}) "
                f"but no fault-correction exercises were selected this session"
            )

    # ── Check 9: Strength limiter coverage ────────────────────
    # Warn when a declared strength limiter has no matching exercise this session.
    # Uses keyword matching against exercise names — not exhaustive but catches
    # the common case of squat/pull/overhead limiters with no relevant work.
    strength_limiters = athlete.get("strength_limiters") or []
    if strength_limiters:
        prescribed_names_lower = [(ex.get("exercise_name") or "").lower() for ex in session_exercises]
        for limiter in strength_limiters:
            keywords = _LIMITER_KEYWORDS.get(limiter, [])
            if not keywords:
                continue
            if not any(kw in name for kw in keywords for name in prescribed_names_lower):
                warnings.append(
                    f"Strength limiter '{limiter}' not addressed — "
                    f"consider adding {keywords[0]}-focused work"
                )

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        session_comp_reps=comp_lift_reps,
    )
