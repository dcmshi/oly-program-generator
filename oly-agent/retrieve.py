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

from collections import Counter

from models import AthleteContext, ProgramPlan, RetrievalContext, SessionTemplate, WeekTarget

from shared.constants import (
    HYBRID_SEARCH_ENABLED,
    INTENSITY_BAND_WIDTH_PCT,
    MAX_CHUNKS_PER_SOURCE_IN_CONTEXT,
    MAX_CONTEXT_CHUNKS,
    MAX_FAULT_CHUNKS_IN_CONTEXT,
    VECTOR_SEARCH_DEFAULT_TOP_K,
    VECTOR_SEARCH_MIN_SIMILARITY,
)
from shared.db import fetch_all
from shared.prilepin import get_prilepin_data, get_prilepin_zone

logger = logging.getLogger(__name__)


# ── Per-session retrieval (RAG-H4) ────────────────────────────────

def _humanize(token: str) -> str:
    """`early_arm_bend` → `early arm bend` — underscores embed as noise."""
    return str(token).replace("_", " ").strip()


def build_session_query(
    athlete_context: AthleteContext,
    plan: ProgramPlan,
    session_template: SessionTemplate,
    week_target: WeekTarget,
) -> str:
    """The retrieval query for ONE session: movement, supporting work, phase,
    intensity band (rounded to INTENSITY_BAND_WIDTH_PCT so weeks in the same band
    share a cache entry), level, deload flag, faults, emphasis and limiters."""
    lo = int(week_target.intensity_floor // INTENSITY_BAND_WIDTH_PCT * INTENSITY_BAND_WIDTH_PCT)
    hi = int(-(-week_target.intensity_ceiling // INTENSITY_BAND_WIDTH_PCT) * INTENSITY_BAND_WIDTH_PCT)
    secondary = ", ".join(_humanize(m) for m in session_template.secondary_movements) or "no supporting work"
    parts = [
        f"exercise selection for a {_humanize(session_template.primary_movement)} session "
        f"with {secondary} support",
        f"during the {_humanize(plan.phase)} phase at {lo}-{hi}% intensity",
        f"{athlete_context.level} athlete",
    ]
    if week_target.is_deload:
        parts.append("deload week")
    if athlete_context.technical_faults:
        parts.append("addressing faults: " + ", ".join(_humanize(f) for f in athlete_context.technical_faults))
    lift_emphasis = athlete_context.athlete.get("lift_emphasis") or "balanced"
    if lift_emphasis != "balanced":
        parts.append(f"{_humanize(lift_emphasis)} lift focus")
    limiters = athlete_context.athlete.get("strength_limiters") or []
    if limiters:
        parts.append("addressing strength limiters: " + ", ".join(_humanize(s) for s in limiters))
    return ", ".join(parts)


def _rank(chunk: dict) -> float:
    return float(chunk.get("score") or chunk.get("similarity") or 0.0)


def compose_session_context(
    session_chunks: list[dict],
    fault_chunks: list[dict],
    has_faults: bool,
    max_chunks: int = MAX_CONTEXT_CHUNKS,
    max_fault_chunks: int = MAX_FAULT_CHUNKS_IN_CONTEXT,
    per_source_cap: int = MAX_CHUNKS_PER_SOURCE_IN_CONTEXT,
) -> list[dict]:
    """Pick the chunks one session prompt will show.

    Up to ``max_fault_chunks`` fault chunks first (only when the athlete has
    faults), round-robin across faults so one fault can't take every slot, then
    the session's own chunks by score; deduped by id; at most ``per_source_cap``
    from any one source so a single book can't fill the context.
    """
    chosen: list[dict] = []
    seen: set = set()
    per_source: Counter = Counter()

    def _take(c: dict) -> bool:
        cid, sid = c.get("id"), c.get("source_id")
        if cid in seen or per_source[sid] >= per_source_cap:
            return False
        seen.add(cid)
        per_source[sid] += 1
        chosen.append(c)
        return True

    if has_faults and fault_chunks:
        by_fault: dict = {}
        for c in fault_chunks:
            by_fault.setdefault(c.get("fault", "_"), []).append(c)
        queues = [sorted(v, key=_rank, reverse=True) for v in by_fault.values()]
        taken = 0
        while taken < max_fault_chunks and any(queues):
            for q in queues:
                if taken >= max_fault_chunks:
                    break
                while q:
                    if _take(q.pop(0)):
                        taken += 1
                        break

    for c in sorted(session_chunks, key=_rank, reverse=True):
        if len(chosen) >= max_chunks:
            break
        _take(c)

    return chosen[:max_chunks]


def retrieve_session_context(
    vector_loader,
    athlete_context: AthleteContext,
    plan: ProgramPlan,
    session_template: SessionTemplate,
    week_target: WeekTarget,
    retrieval_context: RetrievalContext,
    top_k: int | None = None,
    cache: dict | None = None,
) -> list[dict]:
    """Retrieve + compose the knowledge context for ONE session (RAG-H4).

    The query is cached per program (``cache`` keyed by query string), so a
    4-week × 4-day program issues one search per distinct
    (template, phase, intensity band) rather than sixteen. Returns [] when no
    vector_loader is available or the search fails — generation continues
    without knowledge context, as before.
    """
    if vector_loader is None:
        return []
    cache = cache if cache is not None else {}
    top_k = top_k or VECTOR_SEARCH_DEFAULT_TOP_K
    query = build_session_query(athlete_context, plan, session_template, week_target)
    if query not in cache:
        try:
            cache[query] = vector_loader.similarity_search(
                query=query,
                top_k=top_k * 2,  # headroom for the per-source cap + fault dedupe
                preferred_chunk_types=["programming_rationale", "periodization"],
                min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                hybrid=HYBRID_SEARCH_ENABLED,
            )
        except Exception as e:
            logger.warning(f"Vector search failed for session '{session_template.label}': {e}")
            cache[query] = []
    composed = compose_session_context(
        cache[query],
        retrieval_context.fault_correction_chunks,
        has_faults=bool(athlete_context.technical_faults),
    )
    # copies, so the cached rows stay pristine; the query travels with each
    # chunk into generation_log.retrieval_set (RAG-M5)
    return [{**c, "session_query": query} for c in composed]


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
        # jerk + squat too: the selectable jerk fault (dip_forward) is addressed
        # only by jerk-family exercises, and squat correctives never surfaced —
        # the prompt then showed "no exercises" and disabled Check 8 (audit5-M3)
        for family in ("snatch", "clean", "jerk", "squat"):
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
        strength_limiters = athlete_context.athlete.get("strength_limiters") or []

        # Session-template queries no longer run here: retrieval for the
        # session prompt is per session (retrieve_session_context), keyed by
        # template + phase + intensity band. The old program-level pass queried
        # only session_templates[:2] and every prompt reused the same four
        # snippets (RAG-H4). `programming_rationale` now carries the
        # limiter-driven chunks only.

        # Fault correction — search ALL faults, not just the first two
        if athlete_context.technical_faults:
            fault_seen: set[int] = set()
            for fault in athlete_context.technical_faults:
                try:
                    chunks = vector_loader.similarity_search(
                        query=f"correcting {fault} in weightlifting, {level_context}",
                        top_k=top_k,
                        preferred_chunk_types=["fault_correction"],
                        min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                        hybrid=HYBRID_SEARCH_ENABLED,
                    )
                    for c in chunks:
                        if c.get("id") not in fault_seen:
                            fault_seen.add(c["id"])
                            # remember which fault surfaced it so the session
                            # context can round-robin across faults (RAG-H4)
                            fault_correction_chunks.append({**c, "fault": fault})
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
                    # `methodology` dropped: the inference never assigns it (RAG-H2)
                    preferred_chunk_types=["programming_rationale", "periodization"],
                    min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                    hybrid=HYBRID_SEARCH_ENABLED,
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
