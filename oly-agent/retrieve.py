# oly-agent/retrieve.py
"""
Step 3: RETRIEVE — Pull knowledge for exercise selection.

Three retrieval paths:
  A) Fault-based exercise lookup (structured DB query)
  B) Template references (published program templates)
  C) Contextual reasoning (vector similarity search)

Also loads available exercises and substitution mappings.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.constants import VECTOR_SEARCH_DEFAULT_TOP_K, VECTOR_SEARCH_MIN_SIMILARITY
from shared.db import fetch_all
from shared.prilepin import get_prilepin_zone, get_prilepin_data
from models import AthleteContext, ProgramPlan, RetrievalContext

logger = logging.getLogger(__name__)


def retrieve(
    athlete_context: AthleteContext,
    plan: ProgramPlan,
    conn,
    vector_loader=None,
    settings=None,
) -> RetrievalContext:
    """Gather all knowledge the LLM needs to generate sessions.

    Args:
        athlete_context: Output of assess()
        plan: Output of plan()
        conn: Open psycopg2 connection
        vector_loader: VectorLoader instance from oly-ingestion (optional).
                       If None, vector search is skipped — useful for testing.
        settings: Optional settings object. Reads ``vector_search_top_k`` (default 5).
    """
    top_k = getattr(settings, "vector_search_top_k", VECTOR_SEARCH_DEFAULT_TOP_K)
    # ── Path A: Fault-based exercise lookup ──────────────────
    fault_exercises: dict[str, list[dict]] = {}
    if athlete_context.technical_faults:
        for family in ("snatch", "clean"):
            rows = fetch_all(
                conn,
                """
                SELECT e.name, e.category, e.primary_purpose,
                       e.faults_addressed, e.complexity_level,
                       e.typical_intensity_low, e.typical_intensity_high,
                       e.typical_sets_low, e.typical_sets_high,
                       e.typical_reps_low, e.typical_reps_high
                FROM exercises e
                WHERE e.faults_addressed && %s
                  AND e.complexity_level <= %s
                  AND e.movement_family = %s
                ORDER BY e.complexity_level,
                         array_length(e.faults_addressed, 1) DESC
                LIMIT 10
                """,
                (athlete_context.technical_faults, plan.max_complexity, family),
            )
            if rows:
                fault_exercises[family] = rows

    # ── Path B: Template references ───────────────────────────
    template_references = fetch_all(
        conn,
        """
        SELECT name, program_structure, notes
        FROM program_templates
        WHERE athlete_level IN (%s, 'any')
          AND phases_included @> ARRAY[%s]::training_phase[]
          AND sessions_per_week BETWEEN %s AND %s
        ORDER BY source_id
        LIMIT 3
        """,
        (
            athlete_context.level,
            plan.phase,
            athlete_context.sessions_per_week - 1,
            athlete_context.sessions_per_week + 1,
        ),
    )

    # ── Path C: Vector search ─────────────────────────────────
    programming_rationale: list[dict] = []
    fault_correction_chunks: list[dict] = []

    if vector_loader is not None:
        seen_chunk_ids: set[int] = set()

        # Build reusable context strings for richer query construction
        level_context = f"{athlete_context.level} athlete"
        lift_emphasis = athlete_context.athlete.get("lift_emphasis") or "balanced"
        strength_limiters = athlete_context.athlete.get("strength_limiters") or []

        faults_context = (
            f", addressing faults: {', '.join(athlete_context.technical_faults)}"
            if athlete_context.technical_faults else ""
        )
        emphasis_context = (
            f", {lift_emphasis.replace('_', ' ')} lift focus"
            if lift_emphasis != "balanced" else ""
        )
        limiters_context = (
            f", addressing strength limiters: "
            f"{', '.join(s.replace('_', ' ') for s in strength_limiters)}"
            if strength_limiters else ""
        )

        # Session template queries — enriched with lift emphasis + strength limiters
        for session_tmpl in plan.session_templates[:2]:
            try:
                chunks = vector_loader.similarity_search(
                    query=(
                        f"exercise selection for {session_tmpl.primary_movement} "
                        f"during {plan.phase} phase, {level_context}"
                        f"{faults_context}{emphasis_context}{limiters_context}"
                    ),
                    top_k=top_k,
                    chunk_types=["programming_rationale", "periodization"],
                    min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                )
                for c in chunks:
                    if c.get("id") not in seen_chunk_ids:
                        seen_chunk_ids.add(c["id"])
                        programming_rationale.append(c)
            except Exception as e:
                logger.warning(f"Vector search failed for session template: {e}")

        # Fault correction — search ALL faults, not just the first two
        if athlete_context.technical_faults:
            fault_seen: set[int] = set()
            for fault in athlete_context.technical_faults:
                try:
                    chunks = vector_loader.similarity_search(
                        query=f"correcting {fault} in weightlifting, {level_context}",
                        top_k=top_k,
                        chunk_types=["fault_correction"],
                        min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                    )
                    for c in chunks:
                        if c.get("id") not in fault_seen:
                            fault_seen.add(c["id"])
                            fault_correction_chunks.append(c)
                except Exception as e:
                    logger.warning(f"Vector search failed for fault '{fault}': {e}")

        # Strength limiter searches — pull targeted programming content per limiter
        for limiter in strength_limiters:
            limiter_term = limiter.replace("_limited", "").replace("_", " ").strip()
            try:
                chunks = vector_loader.similarity_search(
                    query=(
                        f"{limiter_term} strength development "
                        f"for {level_context} weightlifter"
                    ),
                    top_k=top_k,
                    chunk_types=["programming_rationale", "periodization", "methodology"],
                    min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                )
                for c in chunks:
                    if c.get("id") not in seen_chunk_ids:
                        seen_chunk_ids.add(c["id"])
                        programming_rationale.append(c)
            except Exception as e:
                logger.warning(f"Vector search failed for limiter '{limiter}': {e}")
    else:
        logger.info("No vector_loader provided — skipping similarity search")

    # ── Available exercises ───────────────────────────────────
    available_exercises = fetch_all(
        conn,
        """
        SELECT e.id, e.name, e.movement_family, e.category, e.primary_purpose,
               e.complexity_level, e.faults_addressed,
               e.typical_intensity_low, e.typical_intensity_high,
               e.typical_sets_low, e.typical_sets_high,
               e.typical_reps_low, e.typical_reps_high
        FROM exercises e
        WHERE e.complexity_level <= %s
        ORDER BY e.movement_family, e.complexity_level
        """,
        (plan.max_complexity,),
    )

    # ── Substitutions (for injured athletes) ──────────────────
    available_substitutions: dict[str, list] = {}
    if athlete_context.injuries:
        sub_rows = fetch_all(
            conn,
            """
            SELECT es.exercise_id, e_orig.name AS original_name,
                   e_sub.name AS substitute_name, e_sub.primary_purpose,
                   es.substitution_context, es.notes
            FROM exercise_substitutions es
            JOIN exercises e_orig ON es.exercise_id = e_orig.id
            JOIN exercises e_sub  ON es.substitute_exercise_id = e_sub.id
            WHERE es.substitution_context IN ('injury_modification', 'equipment_limitation')
            """,
        )
        for row in sub_rows:
            orig = row["original_name"]
            available_substitutions.setdefault(orig, []).append(row)

    # ── Prilepin targets for this intensity range ─────────────
    prilepin_targets: dict[str, dict] = {}
    if plan.weekly_targets:
        # Use the first non-deload week as representative
        working_weeks = [t for t in plan.weekly_targets if not t.is_deload]
        ref = working_weeks[0] if working_weeks else plan.weekly_targets[0]
        midpoint = (ref.intensity_floor + ref.intensity_ceiling) / 2
        zone_key = get_prilepin_zone(midpoint)
        if zone_key:
            data = get_prilepin_data(zone_key)
            if data:
                prilepin_targets[zone_key] = data

    logger.info(
        f"Retrieved: {sum(len(v) for v in fault_exercises.values())} fault exercises, "
        f"{len(template_references)} template refs, "
        f"{len(programming_rationale)} rationale chunks, "
        f"{len(fault_correction_chunks)} fault chunks, "
        f"{len(available_exercises)} available exercises"
    )

    return RetrievalContext(
        fault_exercises=fault_exercises,
        template_references=template_references,
        programming_rationale=programming_rationale,
        fault_correction_chunks=fault_correction_chunks,
        available_substitutions=available_substitutions,
        active_principles=plan.active_principles,
        prilepin_targets=prilepin_targets,
        available_exercises=available_exercises,
    )
