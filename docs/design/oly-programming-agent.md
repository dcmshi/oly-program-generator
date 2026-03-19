# Olympic Weightlifting Programming Agent — Design Document

## Overview

This document covers the programming agent that generates Olympic weightlifting training programs using the knowledge base built in Phases 1-5. The agent sits on top of the existing infrastructure: **1,681 knowledge chunks** across 433 sources in pgvector, **~50 seeded exercises** with fault mappings and substitutions, **Prilepin's chart**, **programming principles**, and **program templates** — all in a single Postgres instance.

The agent's job is to take an athlete's profile, training history, and goals, then produce a structured training block (typically 3-8 weeks) that is grounded in the knowledge base, respects established programming constraints, and adapts over time based on athlete outcomes.

### Design Principles

1. **Structured data first, LLM second.** The agent should resolve as much as possible through deterministic queries (Prilepin's chart, principle lookups, exercise taxonomy) before involving the LLM. The LLM handles nuanced decisions — exercise selection rationale, adaptation of templates to individual athletes, explaining programming choices — not arithmetic.

2. **Prescriptions are auditable.** Every decision the agent makes (why this exercise, why this intensity, why this volume) should trace back to either a structured principle or a retrieved knowledge chunk. The athlete (or coach) should be able to ask "why am I doing snatch pulls at 95% this week?" and get a grounded answer.

3. **Start simple, layer complexity.** V1 generates a single mesocycle for one athlete. No multi-athlete management, no periodization across macrocycles, no real-time autoregulation. Those come later as the feedback loop matures.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        USER INTERFACE                            │
│  (CLI initially, web UI later)                                   │
│  - Create/update athlete profile                                 │
│  - Request a new program                                         │
│  - Log training results                                          │
│  - View program with rationale                                   │
└───────────────────────┬──────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│                    AGENT ORCHESTRATOR                             │
│                                                                  │
│  Step 1: ASSESS  ──→  Gather athlete context, determine needs    │
│  Step 2: PLAN    ──→  Select phase, structure, parameters        │
│  Step 3: RETRIEVE ──→ Pull relevant knowledge + constraints      │
│  Step 4: GENERATE ──→ Build the program session by session       │
│  Step 5: VALIDATE ──→ Check against principles + Prilepin's      │
│  Step 6: EXPLAIN  ──→ Generate rationale for key decisions       │
│                                                                  │
└───────┬──────────┬──────────┬──────────┬─────────────────────────┘
        │          │          │          │
        ▼          ▼          ▼          ▼
┌────────────┐ ┌────────┐ ┌──────────┐ ┌──────────────────────────┐
│ Athlete DB │ │ Vector │ │Structured│ │ LLM                      │
│ (profiles, │ │ Store  │ │ Tables   │ │ (Claude for generation,  │
│  logs,     │ │(chunks)│ │(Prilepin,│ │  explanation, exercise   │
│  outcomes) │ │        │ │ exercises│ │  selection reasoning)    │
│            │ │        │ │ principles│ │                          │
└────────────┘ └────────┘ └──────────┘ └──────────────────────────┘
```

---

## Project Structure

The agent lives alongside the ingestion pipeline in a monorepo. Shared modules (database access, vector search, config) move to a common package.

```
oly-program-generator/
├── shared/                          # Common modules used by both pipeline and agent
│   ├── __init__.py
│   ├── config.py                    # Unified settings (DB, API keys, model config)
│   ├── db.py                        # Postgres connection pool + helpers
│   ├── llm.py                       # Anthropic client init + cost estimation
│   ├── vector_search.py             # VectorLoader (moved from ingestion)
│   └── prilepin.py                  # Prilepin's chart lookup + zone helpers
│
├── oly-ingestion/                   # Phase 1-5 ingestion pipeline (existing)
│   ├── pipeline.py
│   ├── extractors/
│   ├── processors/
│   ├── loaders/
│   └── ...
│
├── oly-agent/                       # Phase 6 programming agent (new)
│   ├── __init__.py
│   ├── orchestrator.py              # Main 6-step pipeline: assess → explain
│   ├── assess.py                    # Step 1: gather athlete context
│   ├── plan.py                      # Step 2: phase selection, week targets, session templates
│   ├── retrieve.py                  # Step 3: fault exercises, templates, vector search
│   ├── generate.py                  # Step 4: per-session LLM generation + JSON parsing
│   ├── validate.py                  # Step 5: Prilepin's, principles, constraints
│   ├── explain.py                   # Step 6: program-level rationale
│   ├── models.py                    # Data classes: AthleteContext, ProgramPlan, etc.
│   ├── phase_profiles.py            # Week-to-week progression curves per phase
│   ├── session_templates.py         # Session distribution logic (3-day, 4-day, 5-day)
│   ├── weight_resolver.py           # intensity_pct × maxes → absolute_weight_kg
│   └── feedback.py                  # ProgramOutcome computation, max promotion
│
├── schema.sql                       # Phase 1-5 DDL + seed data
├── athlete_schema.sql               # Phase 6 athlete + program DDL
├── docker-compose.yml
├── requirements.txt
├── .env
└── .gitignore
```

**What moves to `shared/`:** The `VectorLoader` (similarity search), database connection logic, config, and LLM client initialization currently live inside `oly-ingestion/` or are new for the agent. Since the agent needs the same database connection and vector search, these move to `shared/` and both packages import from there. The ingestion-specific code (chunker, classifier, extractors) stays in `oly-ingestion/`. New shared modules: `llm.py` (Anthropic client + cost estimation) and `prilepin.py` (zone lookups).

---

## Agent Configuration

```python
# shared/config.py (extend existing Settings)

@dataclass
class AgentSettings:
    # ── LLM ──────────────────────────────────────────────────
    generation_model: str = "claude-sonnet-4-20250514"
    generation_max_tokens: int = 4096
    generation_temperature: float = 0.3     # low temp for structured output consistency
    explanation_model: str = "claude-sonnet-4-20250514"
    explanation_temperature: float = 0.7    # slightly higher for natural prose

    # ── Retry / error handling ───────────────────────────────
    max_generation_retries: int = 2         # retries per session on validation failure
    max_parse_retries: int = 2              # retries on malformed JSON
    retry_delay_seconds: float = 1.0

    # ── Retrieval ────────────────────────────────────────────
    vector_search_top_k: int = 5            # chunks per query
    max_principles_per_session: int = 10    # cap to avoid prompt bloat
    max_template_references: int = 3

    # ── Generation constraints ───────────────────────────────
    max_exercises_per_session: int = 6      # hard cap regardless of principles
    min_exercises_per_session: int = 3
    max_sessions_per_week: int = 6
    max_program_weeks: int = 12

    # ── Cost tracking ────────────────────────────────────────
    track_token_usage: bool = True          # log input/output tokens per call
    cost_limit_per_program: float = 1.00    # abort if estimated cost exceeds this
```

**LLM client initialization:**

The agent uses the Anthropic Python SDK (`anthropic` package) for all LLM calls. A single client instance is created at startup and passed through the pipeline.

```python
# shared/llm.py
"""
LLM client initialization. Single client instance shared across agent steps.
"""

from anthropic import Anthropic


def create_llm_client(settings) -> Anthropic:
    """Create the Anthropic client for the agent.

    Used by:
    - generate.py (Step 4: per-session program generation)
    - explain.py (Step 6: program rationale)
    - plan.py (Step 2: optional, for ambiguous planning decisions)
    """
    if not settings.anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required for the programming agent. "
            "Set it in .env or pass via environment variable."
        )
    return Anthropic(api_key=settings.anthropic_api_key)


# Token cost estimates (as of 2025, Claude Sonnet)
# Used for cost tracking in generation_log
COST_PER_INPUT_TOKEN = 3.0 / 1_000_000    # $3.00 per 1M input tokens
COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000  # $15.00 per 1M output tokens


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for an LLM call."""
    return (input_tokens * COST_PER_INPUT_TOKEN +
            output_tokens * COST_PER_OUTPUT_TOKEN)
```

The orchestrator creates the client once and passes it to each step:

```python
# In oly-agent/orchestrator.py (pseudocode)
from shared.llm import create_llm_client, estimate_cost

client = create_llm_client(settings)

# Cost tracking: the orchestrator sums estimated_cost_usd across all
# generation calls and aborts if it exceeds settings.cost_limit_per_program.
cumulative_cost = 0.0

for week in weeks:
    for session in sessions:
        result = generate_session_with_retries(...)
        cumulative_cost += estimate_cost(result.input_tokens, result.output_tokens)

        if cumulative_cost > settings.cost_limit_per_program:
            logger.error(
                f"Cost limit exceeded: ${cumulative_cost:.4f} > "
                f"${settings.cost_limit_per_program:.2f}. "
                f"Aborting program generation."
            )
            # Mark program as 'draft' with partial results
            break
```

---

## Athlete Data Model

These tables extend the existing schema. They live in the same Postgres instance alongside the knowledge base tables.

### Entity Relationships

```
athletes ─────────┬── athlete_maxes (current + historical PRs)
                  │
                  ├── athlete_goals
                  │
                  ├── generated_programs ─── program_sessions ─── session_exercises
                  │
                  └── training_logs ─── training_log_exercises
                                              │
                                              └── (FK to exercises table from Phase 1)
```

### Schema

```sql
-- ============================================================
-- ATHLETE DATA MODEL
-- Run after the Phase 1-5 schema (schema.sql)
-- ============================================================

-- ── Athlete Profiles ────────────────────────────────────────

CREATE TYPE athlete_level AS ENUM (
    'beginner',         -- <1 year of weightlifting-specific training
    'intermediate',     -- 1-3 years, consistent technique on comp lifts
    'advanced',         -- 3-7 years, competing regularly
    'elite'             -- 7+ years, national/international level
);

CREATE TYPE biological_sex AS ENUM ('male', 'female');

CREATE TABLE athletes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(300),
    level athlete_level NOT NULL,
    biological_sex biological_sex,

    -- Physical attributes (nullable — not all athletes will provide)
    bodyweight_kg NUMERIC(5,1),
    height_cm NUMERIC(5,1),
    age INT,
    weight_class VARCHAR(20),              -- e.g. '89', '102', '73' (kg)

    -- Training context
    training_age_years NUMERIC(4,1),       -- years of weightlifting-specific training
    sessions_per_week INT DEFAULT 4
        CHECK (sessions_per_week BETWEEN 1 AND 14),
    session_duration_minutes INT DEFAULT 90,
    available_equipment TEXT[],             -- e.g. {'barbell', 'blocks', 'jerk_blocks', 'straps'}

    -- Known limitations and preferences
    injuries TEXT[],                        -- e.g. {'left_wrist_soreness', 'lower_back_fatigue'}
    technical_faults TEXT[],               -- maps to exercises.faults_addressed vocabulary
                                           -- e.g. {'slow_turnover', 'forward_balance_off_floor'}
    exercise_preferences JSONB DEFAULT '{}',
    -- Schema:
    -- {
    --   "prefer": ["hang_snatch", "power_clean"],
    --   "avoid": ["snatch_from_deficit"],     -- injury, discomfort, equipment
    --   "avoid_reasons": {"snatch_from_deficit": "no deficit platform available"}
    -- }

    notes TEXT,                             -- free-text coach/athlete notes
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);


-- ── Athlete Maxes (Current + Historical) ────────────────────
-- Tracks both current working maxes and PR history.
-- The agent uses current_max for percentage calculations.
-- Historical data feeds the feedback loop (are we progressing?).

CREATE TABLE athlete_maxes (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    exercise_id INT NOT NULL REFERENCES exercises(id),

    weight_kg NUMERIC(6,1) NOT NULL,
    is_competition_result BOOLEAN DEFAULT FALSE,  -- was this in a meet?
    rpe NUMERIC(3,1),                             -- how hard was it? (if known)
    date_achieved DATE NOT NULL,

    -- Type: 'current' rows are what the agent uses for % calculations.
    -- When a new max is set, the old 'current' becomes 'historical'.
    max_type VARCHAR(20) NOT NULL DEFAULT 'current'
        CHECK (max_type IN ('current', 'historical', 'estimated')),

    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Only one 'current' max per athlete per exercise
CREATE UNIQUE INDEX idx_maxes_unique_current
    ON athlete_maxes (athlete_id, exercise_id)
    WHERE max_type = 'current';

CREATE INDEX idx_maxes_athlete ON athlete_maxes (athlete_id);
CREATE INDEX idx_maxes_current ON athlete_maxes (athlete_id, max_type)
    WHERE max_type = 'current';


-- ── Athlete Goals ───────────────────────────────────────────
-- What is the athlete working toward? This drives phase selection,
-- exercise selection, and intensity targets.

CREATE TYPE goal_type AS ENUM (
    'competition_prep',     -- peaking for a specific meet
    'general_strength',     -- off-season base building
    'technique_focus',      -- fixing faults, refining positions
    'pr_attempt',           -- training cycle aimed at new maxes in the gym
    'return_to_sport',      -- coming back from injury or layoff
    'work_capacity'         -- building volume tolerance, GPP
);

CREATE TABLE athlete_goals (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    goal goal_type NOT NULL,

    -- For competition_prep
    competition_date DATE,
    competition_name VARCHAR(200),
    target_total_kg NUMERIC(6,1),          -- desired snatch + C&J total

    -- For PR attempts
    target_snatch_kg NUMERIC(6,1),
    target_cj_kg NUMERIC(6,1),

    -- For technique focus
    target_faults TEXT[],                   -- which faults to address this cycle

    -- General
    priority INT DEFAULT 1                 -- 1 = primary goal
        CHECK (priority BETWEEN 1 AND 5),
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_goals_athlete ON athlete_goals (athlete_id);
CREATE INDEX idx_goals_active ON athlete_goals (athlete_id, is_active)
    WHERE is_active = TRUE;


-- ── Generated Programs ──────────────────────────────────────
-- The output of the agent. Each row is a complete mesocycle
-- (typically 3-8 weeks). Links back to the athlete and the
-- knowledge sources that informed it.

CREATE TYPE program_status AS ENUM (
    'draft',        -- generated but not reviewed
    'active',       -- athlete is currently running this
    'completed',    -- finished, ready for outcome analysis
    'abandoned',    -- stopped early (injury, schedule change, etc.)
    'superseded'    -- replaced by a new program
);

CREATE TABLE generated_programs (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,

    -- Program metadata
    name VARCHAR(300),
    status program_status NOT NULL DEFAULT 'draft',
    phase training_phase NOT NULL,          -- from existing enum
    duration_weeks INT NOT NULL CHECK (duration_weeks BETWEEN 1 AND 16),
    sessions_per_week INT NOT NULL,
    start_date DATE,
    end_date DATE,

    -- What goal was this program built for?
    goal_id INT REFERENCES athlete_goals(id),

    -- Agent context snapshot (reproducibility)
    athlete_snapshot JSONB NOT NULL,        -- athlete profile at generation time
    maxes_snapshot JSONB NOT NULL,          -- maxes used for % calculations
    generation_params JSONB NOT NULL,       -- model, temperature, retrieval config
    knowledge_sources_used JSONB,           -- which chunks/principles/templates informed this

    -- Program-level rationale (LLM-generated explanation)
    rationale TEXT,

    -- Outcome tracking (filled after completion)
    outcome_summary JSONB,
    -- Schema:
    -- {
    --   "maxes_before": {"snatch": 100, "cj": 125},
    --   "maxes_after": {"snatch": 103, "cj": 127},
    --   "adherence_pct": 85,
    --   "avg_rpe_deviation": 0.3,
    --   "athlete_feedback": "Felt strong weeks 3-4, fatigued week 5"
    -- }

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_programs_athlete ON generated_programs (athlete_id);
CREATE INDEX idx_programs_status ON generated_programs (status);
CREATE INDEX idx_programs_active ON generated_programs (athlete_id, status)
    WHERE status = 'active';


-- ── Program Sessions (day-level prescriptions) ──────────────
-- Each row = one training session within a generated program.

CREATE TABLE program_sessions (
    id SERIAL PRIMARY KEY,
    program_id INT NOT NULL REFERENCES generated_programs(id) ON DELETE CASCADE,
    week_number INT NOT NULL,
    day_number INT NOT NULL,
    session_label VARCHAR(200),             -- e.g. 'Monday — Snatch + Squat'

    -- Session-level targets
    estimated_duration_minutes INT,
    session_rpe_target NUMERIC(3,1),
    focus_area VARCHAR(100),                -- e.g. 'snatch_technique', 'heavy_pulls'

    notes TEXT,

    UNIQUE(program_id, week_number, day_number)
);

CREATE INDEX idx_sessions_program ON program_sessions (program_id);


-- ── Session Exercises (exercise-level prescriptions) ────────
-- Each row = one exercise prescription within a session.
-- This is what the athlete actually sees and executes.

CREATE TABLE session_exercises (
    id SERIAL PRIMARY KEY,
    session_id INT NOT NULL REFERENCES program_sessions(id) ON DELETE CASCADE,
    exercise_order INT NOT NULL,

    -- What to do
    exercise_id INT REFERENCES exercises(id),
    complex_id INT REFERENCES exercise_complexes(id),  -- if it's a complex
    exercise_name VARCHAR(200) NOT NULL,                -- denormalized for display

    -- How much
    sets INT NOT NULL CHECK (sets >= 1),
    reps INT NOT NULL CHECK (reps >= 1),
    intensity_pct NUMERIC(5,2)
        CHECK (intensity_pct IS NULL OR (intensity_pct > 0 AND intensity_pct <= 120)),
    intensity_reference VARCHAR(100),       -- whose 1RM? e.g. 'snatch', 'back_squat'
    absolute_weight_kg NUMERIC(6,1),        -- resolved from athlete_maxes × intensity_pct

    -- Modifiers
    rpe_target NUMERIC(3,1),
    tempo VARCHAR(20),
    rest_seconds INT,
    backoff_sets INT DEFAULT 0,
    backoff_intensity_pct NUMERIC(5,2),
    is_max_attempt BOOLEAN DEFAULT FALSE,

    -- Why this exercise was selected (traceability)
    selection_rationale TEXT,                -- e.g. 'Addresses slow_turnover fault per principle #12'
    source_principle_ids INT[],             -- FK refs to programming_principles
    source_chunk_ids INT[],                 -- FK refs to knowledge_chunks

    notes TEXT,

    UNIQUE(session_id, exercise_order)
);

CREATE INDEX idx_session_exercises_session ON session_exercises (session_id);
CREATE INDEX idx_session_exercises_exercise ON session_exercises (exercise_id);


-- ── Training Logs (what actually happened) ──────────────────
-- Athlete records what they did. This is the feedback signal.

CREATE TABLE training_logs (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    session_id INT REFERENCES program_sessions(id),  -- nullable: athlete may log ad-hoc sessions
    log_date DATE NOT NULL,

    -- Session-level feedback
    overall_rpe NUMERIC(3,1),
    session_duration_minutes INT,
    bodyweight_kg NUMERIC(5,1),
    sleep_quality INT CHECK (sleep_quality IS NULL OR sleep_quality BETWEEN 1 AND 5),
    stress_level INT CHECK (stress_level IS NULL OR stress_level BETWEEN 1 AND 5),
    athlete_notes TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_logs_athlete ON training_logs (athlete_id);
CREATE INDEX idx_logs_date ON training_logs (athlete_id, log_date);
CREATE INDEX idx_logs_session ON training_logs (session_id);


-- ── Training Log Exercises (what was lifted) ────────────────
-- Per-exercise results within a training session.

CREATE TABLE training_log_exercises (
    id SERIAL PRIMARY KEY,
    log_id INT NOT NULL REFERENCES training_logs(id) ON DELETE CASCADE,

    -- What was prescribed (link back to program)
    session_exercise_id INT REFERENCES session_exercises(id),

    -- What was actually done
    exercise_id INT REFERENCES exercises(id),
    exercise_name VARCHAR(200) NOT NULL,    -- denormalized
    sets_completed INT NOT NULL,
    reps_per_set INT[],                     -- array: [3, 3, 3, 2, 1] for a drop-off set
    weight_kg NUMERIC(6,1) NOT NULL,

    -- Quality metrics
    rpe NUMERIC(3,1),
    make_rate NUMERIC(3,2),                 -- 0.0-1.0: what % of attempts were made?
    technical_notes TEXT,                    -- e.g. 'bar drifted forward on rep 2'
    video_url VARCHAR(500),                 -- optional: link to video for review

    -- Deviation from prescription
    prescribed_weight_kg NUMERIC(6,1),      -- what was the program?
    weight_deviation_kg NUMERIC(6,1),       -- actual - prescribed (positive = heavier)
    rpe_deviation NUMERIC(3,1),             -- actual RPE - target RPE

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_log_exercises_log ON training_log_exercises (log_id);
CREATE INDEX idx_log_exercises_exercise ON training_log_exercises (exercise_id);
CREATE INDEX idx_log_exercises_prescribed ON training_log_exercises (session_exercise_id);


-- ── Generation Log (tracks each LLM call during program generation) ─
-- One row per session generation attempt. Enables:
--   - Cost tracking (input/output tokens per call)
--   - Debugging bad generations (full prompt + response stored)
--   - Retry history (attempt_number tracks retries)
--   - Session context continuity (what was already prescribed this week)

CREATE TABLE generation_log (
    id SERIAL PRIMARY KEY,
    program_id INT NOT NULL REFERENCES generated_programs(id) ON DELETE CASCADE,
    session_id INT REFERENCES program_sessions(id),

    -- Which session this was for
    week_number INT NOT NULL,
    day_number INT NOT NULL,
    attempt_number INT NOT NULL DEFAULT 1,

    -- LLM call details
    model VARCHAR(100) NOT NULL,
    prompt_text TEXT NOT NULL,              -- full prompt sent to LLM
    raw_response TEXT,                      -- raw LLM output (before parsing)
    parsed_response JSONB,                 -- successfully parsed JSON (if any)

    -- Token tracking for cost
    input_tokens INT,
    output_tokens INT,
    estimated_cost_usd NUMERIC(8,4),

    -- Outcome
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'success', 'parse_error', 'validation_error', 'failed')),
    validation_errors TEXT[],              -- errors from Step 5 that triggered a retry
    error_message TEXT,                    -- parse errors, API errors, etc.

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_generation_log_program ON generation_log (program_id);
CREATE INDEX idx_generation_log_session ON generation_log (week_number, day_number);
```

---

## Cold Start: First-Time Athlete Onboarding

When an athlete has no training history, no previous programs, and potentially incomplete maxes, the agent needs enough information to produce a reasonable first program.

### Required Inputs (minimum viable athlete)

The athlete must provide at minimum:

1. **Competition lift maxes** — Snatch and Clean & Jerk (gym or competition). These are non-negotiable; percentage-based programming doesn't work without them.
2. **Training experience level** — beginner/intermediate/advanced/elite. Self-reported is fine for V1.
3. **Sessions per week** — how many days can they train?

### Max Estimation from Competition Lifts

If the athlete only provides Snatch and C&J maxes, the agent estimates related maxes using established ratios. These get stored as `max_type = 'estimated'` and are replaced by real maxes as the athlete logs training data.

```python
# oly-agent/assess.py

# Typical ratios relative to competition lifts (from Everett, Takano, Medvedev).
# These are starting estimates — actual ratios vary widely by athlete.
MAX_ESTIMATION_RATIOS = {
    # Relative to Clean & Jerk
    "front_squat": {"reference": "clean_and_jerk", "ratio": 1.10},   # FS ≈ 110% of C&J
    "back_squat": {"reference": "clean_and_jerk", "ratio": 1.30},    # BS ≈ 130% of C&J
    "clean_pull": {"reference": "clean_and_jerk", "ratio": 1.10},    # CP ≈ 110% of C&J
    "clean_deadlift": {"reference": "clean_and_jerk", "ratio": 1.20},
    "push_press": {"reference": "clean_and_jerk", "ratio": 0.75},    # PP ≈ 75% of C&J

    # Relative to Snatch
    "snatch_pull": {"reference": "snatch", "ratio": 1.15},           # SP ≈ 115% of SN
    "snatch_deadlift": {"reference": "snatch", "ratio": 1.25},
    "overhead_squat": {"reference": "snatch", "ratio": 0.90},        # OHS ≈ 90% of SN
}

def estimate_missing_maxes(
    known_maxes: dict[str, float],
) -> dict[str, tuple[float, str]]:
    """Estimate missing maxes from known competition lift maxes.

    Returns: {exercise_name: (estimated_kg, "estimated")}
    """
    estimated = {}
    for exercise, config in MAX_ESTIMATION_RATIOS.items():
        if exercise not in known_maxes:
            ref_max = known_maxes.get(config["reference"])
            if ref_max:
                estimated[exercise] = (
                    round(ref_max * config["ratio"] * 2) / 2,  # round to 0.5kg
                    "estimated",
                )
    return estimated
```

### Cold-Start Program Defaults

For first-time athletes with no training history:

- **Phase:** Always `accumulation` or `general_prep` — never start with intensification
- **Duration:** 4 weeks (shorter cycle to get data faster)
- **Intensity:** Conservative ceiling (80% for intermediates, 75% for beginners)
- **Volume:** Mid-range Prilepin's targets (not pushing the upper limits)
- **Deload:** Week 4 (deload built into the first cycle to teach the pattern)
- **Exercise selection:** Favor simpler variations (lower `complexity_level`), competition lifts + basic strength work. Avoid advanced positional drills until faults are identified from training logs.

The ASSESS step's `previous_program` and `recent_logs` fields will be `None` — the PLAN step handles this explicitly:

```python
# In plan.py
if athlete_context.previous_program is None:
    # Cold start: conservative defaults
    plan.intensity_ceiling = min(plan.intensity_ceiling, 80.0)
    plan.duration_weeks = min(plan.duration_weeks, 4)
    # Bias toward lower complexity exercises
    max_complexity = 2 if athlete_context.level == "beginner" else 3
```

---

## Agent Orchestration

The agent runs as a six-step pipeline. Each step has a clear input, output, and retrieval strategy. Steps 1-3 gather context, step 4 generates, step 5 validates, step 6 explains.

### Step 1: ASSESS — Gather Athlete Context

**Purpose:** Build a complete picture of where the athlete is right now.

**Inputs:** `athlete_id`

**Queries:**
```sql
-- Current profile
SELECT * FROM athletes WHERE id = $athlete_id;

-- Active goal
SELECT * FROM athlete_goals
WHERE athlete_id = $athlete_id AND is_active = TRUE
ORDER BY priority LIMIT 1;

-- Current maxes
SELECT e.name, e.movement_family, am.weight_kg, am.date_achieved, am.rpe
FROM athlete_maxes am
JOIN exercises e ON am.exercise_id = e.id
WHERE am.athlete_id = $athlete_id AND am.max_type = 'current';

-- Recent training history (last completed program, if any)
SELECT gp.phase, gp.duration_weeks, gp.outcome_summary
FROM generated_programs gp
WHERE gp.athlete_id = $athlete_id AND gp.status = 'completed'
ORDER BY gp.end_date DESC LIMIT 1;

-- Recent training load (last 2 weeks of logs)
SELECT tle.exercise_name, tle.weight_kg, tle.sets_completed,
       tle.rpe, tle.make_rate, tl.log_date
FROM training_log_exercises tle
JOIN training_logs tl ON tle.log_id = tl.id
WHERE tl.athlete_id = $athlete_id
  AND tl.log_date >= CURRENT_DATE - INTERVAL '14 days'
ORDER BY tl.log_date DESC;
```

**Output:** `AthleteContext` — a structured object containing profile, maxes, goal, recent history, and readiness signals.

```python
@dataclass
class AthleteContext:
    athlete: dict                   # full profile row
    level: str                      # beginner/intermediate/advanced/elite
    maxes: dict[str, float]         # {"snatch": 100.0, "clean_and_jerk": 125.0, ...}
                                    # Built from DB rows via weight_resolver.build_maxes_dict()
                                    # Keys are intensity_reference strings, not exercise names
    active_goal: dict | None        # goal row or None
    previous_program: dict | None   # last completed program summary
    recent_logs: list[dict]         # last 2 weeks of training data
    technical_faults: list[str]     # from athlete profile
    injuries: list[str]
    sessions_per_week: int
    weeks_to_competition: int | None  # calculated from goal.competition_date
```

### Step 2: PLAN — Determine Program Parameters

**Purpose:** Decide the *shape* of the program before generating any exercises. This is where the agent picks the training phase, block duration, volume/intensity targets, and session structure.

**Decision tree:**

```
Has competition date?
├─ YES → How many weeks out?
│   ├─ >12 weeks → accumulation or general_prep
│   ├─ 8-12 weeks → accumulation → intensification
│   ├─ 4-8 weeks → intensification → realization
│   └─ <4 weeks → realization → competition
│
└─ NO → What's the goal?
    ├─ general_strength → accumulation (4-6 weeks)
    ├─ technique_focus → accumulation with positional emphasis (4 weeks)
    ├─ pr_attempt → intensification → realization (4-6 weeks)
    ├─ work_capacity → general_prep (4-6 weeks)
    └─ return_to_sport → general_prep, reduced intensity (3-4 weeks)
```

**Phase progression profiles** — concrete week-to-week intensity and volume curves. These are deterministic (not LLM-generated) and parameterized by phase, duration, and athlete level.

```python
# oly-agent/phase_profiles.py
"""
Week-to-week progression curves for each training phase.
These define the shape of the program: how intensity climbs,
how volume tapers, and where the deload sits.

All values are guidelines for competition lifts (snatch, C&J).
Strength work (squats, pulls) follows similar curves but with
different absolute percentages (resolved via intensity_reference).
"""

PHASE_PROFILES = {
    "accumulation": {
        # 4-week accumulation: moderate intensity, high volume, linear ramp
        "description": "Build work capacity with moderate loads. Volume is primary driver.",
        "default_weeks": 4,
        "deload_week": 4,           # last week is a deload
        "weeks": {
            1: {"intensity_floor": 68, "intensity_ceiling": 76, "volume_modifier": 0.85, "reps_per_set_range": [3, 5]},
            2: {"intensity_floor": 70, "intensity_ceiling": 78, "volume_modifier": 1.00, "reps_per_set_range": [3, 5]},
            3: {"intensity_floor": 72, "intensity_ceiling": 80, "volume_modifier": 1.00, "reps_per_set_range": [2, 5]},
            4: {"intensity_floor": 65, "intensity_ceiling": 73, "volume_modifier": 0.60, "reps_per_set_range": [2, 4]},  # deload
        },
        "intensity_progression": "linear",   # +2-3% per week
        "volume_trend": "stable_then_deload", # weeks 1-3 stable, week 4 drops
    },

    "intensification": {
        # 4-week intensification: intensity climbs, volume drops
        "description": "Shift emphasis from volume to intensity. Fewer reps, heavier loads.",
        "default_weeks": 4,
        "deload_week": None,        # no deload — this phase IS the ramp
        "weeks": {
            1: {"intensity_floor": 75, "intensity_ceiling": 83, "volume_modifier": 1.00, "reps_per_set_range": [2, 4]},
            2: {"intensity_floor": 78, "intensity_ceiling": 86, "volume_modifier": 0.90, "reps_per_set_range": [2, 3]},
            3: {"intensity_floor": 80, "intensity_ceiling": 90, "volume_modifier": 0.80, "reps_per_set_range": [1, 3]},
            4: {"intensity_floor": 83, "intensity_ceiling": 93, "volume_modifier": 0.70, "reps_per_set_range": [1, 2]},
        },
        "intensity_progression": "linear",   # +3% per week
        "volume_trend": "descending",         # steady decline
    },

    "realization": {
        # 3-week realization / peaking: high intensity, minimal volume
        "description": "Peak for competition or PR attempts. Heavy singles and doubles.",
        "default_weeks": 3,
        "deload_week": 3,           # final week is the taper
        "weeks": {
            1: {"intensity_floor": 85, "intensity_ceiling": 95, "volume_modifier": 0.65, "reps_per_set_range": [1, 2]},
            2: {"intensity_floor": 88, "intensity_ceiling": 98, "volume_modifier": 0.50, "reps_per_set_range": [1, 2]},
            3: {"intensity_floor": 70, "intensity_ceiling": 85, "volume_modifier": 0.35, "reps_per_set_range": [1, 2]},  # taper
        },
        "intensity_progression": "peak_then_taper",
        "volume_trend": "sharply_descending",
    },

    "general_prep": {
        # 4-6 week GPP: broad base, moderate everything
        "description": "General preparation. Balanced volume and intensity, broad exercise selection.",
        "default_weeks": 5,
        "deload_week": 5,
        "weeks": {
            1: {"intensity_floor": 65, "intensity_ceiling": 73, "volume_modifier": 0.80, "reps_per_set_range": [3, 6]},
            2: {"intensity_floor": 67, "intensity_ceiling": 75, "volume_modifier": 0.90, "reps_per_set_range": [3, 6]},
            3: {"intensity_floor": 68, "intensity_ceiling": 77, "volume_modifier": 1.00, "reps_per_set_range": [3, 5]},
            4: {"intensity_floor": 70, "intensity_ceiling": 78, "volume_modifier": 1.00, "reps_per_set_range": [3, 5]},
            5: {"intensity_floor": 63, "intensity_ceiling": 70, "volume_modifier": 0.55, "reps_per_set_range": [2, 4]},  # deload
        },
        "intensity_progression": "gradual_linear", # +1-2% per week
        "volume_trend": "ascending_then_deload",
    },
}


def build_weekly_targets(phase: str, duration_weeks: int, athlete_level: str) -> list:
    """Build WeekTarget objects from a phase profile.

    Adjustments by athlete level:
    - Beginners: intensity ceiling reduced 3-5%, volume increased 10%
    - Advanced: intensity ceiling increased 2%, volume reduced 5%
    - Elite: wider intensity range, volume per Prilepin's optimal
    """
    profile = PHASE_PROFILES[phase]

    # If requested duration differs from default, adjust the profile
    base_weeks = list(profile["weeks"].items())
    deload_week = profile.get("deload_week")

    if duration_weeks > len(base_weeks):
        # Extend by repeating middle weeks (the "working" weeks, not deload).
        # Example: 4-week accumulation (weeks 1,2,3,deload) extended to 6 weeks
        # → weeks 1,2,3,2,3,deload (repeat middle, push deload to end)
        working_weeks = [(k, v) for k, v in base_weeks if k != deload_week]
        deload_data = [(k, v) for k, v in base_weeks if k == deload_week]
        extra_needed = duration_weeks - len(base_weeks)

        # Repeat from the middle of working weeks
        mid = len(working_weeks) // 2
        repeat_pool = working_weeks[mid:]
        extended = list(working_weeks)
        for i in range(extra_needed):
            extended.append(repeat_pool[i % len(repeat_pool)])
        if deload_data:
            extended.append(deload_data[0])

        # Re-number weeks sequentially
        base_weeks = [(i + 1, data) for i, (_, data) in enumerate(extended)]
        deload_week = duration_weeks  # deload is always the last week

    elif duration_weeks < len(base_weeks):
        # Compress by dropping early ramp-up weeks (keep the peak + deload).
        # Example: 5-week general_prep compressed to 3 weeks
        # → drop weeks 1-2, keep weeks 3,4,deload
        trim = len(base_weeks) - duration_weeks
        trimmed = base_weeks[trim:]
        base_weeks = [(i + 1, data) for i, (_, data) in enumerate(trimmed)]
        if deload_week:
            deload_week = duration_weeks

    level_adjustments = {
        "beginner":     {"intensity_offset": -4, "volume_scale": 1.10},
        "intermediate": {"intensity_offset":  0, "volume_scale": 1.00},
        "advanced":     {"intensity_offset": +2, "volume_scale": 0.95},
        "elite":        {"intensity_offset": +3, "volume_scale": 0.90},
    }
    adj = level_adjustments.get(athlete_level, level_adjustments["intermediate"])

    targets = []
    for week_num, week_data in base_weeks:
        if week_num > duration_weeks:
            break
        targets.append({
            "week_number": week_num,
            "intensity_floor": week_data["intensity_floor"] + adj["intensity_offset"],
            "intensity_ceiling": min(week_data["intensity_ceiling"] + adj["intensity_offset"], 100),
            "volume_modifier": week_data["volume_modifier"] * adj["volume_scale"],
            "reps_per_set_range": week_data["reps_per_set_range"],
            "is_deload": week_num == deload_week,
        })

    return targets
```

**Session distribution logic** — maps `sessions_per_week` to concrete session templates. This defines the program → session → day hierarchy.

```python
# oly-agent/session_templates.py
"""
Session templates by training frequency.

Hierarchy:
  Program (e.g., 4-week accumulation)
    └── Week (e.g., Week 2)
        └── Session (e.g., Day 1: Snatch + Squat)
            └── Exercise (e.g., Snatch 5×2 @ 78%)

Each session template specifies:
  - Which movement family is primary (the competition lift)
  - What supporting work follows (squats, pulls, accessories)
  - What share of weekly volume this session carries
"""

SESSION_DISTRIBUTIONS = {
    3: {
        "description": "3-day: each session covers one competition lift + strength",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.35,
                "notes": "Snatch emphasis day. Back squat or front squat after.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Pulls",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "pull"],
                "session_volume_share": 0.40,
                "notes": "C&J emphasis. Pulling work supports clean positions.",
            },
            {
                "day_number": 3,
                "label": "Snatch + Clean (light) + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["clean", "squat"],
                "session_volume_share": 0.25,
                "notes": "Lighter session. Both lifts at reduced intensity + squat.",
            },
        ],
    },

    4: {
        "description": "4-day: alternating snatch/C&J focus with dedicated squat work",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.30,
                "notes": "Primary snatch day. Heavy squat follows.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Pulls",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "pull"],
                "session_volume_share": 0.30,
                "notes": "Primary C&J day. Clean pulls or snatch pulls after.",
            },
            {
                "day_number": 3,
                "label": "Snatch Variations + Accessories",
                "primary_movement": "snatch",
                "secondary_movements": ["pull", "accessory"],
                "session_volume_share": 0.20,
                "notes": "Snatch variant work (hang, power, positional). Lighter day.",
            },
            {
                "day_number": 4,
                "label": "Clean & Jerk + Squat",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "squat"],
                "session_volume_share": 0.20,
                "notes": "C&J variant or complex. Front squat emphasis.",
            },
        ],
    },

    5: {
        "description": "5-day: high frequency with dedicated technique and squat days",
        "sessions": [
            {
                "day_number": 1,
                "label": "Snatch + Back Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.25,
                "notes": "Heavy snatch day. Back squat primary strength.",
            },
            {
                "day_number": 2,
                "label": "Clean & Jerk + Pulls",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "pull"],
                "session_volume_share": 0.25,
                "notes": "Heavy C&J day. Pulling work.",
            },
            {
                "day_number": 3,
                "label": "Snatch Technique + Front Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["squat"],
                "session_volume_share": 0.15,
                "notes": "Lighter snatch work. Positional drills. Front squat.",
            },
            {
                "day_number": 4,
                "label": "Clean Technique + Accessories",
                "primary_movement": "clean",
                "secondary_movements": ["jerk", "accessory"],
                "session_volume_share": 0.15,
                "notes": "Clean variants, jerk practice. Lighter session.",
            },
            {
                "day_number": 5,
                "label": "Heavy Singles / Complex + Squat",
                "primary_movement": "snatch",
                "secondary_movements": ["clean", "squat"],
                "session_volume_share": 0.20,
                "notes": "Both lifts. Work to heavy singles or complexes. Squat.",
            },
        ],
    },
}


def get_session_templates(sessions_per_week: int) -> list[dict]:
    """Get session templates for a given training frequency."""
    if sessions_per_week not in SESSION_DISTRIBUTIONS:
        # Default to closest supported frequency
        closest = min(SESSION_DISTRIBUTIONS.keys(),
                      key=lambda k: abs(k - sessions_per_week))
        return SESSION_DISTRIBUTIONS[closest]["sessions"]
    return SESSION_DISTRIBUTIONS[sessions_per_week]["sessions"]
```

```sql
-- Get applicable programming principles for this phase + athlete level
SELECT principle_name, recommendation, rationale, priority
FROM programming_principles
WHERE (condition->>'phase' IS NULL OR condition->>'phase' = $phase)
  AND (condition->'athlete_level' IS NULL
       OR condition->'athlete_level' @> to_jsonb($athlete_level)::jsonb)
ORDER BY priority DESC;

-- Get Prilepin's targets for expected intensity zones
SELECT * FROM prilepin_chart
WHERE movement_type = 'competition_lifts'
  AND $target_intensity BETWEEN intensity_range_low AND intensity_range_high;
```

**Retrieval — vector search:**

```python
# Semantic search for phase-specific programming rationale
chunks = vector_loader.similarity_search(
    query=f"programming {phase} phase for {athlete_level} weightlifter",
    top_k=5,
    chunk_types=["periodization", "programming_rationale"],
    topics=[f"{phase}_phase", "volume_management", "intensity_prescription"],
    athlete_level=athlete_level,
)
```

**Output:** `ProgramPlan`

```python
@dataclass
class ProgramPlan:
    phase: str                        # training_phase enum value
    duration_weeks: int
    sessions_per_week: int
    deload_week: int | None           # which week is the deload? (e.g., 4)

    # Volume and intensity envelopes (per week)
    weekly_targets: list[WeekTarget]

    # Session templates (what kind of session each day is)
    session_templates: list[SessionTemplate]

    # Which principles are driving this plan
    active_principles: list[dict]

    # Retrieved context that informed the plan
    supporting_chunks: list[dict]


@dataclass
class WeekTarget:
    week_number: int
    volume_modifier: float             # 1.0 = baseline, 0.6 = deload
    intensity_floor: float             # min % for competition lifts
    intensity_ceiling: float           # max % for competition lifts
    total_competition_lift_reps: int   # target from Prilepin's
    is_deload: bool


@dataclass
class SessionTemplate:
    day_number: int
    label: str                         # e.g. 'Snatch + Squat', 'Clean & Jerk + Pulls'
    primary_movement: str              # movement_family for the main lift
    secondary_movements: list[str]     # supporting work
    session_volume_share: float        # what % of weekly volume goes here (e.g. 0.30)
```

### Step 3: RETRIEVE — Pull Knowledge for Exercise Selection

**Purpose:** Gather the specific knowledge the LLM needs to make good exercise selection decisions. This is the most retrieval-heavy step.

**Three retrieval paths, executed in parallel:**

**Path A — Fault-based exercise lookup (structured):**
```sql
-- If the athlete has known technical faults, find exercises that address them
SELECT e.name, e.category, e.primary_purpose, e.faults_addressed,
       e.typical_intensity_low, e.typical_intensity_high,
       e.typical_sets_low, e.typical_sets_high,
       e.typical_reps_low, e.typical_reps_high
FROM exercises e
WHERE e.faults_addressed && $athlete_faults    -- array overlap
  AND e.complexity_level <= $max_complexity     -- based on athlete level
  AND e.movement_family = $target_family        -- e.g. 'snatch'
ORDER BY e.complexity_level, array_length(e.faults_addressed, 1) DESC;
```

**Path B — Template reference (structured):**
```sql
-- Find published program templates similar to what we're building
SELECT name, program_structure, notes
FROM program_templates
WHERE athlete_level IN ($athlete_level, 'any')
  AND goal = $goal_type
  AND phases_included @> ARRAY[$phase]::training_phase[]
  AND sessions_per_week BETWEEN $sessions - 1 AND $sessions + 1
ORDER BY source_id;  -- prefer higher-credibility sources
```

**Path C — Contextual reasoning (vector search):**
```python
# For each session template, retrieve relevant programming reasoning
for session in plan.session_templates:
    chunks = vector_loader.similarity_search(
        query=f"exercise selection for {session.primary_movement} "
              f"during {plan.phase} phase",
        top_k=3,
        chunk_types=["programming_rationale", "exercise_selection_rationale"],
        min_density="medium",
    )

# If athlete has specific faults, also retrieve correction strategies
if athlete_context.technical_faults:
    for fault in athlete_context.technical_faults:
        chunks = vector_loader.similarity_search(
            query=f"correcting {fault} in weightlifting",
            top_k=3,
            chunk_types=["fault_correction", "methodology"],
        )
```

**Also check exercise substitutions if athlete has injuries/restrictions:**
```sql
SELECT es.*, e_sub.name as substitute_name, e_sub.primary_purpose
FROM exercise_substitutions es
JOIN exercises e_sub ON es.substitute_exercise_id = e_sub.id
WHERE es.exercise_id IN (
    SELECT id FROM exercises WHERE movement_family = $family
)
AND es.substitution_context IN ('injury_modification', 'equipment_limitation')
AND es.exercise_id IN (
    SELECT id FROM exercises
    WHERE name = ANY($exercises_to_check)
);
```

**Output:** `RetrievalContext` — everything the LLM needs to generate the program.

```python
@dataclass
class RetrievalContext:
    fault_exercises: dict[str, list[dict]]     # fault → matching exercises
    template_references: list[dict]            # similar published programs
    programming_rationale: list[dict]          # relevant knowledge chunks
    fault_correction_chunks: list[dict]        # fault-specific guidance
    available_substitutions: dict[str, list]   # exercise → substitutes
    active_principles: list[dict]              # constraints to respect
    prilepin_targets: dict[str, dict]          # intensity zone → rep/set targets
```

### Step 4: GENERATE — Build the Program

**Purpose:** The LLM produces the actual session-by-session program using the plan from Step 2 and the retrieval context from Step 3.

**LLM strategy:** One LLM call per session (not per week, not per program). This keeps the context focused and the output manageable. A 4-week, 4-session-per-week program = 16 LLM calls, but each is small and targeted.

**Session ordering and context:** Sessions are generated in order within each week (Day 1 → Day 2 → ... → Day N), then week by week (Week 1 → Week 2 → ...). Each generation call includes a summary of what was already prescribed earlier in the same week so the LLM can manage cumulative weekly volume and avoid redundant exercise selection.

**Prompt structure (per session):**

```
SYSTEM:
You are an Olympic weightlifting programming assistant. You generate
training sessions grounded in established programming methodology.

You MUST:
- Prescribe exercises, sets, reps, and intensities as structured JSON
- Stay within the volume and intensity targets provided
- Select exercises from the provided exercise list (do not invent exercises)
- Respect all active programming principles
- Account for exercises already prescribed earlier this week (see below)
- Provide brief rationale for each exercise choice

You MUST NOT:
- Exceed the intensity ceiling for this week
- Prescribe more total reps than Prilepin's chart allows for the target intensity
- Include exercises the athlete should avoid
- Prescribe movements beyond the athlete's complexity level
- Duplicate the primary movement pattern from earlier sessions this week
  (unless the session template explicitly calls for it)

USER:
## Athlete Profile
{athlete_context as structured summary}

## Current Maxes
{maxes table}

## Program Plan
Phase: {phase}
Week {week_number} of {duration_weeks}
Volume modifier: {volume_modifier} (1.0 = baseline)
Intensity range: {intensity_floor}% - {intensity_ceiling}%
Reps per set range: {reps_per_set_range}
Competition lift rep target this session: {session_rep_target} (from Prilepin's × session_volume_share)

## Session Template
Day {day_number}: {label}
Primary movement: {primary_movement}
Secondary movements: {secondary_movements}

## Already Prescribed This Week
{summary of exercises, sets, reps, intensities from earlier sessions this week}
Cumulative competition lift reps so far: {cumulative_comp_reps}
Remaining weekly budget: {remaining_reps}

## Available Exercises
{filtered exercise list with typical prescriptions}

## Active Principles
{principles with conditions and recommendations}

## Programming Context
{retrieved knowledge chunks — rationale for this phase and session type}

## Exercises to Emphasize (fault correction)
{fault-matched exercises with explanations}

## Exercises to Avoid
{athlete restrictions + reasons}

## Instructions
Generate the training session as a JSON array of exercise prescriptions.
Each exercise must include:
- exercise_name (MUST match an exercise from the Available Exercises list exactly)
- exercise_order (1-indexed position in the session)
- sets, reps, intensity_pct, intensity_reference
- rest_seconds
- rpe_target
- selection_rationale (1-2 sentences: why this exercise, why this prescription)
- source_principle_ids (which principles support this choice, as integer array)

Respond ONLY with a valid JSON array. No markdown fences, no preamble, no explanation outside the JSON.
```

**Output schema (per session):**

```json
[
    {
        "exercise_name": "Snatch",
        "exercise_order": 1,
        "sets": 5,
        "reps": 2,
        "intensity_pct": 78,
        "intensity_reference": "snatch",
        "rest_seconds": 90,
        "rpe_target": 7.5,
        "selection_rationale": "Primary competition lift. Week 2 accumulation: building volume at moderate intensity. 5x2 at 78% gives 10 reps in the 70-80% Prilepin zone (optimal: 18, range: 12-24).",
        "source_principle_ids": [3, 7]
    },
    {
        "exercise_name": "Pause Snatch (at knee)",
        "exercise_order": 2,
        "sets": 3,
        "reps": 2,
        "intensity_pct": 68,
        "intensity_reference": "snatch",
        "rest_seconds": 75,
        "rpe_target": 7.0,
        "selection_rationale": "Addresses athlete fault 'hips_rising_fast'. Pause at knee reinforces back angle and patience off the floor. Lower intensity allows focus on position.",
        "source_principle_ids": [12]
    }
]
```

**JSON parsing and error handling:**

```python
# oly-agent/generate.py

import json
import re
import logging
import time
from dataclasses import dataclass

from shared.llm import estimate_cost

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Result of a single session generation attempt."""
    exercises: list[dict] | None    # parsed exercises, or None on failure
    raw_response: str               # raw LLM output
    input_tokens: int
    output_tokens: int
    status: str                     # 'success', 'parse_error', 'validation_error', 'failed'
    error_message: str | None
    attempt_number: int


def parse_llm_response(raw_response: str) -> list[dict]:
    """Parse LLM response into exercise list.

    Handles common LLM output issues:
    - Markdown code fences (```json ... ```)
    - Preamble text before the JSON
    - Trailing text after the JSON
    - Single-object response (should be array)

    Raises ValueError if parsing fails after all cleanup attempts.
    """
    text = raw_response.strip()

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Try direct parse first
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            result = [result]  # wrap single object in array
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in the response
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Try to find JSON object and wrap
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return [result]
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}...")


def validate_exercise_names(
    exercises: list[dict],
    available_exercises: list[str],
) -> list[str]:
    """Check that all exercise names match the available list.

    Returns list of error messages for mismatched names.
    """
    available_lower = {name.lower(): name for name in available_exercises}
    errors = []
    for ex in exercises:
        name = ex.get("exercise_name", "")
        if name.lower() not in available_lower:
            # Try fuzzy match
            close = [n for n in available_exercises
                     if name.lower() in n.lower() or n.lower() in name.lower()]
            if close:
                errors.append(
                    f"Unknown exercise '{name}'. Did you mean: {', '.join(close[:3])}?"
                )
            else:
                errors.append(f"Unknown exercise '{name}'. Not in available exercises list.")
    return errors


def generate_session_with_retries(
    prompt: str,
    llm_client,
    settings,
    available_exercises: list[str],
    validator_fn,          # Step 5 validation function
    program_id: int,
    week_number: int,
    day_number: int,
    db_conn,               # for logging to generation_log
) -> GenerationResult:
    """Generate a single session with parse + validation retries.

    Retry flow:
    1. Call LLM → parse JSON → validate exercise names → validate via Step 5
    2. On parse error: retry with "respond only with valid JSON" appended
    3. On validation error: retry with validation errors added to prompt
    4. After max retries: return failed result for human review
    """
    last_result = None
    current_prompt = prompt

    for attempt in range(1, settings.max_generation_retries + settings.max_parse_retries + 1):
        logger.info(f"  Generating W{week_number}D{day_number} (attempt {attempt})")

        try:
            # LLM call
            response = llm_client.messages.create(
                model=settings.generation_model,
                max_tokens=settings.generation_max_tokens,
                temperature=settings.generation_temperature,
                messages=[{"role": "user", "content": current_prompt}],
            )
            raw = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

        except Exception as e:
            logger.error(f"  LLM API error: {e}")
            _log_generation(db_conn, program_id, week_number, day_number,
                            attempt, settings.generation_model,
                            current_prompt, str(e), None,
                            0, 0, "failed", error_message=str(e))
            time.sleep(settings.retry_delay_seconds * attempt)
            continue

        # Parse
        try:
            exercises = parse_llm_response(raw)
        except ValueError as e:
            logger.warning(f"  Parse error (attempt {attempt}): {e}")
            _log_generation(db_conn, program_id, week_number, day_number,
                            attempt, settings.generation_model,
                            current_prompt, raw, None,
                            input_tokens, output_tokens, "parse_error",
                            error_message=str(e))
            # Retry with stronger JSON instruction
            current_prompt = prompt + (
                "\n\nIMPORTANT: Your previous response was not valid JSON. "
                "Respond with ONLY a JSON array. No markdown, no explanation."
            )
            time.sleep(settings.retry_delay_seconds)
            continue

        # Validate exercise names
        name_errors = validate_exercise_names(exercises, available_exercises)
        if name_errors:
            logger.warning(f"  Exercise name errors: {name_errors}")
            _log_generation(db_conn, program_id, week_number, day_number,
                            attempt, settings.generation_model,
                            current_prompt, raw, exercises,
                            input_tokens, output_tokens, "validation_error",
                            validation_errors=name_errors)
            current_prompt = prompt + (
                "\n\nIMPORTANT: Your previous response contained invalid exercise names:\n"
                + "\n".join(f"- {e}" for e in name_errors)
                + "\nUse ONLY exercise names from the Available Exercises list."
            )
            time.sleep(settings.retry_delay_seconds)
            continue

        # Step 5 validation
        validation = validator_fn(exercises)
        if not validation.is_valid:
            logger.warning(f"  Validation errors: {validation.errors}")
            _log_generation(db_conn, program_id, week_number, day_number,
                            attempt, settings.generation_model,
                            current_prompt, raw, exercises,
                            input_tokens, output_tokens, "validation_error",
                            validation_errors=validation.errors)
            current_prompt = prompt + (
                "\n\nIMPORTANT: Your previous response failed validation:\n"
                + "\n".join(f"- {e}" for e in validation.errors)
                + "\nFix these issues in your next response."
            )
            time.sleep(settings.retry_delay_seconds)
            continue

        # Success
        if validation.warnings:
            logger.info(f"  Warnings (non-blocking): {validation.warnings}")

        _log_generation(db_conn, program_id, week_number, day_number,
                        attempt, settings.generation_model,
                        current_prompt, raw, exercises,
                        input_tokens, output_tokens, "success")

        return GenerationResult(
            exercises=exercises,
            raw_response=raw,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status="success",
            error_message=None,
            attempt_number=attempt,
        )

    # All retries exhausted
    logger.error(f"  Failed after {attempt} attempts for W{week_number}D{day_number}")
    return GenerationResult(
        exercises=None,
        raw_response=raw if 'raw' in dir() else "",
        input_tokens=0,
        output_tokens=0,
        status="failed",
        error_message=f"Exhausted all retries. Last errors: {validation.errors if 'validation' in dir() else 'parse failure'}",
        attempt_number=attempt,
    )


def _log_generation(db_conn, program_id, week_number, day_number,
                    attempt, model, prompt, raw_response, parsed,
                    input_tokens, output_tokens, status,
                    validation_errors=None, error_message=None):
    """Log generation attempt to generation_log table."""
    cost = estimate_cost(input_tokens, output_tokens)
    cursor = db_conn.cursor()
    cursor.execute(
        """
        INSERT INTO generation_log
            (program_id, week_number, day_number, attempt_number,
             model, prompt_text, raw_response, parsed_response,
             input_tokens, output_tokens, estimated_cost_usd, status,
             validation_errors, error_message)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (program_id, week_number, day_number, attempt, model,
         prompt, raw_response, json.dumps(parsed) if parsed else None,
         input_tokens, output_tokens, cost, status,
         validation_errors, error_message),
    )
    db_conn.commit()
    cursor.close()
```

**Post-generation resolution:** After the LLM generates a session, three resolution steps run before DB insert:

```python
# oly-agent/weight_resolver.py
"""
Post-generation resolution: maps LLM output fields to DB-ready values.

The LLM outputs: exercise_name, intensity_pct, intensity_reference, source_principle_ids
The DB needs:    exercise_id, absolute_weight_kg, source_chunk_ids

These functions bridge that gap.
"""

import logging

logger = logging.getLogger(__name__)


# ── Exercise name → intensity_reference key mapping ──────────
# The DB stores maxes by exercise_id (FK to exercises table).
# The LLM uses intensity_reference strings like "snatch", "clean_and_jerk".
# The ASSESS step loads maxes from the DB and needs to key them by
# intensity_reference for use in resolve_weights().
#
# This mapping converts exercise names from the DB into the
# intensity_reference vocabulary used throughout the agent.

EXERCISE_NAME_TO_INTENSITY_REF = {
    "Snatch":           "snatch",
    "Clean & Jerk":     "clean_and_jerk",
    "Clean":            "clean",
    "Back Squat":       "back_squat",
    "Front Squat":      "front_squat",
    "Snatch Pull":      "snatch_pull",
    "Clean Pull":       "clean_pull",
    "Snatch Deadlift":  "snatch_deadlift",
    "Clean Deadlift":   "clean_deadlift",
    "Push Press":       "push_press",
    "Overhead Squat":   "overhead_squat",
    "Jerk":             "jerk",
}


def build_maxes_dict(db_maxes: list[dict]) -> dict[str, float]:
    """Convert DB max rows into a dict keyed by intensity_reference.

    Input: rows from ASSESS step query:
        [{"name": "Snatch", "weight_kg": 100.0}, {"name": "Back Squat", ...}]

    Output: {"snatch": 100.0, "back_squat": 160.0, ...}

    Exercises not in the mapping are stored by lowercase name with spaces
    replaced by underscores as a fallback.
    """
    maxes = {}
    for row in db_maxes:
        name = row["name"]
        ref = EXERCISE_NAME_TO_INTENSITY_REF.get(name)
        if ref is None:
            # Fallback: normalize name to snake_case
            ref = name.lower().replace(" ", "_").replace("&", "and")
            logger.debug(f"No explicit mapping for '{name}', using '{ref}'")
        maxes[ref] = float(row["weight_kg"])
    return maxes


def resolve_exercise_ids(
    session_exercises: list[dict],
    exercise_lookup: dict[str, int],
) -> list[dict]:
    """Resolve exercise_name → exercise_id using a pre-loaded lookup.

    Args:
        session_exercises: LLM-generated exercise list
        exercise_lookup: {exercise_name_lower: exercise_id} built from DB at startup

    The lookup is built once per agent run:
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

    Maxes dict is keyed by intensity_reference value:
        {"snatch": 100.0, "clean_and_jerk": 125.0, "back_squat": 160.0, ...}
    """
    for ex in session_exercises:
        ref = ex.get("intensity_reference", "")
        pct = ex.get("intensity_pct")
        if ref and pct and ref in maxes:
            raw_kg = maxes[ref] * (pct / 100)
            # Round to nearest 0.5kg (standard plate increments)
            ex["absolute_weight_kg"] = round(raw_kg * 2) / 2
        else:
            ex["absolute_weight_kg"] = None
    return session_exercises


def attach_source_chunk_ids(
    session_exercises: list[dict],
    retrieval_context: dict,
) -> list[dict]:
    """Attach source_chunk_ids from the retrieval context to each exercise.

    The LLM doesn't know chunk IDs — it works with the text content.
    Instead, we attach chunk IDs programmatically based on which retrieval
    path provided context for the exercise's movement family.

    Logic:
    - Competition lift exercises get the programming_rationale chunk IDs
    - Fault-correction exercises get the fault_correction chunk IDs
    - All exercises get any template reference chunk IDs

    This is approximate but sufficient for traceability. The rationale
    text in selection_rationale provides the human-readable explanation;
    source_chunk_ids provides the machine-navigable link back to the
    knowledge base.
    """
    # Collect chunk IDs by category from retrieval context
    rationale_ids = [c["id"] for c in retrieval_context.get("programming_rationale", []) if "id" in c]
    fault_ids = [c["id"] for c in retrieval_context.get("fault_correction_chunks", []) if "id" in c]

    for ex in session_exercises:
        chunk_ids = []

        # If this exercise addresses a fault, link the fault correction chunks
        rationale = ex.get("selection_rationale", "").lower()
        if any(fault in rationale for fault in ("fault", "address", "correct", "fix")):
            chunk_ids.extend(fault_ids)

        # All exercises get the general programming rationale chunks
        chunk_ids.extend(rationale_ids)

        ex["source_chunk_ids"] = list(set(chunk_ids))  # deduplicate

    return session_exercises
```

### Step 5: VALIDATE — Check the Program

**Purpose:** Automated checks against Prilepin's chart, programming principles, and common-sense constraints. Catches LLM errors before the program reaches the athlete.

**Prilepin zone helpers:**

```python
# shared/prilepin.py
"""
Prilepin's chart lookup functions.
Used by both the PLAN step (setting rep targets) and VALIDATE step (checking compliance).
"""

# Prilepin's zones — mirrors the prilepin_chart table for in-memory use.
# Loaded from DB at startup, but hardcoded here as a fallback.
PRILEPIN_ZONES = [
    {"zone": "55-65", "low": 55, "high": 65, "reps_per_set": (3, 6), "optimal": 24, "range": (18, 30)},
    {"zone": "70-80", "low": 70, "high": 80, "reps_per_set": (3, 6), "optimal": 18, "range": (12, 24)},
    {"zone": "80-90", "low": 80, "high": 90, "reps_per_set": (2, 4), "optimal": 15, "range": (10, 20)},
    {"zone": "90-100", "low": 90, "high": 100, "reps_per_set": (1, 2), "optimal": 7, "range": (4, 10)},
]


def get_prilepin_zone(intensity_pct: float) -> str | None:
    """Map an intensity percentage to its Prilepin zone string.

    Returns the zone key (e.g., "80-90") or None if below 55%.
    Intensities above 100% (pulls) map to the 90-100 zone.

    Examples:
        get_prilepin_zone(73)  → "70-80"
        get_prilepin_zone(85)  → "80-90"
        get_prilepin_zone(95)  → "90-100"
        get_prilepin_zone(105) → "90-100"  (pulls)
        get_prilepin_zone(50)  → None       (below chart)
    """
    if intensity_pct > 100:
        return "90-100"  # pulls above 100% of comp lift use the top zone
    for zone in PRILEPIN_ZONES:
        if zone["low"] <= intensity_pct <= zone["high"]:
            return zone["zone"]
    # Below 55% — not on the chart (warm-up territory)
    return None


def get_prilepin_data(zone_key: str) -> dict | None:
    """Get full Prilepin data for a zone.

    Returns dict with: reps_per_set, optimal, range (total_reps_low, total_reps_high)
    """
    for zone in PRILEPIN_ZONES:
        if zone["zone"] == zone_key:
            return {
                "reps_per_set_low": zone["reps_per_set"][0],
                "reps_per_set_high": zone["reps_per_set"][1],
                "optimal_total_reps": zone["optimal"],
                "total_reps_range_low": zone["range"][0],
                "total_reps_range_high": zone["range"][1],
            }
    return None


def compute_session_rep_target(
    intensity_floor: float,
    intensity_ceiling: float,
    session_volume_share: float,
    volume_modifier: float = 1.0,
) -> int:
    """Compute a target rep count for competition lifts in a single session.

    Uses the midpoint of the intensity range to find the Prilepin zone,
    then scales the optimal total by session share and volume modifier.

    Example:
        intensity 70-80%, session_volume_share=0.30, volume_modifier=1.0
        → zone "70-80", optimal=18, target = 18 × 0.30 × 1.0 = 5 reps
        (round up to ensure meaningful work)
    """
    midpoint = (intensity_floor + intensity_ceiling) / 2
    zone_key = get_prilepin_zone(midpoint)
    if not zone_key:
        return 6  # fallback for sub-55% warm-up work

    zone_data = get_prilepin_data(zone_key)
    if not zone_data:
        return 6

    raw_target = zone_data["optimal_total_reps"] * session_volume_share * volume_modifier
    return max(3, round(raw_target))  # minimum 3 reps to be meaningful
```

**Validation checks:**

```python
# oly-agent/validate.py

from shared.prilepin import get_prilepin_zone, get_prilepin_data

@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str]       # hard failures — must fix
    warnings: list[str]     # soft issues — worth reviewing
    session_comp_reps: dict  # zone → reps prescribed in THIS session
                             # (caller accumulates across sessions for weekly tracking)

def validate_session(
    session_exercises: list[dict],
    week_target: dict,              # WeekTarget from phase profile
    active_principles: list[dict],
    athlete: dict,                  # AthleteContext fields
    week_cumulative_reps: dict | None = None,  # zone → reps already prescribed this week
) -> ValidationResult:
    errors = []
    warnings = []

    # ── Check 1: Prilepin's volume compliance ────────────
    # Count total reps per intensity zone for competition lifts
    comp_lift_reps = {}  # zone → total reps (this session)
    for ex in session_exercises:
        if ex.get("intensity_reference") in ("snatch", "clean_and_jerk", "clean"):
            pct = ex["intensity_pct"]
            zone = get_prilepin_zone(pct)
            if zone is None:
                continue  # below 55%, not on the chart
            total = ex["sets"] * ex["reps"]
            comp_lift_reps[zone] = comp_lift_reps.get(zone, 0) + total

    for zone, session_total in comp_lift_reps.items():
        zone_data = get_prilepin_data(zone)
        if not zone_data:
            continue

        # Check reps-per-set compliance for this zone
        rps_low = zone_data["reps_per_set_low"]
        rps_high = zone_data["reps_per_set_high"]

        # Check weekly cumulative if provided
        if week_cumulative_reps:
            weekly_total = week_cumulative_reps.get(zone, 0) + session_total
            if weekly_total > zone_data["total_reps_range_high"]:
                errors.append(
                    f"Prilepin weekly violation: {weekly_total} cumulative reps "
                    f"in {zone}% zone exceeds weekly max of "
                    f"{zone_data['total_reps_range_high']}"
                )

    # ── Check 2: Intensity envelope ──────────────────────
    for ex in session_exercises:
        pct = ex.get("intensity_pct", 0)
        if pct > week_target["intensity_ceiling"]:
            errors.append(
                f"{ex['exercise_name']} at {pct}% exceeds week ceiling "
                f"of {week_target['intensity_ceiling']}%"
            )
        if pct < week_target["intensity_floor"] and pct > 0:
            # Below floor is a warning, not an error (warm-up sets, accessory work)
            if ex.get("intensity_reference") in ("snatch", "clean_and_jerk"):
                warnings.append(
                    f"{ex['exercise_name']} at {pct}% is below floor of "
                    f"{week_target['intensity_floor']}% for competition lifts"
                )

    # ── Check 3: Reps-per-set compliance ─────────────────
    for ex in session_exercises:
        pct = ex.get("intensity_pct", 0)
        if pct >= 90 and ex["reps"] > 2:
            errors.append(
                f"{ex['exercise_name']}: {ex['reps']} reps at {pct}% — "
                f"Prilepin allows max 2 reps per set above 90%"
            )
        if pct >= 80 and ex["reps"] > 4:
            warnings.append(
                f"{ex['exercise_name']}: {ex['reps']} reps at {pct}% — "
                f"Prilepin suggests max 4 reps per set in 80-90% zone"
            )

    # ── Check 4: Exercise not in avoid list ──────────────
    avoid_list = athlete.get("exercise_preferences", {}).get("avoid", [])
    for ex in session_exercises:
        if ex["exercise_name"].lower().replace(" ", "_") in avoid_list:
            errors.append(
                f"{ex['exercise_name']} is in athlete's avoid list"
            )

    # ── Check 5: Principle compliance ────────────────────
    for principle in active_principles:
        rec = principle.get("recommendation", {})

        # Max exercises per session
        max_ex = rec.get("max_exercises_per_session")
        if max_ex and len(session_exercises) > max_ex:
            warnings.append(
                f"Session has {len(session_exercises)} exercises, "
                f"principle '{principle['principle_name']}' recommends max {max_ex}"
            )

        # Competition lifts first
        if rec.get("competition_lifts_first"):
            first_ex = session_exercises[0] if session_exercises else {}
            if first_ex.get("intensity_reference") not in ("snatch", "clean_and_jerk", "clean"):
                warnings.append(
                    f"First exercise is {first_ex.get('exercise_name', '?')}, "
                    f"but principle requires competition lifts first"
                )

    # ── Check 6: Session duration estimate ───────────────
    estimated_minutes = sum(
        ex["sets"] * (30 + (ex.get("rest_seconds", 90)))  # ~30s per set + rest
        for ex in session_exercises
    ) / 60
    if estimated_minutes > athlete.get("session_duration_minutes", 90) * 1.2:
        warnings.append(
            f"Estimated session duration ({estimated_minutes:.0f} min) exceeds "
            f"athlete's available time ({athlete.get('session_duration_minutes', 90)} min)"
        )

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        session_comp_reps=comp_lift_reps,  # caller adds to week_cumulative_reps
    )
```

**If validation fails:** The agent re-runs Step 4 for the failed session with the errors included in the prompt as additional constraints. Maximum 2 retries before flagging for human review.

### Step 6: EXPLAIN — Generate Rationale

**Purpose:** Produce human-readable explanations for the program as a whole and for key exercise choices. This runs once after all sessions are generated and validated.

**Program-level rationale prompt:**

```
Given this athlete profile, goal, and the generated program below,
write a 3-5 paragraph explanation covering:
1. Why this training phase was selected
2. How volume and intensity progress across the weeks
3. Key exercise selections and what they address
4. What the athlete should expect to feel week by week
5. What signals would indicate the program needs adjustment

Write for the athlete, not a coach. Clear, direct, no jargon without explanation.
```

This rationale is stored in `generated_programs.rationale` and displayed to the athlete alongside the program.

---

## Retrieval Strategy Summary

| Agent Step | Structured Queries | Vector Search | LLM Calls |
|------------|-------------------|---------------|-----------|
| **1. ASSESS** | Athletes, maxes, goals, recent logs | None | None |
| **2. PLAN** | Principles for phase + level, Prilepin's chart | Phase-specific programming rationale (3-5 chunks) | 1 (plan decisions for ambiguous cases) |
| **3. RETRIEVE** | Fault-based exercises, templates, substitutions | Exercise selection rationale, fault correction guidance (10-20 chunks total) | None |
| **4. GENERATE** | None (all context already gathered) | None | 1 per session (e.g., 16 for 4-week × 4-day) |
| **5. VALIDATE** | Prilepin's chart, principles | None | 0-2 retries on failed sessions |
| **6. EXPLAIN** | None | None | 1 (program-level rationale) |

**Total per program generation:** ~20-25 LLM calls for a 4-week, 4-session/week program. At Claude Sonnet pricing, roughly $0.10-0.20 per program.

---

## Feedback Loop

The feedback loop is what makes this system improve over time. It operates at two levels: immediate (within the current program) and historical (across programs).

### Immediate Feedback (Mid-Program Adjustments)

Not automated in V1. The athlete logs training results, and the system surfaces warning signals for the coach/athlete to act on manually:

```sql
-- Surface: athlete consistently exceeding RPE targets
SELECT tle.exercise_name,
       AVG(tle.rpe - se.rpe_target) as avg_rpe_overshoot,
       COUNT(*) as sessions
FROM training_log_exercises tle
JOIN session_exercises se ON tle.session_exercise_id = se.id
JOIN program_sessions ps ON se.session_id = ps.id
WHERE ps.program_id = $active_program_id
  AND tle.rpe IS NOT NULL AND se.rpe_target IS NOT NULL
GROUP BY tle.exercise_name
HAVING AVG(tle.rpe - se.rpe_target) > 1.0;  -- RPE overshoot > 1 point

-- Surface: declining make rates on competition lifts
SELECT tle.exercise_name,
       ps.week_number,
       AVG(tle.make_rate) as avg_make_rate
FROM training_log_exercises tle
JOIN training_logs tl ON tle.log_id = tl.id
JOIN session_exercises se ON tle.session_exercise_id = se.id
JOIN program_sessions ps ON se.session_id = ps.id
WHERE ps.program_id = $active_program_id
  AND tle.exercise_id IN (
      SELECT id FROM exercises WHERE category = 'competition'
  )
GROUP BY tle.exercise_name, ps.week_number
ORDER BY tle.exercise_name, ps.week_number;
```

### Historical Feedback (Across Programs)

When a program completes, the agent analyzes outcomes to inform the *next* program:

```python
@dataclass
class ProgramOutcome:
    """Computed when a program transitions to 'completed' status."""
    program_id: int
    athlete_id: int

    # Did maxes improve?
    maxes_delta: dict[str, float]           # {"snatch": +3.0, "clean_and_jerk": +2.0}

    # Adherence
    sessions_prescribed: int
    sessions_completed: int
    adherence_pct: float

    # Load tolerance
    avg_rpe_deviation: float                # positive = harder than intended
    avg_make_rate: float                    # across competition lifts

    # Volume tolerance signals
    rpe_trend_by_week: list[float]          # week-over-week avg RPE
    make_rate_trend_by_week: list[float]

    # What worked / didn't
    exercises_with_best_make_rates: list[str]
    exercises_with_worst_make_rates: list[str]
    faults_that_improved: list[str]         # based on make rate changes
    faults_still_present: list[str]
```

**How the next program uses this:**

1. **Volume calibration:** If RPE consistently ran 1+ point above target, the next program reduces volume by 10-15%. If under, volume can increase.

2. **Intensity calibration:** If make rates declined sharply in weeks with higher intensity, the athlete may need a longer accumulation phase before intensifying.

3. **Exercise selection refinement:** If make rates on power snatches improved (suggesting better turnover speed) but not full snatches (suggesting receiving position weakness), the next cycle emphasizes overhead squats and snatch balances.

4. **Fault tracking:** The agent updates `athletes.technical_faults` based on logged data. If an athlete's make rates on snatches improved and `slow_turnover` was a listed fault, it may be removed or deprioritized.

5. **Max adjustments:** New gym PRs or competition results update `athlete_maxes`. The old 'current' max becomes 'historical', new max becomes 'current'. All future percentage calculations use the new value.

```sql
-- Promote a new max: archive old current, insert new current
UPDATE athlete_maxes SET max_type = 'historical'
WHERE athlete_id = $athlete_id
  AND exercise_id = $exercise_id
  AND max_type = 'current';

INSERT INTO athlete_maxes (athlete_id, exercise_id, weight_kg, max_type, date_achieved, rpe)
VALUES ($athlete_id, $exercise_id, $new_weight, 'current', CURRENT_DATE, $rpe);
```

---

## Program Output Format

What the athlete actually sees. This is the presentation layer for a generated program.

```
═══════════════════════════════════════════════════
  4-WEEK ACCUMULATION BLOCK — Snatch & C&J Focus
  Athlete: David | Start: March 10, 2026
═══════════════════════════════════════════════════

  PROGRAM RATIONALE:
  This block builds volume at moderate intensities (70-82%) to
  develop work capacity and reinforce positions. Your snatch
  technique has been showing forward balance off the floor —
  we've included pause snatches and halting deadlifts to address
  this. Intensity progresses ~3% per week, with week 4 as a
  deload at 60% volume.

  Expect weeks 2-3 to feel challenging. If RPE consistently
  exceeds 8 on competition lifts, reduce working weights by 2-3%.

───────────────────────────────────────────────────
  WEEK 1 — Day 1 (Monday): Snatch + Squat
───────────────────────────────────────────────────

  1. Snatch                     5 × 2 @ 73% (73 kg)   Rest: 90s
     → Building volume in primary zone.

  2. Pause Snatch (at knee)     3 × 2 @ 65% (65 kg)   Rest: 75s
     → Reinforces back angle over the knee. Addresses
       forward balance fault.

  3. Back Squat                 4 × 5 @ 75% (113 kg)   Rest: 2 min
     → Leg strength base. Tempo: 3-1-X-0.

  4. Snatch Pull                3 × 3 @ 90% (90 kg)   Rest: 90s
     → Pull strength. Same positions as the snatch.

  Session total: ~60 min | Target RPE: 7.0
```

---

## V1 Scope and Limitations

What V1 **does:**
- Generate a single mesocycle (3-8 weeks) for one athlete
- Ground all decisions in the knowledge base with traceability
- Validate against Prilepin's and programming principles
- Produce auditable rationale for the program and key exercise choices
- Track training logs and compute outcome metrics
- Use historical outcomes to inform the *next* program request

What V1 **does not do:**
- Auto-adjust a running program mid-cycle based on daily logs
- Plan across multiple mesocycles (macrocycle periodization)
- Handle multiple athletes simultaneously
- Auto-detect faults from video
- Generate warm-up or cooldown protocols
- Handle weight cuts or nutrition
- Provide real-time coaching cues during sessions

---

## Implementation Order

### Phase 6a: Project Restructure + Athlete Schema (Day 1)

1. **Restructure to monorepo** — Create `shared/` directory. Move `VectorLoader` (vector search), database connection logic, and config to `shared/`. Create `shared/llm.py` (Anthropic client + cost estimation). Update imports in `oly-ingestion/`. Create `oly-agent/` directory with `__init__.py`.
2. **Create `shared/config.py`** — Merge existing `Settings` with `AgentSettings`. Single config source for both pipeline and agent.
3. **Create `shared/prilepin.py`** — Move Prilepin's lookup logic here. Implement `get_prilepin_zone()`, `get_prilepin_data()`, `compute_session_rep_target()`.
4. **Apply `athlete_schema.sql`** — `psql -f athlete_schema.sql`. Verify all 9 new tables exist (athletes, maxes, goals, generated_programs, sessions, session_exercises, training_logs, training_log_exercises, generation_log).
5. **Verify seed data** — Query test athlete David, confirm maxes and goal are populated correctly. Verify estimated maxes would be computed correctly for missing exercises.

### Phase 6b: Agent Models + Assess + Plan (Days 2-3)

6. **Implement `oly-agent/models.py`** — All data classes: `AthleteContext`, `ProgramPlan`, `WeekTarget`, `SessionTemplate`, `RetrievalContext`, `GenerationResult`, `ValidationResult`, `ProgramOutcome`.
7. **Implement `oly-agent/phase_profiles.py`** — Copy phase progression profiles from this doc. Test: `build_weekly_targets("accumulation", 4, "intermediate")` returns 4 `WeekTarget` dicts with correct intensity/volume progression.
8. **Implement `oly-agent/session_templates.py`** — Copy session distribution logic. Test: `get_session_templates(4)` returns 4 session templates with correct volume shares summing to 1.0.
9. **Implement `oly-agent/assess.py`** — ASSESS step queries. Test with seed athlete: `AthleteContext` populated with David's profile, maxes, goal, empty logs.
10. **Implement `oly-agent/plan.py`** — PLAN step decision tree. Test: given David's goal (`general_strength`, no competition), verify plan selects `accumulation`, 4 weeks, 4 sessions/week. Verify weekly targets match phase profile.

### Phase 6c: Retrieve + Generate (Days 4-5)

11. **Implement `oly-agent/retrieve.py`** — Three retrieval paths (fault-based exercises, template reference, vector search). Test: for David's faults (`forward_balance_off_floor`, `slow_turnover`), verify fault exercises include Pause Snatch, Muscle Snatch, etc.
12. **Implement `oly-agent/generate.py`** — Per-session generation with JSON parsing, exercise name validation, retry logic, and generation logging. Test: generate one session, verify JSON parses, exercise names are valid, generation_log row created.
13. **Implement `oly-agent/weight_resolver.py`** — `intensity_pct × maxes → absolute_weight_kg`. Round to 0.5kg. Test: 78% of 100kg snatch = 78kg → 78.0.
14. **Implement cold-start logic in assess.py** — Max estimation from competition lifts using `MAX_ESTIMATION_RATIOS`. Test: athlete with only snatch + C&J maxes gets estimated FS, BS, pull maxes.

### Phase 6d: Validate + Explain + Orchestrate (Day 6)

15. **Implement `oly-agent/validate.py`** — All validation checks (Prilepin's compliance with cumulative weekly tracking, intensity envelope, reps-per-set, avoid list, principle compliance, duration estimate). Test with known-bad sessions: verify errors are caught.
16. **Implement `oly-agent/explain.py`** — Program-level rationale generation. Single LLM call after all sessions pass validation.
17. **Implement `oly-agent/orchestrator.py`** — Wire all 6 steps together. Session ordering: Day 1 → Day N within each week, Week 1 → Week N. Pass cumulative session context between generation calls. Test: generate a full 4-week program for David end-to-end.

### Phase 6e: Training Logs + Feedback (Week 2)

18. Build CLI for logging training results (exercise, sets completed, weight, RPE, make rate).
19. Implement `oly-agent/feedback.py` — `ProgramOutcome` computation, max promotion workflow, readiness signals.
20. Test: log a full week of simulated training data, compute outcomes, verify feedback signals.

### Phase 6f: Full Integration Test (Week 2)

21. Generate a real program for David with his actual maxes.
22. Log 2-3 weeks of simulated training data with realistic RPE and make rates.
23. Mark program complete, compute outcomes.
24. Generate a follow-up program — verify it adapts (volume calibration from RPE history, exercise selection reflects fault progress, intensity ceiling adjusted if make rates declined).
