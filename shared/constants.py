# shared/constants.py
"""
Project-wide numeric constants. Import from here instead of using magic numbers.
"""

# ── Session duration ─────────────────────────────────────────────
DEFAULT_SESSION_DURATION_MINUTES: int = 90
SESSION_DURATION_TOLERANCE: float = 1.2  # warn if estimated > available * this
SECONDS_PER_SET: int = 30                # assumed working time per set
DEFAULT_REST_SECONDS: int = 90           # assumed rest when an exercise has none
MIN_SESSION_DURATION_MINUTES: int = 30   # floor for a session duration estimate

# ── Prilepin chart ───────────────────────────────────────────────
PRILEPIN_HARD_CAP_MULTIPLIER: float = 1.5   # hard session-volume cap = range_high * this
MIN_SESSION_REPS: int = 3                    # minimum reps to be a meaningful set
WEEKLY_REP_BUDGET_TOLERANCE: float = 1.25   # warn when weekly comp reps exceed budget × this (AGT-L3)

# ── Vector search ────────────────────────────────────────────────
VECTOR_SEARCH_DEFAULT_TOP_K: int = 5
VECTOR_SEARCH_MIN_SIMILARITY: float = 0.45  # drop chunks below this cosine similarity

# ── Prompt construction ──────────────────────────────────────────
SNIPPET_MAX_CHARS: int = 1500        # max chars of a knowledge chunk shown in prompt
# 600 showed ~15% of a 2-5k-char chunk — the preamble and topic sentence, while
# the prescription sits in the tail (RAG-H4). Retrieval unit ≈ display unit now.
MAX_PRINCIPLES_IN_PROMPT: int = 8   # max active principles sent to LLM
MAX_RECENT_LOGS_IN_PROMPT: int = 10  # recent training entries shown in prompt
PROMPT_LENGTH_WARN_CHARS: int = 20_000  # log warning if prompt exceeds this (~5k tokens)

# ── Traceability ─────────────────────────────────────────────────
MAX_SOURCE_CHUNKS_PER_EXERCISE: int = 3  # most-relevant chunk ids attached per exercise

# ── Intensity envelope ───────────────────────────────────────────
# The week ceiling applies to competition lifts only; pulls/squats reference
# their own max and are routinely programmed supramaximally. Non-comp lifts
# above this % still warn to catch gross typos.
SUPRAMAX_INTENSITY_WARN_PCT: float = 120.0
WARMUP_INTENSITY_CUTOFF_PCT: float = 65.0  # sets at/below this are warm-ups (sub-floor by design)
# Volume accounting excludes only the MANDATED warmup band (prompt: 50-60%).
# Reusing the 65% sub-floor cutoff deleted the entire 55-65 Prilepin zone from
# Check 1/1b — 120 reps @62% passed silently on low-intensity weeks (audit3-M1).
WARMUP_VOLUME_EXCLUSION_PCT: float = 60.0

# ── Weight resolution ────────────────────────────────────────────
WEIGHT_ROUND_INCREMENT: float = 0.5  # round absolute weights to nearest 0.5 kg

# ── Web list caps ────────────────────────────────────────────────
MAX_PROGRAM_LIST_ROWS: int = 100   # program list page (most recent first)
MAX_HISTORY_ROWS: int = 200        # per-exercise history page (most recent first)
MAX_LOG_BACKFILL_DAYS: int = 365   # how far back a training log may be dated

# ── DB column widths mirrored in validation ─────────────────────
# Mirrors migration 0001; over-long values are a driver error (asyncpg 22001 /
# psycopg2 StringDataRightTruncation), not a validation message.
EXERCISE_NAME_MAX_CHARS: int = 200      # session_exercises / training_log_exercises
INTENSITY_REFERENCE_MAX_CHARS: int = 100  # session_exercises.intensity_reference
MAX_RPE_TARGET: float = 99.9            # session_exercises.rpe_target NUMERIC(3,1)

# ── Phase advancement & outcome adjustments ─────────────────────
# Used by plan._advance_phase / plan._apply_outcome_adjustments and mirrored
# by feedback._compute_phase_verdict — keep both reading from here.
ADVANCE_MIN_ADHERENCE_PCT: float = 70.0   # adherence required to advance phase
ADVANCE_MIN_MAKE_RATE: float = 0.75       # make rate required to advance phase
ADVANCE_MAX_RPE_DEVIATION: float = 1.5    # RPE deviation above this blocks advancement
ADJUST_RPE_DEVIATION: float = 1.0         # RPE deviation above this triggers volume reduction
EXCELLENT_ADHERENCE_PCT: float = 90.0     # adherence for "excellent performance" intensity boost
EXCELLENT_MAKE_RATE: float = 0.85         # make rate for "excellent performance" intensity boost

# ── Trend detection (feedback._compute_trend) ───────────────────
# Half-average difference needed to call a sequence ascending/descending.
# RPE deviations swing by whole points; make rates are 0-1 fractions, so they
# need a much smaller threshold or every real decline reads as "stable".
RPE_TREND_THRESHOLD: float = 0.5          # for RPE-deviation sequences
MAKE_RATE_TREND_THRESHOLD: float = 0.07   # for make-rate (0-1) sequences

# ── pgvector HNSW query settings ────────────────────────────────
# A filtered HNSW scan collects ef_search candidates and only THEN applies the
# WHERE clause. With the production chunk_type + min_similarity predicates that
# returned 0 rows on 46/60 probe queries once the planner used the index
# (RAG-H5). iterative_scan (pgvector >= 0.8) keeps scanning until LIMIT is met;
# a wider ef_search cuts how often that is needed. Applied per transaction by
# VectorLoader.similarity_search.
HNSW_EF_SEARCH: int = 100
HNSW_ITERATIVE_SCAN: str = "relaxed_order"

# ── Ingestion sectioning (RAG-H1) ───────────────────────────────
# Heading-delimited sections are capped before classification so one program
# table inside a 60k-char chapter can't route the whole chapter to the template
# parser; the cap equals the principle-extraction window. Fragments below the
# minimum (heading regexes over-fire on tabular lines like "1.5 Snatch 3x3" or
# "Week 2 …", carving a table into one-line sections) are folded back into
# their neighbour with the heading line restored.
CLASSIFY_SECTION_MAX_CHARS: int = 8000
MIN_SECTION_CHARS: int = 300

# ── chunk_type preference in retrieval (RAG-H2) ─────────────────
# chunk_type is a first-match keyword label; `concept` is ~61% of the corpus
# and the old hard filter left session generation 15% of the chunks (0 of the
# deload chunks). It is now a soft preference: the vector search takes a
# candidate pool by pure similarity, then adds the boost to preferred types
# and re-ranks. Pool = max(top_k * multiplier, min candidates).
CHUNK_TYPE_PREFERENCE_BOOST: float = 0.05
VECTOR_SEARCH_CANDIDATE_MULTIPLIER: int = 4
VECTOR_SEARCH_MIN_CANDIDATES: int = 20

# ── Principle selection (RAG-H3) ────────────────────────────────
# plan._load_principles pre-filters by phase/level in SQL (a superset); the
# per-session match in principle_matcher applies every condition field, so the
# SQL cap only needs to leave enough candidates for that second pass.
MAX_PRINCIPLE_CANDIDATES: int = 50

# ── Session context assembly (RAG-H4) ───────────────────────────
# Retrieval runs per session (query keyed by template + phase + intensity
# band, cached per program); the composed context shows whole-ish chunks,
# not 600-char heads, with at most this many chunks / fault chunks / chunks
# from one source.
MAX_CONTEXT_CHUNKS: int = 4
MAX_FAULT_CHUNKS_IN_CONTEXT: int = 2
MAX_CHUNKS_PER_SOURCE_IN_CONTEXT: int = 2
INTENSITY_BAND_WIDTH_PCT: int = 5  # session queries share a cache entry within this band

# ── Hybrid retrieval (RAG-M1) ───────────────────────────────────
# Dense-only search matched exercise names, "Prilepin", %/reps notation and
# Soviet abbreviations only through the embedding. The lexical leg is a
# Postgres tsvector (migration 0009) fused with the vector leg by reciprocal
# rank: score = Σ 1/(RRF_K + rank). The chunk_type preference is re-scaled to
# the RRF range (a rank-1 hit in one leg is worth 1/61 ≈ 0.0164).
HYBRID_SEARCH_ENABLED: bool = True
RRF_K: int = 60
HYBRID_CANDIDATES_PER_LEG: int = 20
CHUNK_TYPE_PREFERENCE_BOOST_RRF: float = 0.004  # ≈ moving up ~15 ranks in one leg

# ── Token accounting (RAG-M2) ───────────────────────────────────
# Chunk sizes and the embedding cap are counted with the embedding model's own
# tokenizer (cl100k_base for text-embedding-3-*). The words×1.3 estimate
# under-counted notation like "(85%/4)4 20:108:280" by ~5× (measured: 24
# tokens vs 5). The word estimate remains the offline fallback.
EMBED_MAX_TOKENS: int = 8191          # text-embedding-3-* input limit
TOKENS_PER_WORD_FALLBACK: float = 1.3  # used only when tiktoken is unavailable
