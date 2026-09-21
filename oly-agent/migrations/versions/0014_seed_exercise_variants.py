"""Seed the exercise variants a real coach's block uses (DOG-1f).

Importing the athlete's 11-week General Strength sheet showed the catalogue
could not express it: every deficit / block / extension / pull-to-hip variant
collapsed onto the base lift and the unloaded work (jumps, rows, split squats,
back extensions) had no entry, so the generator rotated 13 exercises. This
adds 27 curated rows with typical prescriptions (so they render in the
prompt) and wires parent_exercise_id. ON CONFLICT (name) DO NOTHING keeps a
DB that already carries any of these names intact.

Revision ID: 0014_seed_exercise_variants
Revises: 0013_drop_redundant_hash_index
Create Date: 2026-09-16
"""

from alembic import op

revision = "0014_seed_exercise_variants"
down_revision = "0013_drop_redundant_hash_index"
branch_labels = None
depends_on = None

# (name, category, family, start_position, is_power, complexity, purpose,
#  secondary_purposes, faults_addressed, sets_lo, sets_hi, reps_lo, reps_hi,
#  pct_lo, pct_hi, rest_s, equipment, cues, parent_name)
_ROWS = [
    # ── Snatch family ────────────────────────────────────────────────────────
    ("Snatch from Deficit", "competition_variant", "snatch", "floor", False, 3,
     "Longer first pull from a raised platform — strength and patience off the floor",
     ["leg drive", "positional strength"], ["weak_off_floor", "hips_rising_fast", "forward_balance_off_floor"],
     3, 5, 1, 3, 60, 85, 120, ["barbell", "deficit_platform"],
     ["push the floor away", "stay over the bar", "patient off the floor"], "Snatch"),
    ("Power Snatch from Deficit", "competition_variant", "snatch", "floor", True, 3,
     "Deficit pull with a power receive — leg drive and a fast finish without the full squat",
     ["speed", "leg drive"], ["weak_off_floor", "not_finishing_pull"],
     3, 5, 2, 3, 55, 75, 90, ["barbell", "deficit_platform"],
     ["push the floor away", "finish tall", "fast elbows"], "Power Snatch"),
    ("Block Snatch (above knee)", "competition_variant", "snatch", "blocks_above_knee", False, 2,
     "Second pull and turnover from a static start above the knee — speed under the bar",
     ["turnover speed", "hip contact"], ["slow_turnover", "not_finishing_pull", "no_hip_contact"],
     3, 5, 1, 3, 65, 90, 120, ["barbell", "blocks"],
     ["load the hamstrings", "brush the hip", "punch under"], "Snatch"),
    ("Block Snatch (knee)", "competition_variant", "snatch", "blocks_at_knee", False, 2,
     "Pull from a dead start at the knee — the transition through the middle under load",
     ["positional strength", "timing development"], ["hips_rising_fast", "early_arm_bend", "not_finishing_pull"],
     3, 5, 1, 3, 65, 88, 120, ["barbell", "blocks"],
     ["chest up", "knees back then under", "patient through the middle"], "Snatch"),
    ("Snatch Extension", "pull", "snatch", "floor", False, 1,
     "Pull to full extension with no arm pull — timing and finish of the second pull",
     ["extension", "posterior chain"], ["not_finishing_pull", "passive_hip_extension", "early_arm_bend"],
     3, 5, 2, 4, 80, 100, 90, ["barbell", "straps"],
     ["finish tall", "shoulders up not back", "long arms"], "Snatch Pull"),
    ("Snatch Pull to Hip", "pull", "snatch", "floor", False, 1,
     "Pull that stops at hip contact — bar path into the hip and patience in the second pull",
     ["bar path", "positional awareness"], ["no_hip_contact", "early_arm_bend", "hips_rising_fast"],
     3, 5, 2, 4, 80, 100, 90, ["barbell", "straps"],
     ["sweep the bar in", "meet it at the hip", "no shrug"], "Snatch Pull"),
    ("Snatch Grip Romanian Deadlift", "strength", "hinge", None, False, 1,
     "Posterior chain and back tightness in the snatch grip",
     ["hamstring strength", "back tightness"], ["lost_back_tightness", "hips_rising_fast"],
     3, 4, 4, 8, 40, 60, 90, ["barbell", "straps"],
     ["hips back", "bar on the thighs", "flat back"], None),
    # ── Clean family ─────────────────────────────────────────────────────────
    ("Clean from Deficit", "competition_variant", "clean", "floor", False, 3,
     "Longer first pull from a raised platform — strength and patience off the floor",
     ["leg drive", "positional strength"], ["weak_off_floor", "hips_rising_fast", "forward_balance_off_floor"],
     3, 5, 1, 3, 60, 85, 120, ["barbell", "deficit_platform"],
     ["push the floor away", "stay over the bar", "patient off the floor"], "Clean"),
    ("Power Clean from Deficit", "competition_variant", "clean", "floor", True, 3,
     "Deficit pull with a power receive — leg drive and a fast finish without the full squat",
     ["speed", "leg drive"], ["weak_off_floor", "not_finishing_pull"],
     3, 5, 2, 3, 55, 75, 90, ["barbell", "deficit_platform"],
     ["push the floor away", "finish tall", "fast elbows"], "Power Clean"),
    ("Block Clean (above knee)", "competition_variant", "clean", "blocks_above_knee", False, 2,
     "Second pull and turnover from a static start above the knee — speed under the bar",
     ["turnover speed", "rack timing"], ["slow_turnover", "not_finishing_pull", "bar_crashing"],
     3, 5, 1, 3, 65, 90, 120, ["barbell", "blocks"],
     ["load the hamstrings", "brush the thigh", "elbows through"], "Clean"),
    ("Block Clean (knee)", "competition_variant", "clean", "blocks_at_knee", False, 2,
     "Pull from a dead start at the knee — the transition through the middle under load",
     ["positional strength", "timing development"], ["hips_rising_fast", "early_arm_bend", "not_finishing_pull"],
     3, 5, 1, 3, 65, 88, 120, ["barbell", "blocks"],
     ["chest up", "knees back then under", "patient through the middle"], "Clean"),
    ("Clean Extension", "pull", "clean", "floor", False, 1,
     "Pull to full extension with no arm pull — timing and finish of the second pull",
     ["extension", "posterior chain"], ["not_finishing_pull", "passive_hip_extension", "early_arm_bend"],
     3, 5, 2, 4, 80, 100, 90, ["barbell", "straps"],
     ["finish tall", "shoulders up not back", "long arms"], "Clean Pull"),
    ("Clean Pull to Hip", "pull", "clean", "floor", False, 1,
     "Pull that stops at hip contact — bar path into the thigh and patience in the second pull",
     ["bar path", "positional awareness"], ["no_hip_contact", "early_arm_bend", "hips_rising_fast"],
     3, 5, 2, 4, 80, 100, 90, ["barbell", "straps"],
     ["sweep the bar in", "meet it at the thigh", "no shrug"], "Clean Pull"),
    ("Stiff-Leg Deadlift", "strength", "hinge", None, False, 1,
     "Clean-grip stiff-leg pull — hamstring and back strength for the first pull",
     ["hamstring strength", "back tightness"], ["lost_back_tightness", "weak_off_floor"],
     3, 4, 4, 6, 60, 80, 120, ["barbell", "straps"],
     ["hips back", "flat back", "bar close"], None),
    # ── Jerk / press ─────────────────────────────────────────────────────────
    ("Push Press Behind the Neck", "accessory", "jerk", "behind_neck", False, 1,
     "Overhead drive and lockout in the jerk-grip receiving line",
     ["overhead strength", "lockout"], ["missed_lockout", "soft_receiving_position"],
     3, 5, 3, 5, 50, 75, 90, ["barbell", "squat_rack"],
     ["vertical dip", "drive then press", "head through"], "Push Press"),
    ("Jerk Dip", "positional", "jerk", "rack", False, 1,
     "Dip and hold with a heavy bar — vertical dip position and trunk bracing under supramaximal load",
     ["trunk bracing", "dip position"], ["dip_forward"],
     3, 4, 3, 3, 90, 110, 120, ["barbell", "squat_rack"],
     ["knees out", "weight mid-foot", "brace before the dip"], "Jerk"),
    # ── Squat ────────────────────────────────────────────────────────────────
    ("Close-Stance Back Squat", "strength", "squat", None, False, 1,
     "Narrow-stance squat — quad emphasis and an upright torso",
     ["quad strength", "upright posture"], ["weak_legs"],
     3, 5, 3, 8, 60, 80, 120, ["barbell", "squat_rack"],
     ["knees forward", "chest tall", "heels down"], "Back Squat"),
    ("Quarter Squat", "strength", "squat", None, False, 1,
     "Supramaximal partial squat — overload the top of the squat and the jerk drive",
     ["leg drive", "overload"], ["weak_legs"],
     3, 5, 2, 5, 100, 120, 150, ["barbell", "squat_rack", "safety_pins"],
     ["brace hard", "short dip", "drive through the floor"], "Back Squat"),
    ("1¼ Front Squat", "positional", "squat", None, False, 2,
     "Front squat with a quarter rebound from the bottom — bottom-position strength and posture",
     ["bottom strength", "trunk posture"], ["soft_receiving_position", "dip_forward"],
     3, 4, 3, 5, 60, 80, 120, ["barbell", "squat_rack"],
     ["elbows up", "sit in", "quarter up then back down"], "Front Squat"),
    # ── Plyometrics / accessories (unloaded — no intensity) ─────────────────
    ("Box Jump", "accessory", "plyometric", None, False, 1,
     "Explosive hip extension and fast feet without bar load",
     ["rate of force development"], ["passive_hip_extension", "not_finishing_pull"],
     3, 5, 3, 5, None, None, 90, ["box"], ["full extension", "soft landing"], None),
    ("Broad Jump", "accessory", "plyometric", None, False, 1,
     "Horizontal power — hip extension through the whole range",
     ["rate of force development"], ["passive_hip_extension"],
     3, 5, 3, 5, None, None, 90, [], ["arms through", "land quiet"], None),
    ("Vertical Jump", "accessory", "plyometric", None, False, 1,
     "Vertical power — the second-pull extension pattern without a bar",
     ["rate of force development"], ["passive_hip_extension", "not_finishing_pull"],
     3, 5, 3, 5, None, None, 90, [], ["dip and drive", "finish tall"], None),
    ("Jumping Squat", "accessory", "plyometric", None, False, 1,
     "Loaded jump with a light bar — rate of force development out of the squat",
     ["rate of force development"], ["passive_hip_extension"],
     3, 5, 3, 6, 20, 40, 120, ["barbell"], ["light bar", "explode", "absorb the landing"], None),
    ("Barbell Row", "accessory", "row", None, False, 1,
     "Upper-back strength for bar control and lockout",
     ["upper back", "lat strength"], ["lost_back_tightness"],
     3, 4, 6, 10, 60, 80, 90, ["barbell"], ["flat back", "pull to the hip"], None),   # % of the barbell_row max
    ("Pendlay Row", "accessory", "row", None, False, 1,
     "Dead-stop row from the floor — upper-back strength with a rigid trunk",
     ["upper back", "trunk rigidity"], ["lost_back_tightness"],
     3, 4, 5, 8, 60, 80, 90, ["barbell"], ["reset every rep", "torso parallel"], None),
    ("Back Extension", "accessory", "hinge", None, False, 1,
     "Spinal erector endurance for pulling positions",
     ["back endurance"], ["lost_back_tightness"],
     3, 4, 8, 15, None, None, 60, ["ghd_bench"], ["neutral spine", "squeeze the glutes"], None),
    ("Bulgarian Split Squat", "accessory", "squat", None, False, 1,
     "Single-leg strength and hip stability",
     ["single-leg strength", "hip stability"], ["knee_cave_in_recovery"],
     3, 4, 6, 10, None, None, 90, ["dumbbells", "bench"], ["knee tracks the toe", "tall torso"], None),
]

_NAMES = [r[0] for r in _ROWS]


def _pg_array(values: list[str] | None) -> str:
    if not values:
        return "'{}'"
    return "ARRAY[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]::text[]"


def _sql_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def upgrade() -> None:
    for (name, category, family, start, is_power, complexity, purpose, secondary, faults,
         sets_lo, sets_hi, reps_lo, reps_hi, pct_lo, pct_hi, rest, equipment, cues, parent) in _ROWS:
        op.execute(
            "INSERT INTO exercises (name, category, movement_family, start_position, is_power, "
            "is_muscle, has_pause, is_no_feet, complexity_level, primary_purpose, "
            "secondary_purposes, faults_addressed, typical_sets_low, typical_sets_high, "
            "typical_reps_low, typical_reps_high, typical_intensity_low, typical_intensity_high, "
            "typical_rest_seconds, equipment_required, cues, parent_exercise_id) VALUES ("
            + ", ".join([
                _sql_value(name), _sql_value(category), _sql_value(family),
                _sql_value(start), _sql_value(is_power), "FALSE", "FALSE", "FALSE",
                _sql_value(complexity), _sql_value(purpose), _pg_array(secondary), _pg_array(faults),
                _sql_value(sets_lo), _sql_value(sets_hi), _sql_value(reps_lo), _sql_value(reps_hi),
                _sql_value(pct_lo), _sql_value(pct_hi), _sql_value(rest), _pg_array(equipment),
                _pg_array(cues),
                f"(SELECT id FROM exercises WHERE name = {_sql_value(parent)})" if parent else "NULL",
            ])
            + ") ON CONFLICT (name) DO NOTHING"
        )


def downgrade() -> None:
    names = ", ".join(_sql_value(n) for n in _NAMES)
    # FKs have no ON DELETE; detach references before removing the rows
    op.execute(f"UPDATE session_exercises SET exercise_id = NULL WHERE exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    op.execute(f"UPDATE training_log_exercises SET exercise_id = NULL WHERE exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    op.execute(f"UPDATE exercises SET parent_exercise_id = NULL WHERE parent_exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    op.execute(f"DELETE FROM exercise_substitutions WHERE exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names})) OR substitute_exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    op.execute(f"DELETE FROM exercises WHERE name IN ({names})")
