"""Postgres enum values that prompts and JSON-schema constraints reference (STRUCT-1).

These mirror `CREATE TYPE … AS ENUM` in `schema.sql` (ingestion) and the
athlete_level enum in the agent's baseline migration; `test_schema_enums.py`
parses `schema.sql` and asserts each tuple matches, so the two can't drift.
Ingestion uses them to constrain LLM output (`output_config.format`), which
is what stops the model padding a condition with `"novice"` or `"peaking"`
(values the matcher can't evaluate) or inventing a `principle_category`
(`"intensification"`, which the DB rejected on the 2026-09-16 re-ingest).
"""

TRAINING_PHASES: tuple[str, ...] = (
    "general_prep", "accumulation", "transmutation", "intensification",
    "realization", "competition", "deload", "transition",
)
ATHLETE_LEVELS: tuple[str, ...] = ("beginner", "intermediate", "advanced", "elite")
MOVEMENT_FAMILIES: tuple[str, ...] = (
    "snatch", "clean", "jerk", "squat", "pull", "press",
    "hinge", "row", "carry", "core", "plyometric",
)
PRINCIPLE_CATEGORIES: tuple[str, ...] = (
    "volume", "intensity", "frequency", "exercise_selection", "periodization",
    "peaking", "recovery", "technique", "load_progression", "deload",
)
RULE_TYPES: tuple[str, ...] = ("hard_constraint", "guideline", "heuristic")
CHUNK_TYPES: tuple[str, ...] = (
    "concept", "methodology", "periodization", "programming_rationale",
    "biomechanics", "case_study", "fault_correction",
    "recovery_adaptation", "competition_strategy", "nutrition_bodyweight",
)
