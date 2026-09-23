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
import math
import sys
import threading
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
    MAX_SOURCE_SHARE_IN_PROGRAM,
    RERANK_ENABLED,
    RERANK_TOP_N,
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
    share a cache entry), level, sex / age-band qualifier (female, youth,
    masters only), deload flag, faults, emphasis and limiters."""
    from demographics import query_qualifiers
    lo = int(week_target.intensity_floor // INTENSITY_BAND_WIDTH_PCT * INTENSITY_BAND_WIDTH_PCT)
    hi = int(-(-week_target.intensity_ceiling // INTENSITY_BAND_WIDTH_PCT) * INTENSITY_BAND_WIDTH_PCT)
    secondary = ", ".join(_humanize(m) for m in session_template.secondary_movements) or "no supporting work"
    parts = [
        f"exercise selection for a {_humanize(session_template.primary_movement)} session "
        f"with {secondary} support",
        f"during the {_humanize(plan.phase)} phase at {lo}-{hi}% intensity",
        f"{athlete_context.level} athlete",
    ]
    # AUD-5: "female athlete" / "youth athlete" / "masters athlete" only — a
    # senior, junior, male or unknown athlete's query is unchanged.
    parts.extend(query_qualifiers(athlete_context))
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


def build_fault_query(fault: str, level: str) -> str:
    """The retrieval query for one technical fault (shared with the eval harness).

    Fault ids are snake_case UI values (`early_arm_bend`); they are humanised
    like every other token so the embedding sees words, not identifiers (RAG-L1).
    """
    return f"correcting {_humanize(fault)} in weightlifting, {level} athlete"


def build_limiter_query(limiter: str, level: str) -> str:
    """The retrieval query for one strength limiter (`squat_limited` → `squat`)."""
    term = limiter.replace("_limited", "").replace("_", " ").strip()
    return f"{term} strength development for {level} athlete weightlifter"


SESSION_PREFERRED_TYPES = ["programming_rationale", "periodization"]
FAULT_PREFERRED_TYPES = ["fault_correction"]


def _rank(chunk: dict) -> float:
    """Sort key within a group: the reranker's order when a rerank ran
    (rerank.py sets `rerank_score`), else the fused / boosted retrieval score."""
    if chunk.get("rerank_score") is not None:
        return 1.0 + float(chunk["rerank_score"])
    return float(chunk.get("score") or chunk.get("similarity") or 0.0)


# Keys under which per-program objects ride in the orchestrator's per-program
# query cache (retrieve_session_context(cache=…)). Neither is a query string.
CONTEXT_STATE_KEY = "\x00context_diversity_state"
RERANKER_KEY = "\x00reranker"


class ContextDiversityState:
    """Program-level memory of what the composed contexts already showed (AUD-3).

    One instance per program, handed to every compose_session_context call of
    that program: per-chunk use counts drive the fault-chunk rotation, per-source
    slot counts drive the program-level source cap. Thread-safe: compose holds
    `lock` for a whole session, so each session reads and updates one consistent
    state. The orchestrator composes on the main thread in (week, day) order
    (the AUD-4 prefetch), which also makes the rotation deterministic."""

    def __init__(self):
        self.lock = threading.RLock()
        self.chunk_uses: Counter = Counter()
        self.source_slots: Counter = Counter()
        self.slots = 0
        self.sessions = 0

    def record(self, chosen: list[dict]) -> None:
        with self.lock:
            self.sessions += 1
            for c in chosen:
                self.chunk_uses[c.get("id")] += 1
                self.source_slots[c.get("source_id")] += 1
                self.slots += 1


_STATE_CREATE_LOCK = threading.Lock()


def context_state_for(cache: dict | None) -> ContextDiversityState | None:
    """The program's diversity state, stored in its query cache on first use."""
    if cache is None:
        return None
    with _STATE_CREATE_LOCK:
        state = cache.get(CONTEXT_STATE_KEY)
        if state is None:
            state = cache[CONTEXT_STATE_KEY] = ContextDiversityState()
    return state


def compose_session_context(
    session_chunks: list[dict],
    fault_chunks: list[dict],
    has_faults: bool,
    max_chunks: int = MAX_CONTEXT_CHUNKS,
    max_fault_chunks: int = MAX_FAULT_CHUNKS_IN_CONTEXT,
    per_source_cap: int = MAX_CHUNKS_PER_SOURCE_IN_CONTEXT,
    state: ContextDiversityState | None = None,
    program_source_share: float = MAX_SOURCE_SHARE_IN_PROGRAM,
) -> list[dict]:
    """Pick the chunks one session prompt will show.

    Up to ``max_fault_chunks`` fault chunks first (only when the athlete has
    faults), round-robin across faults so one fault can't take every slot, then
    the session's own chunks by score; deduped by id; at most ``per_source_cap``
    from any one source *within each group* so a single book can't fill the
    context. The cap is per group on purpose: the fault chunks and the best
    session chunks both tend to come from the one book that covers exercises
    in depth, and a shared cap let two fault chunks from it leave every
    "snatch variations" session with no session context at all (DOG-1).

    With a program-level ``state`` (AUD-3):
    - each fault's ranked list is walked least-shown-first (ties by rank), so the
      fault slots rotate through that fault's chunks across sessions instead of
      repeating its top two in every prompt;
    - a source already holding ``program_source_share`` of the program's slots
      (this session included) is passed over in both groups, but only in a
      first pass: slots still empty after it are filled from the passed-over
      chunks in rank order, so the program cap can change which chunks a
      session gets but never how many (the DOG-1 failure mode).
    Without ``state`` the result is the stateless composition above.
    """
    if state is None:
        return _compose(session_chunks, fault_chunks, has_faults, max_chunks,
                        max_fault_chunks, per_source_cap, None, program_source_share)
    with state.lock:
        chosen = _compose(session_chunks, fault_chunks, has_faults, max_chunks,
                          max_fault_chunks, per_source_cap, state, program_source_share)
        state.record(chosen)
    return chosen


def _compose(session_chunks, fault_chunks, has_faults, max_chunks, max_fault_chunks,
             per_source_cap, state, program_source_share) -> list[dict]:
    chosen: list[dict] = []
    seen: set = set()
    session_sources: Counter = Counter()   # this session, both groups
    passes = (True, False) if state is not None else (False,)

    def _over_program_cap(sid) -> bool:
        horizon = state.slots + max_chunks   # the program's slots once this session is in
        allowance = max(1, math.floor(program_source_share * horizon))
        return state.source_slots[sid] + session_sources[sid] >= allowance

    def _taker(per_source: Counter, respect_program_cap: bool):
        def _take(c: dict) -> bool:
            cid, sid = c.get("id"), c.get("source_id")
            if cid in seen or per_source[sid] >= per_source_cap:
                return False
            if respect_program_cap and _over_program_cap(sid):
                return False
            seen.add(cid)
            per_source[sid] += 1
            session_sources[sid] += 1
            chosen.append(c)
            return True
        return _take

    n_fault = 0
    if has_faults and fault_chunks:
        by_fault: dict = {}
        for c in fault_chunks:
            by_fault.setdefault(c.get("fault", "_"), []).append(c)
        uses = state.chunk_uses if state is not None else Counter()
        # best first; with state, least-shown first (a stable sort keeps rank order on ties)
        ordered = [sorted(sorted(v, key=_rank, reverse=True), key=lambda c: uses[c.get("id")])
                   for v in by_fault.values()]
        fault_per_source: Counter = Counter()
        for respect in passes:
            _take = _taker(fault_per_source, respect)
            queues = [list(q) for q in ordered]
            while n_fault < max_fault_chunks and any(queues):
                for q in queues:
                    if n_fault >= max_fault_chunks:
                        break
                    while q:
                        if _take(q.pop(0)):
                            n_fault += 1
                            break
            if n_fault >= max_fault_chunks:
                break

    session_per_source: Counter = Counter()   # session chunks get their own per-source budget
    ranked_session = sorted(session_chunks, key=_rank, reverse=True)
    for respect in passes:
        _take = _taker(session_per_source, respect)
        for c in ranked_session:
            if len(chosen) >= max_chunks:
                break
            _take(c)
        if len(chosen) >= max_chunks:
            break

    # fault picks first, then session picks in rank order whichever pass took
    # them; the [Cn] labels follow this list (RAG-M5)
    session_part = sorted(chosen[n_fault:], key=_rank, reverse=True)
    return (chosen[:n_fault] + session_part)[:max_chunks]


def default_reranker(cache: dict | None, settings=None):
    """The program's reranker when RERANK_ENABLED (built once per cache), else None.

    Any construction failure (no key, no client) logs a warning and disables
    the rerank for that program — retrieval continues un-reranked."""
    if not RERANK_ENABLED:
        return None
    holder = cache if cache is not None else {}
    if RERANKER_KEY not in holder:
        try:
            from rerank import ListwiseReranker

            from shared.config import Settings
            holder[RERANKER_KEY] = ListwiseReranker.from_settings(settings or Settings())
        except Exception as e:
            logger.warning(f"Rerank disabled: could not build the reranker ({type(e).__name__}: {e})")
            holder[RERANKER_KEY] = None
    return holder[RERANKER_KEY]


def rerank_candidates(query: str, candidates: list[dict], reranker, top_k: int | None = None) -> list[dict]:
    """Apply the optional listwise reranker (rerank.py). A None reranker, or any
    error, returns the candidates unchanged (cut to top_k)."""
    if reranker is None or not candidates:
        return candidates[:top_k] if top_k else candidates
    try:
        return reranker.rerank(query, candidates, top_k=top_k)
    except Exception as e:  # defensive: ListwiseReranker already falls back internally
        logger.warning(f"Rerank raised for {query[:60]!r} ({type(e).__name__}: {e}); keeping the retrieval order")
        return candidates[:top_k] if top_k else candidates


def retrieve_session_context(
    vector_loader,
    athlete_context: AthleteContext,
    plan: ProgramPlan,
    session_template: SessionTemplate,
    week_target: WeekTarget,
    retrieval_context: RetrievalContext,
    top_k: int | None = None,
    cache: dict | None = None,
    reranker=None,
    state: ContextDiversityState | None = None,
) -> list[dict]:
    """Retrieve + compose the knowledge context for ONE session (RAG-H4).

    The query is cached per program (``cache`` keyed by query string), so a
    4-week × 4-day program issues one search per distinct
    (template, phase, intensity band) rather than sixteen. Returns [] when no
    vector_loader is available or the search fails — generation continues
    without knowledge context, as before.

    AUD-3: the program-level diversity state is ``state`` when given, else the
    one kept in ``cache`` (CONTEXT_STATE_KEY), so the orchestrator's
    per-program cache carries it. The reranker is ``reranker`` when given, else
    ``default_reranker(cache)`` (None unless RERANK_ENABLED); with one, the
    search fetches RERANK_TOP_N candidates and the reranked list is cached.
    """
    if vector_loader is None:
        return []
    cache = cache if cache is not None else {}
    state = state if state is not None else context_state_for(cache)
    reranker = reranker if reranker is not None else default_reranker(cache)
    top_k = top_k or VECTOR_SEARCH_DEFAULT_TOP_K
    query = build_session_query(athlete_context, plan, session_template, week_target)
    if query not in cache:
        keep = top_k * 2  # headroom for the per-source cap + fault dedupe
        try:
            found = vector_loader.similarity_search(
                query=query,
                top_k=max(keep, RERANK_TOP_N) if reranker is not None else keep,
                preferred_chunk_types=SESSION_PREFERRED_TYPES,
                min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                hybrid=HYBRID_SEARCH_ENABLED,
            )
            cache[query] = rerank_candidates(query, found, reranker, top_k=keep) if reranker is not None else found
        except Exception as e:
            logger.warning(f"Vector search failed for session '{session_template.label}': {e}")
            cache[query] = []
    composed = compose_session_context(
        cache[query],
        retrieval_context.fault_correction_chunks,
        has_faults=bool(athlete_context.technical_faults),
        state=state,
    )
    # copies, so the cached rows stay pristine; the query travels with each
    # chunk into generation_log.retrieval_set (RAG-M5)
    return [{**c, "session_query": query} for c in composed]


def fetch_fault_chunks(vector_loader, faults: list[str], level: str, top_k: int, reranker=None) -> list[dict]:
    """Fault-correction chunks for every fault (deduped across faults), each
    tagged with the fault that surfaced it and its query. A failed search for
    one fault is logged and skipped."""
    out: list[dict] = []
    fault_seen: set = set()
    for fault in faults:
        fault_query = build_fault_query(fault, level)
        try:
            chunks = vector_loader.similarity_search(
                query=fault_query,
                top_k=max(top_k, RERANK_TOP_N) if reranker is not None else top_k,
                preferred_chunk_types=FAULT_PREFERRED_TYPES,
                min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                hybrid=HYBRID_SEARCH_ENABLED,
            )
            chunks = rerank_candidates(fault_query, chunks, reranker, top_k=top_k)
            for c in chunks:
                if c.get("id") not in fault_seen:
                    fault_seen.add(c["id"])
                    # remember which fault surfaced it so the session context
                    # can round-robin across faults (RAG-H4); its query focuses
                    # the prompt excerpt (AUD-2)
                    out.append({**c, "fault": fault, "retrieval_query": fault_query})
        except Exception as e:
            logger.warning(f"Vector search failed for fault '{fault}': {e}")
    return out


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
        reranker = default_reranker(None, settings)   # None unless RERANK_ENABLED (AUD-3)

        strength_limiters = athlete_context.athlete.get("strength_limiters") or []

        # Session-template queries no longer run here: retrieval for the
        # session prompt is per session (retrieve_session_context), keyed by
        # template + phase + intensity band. The old program-level pass queried
        # only session_templates[:2] and every prompt reused the same four
        # snippets (RAG-H4). `programming_rationale` now carries the
        # limiter-driven chunks only.

        # Fault correction — search ALL faults, not just the first two
        if athlete_context.technical_faults:
            fault_correction_chunks = fetch_fault_chunks(
                vector_loader, athlete_context.technical_faults, athlete_context.level, top_k, reranker
            )

        # Strength limiter searches — pull targeted programming content per limiter
        for limiter in strength_limiters:
            limiter_query = build_limiter_query(limiter, athlete_context.level)
            try:
                chunks = vector_loader.similarity_search(
                    query=limiter_query,
                    top_k=max(top_k, RERANK_TOP_N) if reranker is not None else top_k,
                    # `methodology` dropped: the inference never assigns it (RAG-H2)
                    preferred_chunk_types=SESSION_PREFERRED_TYPES,
                    min_similarity=VECTOR_SEARCH_MIN_SIMILARITY,
                    hybrid=HYBRID_SEARCH_ENABLED,
                )
                chunks = rerank_candidates(limiter_query, chunks, reranker, top_k=top_k)
                for c in chunks:
                    if c.get("id") not in seen_chunk_ids:
                        seen_chunk_ids.add(c["id"])
                        programming_rationale.append({**c, "retrieval_query": limiter_query})
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
