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


def build_weekly_targets(phase: str, duration_weeks: int, athlete_level: str) -> list[dict]:
    """Build WeekTarget dicts from a phase profile.

    Adjustments by athlete level:
    - Beginners:     intensity ceiling -4%, volume +10%
    - Advanced:      intensity ceiling +2%, volume -5%
    - Elite:         intensity ceiling +3%, volume -10%

    If duration_weeks differs from the profile default:
    - Longer: repeat middle working weeks, push deload to end
    - Shorter: drop early ramp-up weeks, keep peak + deload
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

        base_weeks = [(i + 1, data) for i, (_, data) in enumerate(extended)]
        deload_week = duration_weeks

    elif duration_weeks < len(base_weeks):
        trim = len(base_weeks) - duration_weeks
        trimmed = base_weeks[trim:]
        base_weeks = [(i + 1, data) for i, (_, data) in enumerate(trimmed)]
        if deload_week:
            deload_week = duration_weeks

    adj = _LEVEL_ADJUSTMENTS.get(athlete_level, _LEVEL_ADJUSTMENTS["intermediate"])

    targets = []
    for week_num, week_data in base_weeks:
        if week_num > duration_weeks:
            break
        targets.append({
            "week_number": week_num,
            "intensity_floor": week_data["intensity_floor"] + adj["intensity_offset"],
            "intensity_ceiling": min(
                week_data["intensity_ceiling"] + adj["intensity_offset"], 100
            ),
            "volume_modifier": week_data["volume_modifier"] * adj["volume_scale"],
            "reps_per_set_range": week_data["reps_per_set_range"],
            "is_deload": week_num == deload_week,
        })

    return targets
