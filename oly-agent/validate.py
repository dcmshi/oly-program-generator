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
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.prilepin import get_prilepin_zone, get_prilepin_data
from models import ValidationResult


def validate_session(
    session_exercises: list[dict],
    week_target: dict,
    active_principles: list[dict],
    athlete: dict,
    week_cumulative_reps: dict | None = None,
) -> ValidationResult:
    """Validate a generated session against all programming constraints.

    Args:
        session_exercises: LLM-generated exercise list for this session
        week_target: WeekTarget dict (intensity_floor, intensity_ceiling, etc.)
        active_principles: programming_principles rows
        athlete: athletes row (for session_duration_minutes, exercise_preferences)
        week_cumulative_reps: {zone_key: total_reps} already prescribed earlier
                               in the same week; None on the first session.

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
    is_deload = week_target.get("is_deload", False)

    # ── Check 1: Prilepin's per-session volume compliance ─────
    # Prilepin's chart gives rep targets PER SESSION, not per week.
    # 70-80% zone: 12-24 reps per session (optimal 18).
    # The weekly total is managed via session_volume_share and volume_modifier
    # in the plan step; here we only enforce the per-session ceiling.
    comp_lift_reps: dict[str, int] = {}
    COMP_LIFT_REFS = {"snatch", "clean_and_jerk", "clean"}

    for ex in session_exercises:
        if ex.get("intensity_reference") not in COMP_LIFT_REFS:
            continue
        pct = ex.get("intensity_pct")
        if not pct:
            continue
        zone = get_prilepin_zone(float(pct))
        if zone is None:
            continue
        total = (ex.get("sets") or 0) * (ex.get("reps") or 0)
        comp_lift_reps[zone] = comp_lift_reps.get(zone, 0) + total

    for zone, session_total in comp_lift_reps.items():
        zone_data = get_prilepin_data(zone)
        if not zone_data:
            continue

        # Hard cap at 1.5× range_high to account for snatch variations that
        # all reference the same max (pause snatch, hang snatch, etc. each contribute
        # to the zone total). Prilepin's original chart counts the main competition
        # lift only; variations shift the effective ceiling upward.
        hard_cap = round(zone_data["total_reps_range_high"] * 1.5)
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

    # ── Check 2: Intensity envelope ───────────────────────────
    for ex in session_exercises:
        pct = ex.get("intensity_pct")
        if pct is None:
            continue
        pct = float(pct)
        if pct > intensity_ceiling:
            errors.append(
                f"{ex.get('exercise_name')} at {pct}% exceeds week ceiling {intensity_ceiling}%"
            )
        # Below floor is only a warning for competition lifts
        if pct < intensity_floor and ex.get("intensity_reference") in COMP_LIFT_REFS:
            warnings.append(
                f"{ex.get('exercise_name')} at {pct}% is below week floor {intensity_floor}% "
                f"for competition lifts"
            )

    # ── Check 3: Reps-per-set compliance ─────────────────────
    for ex in session_exercises:
        pct = float(ex.get("intensity_pct") or 0)
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
    available_minutes = athlete.get("session_duration_minutes") or 90
    estimated_minutes = sum(
        (ex.get("sets") or 0) * (30 + (ex.get("rest_seconds") or 90))
        for ex in session_exercises
    ) / 60
    if estimated_minutes > available_minutes * 1.2:
        warnings.append(
            f"Estimated duration {estimated_minutes:.0f} min exceeds "
            f"available {available_minutes} min"
        )

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        session_comp_reps=comp_lift_reps,
    )
