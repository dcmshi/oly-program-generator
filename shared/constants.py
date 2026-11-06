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
# An accessory (anything that is not a competition lift, squat or pull) may
# appear in at most this many sessions of one week — Back Extension turned
# up in 10 of 16 sessions of program 12 and 8 of 16 of program 23 (DOG-1).
# A prompt rule + a validator WARNING (never a paid retry).
MAX_ACCESSORY_SESSIONS_PER_WEEK: int = 2
WEEKLY_REP_BUDGET_TOLERANCE: float = 1.25   # warn when weekly comp reps exceed budget × this (AGT-L3)

# ── Vector search ────────────────────────────────────────────────
VECTOR_SEARCH_DEFAULT_TOP_K: int = 5
VECTOR_SEARCH_MIN_SIMILARITY: float = 0.45  # drop chunks below this cosine similarity

# ── Prompt construction ──────────────────────────────────────────
SNIPPET_MAX_CHARS: int = 1500        # max chars of a knowledge chunk shown in prompt
# 600 showed ~15% of a 2-5k-char chunk — the preamble and topic sentence, while
# the prescription sits in the tail (RAG-H4). Retrieval unit ≈ display unit now.
# Most chunks are still longer than that (median ≈ 3.8k chars), so the prompt
# shows a query-focused excerpt (shared/excerpt.focused_excerpt, AUD-2): the
# sentences / lines that share the most query terms, their neighbours and the
# chunk's heading, in order, with skipped stretches marked.
EXCERPT_MAX_PIECE_CHARS: int = 400     # longer sentences / lines are cut at whitespace
EXCERPT_HEADING_MAX_CHARS: int = 160   # a first line this short without end punctuation is a heading
EXCERPT_MIN_TERM_CHARS: int = 3        # shorter tokens are not query terms
EXCERPT_GAP_MARKER: str = " … "        # joins non-adjacent excerpt pieces
MAX_PRINCIPLES_IN_PROMPT: int = 8   # max active principles sent to LLM
MAX_RECENT_LOGS_IN_PROMPT: int = 10  # recent training entries shown in prompt
# Previous Program block (DOG-1e): most-used exercises and last-week top sets
# summarised from the completed program's session_exercises rows.
MAX_PREVIOUS_PROGRAM_EXERCISES: int = 8
MAX_PREVIOUS_PROGRAM_TOP_SETS: int = 5
# Log a warning above this many chars. Re-based 2026-11-06 (DOG-2q): program 58
# measured 22.5-23.6k with the content added since the 20k budget (complexes,
# coach brief, accessory catalogue, fault names) and went 24/24 first-try, so
# 20k warned on every session. It now flags growth past today's prompt.
PROMPT_LENGTH_WARN_CHARS: int = 24_000
# EXPLAIN's view of the program (AGENT-0b): one line per session, warm-ups left
# out. 24 sessions × ~170 chars ≈ 4k; past the cap, lines keep only their first
# EXPLAIN_EXERCISES_PER_SESSION_WHEN_CAPPED exercises, then the tail is cut.
EXPLAIN_PROGRAM_VIEW_MAX_CHARS: int = 8_000
EXPLAIN_EXERCISES_PER_SESSION_WHEN_CAPPED: int = 3
# When a call stops on `max_tokens`, the next attempt doubles its budget up to
# this ceiling. On Sonnet 5 / Opus 5 adaptive thinking counts against
# max_tokens, so a 4,096 budget can be spent before any text is emitted;
# retrying the same request just fails the same way (MODEL-1).
LLM_MAX_TOKENS_CEILING: int = 16_384
# Message Batches (COST-1): offline ingestion calls go through the Batch API at
# half price. Poll cadence, the per-batch request cap (the API allows 100k /
# 256 MB; a smaller batch fails smaller) and how long to wait before giving up
# (the API itself expires a batch after 24 h).
BATCH_POLL_INTERVAL_S: float = 30.0
BATCH_MAX_REQUESTS: int = 2_000
BATCH_TIMEOUT_S: float = 24 * 3600.0

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

# ── Block length (PLAN-1) ────────────────────────────────────────
# A block is the phase profile's default length unless the athlete asks for
# another (`--weeks` / the generate form). Level bounds keep a beginner off an
# 8-week grind and let an advanced lifter run a longer accumulation; a
# competition date still overrides (weeks_to_competition decides realization).
BLOCK_WEEKS_MIN: int = 2
BLOCK_WEEKS_MAX_BY_LEVEL: dict[str, int] = {"beginner": 4, "intermediate": 6, "advanced": 8, "elite": 8}
# Level-aware defaults per phase when nothing is requested (None = profile default).
BLOCK_WEEKS_DEFAULT_BY_LEVEL: dict[str, dict[str, int]] = {
    "beginner":     {"general_prep": 4, "accumulation": 4},
    "intermediate": {},
    "advanced":     {"accumulation": 5},
    "elite":        {"accumulation": 6},
}

# ── Macrocycles (PLAN-3e) ─────────────────────────────────────────
# A macrocycle lays blocks out backward from the meet (or the chosen length's
# final max-test week): realization, intensification, then accumulation blocks
# of the level's default length, with a general-prep opener on long plans.
# Shorter than the minimum is a single block, not a macrocycle.
MACROCYCLE_WEEKS_MIN: int = 6
MACROCYCLE_WEEKS_MAX: int = 52
MACROCYCLE_WEEKS_DEFAULT: int = 12
MACROCYCLE_GENERAL_PREP_MIN_WEEKS: int = 16

# ── Training preferences (PLAN-2 §1.4, §3.4, §3.5, §3.8) ─────────
# Stored under athletes.exercise_preferences["prefs"] (JSONB; "avoid" lives
# beside it). Read by plan.py (deload cadence), generate.py (warm-up and deload
# rules in the prompt) and orchestrator.py (max-test session).
TRAINING_PREFERENCE_OPTIONS: dict[str, tuple[str, ...]] = {
    "warmups":      ("prescribed", "own"),               # program writes 2–3 warm-up sets / athlete warms up alone
    "deload_style": ("volume", "intensity", "none"),     # last week: cut sets, cut load, or no deload week
    "max_test":     ("auto", "always", "never"),         # auto = phase profile decides
    "volume_table": ("prilepin", "medvedev"),            # PLAN-3d: shared/volume_tables.py
}
TRAINING_PREFERENCE_DEFAULTS: dict[str, str] = {"warmups": "prescribed", "deload_style": "volume", "max_test": "auto",
                                                "volume_table": "prilepin"}
DELOAD_EVERY_WEEKS_OPTIONS: tuple[int, ...] = (3, 4, 5, 6)   # extra deload weeks inside a long block; blank = only the last week
# DOG-2d: a block longer than its phase profile with a deload cadence runs as
# waves of (N-1) loading weeks + 1 deload; wave k starts k × this many % points
# above wave 1 (floor and ceiling, before level adjustments / caps), so each
# wave resumes above the last instead of repeating it.
BLOCK_WAVE_STEP_PCT: int = 2

# ── Dosing (code sets the numbers, the model picks the exercises) ──
# Reps per set on the full classic lifts (snatch, clean, jerk, clean & jerk —
# CLASSIC_LIFT_NAMES), separate from the week's range, which keeps governing
# their power / hang / muscle variants. Sources: [221] / [671] Zatsiorsky —
# "typical repetitions range from 1 to 3, with 2 reps being most common (60 %
# of sets)"; [447] / [448] Takano — classic-lift sets "rarely exceed 4", 4 only
# on preparation days. A beginner without olympic_lifting_experience keeps the
# week's range (Medvedev's learning-stage 6 × 50–60 %, [1603] / [2403]).
CLASSIC_LIFT_REPS_PER_SET: dict[str, tuple[int, int]] = {
    "general_prep": (2, 3), "accumulation": (2, 3), "intensification": (1, 3), "realization": (1, 2),
}
# Reps per set on the competition-lift variants (power, hang, block, riser,
# muscle, pause — everything counts_as_competition_lift that is not a classic
# lift), DOSE-1a. Everett: power snatch / clean / jerk, hang snatch / clean,
# block power snatch / clean, riser lifts and squat jerk "1-3 reps" (chunks
# 4684 / 4738 / 4752 / 4680 / 4733 / 4698 / 4720 / 4759, principles [369] /
# [380]), "generally 2-3 reps at submaximal weights … as it's more of a
# technical exercise" (4685 / 4738) → 2–3 in the submaximal phases, 1–3 when
# maximal singles are in play. Tapering for Olympic Weightlifting Competition
# (7893 / 7894, [1997]): power variants as singles in the taper → 1–2 in
# realization. Same numbers as the classic table, separate sources; a
# beginner without olympic_lifting_experience keeps the week's range here too.
VARIANT_LIFT_REPS_PER_SET: dict[str, tuple[int, int]] = {
    "general_prep": (2, 3), "accumulation": (2, 3), "intensification": (1, 3), "realization": (1, 2),
}
CLEAN_AND_JERK_MAX_REPS: int = 2         # a C&J rep is a clean plus a jerk; the catalogue row is 1–2
DOSING_VOLUME_TOLERANCE: float = 0.15     # session comp reps within ±15 % of the target are left alone
DOSING_MAX_SETS: int = 8                  # sets added to reach the target stop here (catalogue max for the lifts)
DOSING_RPE_PER_PCT: float = 0.1           # RPE moves 0.1 per % of load the dosing pass changes

# ── Coach constraints (oly-agent/coach.py, web/coach_form.py) ──
COACH_NOTE_MAX_CHARS: int = 1500
COACH_SESSIONS_PER_WEEK_RANGE: tuple[int, int] = (1, 7)
# Highest % of max a coach may cap the competition lifts at. Below 60 the
# whole block would sit in the deload zone (Prilepin starts at 55).
INTENSITY_CAP_RANGE: tuple[int, int] = (60, 100)
# eval/build_golden.py: one grading reply (a JSON list of {id, grade} for a batch)
GOLDEN_GRADER_MAX_TOKENS: int = 512

# ── Session validation (oly-agent/validate.py) ──
# Prilepin zones whose per-set rep limit (PRILEPIN_ZONES[*]["reps_per_set"][1],
# shared/prilepin.py) validate enforces on the competition lifts, and how hard:
# above 90 % an error, 80–90 % a warning; lighter zones aren't checked per set.
PRILEPIN_PER_SET_SEVERITY: dict[str, str] = {"90-100": "error", "80-90": "warning"}
# (% of max, lowest plausible RPE target): a heavier set with a lower RPE
# target is warned about as miscalibrated. Heaviest band first.
RPE_FLOOR_BY_INTENSITY: tuple[tuple[float, float], ...] = ((90.0, 8.0), (80.0, 7.0))
# session_exercises CHECK (intensity_pct <= 120): validate rejects above it,
# the CSV importer clamps to it.
STORABLE_INTENSITY_PCT_MAX: float = 120.0

# ── Whole-program audit (AGENT-0a, oly-agent/program_audit.py) ──
# Weekly comp-lift reps further than DOSING_VOLUME_TOLERANCE from the summed
# session targets are a warning; further than this, an error.
AUDIT_VOLUME_ERROR_FRACTION: float = 0.40
AUDIT_CEILING_SLACK_PCT: float = 0.5      # rounding slack before a set counts as over the ceiling
# A week whose plan raises the ceiling but whose top comp-lift set falls by
# more than this (or the reverse on volume, by DOSING_VOLUME_TOLERANCE) is a
# block-shape warning.
AUDIT_INTENSITY_SLACK_PCT: float = 2.5
AUDIT_SUMMARY_MAX_FINDINGS: int = 8       # errors / warnings listed in the EXPLAIN prompt

# ── Phase advancement & outcome adjustments ─────────────────────
# Used by plan._advance_phase / plan._apply_outcome_adjustments and mirrored
# by feedback._compute_phase_verdict — keep both reading from here.
ADVANCE_MIN_ADHERENCE_PCT: float = 70.0   # adherence required to advance phase
ADVANCE_MIN_MAKE_RATE: float = 0.75       # make rate required to advance phase
ADVANCE_MAX_RPE_DEVIATION: float = 1.5    # RPE deviation above this blocks advancement
ADJUST_RPE_DEVIATION: float = 1.0         # RPE deviation above this triggers volume reduction
EXCELLENT_ADHERENCE_PCT: float = 90.0     # adherence for "excellent performance" intensity boost
EXCELLENT_MAKE_RATE: float = 0.85         # make rate for "excellent performance" intensity boost
# Outcome nudges are proportional to the miss (PLAN-2 §1.8): the STEP is the
# maximum, reached when the miss equals the FULL_MISS span.
OUTCOME_VOLUME_STEP_ADHERENCE: float = 0.10       # volume_modifier −10 % at 40 % adherence or worse
OUTCOME_ADHERENCE_FULL_MISS_PCT: float = 30.0     # 70 → 40 %
OUTCOME_INTENSITY_STEP_MAKE_RATE: float = 3.0     # ceiling −3 pts at make rate 0.50 or worse
OUTCOME_MAKE_RATE_FULL_MISS: float = 0.25         # 0.75 → 0.50
OUTCOME_VOLUME_STEP_RPE: float = 0.05             # volume −5 % at RPE deviation 2.0 or more
OUTCOME_RPE_FULL_MISS: float = 1.0                # 1.0 → 2.0
OUTCOME_INTENSITY_BOOST_EXCELLENT: float = 2.0    # flat +2 pts

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
# SQL cap only needs to leave enough candidates for that second pass (which
# now ranks by session relevance, so the pool is wide — AUD-1).
MAX_PRINCIPLE_CANDIDATES: int = 300
# AUD-1: only programming categories reach the session prompt — technique and
# recovery rules ("Never Throw the Bar Down", doping bans) carried priority 10
# with empty recommendations and filled every slot under ORDER BY priority.
PROMPT_PRINCIPLE_CATEGORIES: tuple[str, ...] = (
    "volume", "intensity", "frequency", "exercise_selection", "periodization",
    "peaking", "load_progression", "deload",
)
# select_principles(query=…) ranks by priority + weight × (distinct session-query
# terms found in the principle's name / rationale / recommended exercises),
# ties by id, and takes at most this many principles of one category.
PRINCIPLE_RELEVANCE_WEIGHT: float = 1.0
MAX_PRINCIPLES_PER_CATEGORY: int = 3
# A first program (no previous program) caps the ceiling at 80 % (75 % beginner)
# unless the athlete has *recorded* snatch and clean / C&J maxes this recent —
# then the maxes are trustworthy and the cap only held a tested lifter back
# (PLAN-3b, assumption 3.3). Level ceilings (LEVEL_PHASE_OVERRIDES) still apply.
COLD_START_MAX_RECENCY_DAYS: int = 90
COLD_START_CEILING_PCT: float = 80.0             # first-program ceiling without recently tested maxes
COLD_START_CEILING_BEGINNER_PCT: float = 75.0
COLD_START_MAX_WEEKS: int = 4                    # a first program's block length cap
EXERCISE_COMPLEXITY_MAX: int = 5                 # exercises.complexity_level scale (no cap)
COLD_START_MAX_COMPLEXITY: int = 3               # first program: catalogue capped at this complexity
COLD_START_MAX_COMPLEXITY_BEGINNER: int = 2
# validate.py check 13 (PLAN-3c): a squat / pull more than this many points
# outside the week's STRENGTH_CURVE band warns (never errors — no paid retry).
STRENGTH_CURVE_TOLERANCE_PCT: float = 5.0
SQUAT_MAX_REFS: tuple[str, ...] = ("back_squat", "front_squat")
# The condition vocabulary has no injury key, so an injury / rehab rule reads as
# unconditional: program 32 (2026-09-22) showed "[235] Emphasize heavy pulls
# during knee injury recovery — avoid full clean, full snatch, squats" in 10
# prompts of an uninjured athlete. A principle whose name or rationale matches
# one of these word stems is only selected when the athlete has injuries.
INJURY_PRINCIPLE_TERMS: tuple[str, ...] = (
    "injur", "rehab", "return to sport", "return to training", "surgery", "post-op",
    "tendinopathy", "tendinitis", "tendonitis",
)
# The same gap for age: the vocabulary has no age key, so "[1376] Youth Volume
# and Intensity Moderation" is stored as {"athlete_level": ["beginner"]} and
# reached a 35-year-old (program 53, 2026-11-03). A principle whose *name*
# matches one of these patterns is only selected for an athlete in
# YOUTH_PRINCIPLE_AGE_BANDS; unknown age is treated as adult. Name only: ~30
# general rules (warm-ups, loading tables) mention youth or juniors in passing
# in their rationale.
YOUTH_PRINCIPLE_NAME_PATTERNS: tuple[str, ...] = (
    r"\byouth", r"\bchild", r"\badolescen", r"\bteen", r"\bkids?\b", r"\bpubert", r"\bpubesc",
    r"\byoung\b", r"\bjunior", r"\bschool[- ]age", r"\bgrowth (plate|cartilage)", r"\bu1\d\b",
    r"\bage \d+ and under",
)
YOUTH_PRINCIPLE_AGE_BANDS: tuple[str, ...] = ("youth", "junior")
# With athletes.olympic_lifting_experience set, a principle that recommends
# avoiding a competition lift *and* is gated on novice level or training age
# (the "learn the basics first" rules: 5073, 5074, 5079, 1829 …) is skipped.
# Bans gated on phase, injury or movement (transition months, knee rehab)
# still apply.
COMP_LIFT_AVOID_NAMES: frozenset[str] = frozenset({
    "snatch", "clean_and_jerk", "clean and jerk", "clean & jerk", "clean", "jerk",
    "full snatch", "full clean", "olympic lifts", "competition lifts", "classic lifts",
})
NOVICE_LEVELS: frozenset[str] = frozenset({"beginner", "novice", "youth"})
PRINCIPLE_RATIONALE_PROMPT_CHARS: int = 150  # rationale shown per principle line
# DOG-2l: recommendation keys never shown in the session prompt. The extractor
# records `total_reps_max` with no scope, and the corpus uses it per set (447: 4),
# per session (630: 10), per mesocycle (556: 908) and per year (1649: 435). The
# model read a bare number as a session cap: [700] "50" (about 100-rep sets)
# held program 56 to ~40 reps a session. Code sets the volume (DOSE-1). The
# rationale still reaches the prompt. A rule left with nothing else to
# recommend is not selected.
PROMPT_HIDDEN_RECOMMENDATION_KEYS: frozenset[str] = frozenset({"total_reps_max"})

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
# Off since AUD-3 (2026-09-22): under text-embedding-3-large dense-only beats
# every fusion weighting on the gate (nDCG@5 0.796 vs 0.712 equal-weight,
# 0.782 at HYBRID_LEXICAL_WEIGHT 0.1). The lexical path stays working.
HYBRID_SEARCH_ENABLED: bool = False
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

# ── Program template rendering (RAG-M4) ─────────────────────────
# program_templates.program_structure (LLM-parsed weeks/sessions/exercises)
# reached the prompt as name + notes only; the matching week is now rendered
# compactly, capped per template.
MAX_TEMPLATE_CHARS_IN_PROMPT: int = 700
MAX_TEMPLATES_IN_PROMPT: int = 2

# ── Prompt caching (RAG-L3) ─────────────────────────────────────
# The session prompt is ordered static-first (athlete, maxes, catalogue, …)
# and split at PROMPT_STATIC_DYNAMIC_MARKER; the static part is sent as a
# cache_control block when it is long enough to be cacheable at all
# (Anthropic's minimum is ~1,024 tokens ≈ 4,000 chars).
PROMPT_STATIC_DYNAMIC_MARKER: str = "\n## Program Plan\n"
PROMPT_CACHE_MIN_CHARS: int = 4000

# ── DB column widths (ingestion) ────────────────────────────────
# knowledge_chunks.chapter / .section and ingestion_chunk_log.section_title are
# VARCHAR(300); a longer heading line raised StringDataRightTruncation and
# dropped the whole section (RAG-L8). Mirror of migration 0000.
CHUNK_TITLE_MAX_CHARS: int = 300

# ── Chunking (ingestion) ───────────────────────────────────────
# A paragraph opening with a week label ("Week 9", "Week # 12") closes the
# current chunk once it holds at least this fraction of the profile's
# chunk_size, so training logs chunk on week boundaries instead of wherever
# the token budget runs out (MEDVEDEV). 0.7 of the 700-token soviet profile:
# a week of Medvedev's day-by-day logs is 350-650 tokens (1-2 weeks per
# chunk); a week of his exercise-selection listings is ~150 (3-4 per chunk).
# 0.4 cut the listings into 1-2-week chunks of ~300 chars.
WEEK_BOUNDARY_FLUSH_FRACTION: float = 0.7

# ── Chunk quarantine (JEV-1a) ──────────────────────────────────────
# quarantine_chunks.py marks a chunk non-content when Jev's calibrated
# P(junk) reaches this; 0.7 flagged 282 of 4,607 dev-copy chunks (indexes,
# reference lists, TOCs) with 39/40 confirmed on a Sonnet spot-check.
JUNK_QUARANTINE_THRESHOLD: float = 0.7

# ── Principle dedupe (JEV-1b / PRIN-DEDUPE) ───────────────────────
# dedupe_principles.py pairs same-category principles whose name+rationale
# embeddings sit at or above this cosine, then asks Jev whether the pair
# states the same rule; at or above the threshold the later row gets
# duplicate_of and plan._load_principles skips it.
PRINCIPLE_DUPLICATE_MIN_COSINE: float = 0.70
PRINCIPLE_DUPLICATE_THRESHOLD: float = 0.7

# Principle extraction: a window at least this long that comes back with
# `{"principles": []}` is re-asked up to this many times. Kimi K3 returned an
# empty list at random on 2 of 5 identical calls for one Charniga window
# (7–10 principles otherwise; principle_model_compare, 2026-09-22); an empty
# reply is ~12 output tokens, so the retries cost almost nothing.
PRINCIPLE_EMPTY_RETRY_MIN_CHARS: int = 2_000
PRINCIPLE_EMPTY_RETRIES: int = 2

# ── Jev section classifier (JEV-1c) ────────────────────────────────
# pipeline.py --classifier jev takes Jev's content-type Choice for a section
# when its calibrated confidence reaches this; below it the heuristic stands.
JEV_CLASSIFY_MIN_CONFIDENCE: float = 0.5

# ── Vision-OCR quality gate (OCR-QA) ───────────────────────────────
# processors/ocr_quality.py scores every OCR'd page without a reference;
# PDFExtractor re-OCRs suspects at a second zoom and keeps what the two
# views agree on. Thresholds set on the 2026-09-20 caches (Roman,
# Verkhoshansky, Vorobyev): figure pages are ~30 chars, text pages 1.7–2.1k.
OCR_MIN_PAGE_CHARS: int = 40              # below this a page counts as blank
OCR_SHORT_VS_NEIGHBOURS: float = 0.25     # < 25 % of the neighbouring pages' median → suspect
OCR_ECHO_JACCARD: float = 0.6             # 5-word shingle overlap with the previous page → echo
OCR_GARBLED_RATIO_MAX: float = 0.15       # share of non-word tokens tolerated
OCR_NON_ASCII_RATIO_MAX: float = 0.10     # Cyrillic table headers in translations sit ~2 %
OCR_VIEW_AGREEMENT_MIN: float = 0.5       # 3-shingle Jaccard between the two views to call them consistent
OCR_SECOND_VIEW_DPI: int = 200            # first view renders at 150 DPI (_ocr_request)
OCR_VIEW_ROTATIONS_DEG: tuple[float, ...] = (1.5, -1.5)   # second / third view: zoom + slight rotation = independent probe

# ── Two-page spreads (OCR-ROLE part 2b) ────────────────────────────
# A scan of open-book spreads (Kono: 149 of 149 pages landscape) is sent to
# the vision model as two half images per page, split at the gutter, so no
# model reads two book pages from one downscaled image. Only in a scan that is
# mostly landscape — Vorobyev's 7 landscape pages in 319 are single sideways
# diagrams, which a split would cut in half — and only on a page with an empty
# vertical band near the middle and ink on both sides (extractors/spreads.py).
OCR_SPREAD_ASPECT_MIN: float = 1.15       # width / height at or above this is landscape (A4 spread ≈ 1.41)
OCR_SPREAD_DOC_MIN_SHARE: float = 0.5     # landscape share of the scan before any page is split
OCR_SPREAD_GUTTER_BAND: tuple[float, float] = (0.38, 0.62)   # where the gutter is looked for (fraction of width)
OCR_SPREAD_GUTTER_MAX_INK: float = 0.03   # a gutter column is dark on ≤ 3 % of its rows (tolerates a shadow tick)
OCR_SPREAD_GUTTER_MIN_WIDTH: float = 0.01  # the empty band is at least 1 % of the page width
OCR_SPREAD_DETECT_DPI: int = 40           # grayscale render the detector reads

# ── LLM request resilience ───────────────────────────────────────────
# The client is built with max_retries=0 so create_message_with_retries owns
# every retry (logged, exponential backoff) instead of the SDK silently
# retrying inside a 10-minute default timeout. A vision-OCR group of five
# scanned pages answers in 40–90 s on Kimi K3; a request past
# OCR_REQUEST_TIMEOUT_S is a hung socket, not a slow page.
LLM_REQUEST_TIMEOUT_S: float = 300.0
# A 429 is a per-minute window, not a fault: wait this long (or Retry-After if
# longer) up to RATE_LIMIT_MAX_WAITS times without spending the attempt budget.
# OpenRouter caps new accounts at 20 rpm for Claude Haiku.
RATE_LIMIT_RETRY_S: float = 15.0
RATE_LIMIT_MAX_WAITS: int = 20
# OpenAI's flex tier can be unavailable for hours (2026-10-12: ~10:44 onward,
# 20 of 25 Kono OCR groups). A flex-unavailable reply is free, so it is retried
# on flex FLEX_UNAVAILABLE_RETRIES times, FLEX_UNAVAILABLE_RETRY_S apart, before
# FLEX_FALLBACK decides (ask / standard / fail); "wait" at the prompt sleeps
# FLEX_UNAVAILABLE_WAIT_S and tries flex again.
FLEX_UNAVAILABLE_RETRIES: int = 2
FLEX_UNAVAILABLE_RETRY_S: float = 30.0
FLEX_UNAVAILABLE_WAIT_S: float = 300.0
OCR_REQUEST_TIMEOUT_S: float = 240.0
OCR_REQUEST_ATTEMPTS: int = 4
# Page groups OCR'd concurrently (OCR-PERF). Four keeps well under OpenRouter's
# per-key limits for Kimi K3 and cuts a 150-page scan from ~25 to ~7 minutes;
# raise it only if the log shows no 429 retries.
OCR_CONCURRENCY: int = 4
CONTEXTUALIZE_CONCURRENCY: int = 6
# Program generation (AUD-4): week 1 runs first, then weeks 2..N run this many
# at a time (days in order within a week, one psycopg2 connection per worker).
# 1 = fully sequential. Four matches OCR_CONCURRENCY's per-key headroom on
# OpenRouter; a 48-session block drops from ~13 min of LLM time to ~5.
GENERATION_WEEK_CONCURRENCY: int = 4
# Week 1's same-day session, summarised into later weeks' prompts as the
# block's template (exercise names, sets x reps @ %), capped at this length.
BLOCK_TEMPLATE_MAX_CHARS: int = 400

# ── Athlete demographics (AUD-5) ─────────────────────────────────────
# Age bands for the prompt and the session query, as (upper-exclusive age,
# band); an age at or above the last bound is AGE_BAND_MASTERS. Aligned with
# IWF categories (youth 13–17, junior to 20, Masters age groups from 35) and
# the corpus' own vocabulary ("Junior Weightlifting", "Youth to Senior").
AGE_BANDS: tuple[tuple[int, str], ...] = (
    (18, "youth"),
    (21, "junior"),
    (35, "senior"),
)
AGE_BAND_MASTERS: str = "masters"
# Only these change what a session should retrieve. Senior / junior / male /
# unknown athletes keep today's query byte-identical, so the golden eval set
# stays valid for them.
AGE_BAND_QUERY_QUALIFIERS: dict[str, str] = {
    "youth": "youth athlete",
    "masters": "masters athlete",
}
SEX_QUERY_QUALIFIERS: dict[str, str] = {
    "female": "female athlete",
}

# ── Ingestion request budgets and heuristics (AUD-6) ─────────────────
# Output budget for the classifier's LLM fallback: one schema-constrained
# {content_type, confidence, reason} label.
CLASSIFIER_LLM_MAX_TOKENS: int = 128
# relabel_chunk_types.py: one reply labels a group of chunks (index → type).
RELABEL_MAX_TOKENS: int = 1024
# DOG-2g: oly-ingestion/mine_accessories.py — a light-model pass that lists the
# accessory exercises each prose chunk names. A candidate enters `exercises`
# only when this many distinct sources name it, the same bar migration 0023
# used for its hand-picked rows.
ACCESSORY_MINE_MIN_SOURCES: int = 6
# With `--catalyst` a candidate must also be in Catalyst Athletics' exercise
# library, which stands in for the hand review, so two corpus sources are
# enough (2026-11-05: Reverse Hyper, Nordic Curl, Kettlebell Swing … are
# named in 2–5 sources).
ACCESSORY_MINE_MIN_SOURCES_CONFIRMED: int = 2
ACCESSORY_MINE_MIN_PRESCRIPTIONS: int = 2      # mentions with sets/reps before a typical value is set
ACCESSORY_MINE_PASSAGES_PER_CALL: int = 5     # amortises the ≈2.4k-token schema overhead
ACCESSORY_MINE_PASSAGE_CHARS: int = 4000      # chunk median ≈ 3.8k chars
ACCESSORY_MINE_MAX_TOKENS: int = 2048
ACCESSORY_MINE_CONCURRENCY: int = 4
# --ocr-postcorrect: one page of corrected text (a dense page is ~2k chars).
OCR_POSTCORRECT_MAX_TOKENS: int = 4096
# infer_chunk_type scans the section title plus this many leading content
# characters for chunk-type keywords (RAG-H2).
CHUNK_TYPE_PROBE_CHARS: int = 800
# principle_audit.file_text: a PDF whose text layer averages fewer characters
# per page than this is a scan — its text comes from the OCR cache instead.
SCAN_TEXT_MIN_CHARS_PER_PAGE: int = 10

# ── Retrieval fusion, rerank and context diversity (AUD-3) ──────────
# Weight on the lexical leg in the RRF fusion: score = 1/(RRF_K + vec_rank)
# + HYBRID_LEXICAL_WEIGHT/(RRF_K + lex_rank). 1.0 = the original equal-weight
# RRF (RAG-M1); 0 skips the lexical leg. 0.1 was the best weighting in the
# sweep (docs/RETRIEVAL_EVAL.md) — still below dense-only, so it only applies
# when HYBRID_SEARCH_ENABLED is switched back on.
HYBRID_LEXICAL_WEIGHT: float = 0.1
# Optional listwise rerank (oly-agent/rerank.py): the light model reorders the
# top RERANK_TOP_N candidates of each retrieval query in one schema-constrained
# call, cached per query; any error keeps the retrieval order. Off: its gain is
# within its own run-to-run noise on the production-shaped query families and
# the session family regressed (docs/RETRIEVAL_EVAL.md). 15 = the depth the
# golden set grades (dense top 15), so the measurement is not biased by
# ungraded chunks.
RERANK_ENABLED: bool = False
RERANK_TOP_N: int = 15
RERANK_SNIPPET_CHARS: int = 400     # query-focused excerpt per passage shown to the reranker
RERANK_MAX_TOKENS: int = 2048       # GLM's mandatory 1,024-token thinking budget counts against it
RERANK_MAX_ATTEMPTS: int = 2
# Program-level source cap on the composed context: a source already holding
# this share of a program's context slots is passed over while another
# candidate can fill the slot (never leaves a slot empty — DOG-1).
MAX_SOURCE_SHARE_IN_PROGRAM: float = 0.4
