# web/options.py
"""Canonical checkbox/select vocabularies shared by setup and profile.

Single source of truth (WEB-M3): the setup wizard and the profile editor must
offer identical values, or profile saves silently wipe setup-chosen entries and
fault slugs stop matching retrieve.py's fault→exercise mapping. Registered as
Jinja globals in web.app so every template sees the same lists.
"""

# (form/DB value ordering: display label first to mirror the original setup lists)
EQUIPMENT_OPTIONS = [
    ("Barbell",       "barbell"),
    ("Squat rack",    "squat_rack"),
    ("Blocks",        "blocks"),
    ("Straps",        "straps"),
    ("Jerk blocks",   "jerk_blocks"),
    ("Bumper plates", "bumper_plates"),
]

FAULT_OPTIONS = [
    ("Forward balance off floor",  "forward_balance_off_floor"),
    ("Hips rising fast",           "hips_rising_fast"),
    ("Slow turnover",              "slow_turnover"),
    ("Early arm bend",             "early_arm_bend"),
    ("Not finishing pull",         "not_finishing_pull"),
    ("Lost back tightness",        "lost_back_tightness"),
    ("Bar crashing",               "bar_crashing"),
    ("Jumping forward",            "jumping_forward"),
    ("Jumping backward",           "jumping_backward"),
    ("Passive hip extension",      "passive_hip_extension"),
    ("Soft receiving position",    "soft_receiving_position"),
    ("Missed lockout",             "missed_lockout"),
    ("Dip forward (jerk)",         "dip_forward"),
]

STRENGTH_LIMITER_OPTIONS = [
    ("Squat strength (below ratio to comp lifts)", "squat_limited"),
    ("Pulling strength (snatch/clean pull)",       "pull_limited"),
    ("Overhead stability (snatch / jerk)",         "overhead_limited"),
    ("Jerk (C&J is jerk-limited)",                 "jerk_limited"),
    ("Clean (C&J is clean-limited)",               "clean_limited"),
    ("Off-the-floor / first pull strength",        "positional_strength"),
]

# DB enum vocabularies. These are written straight into enum columns, so an
# out-of-vocabulary value is an asyncpg InvalidTextRepresentation → 500, not a
# validation message (WEB-L10). Keep in sync with migration 0001's CREATE TYPE.
GOAL_OPTIONS = [
    ("General strength", "general_strength"),
    ("Competition prep", "competition_prep"),
    ("Technique focus",  "technique_focus"),
    ("PR attempt",       "pr_attempt"),
    ("Work capacity",    "work_capacity"),
    ("Return to sport",  "return_to_sport"),
]

SEX_OPTIONS = [
    ("Male",   "male"),
    ("Female", "female"),
]

VALID_GOALS = {v for _, v in GOAL_OPTIONS}
VALID_SEXES = {v for _, v in SEX_OPTIONS}

MAX_EXERCISES = [
    "Snatch",
    "Clean & Jerk",
    "Back Squat",
    "Front Squat",
    "Snatch Pull",
    "Clean Pull",
    "Push Press",
]
