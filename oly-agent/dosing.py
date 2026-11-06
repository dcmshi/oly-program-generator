# oly-agent/dosing.py
"""
Dosing — code sets the numbers, the model picks the exercises.

Runs between the model's reply (names validated, complexes annotated) and
Step 5 validation. The model's sets × reps × % are a proposal; this pass edits
only what breaks the plan, so a session that already fits comes back unchanged:

  1. Competition lifts (complexes by their components) above the week's
     intensity ceiling come down to it.
  2. Working sets (at or above the week's floor) get reps per set inside the
     range — the full classic lifts use `classic_reps_per_set_range` (C&J
     ≤ CLEAN_AND_JERK_MAX_REPS), variants the week's range — and inside
     Prilepin's per-set limit for the load's zone. Complexes keep their scheme.
  3. Sets are scaled so the session's competition-lift reps land within
     DOSING_VOLUME_TOLERANCE of the session target (removed from the rows with
     the most sets first, added to the first working row first, never past
     DOSING_MAX_SETS), then trimmed until no Prilepin zone exceeds its range
     (Prilepin table only).
  4. Squats and pulls above the warm-up cutoff move into the week's
     strength-curve band (load and reps).
  5. Any load change moves the RPE by DOSING_RPE_PER_PCT per % (half-point
     steps, 5–10), so the RPE stays the athlete's guide when there are no maxes.

Warm-up rows (below the week's floor) and accessories are left as the model
wrote them. Every edit is returned as a readable line for generation_log.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from complexes import comp_reps_per_set, counts_as_competition_lift
from validate import strength_category

from shared.constants import (
    CLEAN_AND_JERK_MAX_REPS,
    DOSING_MAX_SETS,
    DOSING_RPE_PER_PCT,
    DOSING_VOLUME_TOLERANCE,
    WARMUP_INTENSITY_CUTOFF_PCT,
    WARMUP_VOLUME_EXCLUSION_PCT,
)
from shared.exercise_mapping import is_classic_lift
from shared.prilepin import get_prilepin_data, get_prilepin_zone


def _pct(ex: dict) -> float | None:
    try:
        v = ex.get("intensity_pct")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _fmt(ex: dict) -> str:
    pct = _pct(ex)
    return f"{ex.get('sets')}×{ex.get('reps')}" + (f" @ {pct:g}%" if pct is not None else "")


def _set_pct(ex: dict, new: float) -> None:
    """Move the load and carry the RPE with it."""
    old = _pct(ex)
    ex["intensity_pct"] = new
    rpe = ex.get("rpe_target")
    if old is not None and rpe is not None:
        try:
            moved = float(rpe) + (new - old) * DOSING_RPE_PER_PCT
            ex["rpe_target"] = min(10.0, max(5.0, round(moved * 2) / 2))
        except (TypeError, ValueError):
            pass


def _prilepin_rep_cap(pct: float) -> int | None:
    zone = get_prilepin_zone(pct)
    data = get_prilepin_data(zone) if zone else None
    return data.get("reps_per_set_high") if data else None


def _counts_for_volume(ex: dict) -> bool:
    """The rows validate's session volume counts: comp lifts above the warm-up band."""
    pct = _pct(ex)
    return counts_as_competition_lift(ex) and pct is not None and pct > WARMUP_VOLUME_EXCLUSION_PCT \
        and get_prilepin_zone(pct) is not None


def dose_session(exercises: list[dict], week_target: dict, session_rep_target: int | None = None
                 ) -> tuple[list[dict], list[str]]:
    """Fit the session's numbers to the week (module docstring). Returns
    (exercises, edits); the exercise dicts are edited in place and returned."""
    edits: list[str] = []
    if not exercises:
        return exercises, edits
    ceiling = week_target.get("intensity_ceiling")
    floor = week_target.get("intensity_floor") or 0
    week_reps = week_target.get("reps_per_set_range") or [1, 6]
    classic_reps = week_target.get("classic_reps_per_set_range") or week_reps

    for ex in exercises:
        name = ex.get("exercise_name") or "exercise"
        before = _fmt(ex)
        reasons: list[str] = []
        pct = _pct(ex)

        if counts_as_competition_lift(ex):
            # 1. the week's ceiling
            if pct is not None and ceiling is not None and pct > ceiling:
                _set_pct(ex, float(ceiling))
                pct = float(ceiling)
                reasons.append(f"week ceiling {ceiling:g}%")
            # 2. reps per set on working sets (complexes keep their scheme)
            if pct is not None and pct >= floor and not ex.get("complex"):
                classic = is_classic_lift(name)
                lo, hi = classic_reps if classic else week_reps
                if classic and name.strip().lower() == "clean & jerk":
                    hi = min(hi, CLEAN_AND_JERK_MAX_REPS)
                cap = _prilepin_rep_cap(pct)
                if cap is not None:
                    hi = min(hi, cap)
                lo = min(lo, hi)
                reps = _int(ex.get("reps"), 1)
                fitted = min(max(reps, lo), hi)
                if fitted != reps:
                    ex["reps"] = fitted
                    reasons.append(f"{'classic-lift' if classic else 'reps'} range {lo}–{hi}")
        else:
            # 4. squats and pulls into the strength band
            cat = strength_category(ex)
            band = ((week_target.get("strength_targets") or {}).get(cat) if cat else None)
            if band and pct is not None and pct > WARMUP_INTENSITY_CUTOFF_PCT:
                fitted_pct = min(max(pct, float(band["floor"])), float(band["ceiling"]))
                if fitted_pct != pct:
                    _set_pct(ex, fitted_pct)
                    reasons.append(f"{cat} band {band['floor']}–{band['ceiling']}%")
                r_lo, r_hi = band.get("reps") or (None, None)
                reps = _int(ex.get("reps"), 1)
                if r_lo is not None and not r_lo <= reps <= r_hi:
                    ex["reps"] = min(max(reps, r_lo), r_hi)
                    reasons.append(f"{cat} reps {r_lo}–{r_hi}")

        if reasons:
            edits.append(f"{name}: {before} → {_fmt(ex)} ({'; '.join(reasons)})")

    edits += _fit_session_volume(exercises, week_target, session_rep_target, floor)
    return exercises, edits


def _fit_session_volume(exercises: list[dict], week_target: dict, target: int | None,
                        floor: float) -> list[str]:
    """3. Sets toward the session target, then under each Prilepin zone's range."""
    rows = [ex for ex in exercises if _counts_for_volume(ex) and comp_reps_per_set(ex) > 0]
    adjustable = [ex for ex in rows if (_pct(ex) or 0) >= floor]
    if not adjustable:
        return []
    before = {id(ex): _int(ex.get("sets"), 1) for ex in adjustable}

    def total() -> int:
        return sum(_int(ex.get("sets"), 1) * comp_reps_per_set(ex) for ex in rows)

    if target:
        hi = math.floor(target * (1 + DOSING_VOLUME_TOLERANCE))
        lo = math.ceil(target * (1 - DOSING_VOLUME_TOLERANCE))
        while total() > hi:
            cands = [ex for ex in adjustable if _int(ex.get("sets"), 1) > 1]
            if not cands:
                break
            # most sets first; among equals the latest row, so the primary lift keeps its work
            pick = max(cands, key=lambda ex: (_int(ex.get("sets"), 1), exercises.index(ex)))
            pick["sets"] = _int(pick.get("sets"), 1) - 1
        while total() < lo:
            grew = False
            for ex in adjustable:          # session order: the first working row is the primary lift
                if _int(ex.get("sets"), 1) >= DOSING_MAX_SETS:
                    continue
                if total() + comp_reps_per_set(ex) > hi:
                    continue
                ex["sets"] = _int(ex.get("sets"), 1) + 1
                grew = True
                break
            if not grew:
                break

    if (week_target.get("volume_table") or "prilepin") == "prilepin":
        zones: dict[str, list[dict]] = {}
        for ex in rows:
            zones.setdefault(get_prilepin_zone(_pct(ex)), []).append(ex)
        for zone, zrows in zones.items():
            data = get_prilepin_data(zone) or {}
            cap = data.get("total_reps_range_high")
            if not cap:
                continue
            while sum(_int(ex.get("sets"), 1) * comp_reps_per_set(ex) for ex in zrows) > cap:
                cands = [ex for ex in zrows if ex in adjustable and _int(ex.get("sets"), 1) > 1]
                if not cands:
                    break
                pick = max(cands, key=lambda ex: (_int(ex.get("sets"), 1), exercises.index(ex)))
                pick["sets"] = _int(pick.get("sets"), 1) - 1

    out = []
    for ex in adjustable:
        old, new = before[id(ex)], _int(ex.get("sets"), 1)
        if old != new:
            out.append(f"{ex.get('exercise_name')}: {old} → {new} sets (session target {target} comp reps)")
    return out
