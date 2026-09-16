# shared/exercise_mapping.py
"""
Exercise name → intensity_reference mapping and competition lift sets.

Single source of truth used by weight_resolver, validate, and feedback modules.
"""

from shared.constants import WARMUP_VOLUME_EXCLUSION_PCT

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

# Name fragments that mark a derivative of a competition lift — pulls, deadlifts,
# squats, presses, balances — which coaches routinely prescribe as a percentage
# of the snatch / clean / C&J max (Clean Pull @ 90% of clean) without the row
# being a competition lift.
NON_COMP_LIFT_NAME_MARKERS: tuple[str, ...] = (
    "pull", "deadlift", "extension", "squat", "press", "balance", "rdl", "row", "good morning",
)


def is_competition_lift(exercise_name, intensity_reference) -> bool:
    """True for a snatch / clean / jerk / C&J and their power, hang, block,
    pause, muscle … variants: the rows Prilepin's chart, the week's intensity
    ceiling and the weekly comp-lift rep budget are about.

    Keyed on the exercise, not only on the max it borrows: a Clean Pull whose
    percentage refers to the clean max (`intensity_reference="clean"`) is
    still a pull. Deciding by reference alone made the validator reject
    3×3 @ 90% pulls and 88% pulls "above the ceiling", and the generator's
    retries then dropped the pulls altogether (DOG-1 finding).
    """
    if intensity_reference not in COMP_LIFT_REFS:
        return False
    name = (exercise_name or "").lower()
    return not any(marker in name for marker in NON_COMP_LIFT_NAME_MARKERS)


def is_warmup_set(intensity_reference, intensity_pct) -> bool:
    """True if this prescription is one of the mandated warmup ramp sets.

    The generate prompt requires 2-3 sets at 50-60% before each competition
    lift, and validate.py already excludes that band from Prilepin and weekly
    volume for exactly this reason. Keying off the structure — competition lift
    at or below WARMUP_VOLUME_EXCLUSION_PCT — is what makes the UI's "Warmup"
    badge trustworthy; it used to substring-match `selection_rationale`, so it
    fired on prose like "not a warmup priority" and vanished whenever the
    generator reworded (FE-L5).

    Note validate.py's *intensity floor* check uses the wider
    WARMUP_INTENSITY_CUTOFF_PCT (65%) instead. That is a separate question —
    "is this set too light to warn about" — not "is this a warmup".
    """
    if intensity_reference not in COMP_LIFT_REFS or intensity_pct is None:
        return False
    try:
        return float(intensity_pct) <= WARMUP_VOLUME_EXCLUSION_PCT
    except (TypeError, ValueError):
        return False
