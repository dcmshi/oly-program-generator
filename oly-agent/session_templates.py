# oly-agent/session_templates.py
"""
Session templates by training frequency.

Hierarchy:
  Program (e.g., 4-week accumulation)
    Week (e.g., Week 2)
      Session (e.g., Day 1: Snatch + Squat)
        Exercise (e.g., Snatch 5x2 @ 78%)

Each session template specifies:
- Which movement family is primary (the competition lift)
- What supporting work follows (squats, pulls, accessories)
- What share of weekly volume this session carries
"""

SESSION_DISTRIBUTIONS = {
    2: {
        "description": "2-day: both lifts each session, one squat variant each; strength-maintenance frequency",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Clean & Jerk (light) + Back Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["clean", "squat"],
                "session_volume_share": 0.50,
                "notes": "Snatch emphasis, C&J at reduced intensity, back squat.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Snatch (light) + Front Squat",
                "primary_movement": "clean",
                "secondary_movements": ["snatch", "jerk", "squat"],
                "session_volume_share": 0.50,
                "notes": "C&J emphasis, snatch at reduced intensity, front squat.",
            },
        ],
    },

    3: {
        "description": "3-day: each session covers one competition lift + strength",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.35,
                "notes": "Snatch emphasis day. Back squat or front squat after.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Pulls",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "pull"],
                "session_volume_share": 0.40,
                "notes": "C&J emphasis. Pulling work supports clean positions.",
            },
            {
                "day_number": 3,
                "label": "Snatch + Clean (light) + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["clean", "squat"],
                "session_volume_share": 0.25,
                "notes": "Lighter session. Both lifts at reduced intensity + squat.",
            },
        ],
    },

    4: {
        "description": "4-day: alternating snatch/C&J focus with dedicated squat work",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.30,
                "notes": "Primary snatch day. Heavy squat follows.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Pulls",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "pull"],
                "session_volume_share": 0.30,
                "notes": "Primary C&J day. Clean pulls or snatch pulls after.",
            },
            {
                "day_number": 3,
                "label": "Snatch Variations + Accessories",
                "primary_movement": "snatch",
                "secondary_movements": ["pull", "accessory"],
                "session_volume_share": 0.20,
                "notes": "Snatch variant work (hang, power, positional). Lighter day.",
            },
            {
                "day_number": 4,
                "label": "Clean & Jerk + Squat",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "squat"],
                "session_volume_share": 0.20,
                "notes": "C&J variant or complex. Front squat emphasis.",
            },
        ],
    },

    5: {
        "description": "5-day: high frequency with dedicated technique and squat days",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Back Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.25,
                "notes": "Heavy snatch day. Back squat primary strength.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Pulls",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "pull"],
                "session_volume_share": 0.25,
                "notes": "Heavy C&J day. Pulling work.",
            },
            {
                "day_number": 3,
                "label": "Snatch Technique + Front Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.15,
                "notes": "Lighter snatch work. Positional drills. Front squat.",
            },
            {
                "day_number": 4,
                "label": "Clean Technique + Accessories",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "accessory"],
                "session_volume_share": 0.15,
                "notes": "Clean variants, jerk practice. Lighter session.",
            },
            {
                "day_number": 5,
                "label": "Heavy Singles / Complex + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["clean", "squat"],
                "session_volume_share": 0.20,
                "notes": "Both lifts. Work to heavy singles or complexes. Squat.",
            },
        ],
    },

    6: {
        "description": "6-day: two waves of snatch / C&J / strength; the second wave lighter and positional",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Back Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.22,
                "notes": "Heavy snatch day. Back squat.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Pulls",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "pull"],
                "session_volume_share": 0.22,
                "notes": "Heavy C&J day. Clean pulls.",
            },
            {
                "day_number": 3,
                "label": "Snatch Variations + Front Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat", "pull"],
                "session_volume_share": 0.14,
                "notes": "Hang / block / power snatch, snatch pulls, front squat.",
            },
            {
                "day_number": 4,
                "label": "Clean Variations + Jerk",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "accessory"],
                "session_volume_share": 0.14,
                "notes": "Power / hang clean, jerk from the rack, accessories.",
            },
            {
                "day_number": 5,
                "label": "Snatch + Clean & Jerk (moderate) + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["clean", "squat"],
                "session_volume_share": 0.16,
                "notes": "Both lifts at moderate loads. Squat.",
            },
            {
                "day_number": 6,
                "label": "Technique + Positional Strength",
                "primary_movement": "clean",
                "secondary_movements": ["snatch", "accessory"],
                "session_volume_share": 0.12,
                "notes": "Light positional work on both lifts, overhead and trunk accessories.",
            },
        ],
    },
}

# Which day to re-point when the athlete's lift_emphasis is not balanced
# (PLAN-2 §2.2). The *lighter* session of the non-emphasised lift becomes an
# emphasised-lift day, so a snatch-biased athlete on 4 days trains snatch 3:1
# instead of 2:2; on 2–3 days the split stays as is (nothing light enough to
# flip without dropping a lift for the week).
EMPHASIS_FLIP: dict[str, dict[int, int]] = {
    # sessions_per_week → day_number to flip
    "snatch_biased": {4: 4, 5: 4, 6: 6},   # the light C&J-technique day
    "cj_biased":     {4: 3, 5: 3, 6: 3},   # the light snatch-variation day
}
_FLIPPED_DAY: dict[str, dict] = {
    "snatch_biased": {
        "label": "Snatch Variations + Overhead",
        "primary_movement": "snatch",
        "secondary_movements": ["squat", "accessory"],
        "notes": "Extra snatch day for a snatch-biased athlete: hang / block / power snatch, overhead work.",
    },
    "cj_biased": {
        "label": "Clean Variations + Jerk",
        "primary_movement": "clean",
        "secondary_movements": ["jerk", "pull"],
        "notes": "Extra C&J day for a C&J-biased athlete: power / hang clean, jerk from the rack, clean pulls.",
    },
}


def get_session_templates(sessions_per_week: int, lift_emphasis: str | None = None) -> list[dict]:
    """Get session templates for a given training frequency.

    Falls back to the closest supported frequency if the exact value
    isn't in SESSION_DISTRIBUTIONS (currently 3, 4, or 5).
    """
    key = sessions_per_week if sessions_per_week in SESSION_DISTRIBUTIONS else \
        min(SESSION_DISTRIBUTIONS.keys(), key=lambda k: abs(k - sessions_per_week))
    sessions = [dict(s) for s in SESSION_DISTRIBUTIONS[key]["sessions"]]
    flip_day = EMPHASIS_FLIP.get(lift_emphasis or "balanced", {}).get(key)
    if flip_day is not None:
        for s in sessions:
            if s["day_number"] == flip_day:
                s.update(_FLIPPED_DAY[lift_emphasis])
    return sessions
