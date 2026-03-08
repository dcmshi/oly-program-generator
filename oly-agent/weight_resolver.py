# oly-agent/weight_resolver.py
"""
Post-generation resolution: maps LLM output fields to DB-ready values.

The LLM outputs: exercise_name, intensity_pct, intensity_reference, source_principle_ids
The DB needs:    exercise_id, absolute_weight_kg, source_chunk_ids

These functions bridge that gap.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.exercise_mapping import EXERCISE_NAME_TO_INTENSITY_REF  # noqa: F401 (re-exported)

logger = logging.getLogger(__name__)


def build_maxes_dict(db_maxes: list[dict]) -> dict[str, float]:
    """Convert DB max rows into a dict keyed by intensity_reference.

    Input: rows from ASSESS step query:
        [{"name": "Snatch", "weight_kg": 100.0}, ...]

    Output: {"snatch": 100.0, "back_squat": 160.0, ...}

    Exercises not in the explicit mapping use normalized snake_case names.
    """
    maxes = {}
    for row in db_maxes:
        name = row["name"]
        ref = EXERCISE_NAME_TO_INTENSITY_REF.get(name)
        if ref is None:
            ref = name.lower().replace(" ", "_").replace("&", "and")
            logger.debug(f"No explicit mapping for '{name}', using '{ref}'")
        maxes[ref] = float(row["weight_kg"])
    return maxes


def resolve_exercise_ids(
    session_exercises: list[dict],
    exercise_lookup: dict[str, int],
) -> list[dict]:
    """Resolve exercise_name -> exercise_id using a pre-loaded lookup.

    Args:
        session_exercises: LLM-generated exercise list
        exercise_lookup: {exercise_name_lower: exercise_id} from DB

    The lookup is built once at agent startup:
        SELECT id, name FROM exercises;
        exercise_lookup = {name.lower(): id for id, name in rows}
    """
    for ex in session_exercises:
        name = ex.get("exercise_name", "")
        ex_id = exercise_lookup.get(name.lower())
        if ex_id:
            ex["exercise_id"] = ex_id
        else:
            logger.warning(f"Could not resolve exercise_id for '{name}'")
            ex["exercise_id"] = None
    return session_exercises


def resolve_weights(
    session_exercises: list[dict],
    maxes: dict[str, float],
) -> list[dict]:
    """Convert intensity_pct + intensity_reference to absolute_weight_kg.

    Rounds to nearest 0.5kg (standard plate increment).

    Maxes dict is keyed by intensity_reference:
        {"snatch": 100.0, "clean_and_jerk": 125.0, "back_squat": 160.0, ...}
    """
    for ex in session_exercises:
        ref = ex.get("intensity_reference", "")
        pct = ex.get("intensity_pct")
        if ref and pct and ref in maxes:
            raw_kg = maxes[ref] * (pct / 100)
            ex["absolute_weight_kg"] = round(raw_kg * 2) / 2
        else:
            ex["absolute_weight_kg"] = None
            if ref and ref not in maxes:
                logger.warning(
                    f"No max found for intensity_reference='{ref}' "
                    f"(exercise: {ex.get('exercise_name')})"
                )
    return session_exercises


def attach_source_chunk_ids(
    session_exercises: list[dict],
    retrieval_context: dict,
) -> list[dict]:
    """Attach source_chunk_ids from the retrieval context to each exercise.

    The LLM doesn't know chunk IDs — it works with text content.
    We attach chunk IDs based on which retrieval path provided context:
    - Fault-correction exercises -> fault_correction chunk IDs
    - All exercises -> programming_rationale chunk IDs

    This is approximate but sufficient for traceability.
    """
    rationale_ids = [
        c["id"] for c in retrieval_context.get("programming_rationale", []) if "id" in c
    ]
    fault_ids = [
        c["id"] for c in retrieval_context.get("fault_correction_chunks", []) if "id" in c
    ]

    for ex in session_exercises:
        chunk_ids = []
        rationale = ex.get("selection_rationale", "").lower()
        if any(kw in rationale for kw in ("fault", "address", "correct", "fix")):
            chunk_ids.extend(fault_ids)
        chunk_ids.extend(rationale_ids)
        ex["source_chunk_ids"] = list(set(chunk_ids))

    return session_exercises
