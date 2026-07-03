# shared/exercise_mapping.py
"""
Exercise name → intensity_reference mapping and competition lift sets.

Single source of truth used by weight_resolver, validate, and feedback modules.
"""

# Maps canonical DB exercise names to the intensity_reference key used by the agent.
# The LLM outputs intensity_reference; the agent uses it to look up athlete maxes.
EXERCISE_NAME_TO_INTENSITY_REF: dict[str, str] = {
    "Snatch":            "snatch",
    "Clean & Jerk":      "clean_and_jerk",
    "Clean":             "clean",
    "Back Squat":        "back_squat",
    "Front Squat":       "front_squat",
    "Snatch Pull":       "snatch_pull",
    "Clean Pull":        "clean_pull",
    "Snatch Deadlift":   "snatch_deadlift",
    "Clean Deadlift":    "clean_deadlift",
    "Push Press":        "push_press",
    "Overhead Squat":    "overhead_squat",
    "Jerk":              "jerk",
}

# Competition lift intensity_reference values — used for Prilepin volume counting,
# intensity floor warnings, and competition-lifts-first principle checking.
COMP_LIFT_REFS: frozenset[str] = frozenset({"snatch", "clean_and_jerk", "clean"})


def to_intensity_ref(name: str) -> str:
    """Normalize a display exercise name to its intensity_reference key.

    "Clean & Jerk" -> "clean_and_jerk"; unmapped names fall back to
    snake_case. Every module that compares display-name-keyed data against
    intensity-reference-keyed data (maxes snapshots, deltas) must use this
    one function — build_maxes_dict and compute_outcome diverging on the
    fallback is exactly how maxes_delta silently broke.
    """
    ref = EXERCISE_NAME_TO_INTENSITY_REF.get(name)
    if ref is None:
        ref = name.lower().replace(" ", "_").replace("&", "and")
    return ref
