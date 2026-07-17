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
SNIPPET_MAX_CHARS: int = 600         # max chars of a knowledge chunk shown in prompt
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

# ── Weight resolution ────────────────────────────────────────────
WEIGHT_ROUND_INCREMENT: float = 0.5  # round absolute weights to nearest 0.5 kg

# ── Web list caps ────────────────────────────────────────────────
MAX_PROGRAM_LIST_ROWS: int = 100   # program list page (most recent first)
MAX_HISTORY_ROWS: int = 200        # per-exercise history page (most recent first)
MAX_LOG_BACKFILL_DAYS: int = 365   # how far back a training log may be dated

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
