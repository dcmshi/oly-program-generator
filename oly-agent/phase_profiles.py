# oly-agent/phase_profiles.py
"""
Week-to-week progression curves for each training phase.
These define the shape of the program: how intensity climbs,
how volume tapers, and where the deload sits.

All values are guidelines for competition lifts (snatch, C&J).
Strength work (squats, pulls) follows similar curves with different
absolute percentages resolved via intensity_reference.
"""

import math

from shared.constants import BLOCK_WAVE_STEP_PCT, CLASSIC_LIFT_REPS_PER_SET, VARIANT_LIFT_REPS_PER_SET

PHASE_PROFILES = {
    "accumulation": {
        "description": "Build work capacity with moderate loads. Volume is primary driver.",
        "default_weeks": 4,
        "deload_week": 4,
        "includes_max_test": False,
        "weeks": {
            1: {"intensity_floor": 68, "intensity_ceiling": 76, "volume_modifier": 0.85, "reps_per_set_range": [3, 5]},
            2: {"intensity_floor": 70, "intensity_ceiling": 78, "volume_modifier": 1.00, "reps_per_set_range": [3, 5]},
            3: {"intensity_floor": 72, "intensity_ceiling": 80, "volume_modifier": 1.00, "reps_per_set_range": [2, 5]},
            4: {"intensity_floor": 65, "intensity_ceiling": 73, "volume_modifier": 0.60, "reps_per_set_range": [2, 4]},
        },
        "intensity_progression": "linear",
        "volume_trend": "stable_then_deload",
    },

    "intensification": {
        "description": "Shift emphasis from volume to intensity. Fewer reps, heavier loads.",
        "default_weeks": 4,
        "deload_week": None,
        "includes_max_test": True,
        "weeks": {
            1: {"intensity_floor": 75, "intensity_ceiling": 83, "volume_modifier": 1.00, "reps_per_set_range": [2, 4]},
            2: {"intensity_floor": 78, "intensity_ceiling": 86, "volume_modifier": 0.90, "reps_per_set_range": [2, 3]},
            3: {"intensity_floor": 80, "intensity_ceiling": 90, "volume_modifier": 0.80, "reps_per_set_range": [1, 3]},
            4: {"intensity_floor": 83, "intensity_ceiling": 93, "volume_modifier": 0.70, "reps_per_set_range": [1, 2]},
        },
        "intensity_progression": "linear",
        "volume_trend": "descending",
    },

    "realization": {
        "description": "Peak for competition or PR attempts. Heavy singles and doubles.",
        "default_weeks": 3,
        "deload_week": 3,
        "includes_max_test": True,
        "weeks": {
            1: {"intensity_floor": 85, "intensity_ceiling": 95, "volume_modifier": 0.65, "reps_per_set_range": [1, 2]},
            2: {"intensity_floor": 88, "intensity_ceiling": 98, "volume_modifier": 0.50, "reps_per_set_range": [1, 2]},
            3: {"intensity_floor": 70, "intensity_ceiling": 85, "volume_modifier": 0.35, "reps_per_set_range": [1, 2]},
        },
        "intensity_progression": "peak_then_taper",
        "volume_trend": "sharply_descending",
    },

    "general_prep": {
        "description": "General preparation. Balanced volume and intensity, broad exercise selection.",
        "default_weeks": 5,
        "deload_week": 5,
        "includes_max_test": False,
        "weeks": {
            1: {"intensity_floor": 65, "intensity_ceiling": 73, "volume_modifier": 0.80, "reps_per_set_range": [3, 6]},
            2: {"intensity_floor": 67, "intensity_ceiling": 75, "volume_modifier": 0.90, "reps_per_set_range": [3, 6]},
            3: {"intensity_floor": 68, "intensity_ceiling": 77, "volume_modifier": 1.00, "reps_per_set_range": [3, 5]},
            4: {"intensity_floor": 70, "intensity_ceiling": 78, "volume_modifier": 1.00, "reps_per_set_range": [3, 5]},
            5: {"intensity_floor": 63, "intensity_ceiling": 70, "volume_modifier": 0.55, "reps_per_set_range": [2, 4]},
        },
        "intensity_progression": "gradual_linear",
        "volume_trend": "ascending_then_deload",
    },
}

_LEVEL_ADJUSTMENTS = {
    "beginner":     {"intensity_offset": -4, "volume_scale": 1.10},
    "intermediate": {"intensity_offset":  0, "volume_scale": 1.00},
    "advanced":     {"intensity_offset": +2, "volume_scale": 0.95},
    "elite":        {"intensity_offset": +3, "volume_scale": 0.90},
}

# Level × phase overrides on top of the flat offsets (PLAN-2 §1.5–1.6). The
# offsets alone gave a beginner 89 % singles in realization; the Soviet
# textbooks now in the corpus disagree, by class:
#   Medvedev (A Program of Multi-Year Training): beginners train the classic
#     and special-preparatory lifts at 50–70 %, ceiling ≤ 80, "avoid heavy
#     singles/doubles"; Class III (≈ intermediate) fundamental intensity 80 %.
#   Vorobyev (Textbook): 1–6 reps per set for everyone past novice, 6 reps for
#     strength/hypertrophy in beginners–intermediates; advanced/elite singles at
#     85–95 %, speed work at 70–75 %, floor 70 %; ≤ 6 sessions/week.
#   Laputin & Oleshko: Prilepin floors at 70 / 80 / 90 % for intermediates+.
# `ceiling_cap` clamps the week's ceiling, `floor_min` its floor, `reps` replaces
# the reps-per-set range, `volume_scale` multiplies the flat one. Anything not
# listed falls through to _LEVEL_ADJUSTMENTS.
LEVEL_PHASE_OVERRIDES: dict[str, dict[str, dict]] = {
    "beginner": {
        "general_prep":    {"ceiling_cap": 72, "reps": [4, 6], "volume_scale": 1.05},   # Medvedev 50–70 % fundamental band
        "accumulation":    {"ceiling_cap": 76, "reps": [3, 6]},
        "intensification": {"ceiling_cap": 80, "reps": [2, 4]},                          # no heavy singles/doubles
        "realization":     {"ceiling_cap": 85, "reps": [1, 3]},                          # a beginner "peak" is a heavy triple
    },
    "intermediate": {
        "accumulation":    {"floor_min": 70},                                             # Class III fundamental ≈ 80 %, Prilepin floor 70
    },
    "advanced": {
        "accumulation":    {"floor_min": 70},                                             # Vorobyev: strength work never below 70 %
        "intensification": {"ceiling_cap": 95},
        "realization":     {"ceiling_cap": 100},
    },
    "elite": {
        "accumulation":    {"floor_min": 72, "volume_scale": 1.05},                       # elite tolerate more at 70–80 %
        "intensification": {"ceiling_cap": 96},
        "realization":     {"ceiling_cap": 100},
    },
}


def _lift_reps_range(table: dict[str, tuple[int, int]], phase: str, week_range: list[int],
                     athlete_level: str, olympic_lifting_experience: bool) -> list[int] | None:
    """One week's reps per set from a per-phase table, never above the week's
    own range; None for a beginner still learning the lifts, who keeps the
    week's range."""
    if athlete_level == "beginner" and not olympic_lifting_experience:
        return None
    lo, hi = table.get(phase, (1, 3))
    hi = min(hi, week_range[1])
    return [min(lo, hi), hi]


def classic_reps_range(phase: str, week_range: list[int], athlete_level: str,
                       olympic_lifting_experience: bool) -> list[int] | None:
    """Reps per set on the full classic lifts (CLASSIC_LIFT_REPS_PER_SET)."""
    return _lift_reps_range(CLASSIC_LIFT_REPS_PER_SET, phase, week_range, athlete_level,
                            olympic_lifting_experience)


def variant_reps_range(phase: str, week_range: list[int], athlete_level: str,
                       olympic_lifting_experience: bool) -> list[int] | None:
    """Reps per set on the competition-lift variants (VARIANT_LIFT_REPS_PER_SET, DOSE-1a)."""
    return _lift_reps_range(VARIANT_LIFT_REPS_PER_SET, phase, week_range, athlete_level,
                            olympic_lifting_experience)


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _stretch_working_weeks(working: list[dict], n: int, shift: int = 0) -> list[dict]:
    """The profile's working weeks interpolated onto `n` weeks.

    Piecewise-linear through the profile points, first working week → last, so
    a monotonic profile stays a monotonic ramp; with `n` below the profile's
    count the ramp is sampled instead. Intensities are rounded to whole % and
    raised by `shift`; reps come from the nearest profile week. n == 1 is the
    ramp's first week.
    """
    last = len(working) - 1
    out = []
    for i in range(n):
        x = i * last / (n - 1) if n > 1 and last > 0 else 0.0
        lo = min(int(math.floor(x)), last)
        hi = min(lo + 1, last)
        frac = x - lo

        def lerp(key: str, lo: int = lo, hi: int = hi, frac: float = frac) -> float:
            return working[lo][key] + frac * (working[hi][key] - working[lo][key])

        out.append({
            "intensity_floor": _round_half_up(lerp("intensity_floor")) + shift,
            "intensity_ceiling": _round_half_up(lerp("intensity_ceiling")) + shift,
            "volume_modifier": round(lerp("volume_modifier"), 2),
            "reps_per_set_range": list(working[min(_round_half_up(x), last)]["reps_per_set_range"]),
        })
    return out


def _wave_deload_weeks(duration_weeks: int, deload_every_weeks: int | None) -> list[int]:
    """Deload week numbers of a long block: every Nth week, plus the last.

    A trailing partial wave with no loading week (duration ≡ 1 mod N) would put
    two deloads back to back, so the last mid-block deload is dropped and the
    final wave runs N loading weeks instead.
    """
    if not deload_every_weeks or deload_every_weeks < 2:
        return [duration_weeks]
    mids = list(range(deload_every_weeks, duration_weeks, deload_every_weeks))
    if mids and duration_weeks - mids[-1] < 2:
        mids.pop()
    return [*mids, duration_weeks]


def wave_anchor_weeks(weekly_targets: list) -> dict[int, int]:
    """{week_number: the week that anchors it} (DOG-2e). Used by the
    orchestrator to schedule waves and by `program_audit` to judge drift per wave.

    A block is one wave anchored on its first week, unless it has a deload
    before its last week: the first non-deload week after a deload then
    starts a new wave and anchors the weeks up to the next one. A deload
    week belongs to the wave it closes. 8 weeks, deloads 4 and 8 →
    weeks 1–4 on 1, 5–8 on 5; no mid-block deload → every week on the first.
    """
    anchors: dict[int, int] = {}
    anchor, after_deload = None, False
    for wt in weekly_targets:
        if anchor is None or (after_deload and not wt.is_deload):
            anchor, after_deload = wt.week_number, False
        anchors[wt.week_number] = anchor
        after_deload = after_deload or bool(wt.is_deload)
    return anchors


def build_weekly_targets(phase: str, duration_weeks: int, athlete_level: str,
                         deload_every_weeks: int | None = None, deload_style: str = "volume") -> list[dict]:
    """Build WeekTarget dicts from a phase profile.

    Adjustments by athlete level:
    - Beginners:     intensity ceiling -4%, volume +10%
    - Advanced:      intensity ceiling +2%, volume -5%
    - Elite:         intensity ceiling +3%, volume -10%
    then LEVEL_PHASE_OVERRIDES (`ceiling_cap`, `floor_min`, reps, volume) and
    the floor ≤ ceiling − 4 band rule, on every week whatever its length.

    If duration_weeks differs from the profile default:
    - Shorter: drop early ramp-up weeks, keep peak + deload
    - Longer (DOG-2d — repeating the second half plateaued after week 3):
      * no `deload_every_weeks`: the profile's working weeks are stretched by
        linear interpolation over duration − 1 weeks (a monotonic ramp from
        profile week 1 to the last working week), the profile's deload last.
      * `deload_every_weeks = N`: waves of N−1 loading weeks + 1 deload. Each
        wave's loading weeks are the working weeks interpolated onto its
        length, and wave k (0-based) sits k × BLOCK_WAVE_STEP_PCT higher on
        floor and ceiling; deloads copy the profile's deload week. The last
        week is always a deload; a trailing partial wave is interpolated the
        same way (one with no loading week folds into the previous wave).
      * a profile with no deload week (intensification) is stretched over the
        whole block and never gets one (A-L7), N or not.

    Preferences (PLAN-2 §1.4 / §3.5): `deload_every_weeks` adds a deload every
    Nth week (copied from the profile's deload week; at or under the profile
    length the Nth working week is replaced, never the last week twice);
    `deload_style="none"` drops the deload flag from the last week — its loads
    stay the profile's deload week — while mid-block deloads keep theirs.
    """
    profile = PHASE_PROFILES[phase]
    base_weeks = list(profile["weeks"].items())
    deload_week = profile.get("deload_week")
    wave_deloads: set[int] | None = None   # set only on the long path, which places its own deloads

    if duration_weeks > len(base_weeks):
        working = [v for k, v in base_weeks if k != deload_week]
        deload_data = profile["weeks"].get(deload_week) if deload_week else None
        if deload_data is None:
            # Profile has no deload — stretch it over the block, don't fabricate one (A-L7)
            extended = _stretch_working_weeks(working, duration_weeks)
            wave_deloads = set()
        else:
            ends = _wave_deload_weeks(duration_weeks, deload_every_weeks)
            extended, start = [], 1
            for k, end in enumerate(ends):
                extended += _stretch_working_weeks(working, end - start, shift=k * BLOCK_WAVE_STEP_PCT)
                extended.append(deload_data)
                start = end + 1
            wave_deloads = set(ends)
            if deload_style == "none":
                wave_deloads.discard(duration_weeks)
        deload_week = duration_weeks if duration_weeks in wave_deloads else None
        base_weeks = [(i + 1, data) for i, data in enumerate(extended)]

    elif duration_weeks < len(base_weeks):
        trim = len(base_weeks) - duration_weeks
        trimmed = base_weeks[trim:]
        base_weeks = [(i + 1, data) for i, (_, data) in enumerate(trimmed)]
        if deload_week:
            deload_week = duration_weeks

    if deload_style == "none":
        deload_week = None
    level = athlete_level if athlete_level in _LEVEL_ADJUSTMENTS else "intermediate"
    adj = _LEVEL_ADJUSTMENTS[level]
    over = LEVEL_PHASE_OVERRIDES.get(level, {}).get(phase, {})

    profile_deload = profile["weeks"].get(profile.get("deload_week")) if profile.get("deload_week") else None
    targets = []
    for week_num, week_data in base_weeks:
        if week_num > duration_weeks:
            break
        if wave_deloads is not None:
            is_deload = week_num in wave_deloads
        else:
            is_deload = week_num == deload_week
        if (wave_deloads is None and deload_every_weeks and profile_deload is not None and not is_deload
                and week_num % deload_every_weeks == 0 and week_num < duration_weeks):
            week_data, is_deload = profile_deload, True     # extra mid-block deload
        ceiling = min(week_data["intensity_ceiling"] + adj["intensity_offset"], 100)
        floor = week_data["intensity_floor"] + adj["intensity_offset"]
        if "ceiling_cap" in over:
            ceiling = min(ceiling, over["ceiling_cap"])
        if "floor_min" in over and not is_deload:
            floor = max(floor, over["floor_min"])
        floor = min(floor, ceiling - 4)               # keep a usable band (AGT-M1 mirrors this for cold starts)
        targets.append({
            "week_number": week_num,
            "intensity_floor": floor,
            "intensity_ceiling": ceiling,
            "volume_modifier": week_data["volume_modifier"] * adj["volume_scale"] * over.get("volume_scale", 1.0),
            "reps_per_set_range": list(over.get("reps", week_data["reps_per_set_range"])),
            "is_deload": is_deload,
        })

    return targets


# ── Strength-work curve (PLAN-3c, assumption 2.4) ────────────────────────────
# Squats and pulls had no progression of their own — the model chose freely.
# Each phase now carries a band for each, from the corpus's principles (ids in
# `sources`, programming_principles.id):
#   squat  = % of the athlete's own squat max (back / front squat)
#   pull   = % of the competition lift the pull belongs to (snatch / clean)
# The prompt shows the band; validate.py warns (never errors) outside it.
STRENGTH_CURVE: dict[str, dict[str, dict]] = {
    "general_prep": {
        "squat": {"floor": 65, "ceiling": 75, "reps": (4, 6), "sources": (2452, 2131)},    # Medvedev; moderate-rep squatting
        "pull":  {"floor": 85, "ceiling": 100, "reps": (3, 4), "sources": (2950, 4585)},  # Suchomel accumulation; Roman
    },
    "accumulation": {
        "squat": {"floor": 70, "ceiling": 80, "reps": (3, 5), "sources": (2304, 2308)},   # Charniga, leg strength stage 1
        "pull":  {"floor": 90, "ceiling": 110, "reps": (2, 4), "sources": (2950, 4585)},  # Suchomel 60–110; Roman ≥100
    },
    "intensification": {
        "squat": {"floor": 85, "ceiling": 95, "reps": (2, 3), "sources": (2309,)},        # Charniga, stage 2 85–105
        "pull":  {"floor": 95, "ceiling": 110, "reps": (2, 3), "sources": (2955, 3551)},  # Suchomel supramaximal; Vorobyev ±10
    },
    "realization": {
        "squat": {"floor": 75, "ceiling": 88, "reps": (1, 3), "sources": (2807, 2808, 2825, 2766)},  # taper studies
        "pull":  {"floor": 90, "ceiling": 100, "reps": (1, 2), "sources": (1760, 1822)},  # Everett taper doubles
    },
    "deload": {
        "squat": {"floor": 70, "ceiling": 80, "reps": (2, 3), "sources": (1915,)},        # back-off week
        "pull":  {"floor": 90, "ceiling": 100, "reps": (2, 3), "sources": (1916,)},       # moderate pulls in deload
    },
}


def strength_targets(phase: str, is_deload: bool) -> dict[str, dict] | None:
    """The squat / pull bands for a week (the deload row on a deload week)."""
    return STRENGTH_CURVE.get("deload" if is_deload else phase)
