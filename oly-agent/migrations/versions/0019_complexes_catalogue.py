"""exercise_complexes as a usable catalogue (PLAN-3a).

The table was seeded twice (ids 1–6 and 7–12 are the same six complexes) and
nothing read it. The generator now offers complexes, so:

  * drop the duplicate rows (no session_exercises.complex_id references them);
  * UNIQUE(name), so a re-seed can't duplicate them again;
  * movement_family and complexity_level, so retrieval can filter by the plan's
    complexity cap and group them like exercises;
  * seed classic complexes whose components are all existing exercises rows.

Revision ID: 0019_complexes_catalogue
Revises: 0018_embedding_default_large
Create Date: 2026-09-22
"""
import json

from alembic import op

revision = "0019_complexes_catalogue"
down_revision = "0018_embedding_default_large"
branch_labels = None
depends_on = None

# (name, [(component, reps), ...], intensity_reference, family, complexity, low %, high %, purpose)
SEED = [
    ("Power Snatch + Snatch", [("Power Snatch", 1), ("Snatch", 1)], "snatch", "snatch", 3, 65, 85,
     "Speed and aggression in the turnover, then the full receive at the same load"),
    ("Snatch Pull + Snatch", [("Snatch Pull", 1), ("Snatch", 1)], "snatch", "snatch", 3, 70, 90,
     "Grooves the pull position and finish directly into the classic snatch"),
    ("Hang Snatch (above knee) + Snatch", [("Hang Snatch (above knee)", 1), ("Snatch", 1)], "snatch", "snatch", 3,
     65, 85, "Second-pull timing from the hang, then the full lift from the floor"),
    ("Snatch + Snatch Balance", [("Snatch", 1), ("Snatch Balance", 1)], "snatch", "snatch", 3, 60, 80,
     "Receiving speed and overhead stability under the bar"),
    ("Power Clean + Clean", [("Power Clean", 1), ("Clean", 1)], "clean", "clean", 3, 65, 85,
     "Explosive extension, then the full squat receive at the same load"),
    ("Hang Clean (above knee) + Clean", [("Hang Clean (above knee)", 1), ("Clean", 1)], "clean", "clean", 3,
     65, 85, "Hang timing into the full clean"),
    ("Clean + Front Squat", [("Clean", 1), ("Front Squat", 1)], "clean", "clean", 2, 70, 85,
     "Clean recovery strength — a second squat out of the hole"),
    ("Clean + 2 Jerks", [("Clean", 1), ("Jerk", 2)], "clean_and_jerk", "clean", 3, 65, 85,
     "Jerk volume after the clean, as in competition fatigue"),
    ("Push Press + Jerk", [("Push Press", 1), ("Jerk", 1)], "clean_and_jerk", "jerk", 2, 60, 80,
     "Dip-drive strength, then the split under the same load"),
]

_FAMILY = {  # the six complexes seeded earlier, by name
    "Clean + Front Squat + Jerk": ("clean", 3),
    "Snatch + Overhead Squat": ("snatch", 2),
    "Clean Pull + Clean": ("clean", 2),
    "Snatch Pull + Hang Snatch (above knee)": ("snatch", 2),
    "Power Clean + Push Jerk": ("clean", 2),
    "Hang Snatch (below knee) + Hang Snatch (above knee) + Snatch": ("snatch", 3),
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute("""
        DELETE FROM exercise_complexes c USING exercise_complexes d
        WHERE c.name = d.name AND c.id > d.id
          AND NOT EXISTS (SELECT 1 FROM session_exercises se WHERE se.complex_id = c.id)
    """)
    op.execute("ALTER TABLE exercise_complexes ADD CONSTRAINT uq_exercise_complexes_name UNIQUE (name)")
    op.execute("ALTER TABLE exercise_complexes ADD COLUMN IF NOT EXISTS movement_family movement_family")
    op.execute("ALTER TABLE exercise_complexes ADD COLUMN IF NOT EXISTS complexity_level INTEGER NOT NULL DEFAULT 3")
    for name, (family, level) in _FAMILY.items():
        op.execute(f"UPDATE exercise_complexes SET movement_family = {_q(family)}, complexity_level = {level} "
                   f"WHERE name = {_q(name)}")
    for name, comps, ref, family, level, lo, hi, purpose in SEED:
        ordered = json.dumps([{"exercise_name": n, "reps": r} for n, r in comps])
        total = sum(r for _n, r in comps)
        # only seed when every component exists in the exercise catalogue
        names = ", ".join(_q(n) for n, _r in comps)
        op.execute(f"""
            INSERT INTO exercise_complexes
                (name, exercises_ordered, total_reps_per_set, primary_purpose, typical_intensity_low,
                 typical_intensity_high, intensity_reference, movement_family, complexity_level)
            SELECT {_q(name)}, {_q(ordered)}::jsonb, {total}, {_q(purpose)}, {lo}, {hi}, {_q(ref)},
                   {_q(family)}::movement_family, {level}
            WHERE (SELECT count(DISTINCT name) FROM exercises WHERE name IN ({names})) = {len({n for n, _ in comps})}
            ON CONFLICT (name) DO NOTHING
        """)


def downgrade() -> None:
    op.execute("DELETE FROM exercise_complexes WHERE name IN (" + ", ".join(_q(s[0]) for s in SEED) + ")")
    op.execute("ALTER TABLE exercise_complexes DROP COLUMN IF EXISTS complexity_level")
    op.execute("ALTER TABLE exercise_complexes DROP COLUMN IF EXISTS movement_family")
    op.execute("ALTER TABLE exercise_complexes DROP CONSTRAINT IF EXISTS uq_exercise_complexes_name")
