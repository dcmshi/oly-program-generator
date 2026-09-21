# oly-agent/phase_profiles.py
"""
Week-to-week progression curves for each training phase.
These define the shape of the program: how intensity climbs,
how volume tapers, and where the deload sits.

All values are guidelines for competition lifts (snatch, C&J).
Strength work (squats, pulls) follows similar curves with different
absolute percentages resolved via intensity_reference.
"""

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


def build_weekly_targets(phase: str, duration_weeks: int, athlete_level: str,
                         deload_every_weeks: int | None = None, deload_style: str = "volume") -> list[dict]:
    """Build WeekTarget dicts from a phase profile.

    Adjustments by athlete level:
    - Beginners:     intensity ceiling -4%, volume +10%
    - Advanced:      intensity ceiling +2%, volume -5%
    - Elite:         intensity ceiling +3%, volume -10%

    If duration_weeks differs from the profile default:
    - Longer: repeat middle working weeks, push deload to end
    - Shorter: drop early ramp-up weeks, keep peak + deload

    Preferences (PLAN-2 §1.4 / §3.5): `deload_every_weeks` turns every Nth
    working week of a long block into an extra deload (its intensity band and
    volume copied from the profile's deload week); `deload_style="none"` drops
    the deload flag so the last week is a normal working week.
    """
    profile = PHASE_PROFILES[phase]
    base_weeks = list(profile["weeks"].items())
    deload_week = profile.get("deload_week")

    if duration_weeks > len(base_weeks):
        working_weeks = [(k, v) for k, v in base_weeks if k != deload_week]
        deload_data = [(k, v) for k, v in base_weeks if k == deload_week]

        extra_needed = duration_weeks - len(base_weeks)
        mid = len(working_weeks) // 2
        repeat_pool = working_weeks[mid:]
        extended = list(working_weeks)
        for i in range(extra_needed):
            extended.append(repeat_pool[i % len(repeat_pool)])
        if deload_data:
            extended.append(deload_data[0])
            deload_week = duration_weeks   # the appended deload is now the last week
        else:
            deload_week = None             # profile has no deload — don't fabricate one (A-L7)

        base_weeks = [(i + 1, data) for i, (_, data) in enumerate(extended)]

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
        is_deload = week_num == deload_week
        if (deload_every_weeks and profile_deload is not None and not is_deload
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
