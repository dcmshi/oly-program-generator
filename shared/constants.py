# shared/constants.py
"""
Project-wide numeric constants. Import from here instead of using magic numbers.
"""

# ── Session duration ─────────────────────────────────────────────
DEFAULT_SESSION_DURATION_MINUTES: int = 90
SESSION_DURATION_TOLERANCE: float = 1.2  # warn if estimated > available * this

# ── Prilepin chart ───────────────────────────────────────────────
PRILEPIN_HARD_CAP_MULTIPLIER: float = 1.5   # hard session-volume cap = range_high * this
MIN_SESSION_REPS: int = 3                    # minimum reps to be a meaningful set

# ── Vector search ────────────────────────────────────────────────
VECTOR_SEARCH_DEFAULT_TOP_K: int = 5
VECTOR_SEARCH_MIN_SIMILARITY: float = 0.45  # drop chunks below this cosine similarity

# ── Prompt construction ──────────────────────────────────────────
SNIPPET_MAX_CHARS: int = 600         # max chars of a knowledge chunk shown in prompt
MAX_PRINCIPLES_IN_PROMPT: int = 8   # max active principles sent to LLM
MAX_RECENT_LOGS_IN_PROMPT: int = 10  # recent training entries shown in prompt
PROMPT_LENGTH_WARN_CHARS: int = 20_000  # log warning if prompt exceeds this (~5k tokens)

# ── Weight resolution ────────────────────────────────────────────
WEIGHT_ROUND_INCREMENT: float = 0.5  # round absolute weights to nearest 0.5 kg

# ── Phase advancement & outcome adjustments ─────────────────────
# Used by plan._advance_phase / plan._apply_outcome_adjustments and mirrored
# by feedback._compute_phase_verdict — keep both reading from here.
ADVANCE_MIN_ADHERENCE_PCT: float = 70.0   # adherence required to advance phase
ADVANCE_MIN_MAKE_RATE: float = 0.75       # make rate required to advance phase
ADVANCE_MAX_RPE_DEVIATION: float = 1.5    # RPE deviation above this blocks advancement
ADJUST_RPE_DEVIATION: float = 1.0         # RPE deviation above this triggers volume reduction
EXCELLENT_ADHERENCE_PCT: float = 90.0     # adherence for "excellent performance" intensity boost
EXCELLENT_MAKE_RATE: float = 0.85         # make rate for "excellent performance" intensity boost
