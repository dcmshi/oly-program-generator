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
}


def get_session_templates(sessions_per_week: int) -> list[dict]:
    """Get session templates for a given training frequency.

    Falls back to the closest supported frequency if the exact value
    isn't in SESSION_DISTRIBUTIONS (currently 3, 4, or 5).
    """
    if sessions_per_week in SESSION_DISTRIBUTIONS:
        return SESSION_DISTRIBUTIONS[sessions_per_week]["sessions"]
    closest = min(SESSION_DISTRIBUTIONS.keys(), key=lambda k: abs(k - sessions_per_week))
    return SESSION_DISTRIBUTIONS[closest]["sessions"]
