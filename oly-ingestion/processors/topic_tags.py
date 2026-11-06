# processors/topic_tags.py
"""
Keyword → topic tagging for chunks (Pass 1 of the chunker's topic tags;
CLEAN-4: split out of processors/chunker.py, which re-exports these names).
After editing KEYWORD_TO_TOPIC run `retag_chunks.py` so stored chunks follow.
"""

import re

KEYWORD_TO_TOPIC: dict[str, list[str]] = {
    # Periodization
    "accumulation": ["accumulation_phase"],
    "intensification": ["intensification_phase"],
    "realization": ["realization_phase", "competition_peaking"],
    "peaking": ["competition_peaking"],
    "taper": ["competition_peaking", "volume_management"],
    "deload": ["deload_strategy", "recovery_protocols"],
    "overtraining": ["overtraining_detection", "fatigue_management"],
    "supercompensation": ["adaptation_theory"],
    "mesocycle": ["periodization_models", "annual_planning"],
    "macrocycle": ["periodization_models", "annual_planning"],
    "microcycle": ["periodization_models"],
    "block periodization": ["periodization_models"],
    "linear periodization": ["periodization_models"],
    "undulating": ["periodization_models"],

    # Lifts
    "snatch": ["snatch_technique", "snatch_programming"],
    "clean": ["clean_technique", "clean_programming"],
    "jerk": ["jerk_technique", "jerk_programming"],
    "front squat": ["squat_programming"],
    "back squat": ["squat_programming"],
    "snatch pull": ["pull_programming", "snatch_programming"],
    "clean pull": ["pull_programming", "clean_programming"],

    # Programming concepts
    "prilepin": ["volume_management", "intensity_prescription"],
    "volume": ["volume_management"],
    "intensity": ["intensity_prescription"],
    "frequency": ["periodization_models"],
    "1rm": ["intensity_prescription", "load_progression"],
    "rpe": ["intensity_prescription", "fatigue_management"],

    # Athlete management
    "beginner": ["beginner_development"],
    "novice": ["beginner_development"],
    "intermediate": ["intermediate_development"],
    "advanced": ["advanced_development"],
    "training age": ["beginner_development", "load_progression"],
    "fault": ["fault_correction"],
    "correction": ["fault_correction"],
    "error": ["fault_correction"],
    "miss": ["fault_correction"],

    # Competition
    "competition": ["competition_strategy"],
    "attempt selection": ["competition_strategy"],
    "attempt": ["competition_strategy"],
    "opener": ["competition_strategy"],
    "warm-up": ["competition_strategy"],
    "weight class": ["weight_class_management"],
    "make weight": ["weight_class_management"],

    # Peaking / competition prep
    "weeks out": ["competition_peaking"],
    "pre-competition": ["competition_peaking"],
    "competition preparation": ["competition_peaking"],
    "competition prep": ["competition_peaking"],
    "peak": ["competition_peaking"],
    "reduce volume": ["volume_management", "competition_peaking"],
    "volume reduction": ["volume_management", "competition_peaking"],

    # Programming / periodization
    "periodization": ["periodization_models"],
    "program design": ["periodization_models"],
    "programming": ["periodization_models"],
    "training block": ["periodization_models"],
    "training cycle": ["periodization_models", "annual_planning"],
    "annual plan": ["annual_planning"],
    "max effort": ["intensity_prescription"],
    "maximal": ["intensity_prescription"],
    "load progression": ["load_progression"],
    "progression": ["load_progression"],

    # Lifts (additional)
    "overhead squat": ["snatch_technique", "squat_programming"],
    "hang snatch": ["snatch_programming"],
    "hang clean": ["clean_programming"],
    "pull": ["pull_programming"],
    "push press": ["jerk_programming"],

    # Recovery
    "recovery": ["recovery_protocols"],
    "adaptation": ["adaptation_theory"],
    "fatigue": ["fatigue_management"],
    "sleep": ["recovery_protocols"],
    "nutrition": ["nutrition_bodyweight"],

    # Soviet / Eastern-bloc terminology (Laputin, Medvedev, Verkhoshansky)
    "sporting form": ["competition_peaking", "periodization_models"],
    "sports form": ["competition_peaking", "periodization_models"],
    "trainability": ["adaptation_theory", "load_progression"],
    "training load": ["volume_management", "fatigue_management"],
    "educational-training": ["periodization_models"],
    "training preparation": ["periodization_models"],
    "special preparatory": ["periodization_models"],
    "general preparatory": ["periodization_models"],
    "preparatory period": ["periodization_models", "accumulation_phase"],
    "competitive period": ["competition_peaking", "periodization_models"],
    "transitional period": ["deload_strategy", "recovery_protocols"],
    "recuperation": ["recovery_protocols"],
    "rehabilitation": ["recovery_protocols"],
    "pharmacological": ["recovery_protocols"],
    "food supplement": ["nutrition_bodyweight", "recovery_protocols"],
    "bioenergetics": ["adaptation_theory"],
    "triathlon": ["competition_strategy"],   # old Soviet 3-lift format (press + snatch + C&J)
    "biathlon": ["competition_strategy"],    # modern 2-lift format (snatch + C&J)
    "tonnage": ["volume_management"],
    "number of lifts": ["volume_management"],
    "lifting total": ["competition_strategy"],
    "total result": ["competition_strategy"],
    "world record": ["competition_strategy"],
    "national team": ["competition_strategy"],
    "master of sport": ["advanced_development"],
    "candidate master": ["intermediate_development"],

    # Medvedev exercise abbreviations (A Program of Multi-Year Training)
    # These appear in workout listings as abbreviated notation
    "p. sn": ["snatch_programming"],          # Power Snatch
    "cl. sn": ["snatch_programming"],          # Classic Snatch
    "sn. pu": ["pull_programming", "snatch_programming"],  # Snatch Pull
    "p. cl": ["clean_programming"],            # Power Clean
    "sq. cl": ["clean_programming"],           # Squat Clean
    "cl. pu": ["pull_programming", "clean_programming"],   # Clean Pull
    "pu. j": ["jerk_programming"],             # Push Jerk
    "c + j": ["clean_programming", "jerk_programming"],    # Clean + Jerk
    "fr. sq": ["squat_programming"],           # Front Squat
    "b. sq": ["squat_programming"],            # Back Squat
    "ov. sq": ["snatch_technique", "squat_programming"],   # Overhead Squat
    "be. pr": ["jerk_programming"],            # Behind Press
    "hyper": ["squat_programming"],            # Hyperextension
    "multi-year": ["beginner_development", "periodization_models"],
    "year of training": ["beginner_development", "periodization_models", "load_progression"],
    "class iii": ["beginner_development"],
    "class ii": ["intermediate_development"],
    "class i": ["intermediate_development"],
    "candidate master of sport": ["advanced_development"],
    "loading scheme": ["volume_management", "intensity_prescription"],
    "exercise classification": ["periodization_models"],
    "coordination structure": ["snatch_technique", "clean_technique"],
    # RPE / auto-regulation
    "rate of perceived exertion": ["intensity_prescription", "fatigue_management"],
    "rpe scale": ["intensity_prescription", "fatigue_management"],
    "rpe-based": ["intensity_prescription", "fatigue_management"],
    "autoregulation": ["intensity_prescription", "fatigue_management"],
    "auto-regulation": ["intensity_prescription", "fatigue_management"],
    # Rest and recovery timing
    "rest between sets": ["recovery_protocols", "periodization_models"],
    "inter-set rest": ["recovery_protocols"],
    "rest interval": ["recovery_protocols"],
    "between sets": ["recovery_protocols"],
    # Exercise complexity and motor learning
    "technical difficulty": ["exercise_selection_rationale"],
    "motor learning": ["beginner_development", "exercise_selection_rationale"],
    "movement pattern": ["exercise_selection_rationale"],
    "skill acquisition": ["beginner_development", "exercise_selection_rationale"],
    "learning curve": ["beginner_development", "exercise_selection_rationale"],
    "technical proficiency": ["exercise_selection_rationale"],
    # Accessory / supplemental exercise selection
    "accessory": ["exercise_selection_rationale"],
    "supplemental": ["exercise_selection_rationale"],
    "auxiliary": ["exercise_selection_rationale"],
    "good morning": ["exercise_selection_rationale", "pull_programming"],
    "nordic": ["exercise_selection_rationale"],
    "glute-ham": ["exercise_selection_rationale"],

    # Israetel / hypertrophy volume landmarks (Scientific Principles of Hypertrophy Training)
    "hypertrophy": ["exercise_selection_rationale", "volume_management"],
    "muscle growth": ["exercise_selection_rationale", "volume_management"],
    "muscle gain": ["exercise_selection_rationale", "volume_management"],
    "mev": ["exercise_selection_rationale", "volume_management"],
    "mav": ["exercise_selection_rationale", "volume_management"],
    "mrv": ["exercise_selection_rationale", "volume_management"],
    "minimum effective volume": ["exercise_selection_rationale", "volume_management"],
    "maximum adaptive volume": ["exercise_selection_rationale", "volume_management"],
    "maximum recoverable volume": ["exercise_selection_rationale", "volume_management"],
    "hard sets": ["exercise_selection_rationale", "volume_management"],
    "reps in reserve": ["intensity_prescription", "fatigue_management"],
    "rir": ["intensity_prescription", "fatigue_management"],
    "sets per week": ["exercise_selection_rationale", "volume_management"],
    "weekly sets": ["exercise_selection_rationale", "volume_management"],
    "muscle group": ["exercise_selection_rationale"],
    "upper back": ["exercise_selection_rationale"],
    "hypertrophy block": ["exercise_selection_rationale", "periodization_models"],
    "overload": ["load_progression", "adaptation_theory"],
    "progressive overload": ["load_progression", "adaptation_theory"],

    # Starrett / mobility (Becoming a Supple Leopard)
    "mobility": ["fault_correction", "exercise_selection_rationale"],
    "range of motion": ["fault_correction", "exercise_selection_rationale"],
    "soft tissue": ["fault_correction", "recovery_protocols"],
    "joint restriction": ["fault_correction"],
    "hip flexor": ["fault_correction", "clean_technique"],
    "hip mobility": ["fault_correction", "clean_technique", "squat_programming"],
    "ankle mobility": ["fault_correction", "squat_programming"],
    "thoracic": ["fault_correction", "snatch_technique"],
    "thoracic mobility": ["fault_correction", "snatch_technique"],
    "overhead position": ["snatch_technique", "fault_correction"],
    "bracing": ["biomechanics", "exercise_selection_rationale"],
    "spinal mechanics": ["biomechanics"],
    "prehab": ["recovery_protocols", "exercise_selection_rationale"],
    "t-spine": ["fault_correction", "snatch_technique"],
    "hip capsule": ["fault_correction", "clean_technique"],
    "dorsiflexion": ["fault_correction", "squat_programming"],
    "internal rotation": ["fault_correction"],
    "external rotation": ["fault_correction"],
    "tissue quality": ["recovery_protocols", "fault_correction"],
    "lacrosse ball": ["recovery_protocols", "fault_correction"],
    "foam roll": ["recovery_protocols", "fault_correction"],
    "position": ["biomechanics", "exercise_selection_rationale"],

    # Dan John / GPP and loaded carries (Intervention / Can You Go)
    "loaded carry": ["exercise_selection_rationale"],
    "farmer": ["exercise_selection_rationale"],
    "farmer carry": ["exercise_selection_rationale"],
    "sled": ["exercise_selection_rationale"],
    "goblet squat": ["exercise_selection_rationale", "squat_programming"],
    "kettlebell": ["exercise_selection_rationale"],
    "swing": ["exercise_selection_rationale"],
    "hinge": ["exercise_selection_rationale", "pull_programming"],
    "hip hinge": ["exercise_selection_rationale", "pull_programming"],
    "fundamental movement": ["exercise_selection_rationale"],
    "big rocks": ["exercise_selection_rationale", "periodization_models"],
    "general physical preparation": ["exercise_selection_rationale", "periodization_models"],
    "gpp": ["exercise_selection_rationale", "periodization_models"],
    "off-season": ["periodization_models", "exercise_selection_rationale"],
    "conditioning": ["exercise_selection_rationale"],
    "carry": ["exercise_selection_rationale"],
    "push-up": ["exercise_selection_rationale"],
    "get-up": ["exercise_selection_rationale"],
    "turkish get": ["exercise_selection_rationale"],
    "park bench": ["periodization_models"],
    "bus bench": ["periodization_models"],
}


# Ensures chunk_type-derived topics are always present regardless of keyword coverage.
# When pipeline.py sets chunk_type via metadata, these baseline topics are guaranteed
# even if keyword_tag() misses them (sparse content, unusual phrasing, etc.).
CHUNK_TYPE_DEFAULT_TOPICS: dict[str, list[str]] = {
    "fault_correction":      ["fault_correction"],
    "biomechanics":          ["biomechanics"],
    "competition_strategy":  ["competition_strategy"],
    "recovery_adaptation":   ["recovery_protocols", "adaptation_theory"],
    "nutrition_bodyweight":  ["nutrition_bodyweight"],
    "periodization":         ["periodization_models"],
    "programming_rationale": ["periodization_models"],
}


# Word-boundary matchers, built once. Substring tests fired `rir` on "requiring",
# `miss` on "mission"/"permission", `peak` on "speaking", `mev` on "somever" —
# tagging chunks with topics they never discuss (RAG-M7). Multi-word keywords
# keep their internal spacing; a trailing `-` (e.g. "pre-competition") is fine
# because `\b` sits between a word char and a non-word char.
_KEYWORD_MATCHERS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE), topic_list)
    for keyword, topic_list in KEYWORD_TO_TOPIC.items()
]


def keyword_tag(content: str) -> set[str]:
    """Fast keyword-based topic tagging (Pass 1) — whole words only."""
    topics: set[str] = set()
    for matcher, topic_list in _KEYWORD_MATCHERS:
        if matcher.search(content):
            topics.update(topic_list)
    return topics
