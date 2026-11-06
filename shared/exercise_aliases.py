# shared/exercise_aliases.py
"""
The exercise-name vocabulary for imported programs, in one place (CLEAN-1).

Coach-written sheets and notes name lifts their own way ("Sn Balance", "BB
rows", "Block Power Snatch + OH Sq", "FSQ", "(% of CL)"). This module maps
those names to catalogue rows (`exercises.name`) and to the max a percentage
refers to (the pipeline's intensity_reference keys). It used to be split
between import_program_csv.py and import_program_md.py, next to
`shared/exercise_mapping.py` (the generator's display name → reference map),
and renaming a catalogue row silently unmapped imports (NULL exercise_id).
`all_alias_targets()` lists every catalogue name the maps point at;
`tests/test_exercise_aliases.py` checks them against the exercises table
(INTEGRATION_TESTS=1) and the pure rules without a database.

CSV (import_program_csv): EXACT_ALIASES, then hang_variant_for, then
SUBSTRING_ALIASES, first hit wins → catalogue_name_for. Markdown
(import_program_md): MD_ABBREVIATIONS expand Paul's shorthand, MD_ALIASES are
tried before the CSV rules (None = deliberately unmatched), MD_OF_REFS read
"(% of CL)".
"""

# Whole-word abbreviations in Paul's notation → catalogue words.
MD_ABBREVIATIONS: list[tuple[str, str]] = [
    (r"\bFSQ\b", "Front Squat"), (r"\bSN\b", "Snatch"), (r"\bCL\b", "Clean"), (r"\bSQ\b", "Squat"),
    (r"\bSq\b", "Squat"), (r"\bExt\b", "Extension"), (r"\bOH\b", "Overhead"), (r"\bPP\b", "Push Press"),
    (r"\bRev\b", "Reverse"), (r"^Front \+", "Front Squat +"),
]
# Names the CSV alias map doesn't know, checked first (lowercased, expanded).
# None = deliberately no catalogue match (the CSV map's "clean" substring
# would otherwise file an RDL "(% of Clean)" under Clean).
MD_ALIASES: list[tuple[str, str | None]] = [
    ("snatch grip rdl", "Snatch Grip Romanian Deadlift"),
    ("rdl", "Romanian Deadlift"),          # seeded by migration 0023
    ("no foot snatch", "No Feet Snatch"),
    ("no foot clean", "Clean & Jerk"),
    ("deficit snatch extension", "Snatch Pull from Deficit"),
    ("deficit clean extension", None),
    ("deficit snatch", "Snatch from Deficit"),
    ("block snatch", "Block Snatch (knee)"),
    ("block clean extension", "Clean Extension"),
    ("block clean", "Block Clean (knee)"),
    ("hang snatch", "Hang Snatch (above knee)"),
    ("bulgarian split", "Bulgarian Split Squat"),
    ("box jumps", "Box Jump"),
    ("pendlay row", "Pendlay Row"),
    ("bent over bb row", "Barbell Row"),
    ("back extension", "Back Extension"),
]
MD_OF_REFS: dict[str, str] = {"cl": "clean", "clean": "clean", "sn": "snatch", "snatch": "snatch", "pp": "push_press",
            "jerk": "jerk", "fsq": "front_squat", "bsq": "back_squat"}


def intensity_reference_for(name: str) -> str | None:
    """Which athlete max a percentage on this row refers to (pipeline keys)."""
    n = name.lower()
    if "% of cl" in n:
        return "clean"
    if "% of msn" in n or ("muscle snatch" in n and ("1rm" in n or "heavy single" in n)):
        return "muscle_snatch"
    if "clean" in n or n.startswith("cl "):
        return "clean_and_jerk" if "jerk" in n else "clean"
    if "jerk" in n:
        return "jerk"
    if "push press" in n:
        return "push_press"
    if "back squat" in n:
        return "back_squat"
    if "front squat" in n:
        return "front_squat"
    if "snatch" in n or n.startswith("sn ") or n.startswith("power sn"):
        return "snatch"
    if "mid grip deadlift" in n:
        return "clean"
    return None


# Sheet name (lowercased) → catalogue name. Complexes take the primary lift.
# Substring rules run after exact matches; unmatched → exercise_id NULL.
EXACT_ALIASES: dict[str, str] = {
    "hang snatch": "Hang Snatch (above knee)",
    "no foot snatch": "No Feet Snatch",
    "sn balance": "Snatch Balance",
    "snatch extension from deficit": "Snatch Pull from Deficit",
    "snatch grip deadlift": "Snatch Deadlift",
    "sn grip rdl": "Snatch Grip Romanian Deadlift",
    "block power snatch + oh sq": "Power Snatch from Blocks (knee)",
    "block snatch": "Block Snatch (knee)",
    "block snatch (above knee)": "Block Snatch (above knee)",
    "block clean (at knee)": "Block Clean (knee)",
    "block clean (above knee)": "Block Clean (above knee)",
    "mid grip deadlift": "Deadlift",
    "stiff leg deadlift (% of cl)": "Stiff-Leg Deadlift",
    "jerk from rack": "Jerk",
    "jerk dips": "Jerk Dip",
    "btn push press": "Push Press Behind the Neck",
    "block power clean + push press": "Power Clean",
    "clean & jerk": "Clean & Jerk",
    "close stance back squat": "Close-Stance Back Squat",
    "partial back squat (1/4 squat)": "Quarter Squat",
    "1+1/4 front squat": "1¼ Front Squat",
    "box jumps": "Box Jump",
    "broad jumps": "Broad Jump",
    "vertical jumps": "Vertical Jump",
    "jumping squats": "Jumping Squat",
    "bb rows": "Barbell Row",
    "pendlay rows": "Pendlay Row",
    "back extensions": "Back Extension",
    "bulgarian split squats": "Bulgarian Split Squat",
    # Accessories seeded by migration 0023 (DOG-2f) — names from the 646 Beginner Program
    "strict press": "Strict Press",
    "press": "Strict Press",
    "behind the neck press": "Behind the Neck Press",
    "snatch grip behind the neck press": "Snatch Grip Behind the Neck Press",
    "snatch push press": "Snatch Push Press",           # else the "snatch" substring rule catches it
    "good morning": "Good Morning",
    "good mornings": "Good Morning",
    "planks": "Plank",
    "walking lunges": "Walking Lunge",
    "bb step ups": "Step-Up",
    "delt flyes": "Rear Delt Fly",
    "deltoid flyes": "Rear Delt Fly",
    "db delt flyes": "Rear Delt Fly",
    "bent over db flyes": "Rear Delt Fly",
    "banded tricep extensions": "Triceps Extension",
    "db skull crushers": "Triceps Extension",
    "barbell squat jumps": "Jumping Squat",
    "bb squat jumps": "Jumping Squat",
    "bent over bb rows": "Barbell Row",
}
SUBSTRING_ALIASES: list[tuple[str, str]] = [
    # (needle in lowercased name, catalogue name) — first hit wins
    ("clean deadlift + clean + jerk", "Clean & Jerk"),
    ("power clean + jerk", "Clean & Jerk"),
    ("power clean + fsq + jerk", "Clean & Jerk"),
    ("power clean + power jerk", "Clean & Jerk"),
    ("clean + jerk", "Clean & Jerk"),
    ("clean + fsq + jerk", "Clean & Jerk"),
    ("muscle snatch", "Muscle Snatch"),
    ("power snatch from deficit", "Power Snatch from Deficit"),
    ("power snatch", "Power Snatch"),
    ("power sn ", "Power Snatch"),
    ("snatch extension + snatch", "Snatch"),
    ("snatch pull to hip + snatch", "Snatch"),
    ("snatch pull to hip", "Snatch Pull to Hip"),      # before "extension": the first movement names a complex
    ("sn pull to hip", "Snatch Pull to Hip"),
    ("snatch extension", "Snatch Extension"),
    ("sn extension", "Snatch Extension"),
    ("snatch deadlift", "Snatch Deadlift"),
    ("snatch from deficit", "Snatch from Deficit"),
    ("snatch pull", "Snatch Pull"),                     # "Tempo Snatch Pull" was stored as Snatch
    ("snatch push press", "Snatch Push Press"),
    ("pressing snatch balance", "Pressing Snatch Balance"),
    ("snatch balance", "Snatch Balance"),             # "Hang Snatch Balance" was stored as Snatch
    ("shrug", "Shrug"),
    ("snatch", "Snatch"),
    ("power clean from deficit", "Power Clean from Deficit"),
    ("power clean", "Power Clean"),
    ("clean extension", "Clean Extension"),
    ("cl extension", "Clean Extension"),
    ("clean pull to hip", "Clean Pull to Hip"),
    ("clean deadlift", "Clean Deadlift"),
    ("clean from deficit", "Clean from Deficit"),
    ("clean & jerk", "Clean & Jerk"),                  # "Hang Clean & Jerk" was stored as Clean
    ("clean and jerk", "Clean & Jerk"),
    ("clean pull", "Clean Pull"),                       # "Clean Pull to Contact Point" was stored as Clean
    ("clean", "Clean"),
    ("power jerk", "Power Jerk"),
    ("jerk", "Jerk"),
    ("push press", "Push Press"),
    ("back squat", "Back Squat"),
    ("front squat", "Front Squat"),
]


# A hang derivative that is not the lift itself: "Hang Clean Deadlift", "Hang Snatch
# Balance", "Snatch Grip Hang Shrug", "Hang Clean & Jerk" keep their own mapping.
HANG_NOT_THE_LIFT = ("pull", "extension", "high", "deadlift", "rdl", "balance", "shrug", "jerk", "press", "squat")


def hang_variant_for(name: str) -> str | None:
    """A hang snatch / clean → its catalogue row by height, before the substring
    rules collapse it onto the full lift ("Hang Clean (below knee)" was stored as
    Clean, "Hang Power Snatch" as Power Snatch). The catalogue has the hang
    power lifts above the knee only, so a below-knee power hang falls through."""
    n = name.lower()
    if "hang" not in n or "+" in n or "muscle" in n or any(w in n for w in HANG_NOT_THE_LIFT):
        return None
    lift = "Snatch" if "snatch" in n else ("Clean" if "clean" in n else None)
    if lift is None:
        return None
    below = "below knee" in n or "below the knee" in n
    if "power" in n:
        return None if below else f"Hang Power {lift} (above knee)"
    return f"Hang {lift} ({'below' if below else 'above'} knee)"


def catalogue_name_for(name: str) -> str | None:
    n = name.lower().strip()
    if n in EXACT_ALIASES:
        return EXACT_ALIASES[n]
    hang = hang_variant_for(n)
    if hang:
        return hang
    for needle, target in SUBSTRING_ALIASES:
        if needle in n:
            return target
    return None


def all_alias_targets() -> set[str]:
    """Every catalogue name an alias rule can return (None targets excluded)."""
    targets = set(EXACT_ALIASES.values()) | {t for _, t in SUBSTRING_ALIASES}
    targets |= {t for _, t in MD_ALIASES if t}
    for lift in ("Snatch", "Clean"):
        targets |= {f"Hang Power {lift} (above knee)", f"Hang {lift} (above knee)", f"Hang {lift} (below knee)"}
    return targets
