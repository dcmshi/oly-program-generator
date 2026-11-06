"""Seed the accessory catalogue (DOG-2f).

The catalogue had no `press`, `core` or `carry` rows: the accessory pool the
generator could choose from was 2 rows, 3 hinges, 2 pulls and 4 jumps, so
program 53 alternated Barbell Row / Pendlay Row in every session and had no
upper-back, shoulder or thoracic work for an overhead-limited athlete. The
corpus never filled the gap: `pipeline._parse_exercise` catalogues only
sections classified EXERCISE_DESCRIPTION whose name holds a lift keyword
(snatch / clean / jerk / squat / press / pull / deadlift / RDL), and the 47
parsed program templates name accessories in Soviet shorthand or as "Abs" /
"GPP exercise N".

Every movement below is named in the corpus (count of non-quarantined
knowledge chunks / distinct sources on 2026-11-03, regex over raw_content):
bench press 442/82, push press 252/103, row 197/64, sit-up / abs 195/60,
back extension 178/55, shrug 154/54, lunge 139/50, strict / military press
139/41, good morning 112/48, triceps 117/41, pull-up / chin-up 111/37, RDL
66/32, dip 51/29, shoulder dislocate / pass-through 50/28, plank 46/22,
step-up 29/17, behind-the-neck press 38/15, glute-ham raise 19/11, leg raise
16/11, dumbbell row 18/10, thoracic mobility 17/8, delt fly / lateral raise
30/7, Sots press 13/7, reverse hyper 17/6, carries 20/6. Face pulls, band
pull-aparts, Pallof presses and hollow holds appear in one source each and are
left out.

No typical % on the loaded presses and hinges: athletes rarely record a max
for them, so the generator prescribes them by RPE (the program page already
shows the RPE when no kg resolves). Timed / distance work (plank, carries)
has no typical reps — the purpose gives the duration.

Also renames the ingestion artefact "6.2 Hang High Pull" (a section number
kept in the name) to "Hang High Pull".

Revision ID: 0023_seed_accessories
Revises: 0022_olympic_lifting_experience
Create Date: 2026-11-03
"""

from alembic import op

revision = "0023_seed_accessories"
down_revision = "0022_olympic_lifting_experience"
branch_labels = None
depends_on = None

# (name, category, family, complexity, purpose, secondary_purposes, faults_addressed,
#  sets_lo, sets_hi, reps_lo, reps_hi, rest_s, equipment, cues)
_ROWS = [
    # ── Presses / overhead ───────────────────────────────────────────────────
    ("Strict Press", "strength", "press", 1,
     "Overhead pressing strength and a stable lockout without leg drive",
     ["overhead strength", "trunk stiffness"], ["missed_lockout"],
     3, 5, 3, 6, 120, ["barbell", "squat_rack"],
     ["squeeze glutes", "head through at the top", "bar over the ears"]),
    ("Behind the Neck Press", "strength", "press", 1,
     "Strict press from behind the neck — shoulder strength in the jerk-receiving plane",
     ["overhead strength", "shoulder mobility"], ["missed_lockout"],
     3, 5, 3, 6, 120, ["barbell", "squat_rack"],
     ["elbows under the bar", "press straight up", "active shoulders at the top"]),
    ("Snatch Grip Behind the Neck Press", "strength", "press", 1,
     "Strict press in the snatch grip — overhead strength and range in the snatch-receiving position",
     ["overhead stability", "shoulder mobility"], ["missed_lockout", "soft_receiving_position"],
     3, 5, 3, 6, 120, ["barbell", "squat_rack"],
     ["press up and slightly back", "lock and shrug at the top", "ribs down"]),
    ("Sots Press", "positional", "press", 2,
     "Press from the bottom of an overhead squat — balance, upper-back strength and mobility in the snatch receive",
     ["overhead stability", "thoracic mobility", "squat-depth balance"], ["soft_receiving_position", "missed_lockout"],
     3, 4, 3, 5, 120, ["barbell"],
     ["sit tall in the hole", "press behind the head", "stay balanced mid-foot"]),
    ("Bench Press", "strength", "press", 1,
     "Upper-body pushing strength and shoulder-girdle muscle mass",
     ["upper-body strength", "hypertrophy"], [],
     3, 5, 3, 8, 120, ["barbell", "bench"],
     ["shoulder blades back and down", "feet planted", "bar to lower chest"]),
    ("Dips", "accessory", "press", 1,
     "Bodyweight triceps and shoulder strength for the jerk lockout",
     ["triceps strength", "lockout"], ["missed_lockout"],
     3, 4, 6, 12, 90, ["dip_bars"],
     ["shoulders down", "elbows back", "full lockout"]),
    ("Triceps Extension", "accessory", "press", 1,
     "Triceps strength for a firm overhead lockout (band, dumbbell or skull crusher)",
     ["triceps strength"], ["missed_lockout"],
     3, 4, 8, 15, 60, ["dumbbells"],
     ["elbows still", "full extension", "control the lowering"]),
    ("Shoulder Pass-Through", "positional", "press", 1,
     "Dowel or band pass-throughs — shoulder and thoracic range for the overhead positions",
     ["shoulder mobility", "thoracic mobility", "warm-up"], ["soft_receiving_position"],
     2, 3, 10, 15, 30, ["dowel"],
     ["arms straight", "ribs down", "narrow the grip only as range allows"]),
    # ── Upper back / pulling ─────────────────────────────────────────────────
    ("Pull-Up", "accessory", "row", 1,
     "Vertical pulling strength for the lats and upper back",
     ["upper-back strength", "shoulder health"], [],
     3, 4, 5, 10, 90, ["pull_up_bar"],
     ["start from a dead hang", "chest to the bar", "control down"]),
    ("Dumbbell Row", "accessory", "row", 1,
     "Unilateral upper-back strength and posture",
     ["upper-back strength", "posture"], ["lost_back_tightness"],
     3, 4, 8, 12, 60, ["dumbbells", "bench"],
     ["flat back", "elbow to the hip", "pause at the top"]),
    ("Rear Delt Fly", "accessory", "row", 1,
     "Rear-delt and upper-back endurance for shoulder health and overhead posture",
     ["shoulder health", "upper-back endurance"], [],
     3, 4, 10, 15, 60, ["dumbbells"],
     ["soft elbows", "lead with the elbows", "squeeze the shoulder blades"]),
    ("Shrug", "accessory", "pull", 1,
     "Trap strength for the finish of the pull and a solid overhead base",
     ["trap strength", "pull finish"], ["not_finishing_pull"],
     3, 4, 6, 10, 90, ["barbell"],
     ["straight arms", "shrug up not back", "hold at the top"]),
    # ── Hinge / posterior chain ──────────────────────────────────────────────
    ("Good Morning", "strength", "hinge", 1,
     "Posterior-chain strength and back tightness in the hinge",
     ["hamstring strength", "back strength"], ["lost_back_tightness", "hips_rising_fast"],
     3, 4, 5, 8, 120, ["barbell", "squat_rack"],
     ["hips back", "soft knees", "flat back throughout"]),
    ("Romanian Deadlift", "strength", "hinge", 1,
     "Hamstring and back strength in the clean grip with the bar close",
     ["hamstring strength", "back tightness"], ["lost_back_tightness", "forward_balance_off_floor"],
     3, 4, 5, 8, 120, ["barbell", "straps"],
     ["hips back", "bar on the thighs", "shoulders over the bar"]),
    ("Glute-Ham Raise", "accessory", "hinge", 1,
     "Hamstring strength through knee flexion and hip extension",
     ["hamstring strength", "injury resilience"], [],
     3, 4, 6, 10, 90, ["ghd_bench"],
     ["hips extended", "lower under control", "pull with the hamstrings"]),
    ("Reverse Hyperextension", "accessory", "hinge", 1,
     "Lower-back and glute work with spinal decompression",
     ["lower-back endurance", "glute strength"], [],
     3, 4, 10, 15, 60, ["reverse_hyper"],
     ["swing from the hips", "squeeze at the top", "control the return"]),
    # ── Single-leg ───────────────────────────────────────────────────────────
    ("Walking Lunge", "accessory", "squat", 1,
     "Single-leg strength, balance and hip stability (reps per leg)",
     ["single-leg strength", "hip stability"], [],
     3, 4, 8, 12, 90, ["dumbbells"],
     ["long step", "knee tracks over the foot", "upright torso"]),
    ("Step-Up", "accessory", "squat", 1,
     "Single-leg strength and knee stability onto a box (reps per leg)",
     ["single-leg strength", "knee stability"], [],
     3, 4, 5, 8, 90, ["box", "dumbbells"],
     ["drive through the whole foot", "no push from the back leg", "stand tall at the top"]),
    # ── Core ─────────────────────────────────────────────────────────────────
    ("Plank", "accessory", "core", 1,
     "Trunk stiffness for holding the bar overhead and in the squat — 30–60 s holds",
     ["trunk stiffness"], [],
     None, None, None, None, 60, [],
     ["ribs down", "squeeze glutes", "straight line head to heel"]),
    ("Hanging Leg Raise", "accessory", "core", 1,
     "Anterior core and hip-flexor strength",
     ["core strength"], [],
     3, 4, 8, 12, 60, ["pull_up_bar"],
     ["no swing", "posterior tilt at the top", "lower slowly"]),
    ("Sit-Up", "accessory", "core", 1,
     "Abdominal strength (weighted or on a GHD)",
     ["core strength"], [],
     3, 4, 10, 20, 60, [],
     ["controlled tempo", "exhale on the way up"]),
    ("Thoracic Extension (foam roller)", "positional", "core", 1,
     "Thoracic extension mobility for the front rack and overhead positions — 1–2 min",
     ["thoracic mobility", "warm-up"], ["soft_receiving_position"],
     None, None, None, None, 30, ["foam_roller"],
     ["hips down", "extend over the roller segment by segment", "hands support the head"]),
    # ── Carries ──────────────────────────────────────────────────────────────
    ("Farmer's Carry", "accessory", "carry", 1,
     "Grip, trap and trunk strength — 20–40 m walks",
     ["grip strength", "trunk stiffness"], [],
     None, None, None, None, 90, ["dumbbells"],
     ["tall posture", "short quick steps", "shoulders down"]),
    ("Overhead Carry", "accessory", "carry", 1,
     "Overhead stability under load — walk 20–30 m with the bar or a dumbbell locked out",
     ["overhead stability", "trunk stiffness"], ["missed_lockout", "soft_receiving_position"],
     None, None, None, None, 90, ["barbell"],
     ["locked elbows", "bar over the ears", "ribs down"]),
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
    for (name, category, family, complexity, purpose, secondary, faults,
         sets_lo, sets_hi, reps_lo, reps_hi, rest, equipment, cues) in _ROWS:
        op.execute(
            "INSERT INTO exercises (name, category, movement_family, complexity_level, primary_purpose, "
            "secondary_purposes, faults_addressed, typical_sets_low, typical_sets_high, "
            "typical_reps_low, typical_reps_high, typical_rest_seconds, equipment_required, cues) VALUES ("
            + ", ".join([
                _sql_value(name), _sql_value(category), _sql_value(family), _sql_value(complexity),
                _sql_value(purpose), _pg_array(secondary), _pg_array(faults),
                _sql_value(sets_lo), _sql_value(sets_hi), _sql_value(reps_lo), _sql_value(reps_hi),
                _sql_value(rest), _pg_array(equipment), _pg_array(cues),
            ])
            + ") ON CONFLICT (name) DO NOTHING"
        )
    op.execute("UPDATE exercises SET name = 'Hang High Pull' WHERE name = '6.2 Hang High Pull' "
               "AND NOT EXISTS (SELECT 1 FROM exercises WHERE name = 'Hang High Pull')")


def downgrade() -> None:
    names = ", ".join(_sql_value(n) for n in _NAMES)
    # A recorded max (athlete_maxes.exercise_id is NOT NULL) is athlete data: refuse
    # rather than delete it. Note the upgrade skipped names that already existed
    # (ON CONFLICT DO NOTHING); none did when it was applied, so deleting by name is safe.
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM athlete_maxes WHERE exercise_id IN
                   (SELECT id FROM exercises WHERE name IN ({names}))) THEN
            RAISE EXCEPTION 'athlete_maxes reference accessories seeded by 0023; remove those maxes first';
        END IF; END $$""")
    op.execute(f"UPDATE percentage_schemes SET exercise_id = NULL WHERE exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    # FKs have no ON DELETE; detach references before removing the rows
    op.execute(f"UPDATE session_exercises SET exercise_id = NULL WHERE exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    op.execute(f"UPDATE training_log_exercises SET exercise_id = NULL WHERE exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    op.execute(f"DELETE FROM exercise_substitutions WHERE exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names})) OR substitute_exercise_id IN "
               f"(SELECT id FROM exercises WHERE name IN ({names}))")
    op.execute(f"DELETE FROM exercises WHERE name IN ({names})")
    op.execute("UPDATE exercises SET name = '6.2 Hang High Pull' WHERE name = 'Hang High Pull'")
