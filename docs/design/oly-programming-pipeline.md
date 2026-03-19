# Olympic Weightlifting Programming — Ingestion Pipeline Design

## Overview

This document covers the data ingestion pipeline for processing weightlifting programming source material into a hybrid storage system: **pgvector** for semantic retrieval and **structured Postgres tables** for rule-based lookups (Prilepin's chart, percentage schemes, exercise taxonomies).

The key insight: not all programming knowledge should be treated the same way. Some content is best stored as embeddings for semantic search, while other content (tables, formulas, exercise trees) belongs in structured tables that can be queried deterministically.

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     SOURCE MATERIAL                         │
│  Books (PDF/EPUB) │ Articles │ Program Templates │ Tables   │
└────────┬──────────┬──────────┬───────────────────┬──────────┘
         │          │          │                   │
         ▼          ▼          ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│                   DOCUMENT PROCESSOR                        │
│  1. Text extraction (PDF → text, OCR if needed)             │
│  2. Content classification (prose vs table vs program)      │
│  3. Metadata tagging (source, author, topic, chapter)       │
└────────┬──────────┬──────────┬───────────────────┬──────────┘
         │          │          │                   │
         ▼          ▼          ▼                   ▼
┌──────────────┐ ┌─────────┐ ┌──────────┐ ┌──────────────────┐
│  CHUNKER     │ │ TABLE   │ │ PROGRAM  │ │ PRINCIPLE        │
│  (prose →    │ │ PARSER  │ │ TEMPLATE │ │ EXTRACTOR        │
│   semantic   │ │ (structured│ PARSER  │ │ (rules/heuristics│
│   chunks)    │ │  data)  │ │ (YAML)   │ │  → structured)   │
└──────┬───────┘ └────┬────┘ └────┬─────┘ └───────┬──────────┘
       │              │           │                │
       ▼              ▼           ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                      POSTGRES + PGVECTOR                    │
│                                                             │
│  ┌─────────────────┐  ┌──────────────────────────────────┐  │
│  │ Vector Store     │  │ Structured Tables                │  │
│  │ (knowledge_chunks│  │ - prilepin_chart                 │  │
│  │  with embeddings)│  │ - percentage_schemes             │  │
│  │                  │  │ - exercises                      │  │
│  │                  │  │ - program_templates              │  │
│  │                  │  │ - programming_principles         │  │
│  └─────────────────┘  └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Database Schema

### Entity Relationship Overview

```
sources ─────────┬──────────── knowledge_chunks (vector store)
                 │                    │
                 ├──────────── programming_principles
                 │                    │
                 ├──────────── program_templates
                 │                    │
                 │                    └── program_template_exercises ──┐
                 │                                                    │
                 ├──────────── percentage_schemes                     │
                 │                                                    │
                 └──────────── ingestion_runs ─── ingestion_chunk_log─┤
                                                         │            │
exercises ───────┬── exercise_substitutions (self-ref)   │            │
                 ├── exercise_complexes                  │            │
                 └───────────────────────────────────────-─────────────┘
                                                         │
knowledge_chunks ────────────────────────────────────────┘

prilepin_chart (standalone reference, no FKs)
```

### 1. Sources Table

Normalizes source references across all tables. Every piece of ingested knowledge traces back to a source.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 1. SOURCES — normalized reference for all ingested material
-- ============================================================

CREATE TYPE source_type AS ENUM (
    'book', 'article', 'website', 'video', 'research_paper', 'manual'
);

CREATE TABLE sources (
    id SERIAL PRIMARY KEY,
    title VARCHAR(300) NOT NULL,
    author VARCHAR(200),
    source_type source_type NOT NULL,
    publisher VARCHAR(200),
    publication_year INT,
    isbn VARCHAR(20),
    url VARCHAR(500),                              -- for web sources
    credibility_score INT DEFAULT 5                -- 1-10; used to weight conflicting principles
        CHECK (credibility_score BETWEEN 1 AND 10),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(title, author)                          -- prevent duplicate ingestion
);

-- Example data:
-- INSERT INTO sources (title, author, source_type, publication_year, credibility_score)
-- VALUES
--     ('Olympic Weightlifting: A Complete Guide for Athletes and Coaches',
--      'Greg Everett', 'book', 2009, 9),
--     ('Weightlifting Programming: A Winning Coach''s Guide',
--      'Bob Takano', 'book', 2012, 8),
--     ('Managing the Training of Weightlifters',
--      'Nikolai Laputin, Valentin Oleshko', 'book', 1982, 8),
--     ('Science and Practice of Strength Training',
--      'Vladimir Zatsiorsky', 'book', 1995, 9),
--     ('A System of Multi-Year Training in Weightlifting',
--      'Alexei Medvedev', 'book', 1986, 8);
```

**Design rationale:** Sources are separated out rather than stored as inline strings so that (a) you can weight conflicting principles by source credibility, (b) the agent can explain *why* it made a programming decision by citing the source, and (c) you avoid duplicating source metadata across hundreds of rows.

---

### 2. Prilepin's Chart

Standalone reference table. Pure lookup — no embeddings, no foreign keys. The agent queries this directly when prescribing volume at a given intensity.

```sql
-- ============================================================
-- 2. PRILEPIN'S CHART — deterministic volume prescription
-- ============================================================
-- Used by the agent to answer: "How many total reps should I prescribe
-- for snatches at 82%?" → Look up the 80-90% row → 10-20 reps, optimal 15.
--
-- Extended with movement_type because Prilepin's original data was for
-- competition lifts. Squats and pulls can tolerate different volumes.

CREATE TYPE movement_applicability AS ENUM (
    'competition_lifts',    -- snatch, clean & jerk (original Prilepin)
    'squats',               -- front squat, back squat
    'pulls',                -- clean pull, snatch pull, deadlift
    'all'                   -- when no distinction is made
);

CREATE TABLE prilepin_chart (
    id SERIAL PRIMARY KEY,
    intensity_range_low NUMERIC(5,2) NOT NULL
        CHECK (intensity_range_low >= 0 AND intensity_range_low <= 100),
    intensity_range_high NUMERIC(5,2) NOT NULL
        CHECK (intensity_range_high > intensity_range_low AND intensity_range_high <= 100),
    reps_per_set_low INT NOT NULL CHECK (reps_per_set_low >= 1),
    reps_per_set_high INT NOT NULL CHECK (reps_per_set_high >= reps_per_set_low),
    optimal_total_reps INT NOT NULL,
    total_reps_range_low INT NOT NULL,
    total_reps_range_high INT NOT NULL
        CHECK (total_reps_range_high >= total_reps_range_low),
    movement_type movement_applicability NOT NULL DEFAULT 'competition_lifts',
    notes TEXT                                     -- e.g. "Original Prilepin data from Medvedev"
);

-- Seed: original Prilepin's data for competition lifts
-- Extended with warm-up zone (< 55%) for completeness — the agent needs
-- guidance for technique work and warm-up prescriptions, not just working sets.
INSERT INTO prilepin_chart
    (intensity_range_low, intensity_range_high, reps_per_set_low, reps_per_set_high,
     optimal_total_reps, total_reps_range_low, total_reps_range_high, movement_type, notes)
VALUES
    (40, 55, 3, 6, 30, 20, 40, 'competition_lifts', 'Warm-up / barbell complex zone. Not from original Prilepin data.'),
    (55, 65, 3, 6, 24, 18, 30, 'competition_lifts', 'Technique / speed work zone'),
    (70, 80, 3, 6, 18, 12, 24, 'competition_lifts', 'Primary working zone for volume accumulation'),
    (80, 90, 2, 4, 15, 10, 20, 'competition_lifts', 'Intensification zone'),
    (90, 100, 1, 2,  7,  4, 10, 'competition_lifts', 'Realization / peaking zone');

-- Example agent query:
-- SELECT optimal_total_reps, reps_per_set_low, reps_per_set_high
-- FROM prilepin_chart
-- WHERE movement_type = 'competition_lifts'
--   AND 82 BETWEEN intensity_range_low AND intensity_range_high;
-- → optimal_total_reps=15, sets of 2-4
```

**Design rationale:** `movement_applicability` was added because coaches commonly modify Prilepin's guidelines for squats (higher volume tolerance) and pulls (often higher rep ranges). The original chart was derived from competition lift data. This lets you seed separate rows for squats/pulls if your source material supports it, while keeping the original data intact.

---

### 3. Exercises

The exercise taxonomy is one of the most critical tables. It models the full variation tree for Olympic lifts and supporting movements, enabling the agent to select appropriate exercises based on athlete needs, faults, and training phase.

```sql
-- ============================================================
-- 3. EXERCISES — full variation tree for Olympic lifts
-- ============================================================

CREATE TYPE exercise_category AS ENUM (
    'competition',      -- snatch, clean & jerk (as performed in competition)
    'competition_variant', -- power snatch, hang clean, block jerk, etc.
    'strength',         -- squat, press, deadlift, RDL
    'pull',             -- snatch pull, clean pull, halting DL
    'accessory',        -- muscle snatch, snatch balance, push press
    'positional',       -- pause variations, segment lifts, tempo work
    'complex'           -- predefined exercise combinations (see exercise_complexes)
);

CREATE TYPE movement_family AS ENUM (
    'snatch', 'clean', 'jerk',
    'squat', 'pull', 'press',
    'hinge', 'row', 'carry',
    'core', 'plyometric'
);

CREATE TYPE start_position AS ENUM (
    'floor',
    'hang_above_knee',       -- "high hang"
    'hang_at_knee',
    'hang_below_knee',       -- "low hang"
    'blocks_above_knee',
    'blocks_at_knee',
    'blocks_below_knee',
    'behind_neck',           -- for jerks / presses
    'rack'                   -- front rack position
);

CREATE TABLE exercises (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL UNIQUE,
    category exercise_category NOT NULL,
    movement_family movement_family NOT NULL,

    -- Position & variant descriptors (nullable — competition lifts have no variant)
    start_position start_position,
    is_power BOOLEAN DEFAULT FALSE,            -- received above parallel
    is_muscle BOOLEAN DEFAULT FALSE,           -- no rebend of knees
    has_pause BOOLEAN DEFAULT FALSE,           -- pause at a specified position
    pause_position VARCHAR(100),               -- e.g. 'knee', '2-inch off floor', 'receiving position'
    tempo_prescription VARCHAR(50),            -- e.g. '3-1-X-1' (eccentric-pause-concentric-top)
    is_no_feet BOOLEAN DEFAULT FALSE,          -- feet don't move during lift
    is_no_hook BOOLEAN DEFAULT FALSE,          -- no hook grip variant

    -- Hierarchy
    parent_exercise_id INT REFERENCES exercises(id),

    -- Classification & purpose
    complexity_level INT DEFAULT 1
        CHECK (complexity_level BETWEEN 1 AND 5),  -- 1=beginner-safe, 5=advanced only
    primary_purpose TEXT,                       -- free text: 'first pull strength', 'turnover speed and aggression'
    secondary_purposes TEXT[],

    -- Fault correction mapping (critical for the adaptive agent)
    -- The agent uses this to answer: "athlete is missing snatches forward → what exercises help?"
    faults_addressed TEXT[],
    -- Common values:
    --   'early_arm_bend', 'forward_balance_off_floor', 'slow_turnover',
    --   'missed_lockout', 'hips_rising_fast', 'bar_crashing',
    --   'soft_receiving_position', 'inconsistent_timing',
    --   'knee_cave_in_recovery', 'lost_back_tightness'

    -- Prescription defaults (used when the agent needs sensible starting points)
    typical_sets_low INT,
    typical_sets_high INT,
    typical_reps_low INT,
    typical_reps_high INT,
    typical_intensity_low NUMERIC(5,2),        -- as % of competition lift 1RM
    typical_intensity_high NUMERIC(5,2),
    typical_rest_seconds INT,

    -- Metadata
    equipment_required TEXT[],                 -- e.g. {'barbell', 'blocks', 'straps'}
    cues TEXT[],                               -- coaching cues: {'drive through the floor', 'elbows high and outside'}
    video_reference_url VARCHAR(500),          -- link to demo video
    source_id INT REFERENCES sources(id),

    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for common agent queries
CREATE INDEX idx_exercises_family ON exercises (movement_family);
CREATE INDEX idx_exercises_category ON exercises (category);
CREATE INDEX idx_exercises_faults ON exercises USING GIN (faults_addressed);
CREATE INDEX idx_exercises_parent ON exercises (parent_exercise_id);

-- Example data:
-- INSERT INTO exercises (name, category, movement_family, primary_purpose, faults_addressed,
--                        typical_sets_low, typical_sets_high, typical_reps_low, typical_reps_high,
--                        typical_intensity_low, typical_intensity_high)
-- VALUES
--     ('Snatch', 'competition', 'snatch', 'Competition lift', '{}', 3, 8, 1, 3, 70, 100),
--     ('Power Snatch', 'competition_variant', 'snatch', 'Turnover speed, pulling height',
--      '{"slow_turnover", "bar_crashing"}', 3, 6, 1, 3, 65, 85),
--     ('Hang Snatch (above knee)', 'competition_variant', 'snatch', 'Extension timing, hip contact',
--      '{"forward_balance_off_floor", "hips_rising_fast"}', 3, 5, 2, 3, 65, 80),
--     ('Snatch Pull', 'pull', 'snatch', 'First and second pull strength, position reinforcement',
--      '{"hips_rising_fast", "early_arm_bend"}', 3, 5, 2, 4, 90, 110),
--     ('Muscle Snatch', 'accessory', 'snatch', 'Turnover strength, bar path awareness',
--      '{"slow_turnover", "early_arm_bend"}', 3, 4, 3, 5, 50, 65),
--     ('Snatch Balance', 'accessory', 'snatch', 'Overhead receiving position confidence and speed',
--      '{"soft_receiving_position", "missed_lockout"}', 3, 5, 1, 3, 60, 90),
--     ('Pause Back Squat (2s in hole)', 'strength', 'squat', 'Positional strength in receiving position',
--      '{"soft_receiving_position", "knee_cave_in_recovery"}', 3, 5, 2, 5, 70, 85);

-- Example agent query — fault-based exercise selection:
-- SELECT name, primary_purpose, typical_intensity_low, typical_intensity_high
-- FROM exercises
-- WHERE 'slow_turnover' = ANY(faults_addressed)
--   AND complexity_level <= 3
-- ORDER BY complexity_level;
-- → Returns: Power Snatch, Muscle Snatch, Tall Snatch, etc.
```

#### Exercise Substitutions (self-referencing)

Models which exercises can substitute for each other, with context about when the substitution is appropriate.

```sql
-- ============================================================
-- 3b. EXERCISE SUBSTITUTIONS
-- ============================================================
-- Allows the agent to swap exercises based on equipment, injury,
-- or athlete level while maintaining training intent.

CREATE TABLE exercise_substitutions (
    id SERIAL PRIMARY KEY,
    exercise_id INT NOT NULL REFERENCES exercises(id),
    substitute_exercise_id INT NOT NULL REFERENCES exercises(id),
    substitution_context VARCHAR(100) NOT NULL,  -- when is this swap valid?
        -- Common values: 'equipment_limitation', 'injury_modification',
        -- 'beginner_regression', 'advanced_progression', 'fatigue_management'
    preserves_stimulus TEXT,                     -- what training effect carries over
    notes TEXT,

    CHECK (exercise_id != substitute_exercise_id),
    UNIQUE(exercise_id, substitute_exercise_id, substitution_context)
);

-- Example:
-- Hang snatch substitutes for snatch when athlete has a sore back off the floor
-- INSERT INTO exercise_substitutions
--     (exercise_id, substitute_exercise_id, substitution_context, preserves_stimulus)
-- VALUES
--     (1, 3, 'injury_modification', 'Extension timing and turnover without first pull loading');
```

#### Exercise Complexes

Complexes are predefined multi-exercise combinations performed without releasing the bar (e.g., "clean + front squat + jerk"). They're common in weightlifting programming and need their own representation.

```sql
-- ============================================================
-- 3c. EXERCISE COMPLEXES
-- ============================================================

CREATE TABLE exercise_complexes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,                -- e.g. 'Clean + Front Squat + Jerk'
    exercises_ordered JSONB NOT NULL,           -- ordered list with rep counts
    total_reps_per_set INT NOT NULL,            -- sum of all reps in one pass
    primary_purpose TEXT,
    typical_intensity_low NUMERIC(5,2),         -- % of weakest lift in the complex
    typical_intensity_high NUMERIC(5,2),
    intensity_reference VARCHAR(100),           -- which lift's 1RM sets the %? e.g. 'clean_and_jerk'
    source_id INT REFERENCES sources(id),
    notes TEXT
);

-- exercises_ordered JSONB schema:
-- [
--   {"exercise_id": 2, "exercise_name": "Clean", "reps": 1},
--   {"exercise_id": 15, "exercise_name": "Front Squat", "reps": 2},
--   {"exercise_id": 5, "exercise_name": "Jerk", "reps": 1}
-- ]
--
-- Agent query: "Give me a complex that addresses turnover speed for clean & jerk"
-- → Join exercise_complexes.exercises_ordered with exercises.faults_addressed
```

---

### 4. Percentage Schemes

Stores day-by-day, set-by-set intensity prescriptions from published programs. This is the granular training data the agent uses as a reference when building sessions.

```sql
-- ============================================================
-- 4. PERCENTAGE SCHEMES — granular intensity/volume prescriptions
-- ============================================================

CREATE TYPE training_phase AS ENUM (
    'general_prep',         -- GPP, high volume, moderate intensity
    'accumulation',         -- building work capacity
    'transmutation',        -- transitioning volume → intensity
    'intensification',      -- lower volume, higher intensity
    'realization',          -- peaking / expressing strength
    'competition',          -- competition week
    'deload',               -- planned recovery
    'transition'            -- off-season / active rest
);

CREATE TABLE percentage_schemes (
    id SERIAL PRIMARY KEY,
    scheme_name VARCHAR(200) NOT NULL,          -- e.g. 'Catalyst 8-Week Competition Cycle'
    source_id INT REFERENCES sources(id),

    -- Where in the program this prescription lives
    phase training_phase NOT NULL,
    block_number INT,                           -- which mesocycle block (1, 2, 3...)
    week_number INT NOT NULL,
    day_number INT NOT NULL,
    exercise_order INT DEFAULT 1,               -- ordering within the session

    -- What movement
    exercise_id INT REFERENCES exercises(id),   -- FK to specific exercise
    movement_family movement_family,            -- fallback if not tied to specific exercise

    -- Prescription
    sets INT NOT NULL CHECK (sets >= 1),
    reps INT NOT NULL CHECK (reps >= 1),
    intensity_pct NUMERIC(5,2) NOT NULL         -- percentage of 1RM
        CHECK (intensity_pct > 0 AND intensity_pct <= 120),  -- pulls can exceed 100%
    intensity_reference VARCHAR(100)            -- whose 1RM? e.g. 'snatch', 'back_squat', 'clean_and_jerk'
        DEFAULT 'competition_lift',

    -- Optional modifiers
    rpe_target NUMERIC(3,1)                     -- 6.0-10.0 scale
        CHECK (rpe_target IS NULL OR (rpe_target >= 5.0 AND rpe_target <= 10.0)),
    tempo VARCHAR(20),                          -- e.g. '3-1-X-1' or '3010'
    rest_seconds INT CHECK (rest_seconds IS NULL OR rest_seconds >= 0),
    backoff_sets INT DEFAULT 0,                 -- e.g. "3x3 @ 85%, then 2x3 @ 75%"
    backoff_intensity_pct NUMERIC(5,2),
    max_attempts BOOLEAN DEFAULT FALSE,         -- is this a max-out day?

    notes TEXT,

    -- Composite unique: one prescription per slot
    UNIQUE(scheme_name, week_number, day_number, exercise_order)
);

CREATE INDEX idx_schemes_phase ON percentage_schemes (phase);
CREATE INDEX idx_schemes_week ON percentage_schemes (scheme_name, week_number);
CREATE INDEX idx_schemes_exercise ON percentage_schemes (exercise_id);

-- Example data: one week from a competition prep cycle
-- INSERT INTO percentage_schemes
--     (scheme_name, source_id, phase, week_number, day_number, exercise_order,
--      exercise_id, sets, reps, intensity_pct, intensity_reference, rest_seconds)
-- VALUES
--     -- Week 5, Day 1 (Mon): Snatch emphasis
--     ('Catalyst 8-Week Comp Prep', 1, 'intensification', 5, 1, 1,
--      1, 1, 1, 90, 'snatch', 120),                    -- Snatch: work to 90% single
--     ('Catalyst 8-Week Comp Prep', 1, 'intensification', 5, 1, 2,
--      1, 3, 2, 80, 'snatch', 90),                     -- Snatch: 3x2 @ 80% backoff
--     ('Catalyst 8-Week Comp Prep', 1, 'intensification', 5, 1, 3,
--      NULL, 3, 5, 80, 'back_squat', 120),             -- Back Squat: 3x5 @ 80%
--
--     -- Week 5, Day 2 (Tue): Clean & Jerk emphasis
--     ('Catalyst 8-Week Comp Prep', 1, 'intensification', 5, 2, 1,
--      NULL, 1, 1, 88, 'clean_and_jerk', 120),         -- C&J: work to 88%
--     ('Catalyst 8-Week Comp Prep', 1, 'intensification', 5, 2, 2,
--      NULL, 3, 2, 78, 'clean_and_jerk', 90);          -- C&J: 3x2 @ 78%

-- Example agent query — "show me all intensification phase prescriptions for snatches":
-- SELECT week_number, day_number, sets, reps, intensity_pct, rest_seconds
-- FROM percentage_schemes ps
-- JOIN exercises e ON ps.exercise_id = e.id
-- WHERE ps.phase = 'intensification'
--   AND e.movement_family = 'snatch'
-- ORDER BY ps.week_number, ps.day_number, ps.exercise_order;
```

**Design rationale:** The `intensity_reference` field is critical. A "snatch pull at 100%" means 100% of the snatch 1RM, not the pull max. Pulls commonly go to 105-110% of the competition lift. Without this field, the agent would misinterpret intensities for accessory and pull work. The `backoff_sets` + `backoff_intensity_pct` pattern captures a very common prescription style: "work up to 90%, then 3x2 at 80%."

---

### 5. Programming Principles

The rules engine. These are structured, queryable if/then rules extracted from source material by the LLM-assisted principle extractor. The agent evaluates applicable principles when making programming decisions.

```sql
-- ============================================================
-- 5. PROGRAMMING PRINCIPLES — structured rules engine
-- ============================================================

CREATE TYPE principle_category AS ENUM (
    'volume', 'intensity', 'frequency',
    'exercise_selection', 'periodization',
    'peaking', 'recovery', 'technique',
    'load_progression', 'deload'
);

CREATE TYPE rule_type AS ENUM (
    'hard_constraint',      -- violating this is dangerous or clearly wrong
    'guideline',            -- strongly recommended, situationally flexible
    'heuristic'             -- rule of thumb, useful default
);

CREATE TABLE programming_principles (
    id SERIAL PRIMARY KEY,
    principle_name VARCHAR(300) NOT NULL,
    source_id INT REFERENCES sources(id),
    category principle_category NOT NULL,
    rule_type rule_type NOT NULL,

    -- WHEN does this principle apply?
    condition JSONB NOT NULL DEFAULT '{}',

    -- WHAT does this principle recommend?
    recommendation JSONB NOT NULL DEFAULT '{}',

    rationale TEXT,
    priority INT DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    conflicts_with INT[],                       -- IDs of principles this may conflict with

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_principles_category ON programming_principles (category);
CREATE INDEX idx_principles_rule_type ON programming_principles (rule_type);
CREATE INDEX idx_principles_condition ON programming_principles USING GIN (condition);
CREATE INDEX idx_principles_priority ON programming_principles (priority DESC);
```

#### Condition JSONB Schema

The `condition` field describes *when* a principle applies. The agent evaluates these against the current programming context.

```jsonc
// All fields are optional. Absent fields mean "applies to all values."
{
    // Training phase constraints
    "phase": "intensification",                     // or array: ["intensification", "realization"]
    "weeks_out_from_competition": {"lte": 3},       // comparison operators: lte, gte, eq, lt, gt, between

    // Athlete constraints
    "athlete_level": ["intermediate", "advanced"],  // who does this apply to?
    "training_age_years": {"gte": 2},               // years of weightlifting training

    // Temporal constraints
    "week_of_block": {"gte": 3},                    // applies from week 3 onward
    "session_of_week": {"lte": 3},                  // early-week sessions only

    // Movement constraints
    "movement_family": "snatch",                    // which lifts?
    "exercise_category": "competition",

    // Performance constraints
    "recent_make_rate": {"lt": 0.7},                // if athlete is missing >30% of attempts
    "rpe_average_last_week": {"gte": 9.0},          // if athlete is grinding

    // Special conditions
    "is_first_block": true,                         // first block with a new athlete
    "returning_from_injury": true
}
```

#### Recommendation JSONB Schema

The `recommendation` field describes *what* the agent should do when the condition matches.

```jsonc
{
    // Volume adjustments (multiplicative modifiers)
    "volume_modifier": 0.6,                         // reduce volume to 60%
    "total_reps_max": 15,                           // hard cap on total reps for a movement

    // Intensity adjustments
    "intensity_floor": 85,                          // don't go below 85%
    "intensity_ceiling": 95,                        // don't exceed 95%
    "intensity_modifier": 1.05,                     // increase prescribed intensity by 5%

    // Frequency adjustments
    "sessions_per_week_max": 4,
    "competition_lift_frequency": 3,                // how many times per week to include comp lifts

    // Exercise selection guidance
    "prefer_exercises": ["power_snatch", "hang_snatch"],
    "avoid_exercises": ["snatch_from_deficit"],
    "require_exercise_categories": ["positional"],  // must include positional work

    // Structural guidance
    "max_exercises_per_session": 5,
    "competition_lifts_first": true,                // always program comp lifts before strength
    "include_deload_week": true,
    "deload_frequency_weeks": 4,                    // deload every 4th week

    // Recovery
    "rest_between_sets_min": 120,                   // seconds
    "rest_between_sets_max": 180
}
```

#### Example Principles

```sql
-- Example: competition peaking volume reduction
-- INSERT INTO programming_principles
--     (principle_name, source_id, category, rule_type, condition, recommendation, rationale, priority)
-- VALUES (
--     'Reduce volume 40-60% in final 2 weeks before competition',
--     1,  -- Catalyst Athletics
--     'peaking',
--     'guideline',
--     '{"phase": "realization", "weeks_out_from_competition": {"lte": 2}}',
--     '{"volume_modifier": 0.5, "intensity_floor": 88, "intensity_ceiling": 100}',
--     'Allows supercompensation while maintaining neural readiness. Athletes should feel fresh, not fatigued.',
--     9
-- );

-- Example: beginner frequency constraint
-- INSERT INTO programming_principles
--     (principle_name, source_id, category, rule_type, condition, recommendation, rationale, priority)
-- VALUES (
--     'Beginners should snatch and clean & jerk every training session',
--     2,  -- Takano
--     'frequency',
--     'guideline',
--     '{"athlete_level": "beginner", "training_age_years": {"lt": 1}}',
--     '{"competition_lift_frequency": "every_session", "prefer_exercises": ["snatch", "clean_and_jerk"]}',
--     'Skill acquisition requires high frequency. Beginners need maximum practice with the competition movements.',
--     8
-- );

-- Example: hard constraint on max attempts
-- INSERT INTO programming_principles
--     (principle_name, source_id, category, rule_type, condition, recommendation, rationale, priority)
-- VALUES (
--     'Never exceed 3 true max attempts in a single session',
--     4,  -- Zatsiorsky
--     'intensity',
--     'hard_constraint',
--     '{"intensity_floor": 95}',
--     '{"total_reps_max": 3, "rest_between_sets_min": 180}',
--     'Neural fatigue from maximal attempts compounds rapidly. More than 3 attempts degrades performance and increases injury risk.',
--     10
-- );

-- Agent query — "what principles apply to an advanced athlete 2 weeks out from comp?":
-- SELECT principle_name, recommendation, rationale, priority
-- FROM programming_principles
-- WHERE (condition->>'phase' IS NULL OR condition->>'phase' = 'realization')
--   AND (condition->'weeks_out_from_competition'->>'lte' IS NULL
--        OR (condition->'weeks_out_from_competition'->>'lte')::int >= 2)
--   AND (condition->'athlete_level' IS NULL
--        OR condition->'athlete_level' @> '"advanced"')
-- ORDER BY priority DESC;
```

---

### 6. Program Templates

Stores complete published programs as structured JSONB. The agent uses these as reference patterns, not as things to copy verbatim — they inform the shape and rhythm of generated programs.

```sql
-- ============================================================
-- 6. PROGRAM TEMPLATES — complete published programs
-- ============================================================

CREATE TABLE program_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(300) NOT NULL,
    source_id INT REFERENCES sources(id),

    -- Classification
    athlete_level VARCHAR(50) NOT NULL
        CHECK (athlete_level IN ('beginner', 'intermediate', 'advanced', 'elite', 'any')),
    goal VARCHAR(100) NOT NULL
        CHECK (goal IN (
            'general_strength', 'competition_prep', 'technique_focus',
            'hypertrophy', 'work_capacity', 'peaking', 'return_to_sport'
        )),

    -- Structure metadata
    duration_weeks INT NOT NULL CHECK (duration_weeks >= 1),
    sessions_per_week INT NOT NULL CHECK (sessions_per_week BETWEEN 1 AND 14),
    phases_included training_phase[],           -- which phases does this program cover?
    periodization_model VARCHAR(100),           -- 'linear', 'undulating', 'block', 'conjugate', 'bulgarian'

    -- The actual program
    program_structure JSONB NOT NULL,

    -- Outcomes (if known — from the source or from athlete feedback)
    expected_outcomes JSONB,

    notes TEXT,
    tags TEXT[],                                -- e.g. {'competition_prep', '3_day', 'squat_focus'}
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_templates_level ON program_templates (athlete_level);
CREATE INDEX idx_templates_goal ON program_templates (goal);
CREATE INDEX idx_templates_tags ON program_templates USING GIN (tags);
CREATE INDEX idx_templates_phases ON program_templates USING GIN (phases_included);
```

#### program_structure JSONB Schema

```jsonc
{
    "weeks": [
        {
            "week_number": 1,
            "phase": "accumulation",
            "volume_load_description": "Moderate-high volume, moderate intensity",
            "sessions": [
                {
                    "day": 1,
                    "label": "Monday — Snatch + Squat",
                    "exercises": [
                        {
                            "exercise_name": "Snatch",
                            "exercise_id": 1,           // FK to exercises table if linked
                            "sets": 5,
                            "reps": 3,
                            "intensity_pct": 72,
                            "intensity_reference": "snatch",
                            "rest_seconds": 90,
                            "notes": "Focus on consistent bar path"
                        },
                        {
                            "exercise_name": "Back Squat",
                            "exercise_id": null,
                            "sets": 4,
                            "reps": 5,
                            "intensity_pct": 75,
                            "intensity_reference": "back_squat",
                            "rest_seconds": 120,
                            "tempo": "3-1-X-0"
                        }
                    ]
                },
                {
                    "day": 2,
                    "label": "Tuesday — Clean & Jerk + Pulls",
                    "exercises": [ /* ... */ ]
                }
            ]
        }
        // ... weeks 2-8
    ],
    "progression_notes": "Intensity increases ~2-3% per week. Volume decreases in weeks 7-8.",
    "deload_strategy": "Week 4 is a deload: volume reduced 40%, intensity capped at 75%."
}
```

---

### 7. Knowledge Chunks (Vector Store)

Stores embedded prose content for semantic retrieval. This is what the agent searches when it needs contextual reasoning that doesn't fit neatly into structured tables.

```sql
-- ============================================================
-- 7. KNOWLEDGE CHUNKS — semantic vector store (pgvector)
-- ============================================================

CREATE TYPE chunk_type AS ENUM (
    'concept',                   -- general training concept explanation
    'methodology',               -- specific training methodology description
    'periodization',             -- periodization theory and application
    'programming_rationale',     -- WHY a program is structured a certain way
    'biomechanics',              -- lift mechanics, positions, force production
    'case_study',                -- specific athlete examples or outcomes
    'fault_correction',          -- how to identify and fix technical problems
    'recovery_adaptation',       -- recovery protocols, supercompensation, etc.
    'competition_strategy',      -- meet day strategy, attempt selection
    'nutrition_bodyweight'       -- weight management for weight classes
);

CREATE TABLE knowledge_chunks (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,                         -- full text WITH contextual preamble (used for embedding)
    raw_content TEXT NOT NULL,                      -- text WITHOUT preamble (for display and dedup)
    content_hash VARCHAR(64) NOT NULL UNIQUE,      -- SHA-256 of raw_content; prevents duplicate ingestion
    embedding vector(1536),                    -- matches your embedding model dimension
    source_id INT REFERENCES sources(id),
    chapter VARCHAR(300),
    section VARCHAR(300),                      -- sub-chapter section title
    page_range VARCHAR(50),                    -- e.g. '142-145'

    chunk_type chunk_type NOT NULL,
    topics TEXT[] NOT NULL DEFAULT '{}',
    -- Common topics:
    --   'volume_management', 'intensity_prescription', 'competition_peaking',
    --   'snatch_technique', 'clean_technique', 'jerk_technique',
    --   'squat_programming', 'pull_programming', 'beginner_development',
    --   'overtraining', 'deload_strategy', 'periodization_models',
    --   'exercise_selection_rationale', 'attempt_selection'

    athlete_level_relevance VARCHAR(50)        -- which athletes is this most relevant for?
        CHECK (athlete_level_relevance IS NULL OR
               athlete_level_relevance IN ('beginner', 'intermediate', 'advanced', 'elite', 'all')),

    -- Quality / retrieval metadata
    information_density VARCHAR(20) DEFAULT 'medium'  -- how rich is this chunk?
        CHECK (information_density IN ('low', 'medium', 'high')),
    contains_specific_numbers BOOLEAN DEFAULT FALSE,   -- does it have concrete %s, rep ranges, etc.?

    created_at TIMESTAMP DEFAULT NOW()
);

-- Primary similarity search index (HNSW for speed)
CREATE INDEX idx_chunks_embedding ON knowledge_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Filtered search indexes
CREATE INDEX idx_chunks_type ON knowledge_chunks (chunk_type);
CREATE INDEX idx_chunks_topics ON knowledge_chunks USING GIN (topics);
CREATE INDEX idx_chunks_source ON knowledge_chunks (source_id);
CREATE INDEX idx_chunks_level ON knowledge_chunks (athlete_level_relevance);

-- Example agent query — semantic search with filters:
-- SELECT content, chapter, chunk_type, topics,
--        1 - (embedding <=> $query_embedding) AS similarity
-- FROM knowledge_chunks
-- WHERE chunk_type IN ('periodization', 'programming_rationale')
--   AND topics && ARRAY['competition_peaking']
--   AND (athlete_level_relevance IS NULL OR athlete_level_relevance IN ('advanced', 'all'))
-- ORDER BY embedding <=> $query_embedding
-- LIMIT 5;
```

**Design rationale for metadata fields:**

- `information_density` lets you boost chunks that contain concrete, actionable information over vague conceptual discussion during retrieval. When the agent is building a program, "high density" chunks with specific numbers are more useful than general theory.
- `contains_specific_numbers` is a quick filter — if the agent needs concrete prescriptions, it can filter for chunks that actually contain percentage/rep data rather than pure prose.
- `topics` array with GIN index enables pre-filtering before the expensive vector similarity search. Searching "competition peaking" across all 10,000 chunks is slower than first filtering to the ~200 chunks tagged with `competition_peaking` and then running similarity within that set.
- `content_hash` stores a SHA-256 of the raw content (without preamble) for deduplication. On re-ingestion, the pipeline checks this hash before creating a new chunk, preventing duplicates when you re-run against the same source.

---

### 8. Ingestion Tracking (Pipeline Idempotency)

Two tables handle pipeline observability, resumability, and rollback. Without these, a failed ingestion at page 150 of a 300-page book means re-running the entire book — burning embedding API credits and LLM calls you've already paid for.

```sql
-- ============================================================
-- 8. INGESTION TRACKING
-- ============================================================

-- ── Run-level tracking ──────────────────────────────────────
-- One row per pipeline invocation. Tracks status, progress,
-- stats, errors, and enough state to resume on failure.

CREATE TABLE ingestion_runs (
    id SERIAL PRIMARY KEY,
    source_id INT NOT NULL REFERENCES sources(id),
    status ingestion_status NOT NULL DEFAULT 'started',

    -- What was processed
    file_path VARCHAR(500) NOT NULL,
    file_hash VARCHAR(64),                     -- SHA-256 of source file for change detection
    total_pages INT,
    pages_processed INT DEFAULT 0,

    -- Results
    chunks_created INT DEFAULT 0,
    chunks_skipped_dedup INT DEFAULT 0,
    chunks_quarantined INT DEFAULT 0,
    principles_extracted INT DEFAULT 0,
    programs_parsed INT DEFAULT 0,
    exercises_created INT DEFAULT 0,
    tables_parsed INT DEFAULT 0,

    -- Resumability
    last_processed_page INT DEFAULT 0,         -- resume point on failure
    checkpoint_data JSONB,                     -- arbitrary state for the pipeline to resume

    -- Timing
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_seconds NUMERIC(10,2),

    -- Error tracking
    error_message TEXT,
    error_details JSONB,                       -- full traceback, failed section, etc.

    -- Config snapshot (reproduce the run)
    config_snapshot JSONB                      -- chunk_size, overlap, embedding model, etc.
);

-- ── Chunk-level tracking ────────────────────────────────────
-- Links every chunk back to the run that created it.
-- Enables: "rollback run #5" → delete all chunks from that run.

CREATE TABLE ingestion_chunk_log (
    id SERIAL PRIMARY KEY,
    ingestion_run_id INT NOT NULL REFERENCES ingestion_runs(id) ON DELETE CASCADE,
    chunk_id INT NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    page_number INT,
    section_title VARCHAR(300),
    classification VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);
```

**How the pipeline uses these tables:**

1. **Start:** Create an `ingestion_runs` row with `status='started'`, snapshot the config.
2. **Progress:** Update `pages_processed` and `last_processed_page` after each page/section.
3. **Chunk dedup:** Before inserting into `knowledge_chunks`, check `content_hash`. If exists, increment `chunks_skipped_dedup` and skip.
4. **Chunk tracking:** After inserting a chunk, log it in `ingestion_chunk_log` with the run ID.
5. **Failure:** Set `status='failed'`, store the error, leave `last_processed_page` as the resume point.
6. **Resume:** On re-run of the same `file_hash`, find the last failed run, read `last_processed_page`, skip to that page.
7. **Rollback:** To undo a bad run: `DELETE FROM ingestion_runs WHERE id = X` — cascade deletes the chunk log entries. Then delete orphaned chunks: `DELETE FROM knowledge_chunks WHERE id NOT IN (SELECT chunk_id FROM ingestion_chunk_log)`.
8. **Completion:** Set `status='completed'`, record `completed_at` and `duration_seconds`.

**Change detection:** The `file_hash` field stores a SHA-256 of the source file. If you re-run the pipeline on a file that hasn't changed, it skips entirely. If the file has changed (new edition, corrected OCR), it creates a new run. This prevents wasted API calls when iterating on pipeline code without changing source material.

---

### 9. PDF Extraction Challenges

Weightlifting source material presents specific extraction challenges that a generic PDF-to-text pipeline won't handle well. These notes document known issues and recommended approaches per source.

#### Source-Specific Issues

| Source | Format | Key Challenges |
|--------|--------|----------------|
| **Everett (Catalyst Athletics)** | Modern typeset PDF | Lift sequence photos with captions interspersed in text. Sidebars with coaching tips. Tables embedded in prose paragraphs. Relatively clean extraction with PyMuPDF. |
| **Takano** | Modern typeset PDF | Clean layout. Some charts and diagrams that need OCR or manual extraction. Program examples in formatted blocks that may parse as garbled text. |
| **Medvedev** | Scanned Soviet-era translation | Multi-column layout. OCR required. Cyrillic mixed with English. Data tables with inconsistent delimiters. Many charts/diagrams that are images, not text. **Hardest source to extract.** |
| **Laputin & Oleshko** | Scanned Soviet-era translation | Similar to Medvedev. Heavy use of data tables. Some tables have hand-drawn lines that confuse OCR. |
| **Zatsiorsky** | Modern typeset PDF (English edition) | Dense academic formatting. Footnotes, references, figure captions. Some formulas and equations. Relatively clean extraction. |

#### Extraction Strategy

```
Source file
    │
    ├─ Is it a modern, typeset PDF?
    │   ├─ YES → PyMuPDF primary extraction
    │   │         └─ Post-process: strip headers/footers, handle page numbers,
    │   │            detect figure captions (skip or tag as metadata)
    │   │
    │   └─ NO (scanned / poor quality) → OCR pipeline
    │       ├─ Tesseract OCR (free, good for English)
    │       ├─ OR: Google Document AI / AWS Textract (better for tables, paid)
    │       └─ Post-process: spell-check, correct common OCR errors in
    │          weightlifting terminology (e.g., "snalch" → "snatch")
    │
    └─ For ALL sources, regardless of extraction method:
        ├─ Table detection: look for grid patterns, pipe/tab delimiters,
        │   consistent column spacing → route to table parser
        ├─ Image detection: skip images, but keep captions if present
        ├─ Program block detection: day/week headers followed by
        │   exercise prescriptions → route to program parser
        └─ Manual review: flag sections with low OCR confidence
            for human review before ingestion
```

#### OCR Correction Dictionary

For Soviet-era translations, common OCR errors include:

```python
OCR_CORRECTIONS = {
    # Weightlifting terminology
    "snalch": "snatch",
    "c1ean": "clean",
    "jerK": "jerk",
    "squalts": "squats",
    "pu11": "pull",
    "1RM": "1RM",           # numeral 1 vs lowercase L
    "lRM": "1RM",
    "IRM": "1RM",

    # Percentage/number fixes
    "l00%": "100%",
    "9O%": "90%",
    "8O%": "80%",
    "7O%": "70%",

    # Common author names
    "Medvedyev": "Medvedev",
    "Zatsiorski": "Zatsiorsky",
    "Verkoshansky": "Verkhoshansky",

    # Training terminology
    "mesocyc1e": "mesocycle",
    "microcy1e": "microcycle",
    "macrocyc1e": "macrocycle",
    "periodisa tion": "periodisation",
    "hypertrophy": "hypertrophy",  # sometimes splits across lines
}
```

#### Recommended First-Ingestion Order

Based on extraction difficulty (easiest first, build confidence in the pipeline):

1. **Everett** — Cleanest PDF, well-structured, exercises the most code paths (prose, programs, exercises)
2. **Zatsiorsky** — Clean PDF, mostly prose, tests theory-heavy chunking profile
3. **Takano** — Clean PDF, heavy on periodization rationale, good for principles extraction
4. **Medvedev** — OCR required, data-heavy, tests table parsing and Soviet chunking profile
5. **Laputin & Oleshko** — Hardest extraction, save for last

---

### Consolidated Schema File

All DDL from the sections above is assembled into a single `schema.sql` file in dependency order, ready to run against a fresh database. The Docker Compose file mounts it as `/docker-entrypoint-initdb.d/01-schema.sql` so the schema is applied automatically on first `docker compose up`.

The file includes:
1. Extensions (`pgvector`)
2. All enum types
3. All tables in FK dependency order
4. All indexes
5. Seed data: Prilepin's chart, core sources, full exercise taxonomy (snatch/clean/jerk/squat families — ~50 exercises), common complexes, exercise substitutions, and parent-child hierarchy updates

See `schema.sql` in the project root.

---

## Implementation Code

All implementation code is in **`oly-code-reference.md`** — a standalone file organized by module that Claude Code can reference directly during implementation.

### Module Overview

| Module | File | Purpose |
|--------|------|---------|
| **Config** | `config.py` | Pipeline settings, API keys, paths. Loads from `.env`. |
| **Pipeline** | `pipeline.py` | Main orchestrator. Routes content through extraction → classification → chunking → loading. Tracks ingestion runs for resumability. |
| **Chunker** | `processors/chunker.py` | Semantic chunking with source-specific profiles (theory-heavy, programming-focused, Soviet data-heavy, web article). Contextual preambles, keep-together patterns, topic tagging, density estimation, validation. |
| **Classifier** | `processors/classifier.py` | Two-pass content classification: structural heuristics (fast) + LLM fallback (accurate). Routes sections to prose chunker, table parser, program parser, or principle extractor. |
| **Principle Extractor** | `processors/principle_extractor.py` | LLM-assisted extraction of structured if/then rules from prose. Outputs conform to the `condition` / `recommendation` JSONB schemas defined in Section 5 of this doc. |
| **Vector Loader** | `loaders/vector_loader.py` | Batch embedding via OpenAI `text-embedding-3-small`, dedup via `content_hash`, filtered similarity search with pre-filtering on topics/chunk_type/density. |
| **Structured Loader** | `loaders/structured_loader.py` | Upserts to `sources`, `exercises`, `programming_principles`, `program_templates`, `percentage_schemes`. Handles FK resolution. |
| **PDF Extractor** | `extractors/pdf_extractor.py` | PyMuPDF-based text extraction with page-level output, header/footer stripping, and figure caption detection. |

### Infrastructure Files (also in code reference)

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Postgres 16 + pgvector. Mounts `schema.sql` for auto-setup. |
| `requirements.txt` | All Python dependencies. |
| `.env` | API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`). |
| `.gitignore` | Excludes `.env`, `sources/`, logs, Python cache, Docker volumes. |

### Key Implementation Notes

**Embedding model:** OpenAI `text-embedding-3-small` (1536 dims). Cost is ~$0.02/1M tokens — under $1 total for all source books even with multiple re-embedding passes.

**Batch embedding:** The vector loader pre-filters duplicates via `content_hash` *before* hitting the API, then batch-embeds all new chunks (100 per API call). A 300-page book goes from 200+ API round trips to 2-3.

**Remaining TODOs in code:**
1. `pipeline.py` — `_parse_program_template()` is stubbed. Needs an LLM call with the `program_structure` JSONB schema (Section 6) as the target format. Defer to Phase 4-5.
2. `processors/principle_extractor.py` — LLM client placeholder. Wire up Anthropic client in Phase 2, step 11.
3. `processors/classifier.py` — `_llm_classify()` falls back to PROSE. Wire up in Phase 2, step 8.

---

## Recommended Source Material & Ingestion Strategy

| Source | Type | Ingestion Strategy |
|--------|------|-------------------|
| **Prilepin's Chart** | Structured data | Direct insert to `prilepin_chart` table as seed data. No embedding needed. |
| **Catalyst Athletics — Olympic Weightlifting** (Everett) | Book (prose + programs) | Chunk prose → vector store. Extract program templates → `program_templates`. Extract exercise descriptions → `exercises`. |
| **Weightlifting Programming** (Takano) | Book (prose + programs) | Same as Everett. Heavy on periodization rationale — great for principles extraction. |
| **Managing the Training of Weightlifters** (Laputin & Oleshko) | Book (data-heavy, Soviet) | Tables → structured data. Volume/intensity data → `percentage_schemes`. Prose summaries → vector store. |
| **A System of Multi-Year Training in Weightlifting** (Medvedev) | Book (data-heavy) | Primary value is the training load data. Extract percentage distributions → structured tables. |
| **Science and Practice of Strength Training** (Zatsiorsky) | Book (theory-heavy) | Almost entirely prose → vector store. Rich in principles → `programming_principles`. |
| **Catalyst Athletics website programs** | Web content | Parse published cycles → `program_templates` as structured JSONB. |
| **Greg Nuckols / Stronger By Science** | Articles | Chunk articles → vector store. Evidence-based principles → `programming_principles`. |

---

## Chunking Strategy

### The Core Problem

Most RAG guidance recommends small chunks (300-500 tokens) for precise retrieval. Weightlifting programming content breaks this assumption because it is *deeply contextual*. A paragraph about "keeping intensity at 80% during accumulation" is meaningless unless the chunk also captures what accumulation means in that author's periodization model, what phase preceded it, and what athlete population is being discussed.

Small chunks produce retrieval hits that are technically relevant but practically useless to the agent — fragments of advice stripped of the reasoning that makes them applicable. On the other hand, chunks that are too large dilute the embedding signal and return noisy results. The strategy below navigates this trade-off by varying chunk size by content type and source, using contextual overlap to preserve reasoning chains, and applying domain-specific keep-together rules to prevent corrupted chunks.

---

### Content Routing: Chunk vs Structure vs Extract

Before any chunking happens, the classifier must route each content section to the right processing path. This decision has more impact on retrieval quality than chunk size.

**Route to vector store (chunk as prose):**
- Periodization theory discussions and explanations
- Programming rationale — "why we do X in the prep phase"
- Biomechanical explanations of lift mechanics
- Recovery and adaptation discussions
- Case studies and athlete examples
- Exercise selection reasoning
- Historical context and methodology comparisons

**Route to structured tables (do NOT chunk):**
- Rep/set/intensity prescriptions (Prilepin's table, percentage charts)
- Exercise names, descriptions, and taxonomy relationships
- Specific program templates (week-by-week training plans)
- Tabular data of any kind (training load distributions, competition results)

**Route to principle extraction (LLM-assisted → `programming_principles`):**
- Any "if X, then Y" programming logic
- Volume/intensity relationship rules
- Phase-specific recommendations with concrete thresholds
- Recovery and deload guidelines with specific triggers
- Competition prep timelines with week-by-week adjustments

**The hard part: mixed content.** Weightlifting books frequently interleave prose rationale with concrete prescriptions in the same paragraph. For example:

> "During the intensification phase, the lifter should reduce total weekly volume by approximately 25-30% relative to the accumulation block. The snatch and clean & jerk should be performed for singles and doubles at 85-93%, with total session volume not exceeding 10-15 lifts above 80%. This allows the nervous system to adapt to heavier loads while managing fatigue."

This paragraph contains both a retrievable concept (why you reduce volume during intensification) AND extractable principles (25-30% volume reduction, specific intensity ranges, rep caps). The correct handling is:

1. Chunk the full paragraph into the vector store (preserves the reasoning)
2. Also extract the concrete rules into `programming_principles` (makes them queryable)
3. Tag the chunk with `contains_specific_numbers: true` and `information_density: 'high'`

Duplication between the vector store and structured tables is intentional and desirable. The structured version enables deterministic queries; the prose version enables semantic retrieval of the surrounding reasoning.

---

### Chunk Sizing by Source Type

Different sources have different information densities and writing styles. A single chunk size doesn't serve all of them well.

#### Theory-Heavy Sources (1000-1200 tokens)
**Sources:** Zatsiorsky, Verkhoshansky, general sports science texts
**Why large:** These authors build multi-paragraph arguments where each paragraph depends on the previous three. Cutting at 500 tokens almost always severs a reasoning chain.

Example content profile:
- Dense, academic prose
- Few inline prescriptions
- Arguments span 3-5 paragraphs before reaching a conclusion
- Terminology is introduced and then used without re-definition
- Overlap: 250 tokens (generous, to preserve argument continuity)

#### Programming-Focused Sources (800-1000 tokens)
**Sources:** Everett (Catalyst Athletics), Takano, Pendlay
**Why medium:** These mix rationale with program descriptions. Individual sections are more self-contained — a section on "Accumulation Phase Structure" often fully explains itself within 2-3 paragraphs.

Example content profile:
- Alternating prose rationale and concrete program descriptions
- Section headers clearly delineate topics
- Exercise prescriptions often inline: "Snatch 5x3 @ 72%"
- Overlap: 200 tokens (standard)

#### Data-Heavy Soviet Sources (600-800 tokens)
**Sources:** Medvedev, Laputin & Oleshko, Roman
**Why smaller:** These sources are dense with tables, percentages, and volume distributions. Much of the content should be routed to structured tables, not chunked at all. The prose that remains is typically terse and self-contained.

Example content profile:
- Short explanatory paragraphs between large data tables
- Tables should be parsed separately, not chunked
- Prose is functional, not argumentative — smaller chunks lose less context
- Overlap: 150 tokens

#### Web Articles and Blog Posts (500-800 tokens)
**Sources:** Catalyst Athletics website articles, Stronger By Science, JTS
**Why variable:** Articles vary wildly. A Greg Nuckols deep-dive on periodization reads like a textbook (chunk large). A short Catalyst Athletics exercise description is fully self-contained in 200 words (chunk small). Classify article length first:
- Articles > 3000 words: treat like theory-heavy (1000-1200 tokens)
- Articles 1000-3000 words: treat like programming-focused (800 tokens)
- Articles < 1000 words: single chunk or 2-3 small chunks

---

### Keep-Together Rules

Certain content patterns must never be split by a chunk boundary. If a chunk break would land inside one of these patterns, extend the chunk to include the full pattern (even if it slightly exceeds the target size).

```python
KEEP_TOGETHER_PATTERNS = {
    # Rep schemes: "5x3 @ 75%", "3×2 at 85%", "5 sets of 3 reps at 80%"
    "rep_scheme": r"\d+\s*[xX×]\s*\d+\s*(?:@|at)?\s*\d+%",

    # Percentage references: "85% of 1RM", "work up to 90%"
    "percentage_ref": r"\d+%\s*(?:of\s+)?(?:1RM|max|PR|one[- ]rep[- ]max)",

    # Prescription labels: "Sets: 5", "Reps: 3", "Rest: 2 min"
    "prescription_label": r"(?:Sets?|Reps?|Rest|Tempo|RPE|Intensity)\s*:\s*[\d\w]+",

    # Exercise sequences / complexes: "Clean + Front Squat + Jerk"
    "exercise_complex": r"(?:[A-Z][a-z]+\s*(?:Snatch|Clean|Jerk|Squat|Pull|Press|Push))"
                        r"(?:\s*\+\s*(?:[A-Z][a-z]+\s*)*"
                        r"(?:Snatch|Clean|Jerk|Squat|Pull|Press|Push|Balance|Deadlift))+",

    # Multi-line program blocks (day prescriptions)
    # Matches patterns like:
    #   Monday:
    #     Snatch 5x3 @ 72%
    #     Back Squat 4x5 @ 75%
    "daily_program_block": r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
                           r"Day\s+\d+)\s*:?\s*\n(?:\s+.+\n?){1,8}",

    # Numbered lists of exercises (often found in program descriptions)
    # 1. Snatch — 5x3 @ 72%
    # 2. Clean Pull — 4x3 @ 95%
    "numbered_exercise_list": r"(?:\d+\.\s+.+\n?){2,8}",

    # Percentage ranges: "70-80%", "85%-93%"
    "percentage_range": r"\d+\s*-\s*\d+\s*%",

    # Volume prescriptions: "15-20 total reps", "NL = 24"
    "volume_prescription": r"(?:\d+\s*-\s*\d+\s+(?:total\s+)?reps|NL\s*=\s*\d+)",

    # Set/rep matrix patterns (common in Soviet texts):
    # 70%/3x3  75%/3x2  80%/2x2  85%/2x1
    "soviet_notation": r"(?:\d+%\s*/\s*\d+\s*[xX]\s*\d+\s*){2,}",
}
```

**Implementation:** Before splitting, scan the candidate split point. If a regex match spans the split point, extend the current chunk to include the full match. This may push chunks 10-15% over the target size, which is acceptable.

---

### Contextual Overlap (Contextual Preamble)

Standard overlap copies the last N tokens of the previous chunk into the start of the next. This preserves some continuity but doesn't tell the embedding model or LLM *what topic the chunk belongs to*.

A stronger approach for this domain is **contextual preamble** — prepend a short context header to every chunk that identifies its place in the source material's structure:

```
[Source: Olympic Weightlifting: A Complete Guide | Author: Greg Everett]
[Chapter: Competition Preparation | Section: Volume Management in the Final Mesocycle]

<original chunk content starts here>
```

This preamble should be:
- Generated from document structure (chapter titles, section headers) rather than LLM-generated
- Kept short (40-60 tokens) so it doesn't eat into the content budget
- Consistent in format across all chunks for uniform embedding behavior
- NOT included in the token count for chunk sizing (it's metadata, not content)

**Why this matters:** When the agent searches for "how to reduce volume before competition," both a chunk about competition peaking AND a chunk about off-season deloads might have similar content about "reducing volume." The preamble disambiguates them at the embedding level — the competition prep chunk gets a higher similarity score because "Competition Preparation" in the preamble aligns with the query.

```python
def build_contextual_preamble(source_title: str, author: str,
                               chapter: str, section: str) -> str:
    """Build a context header to prepend to every chunk."""
    lines = [f"[Source: {source_title} | Author: {author}]"]
    context_parts = []
    if chapter:
        context_parts.append(f"Chapter: {chapter}")
    if section:
        context_parts.append(f"Section: {section}")
    if context_parts:
        lines.append(f"[{' | '.join(context_parts)}]")
    return "\n".join(lines) + "\n\n"
```

Additionally, standard tail overlap (last 200 tokens of previous chunk) should still be applied *after* the preamble. The two approaches are complementary: the preamble provides structural context, the tail overlap provides content continuity.

---

### Topic Tagging Pass

After chunking, each chunk should be auto-tagged with topics from a controlled vocabulary. This enables the filtered similarity search pattern (filter by topic first, then run vector similarity within the filtered set), which is significantly faster and more precise than unfiltered similarity search across the entire corpus.

#### Controlled Topic Vocabulary

```python
TOPIC_VOCABULARY = {
    # Periodization & programming structure
    "periodization_models",         # linear, undulating, block, conjugate, Bulgarian
    "accumulation_phase",
    "intensification_phase",
    "realization_phase",
    "competition_peaking",
    "deload_strategy",
    "annual_planning",              # macrocycle structure, yearly planning

    # Volume & intensity
    "volume_management",
    "intensity_prescription",
    "load_progression",             # how to increase weight over time
    "volume_intensity_relationship",

    # Competition lifts
    "snatch_technique",
    "snatch_programming",
    "clean_technique",
    "clean_programming",
    "jerk_technique",
    "jerk_programming",

    # Strength work
    "squat_programming",
    "pull_programming",
    "pressing_programming",
    "accessory_selection",

    # Athlete management
    "beginner_development",
    "intermediate_programming",
    "advanced_programming",
    "fault_correction",             # identifying and fixing technical errors
    "exercise_selection_rationale",

    # Recovery & adaptation
    "recovery_protocols",
    "overtraining_detection",
    "adaptation_theory",            # supercompensation, SRA curves
    "fatigue_management",

    # Competition
    "competition_strategy",         # attempt selection, warm-up room, timing
    "weight_class_management",
    "meet_day_protocol",

    # Special topics
    "complexes_and_combinations",
    "tempo_and_positional_work",
    "youth_development",
    "masters_athletes",
    "returning_from_injury",
}
```

#### Tagging Approach: Two-Pass

**Pass 1 — Keyword matching (fast, free):** Scan each chunk for domain-specific keywords and map to topics. This catches the obvious cases.

```python
KEYWORD_TO_TOPIC = {
    # Direct keyword matches
    "accumulation": ["accumulation_phase"],
    "intensification": ["intensification_phase"],
    "realization": ["realization_phase", "competition_peaking"],
    "peaking": ["competition_peaking"],
    "taper": ["competition_peaking", "volume_management"],
    "deload": ["deload_strategy", "recovery_protocols"],
    "overtraining": ["overtraining_detection", "fatigue_management"],
    "supercompensation": ["adaptation_theory"],

    # Lift-specific
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
    "1RM": ["intensity_prescription", "load_progression"],
    "RPE": ["intensity_prescription", "fatigue_management"],

    # Phase/structure patterns
    "mesocycle": ["periodization_models", "annual_planning"],
    "macrocycle": ["periodization_models", "annual_planning"],
    "microcycle": ["periodization_models"],
    "block periodization": ["periodization_models"],
    "linear periodization": ["periodization_models"],
    "undulating": ["periodization_models"],
}

def keyword_tag(chunk_content: str) -> set[str]:
    """Fast keyword-based topic tagging."""
    content_lower = chunk_content.lower()
    topics = set()
    for keyword, topic_list in KEYWORD_TO_TOPIC.items():
        if keyword.lower() in content_lower:
            topics.update(topic_list)
    return topics
```

**Pass 2 — LLM-assisted tagging (slower, more accurate):** For chunks where keyword matching produces zero or ambiguous results, run a lightweight LLM call to classify. This catches nuanced cases where the topic is discussed without using the exact keywords (e.g., a paragraph about "letting the athlete feel heavy loads" is really about `realization_phase` and `competition_peaking` even though neither word appears).

```python
TOPIC_TAGGING_PROMPT = """You are classifying a text chunk from an Olympic weightlifting programming book.

Assign 1-4 topics from this controlled vocabulary:
{vocabulary}

Chunk content:
{content}

Source context: {source_title}, {chapter}

Respond with ONLY a JSON array of topic strings, e.g. ["volume_management", "competition_peaking"].
If none apply, respond with [].
"""

def llm_tag(chunk_content: str, source_title: str, chapter: str,
            vocabulary: set[str]) -> list[str]:
    """LLM-assisted topic tagging for ambiguous chunks."""
    # Only call LLM if keyword tagging produced < 1 topic
    # This keeps costs down — most chunks get tagged by keywords alone
    prompt = TOPIC_TAGGING_PROMPT.format(
        vocabulary=sorted(vocabulary),
        content=chunk_content[:1500],  # truncate to save tokens
        source_title=source_title,
        chapter=chapter,
    )
    # response = llm_client.complete(prompt)
    # return json.loads(response)
    return []
```

**Cost optimization:** In practice, keyword matching handles ~70-80% of chunks. The LLM pass only runs on the remaining 20-30% that have zero or one keyword-matched topic. At ingestion time (not query time), the cost is amortized — you tag once, query many times.

---

### Chunk Quality Validation

After chunking and tagging, run a validation pass to catch common problems before loading into the vector store.

```python
@dataclass
class ChunkValidationResult:
    chunk_id: int
    is_valid: bool
    issues: list[str]

def validate_chunk(chunk: Chunk) -> ChunkValidationResult:
    """Validate a chunk before loading into the vector store."""
    issues = []

    # Too short — probably a fragment that lost its context
    if chunk.token_count < 50:
        issues.append(f"Chunk too short ({chunk.token_count} tokens). "
                      "Likely a fragment — consider merging with adjacent chunk.")

    # Too long — will dilute embedding signal
    if chunk.token_count > 1500:
        issues.append(f"Chunk too long ({chunk.token_count} tokens). "
                      "Consider splitting further.")

    # No topics assigned — will be invisible to filtered search
    if not chunk.metadata.get("topics"):
        issues.append("No topics assigned. Chunk will only appear in unfiltered similarity search.")

    # Contains structured data that should have been routed to tables
    rep_scheme_count = len(re.findall(r"\d+\s*[xX×]\s*\d+\s*@\s*\d+%", chunk.content))
    if rep_scheme_count >= 3:
        issues.append(f"Contains {rep_scheme_count} rep schemes. "
                      "Consider routing to percentage_schemes table instead.")

    # Contains a table (heuristic: 3+ lines with consistent delimiter patterns)
    lines = chunk.content.split("\n")
    table_like_lines = [l for l in lines if l.count("|") >= 2 or l.count("\t") >= 2]
    if len(table_like_lines) >= 3:
        issues.append("Contains what looks like a table. "
                      "Consider parsing as structured data.")

    # Orphaned context — chunk starts mid-sentence
    if chunk.content and chunk.content[0].islower():
        issues.append("Chunk starts with lowercase — likely a mid-sentence split. "
                      "Check chunk boundary alignment.")

    return ChunkValidationResult(
        chunk_id=chunk.metadata.get("chunk_index", -1),
        is_valid=len(issues) == 0,
        issues=issues,
    )
```

Run this on every chunk during ingestion. Log warnings for invalid chunks, quarantine chunks with critical issues (too short, contains un-parsed tables), and auto-fix where possible (merge short chunks with their neighbors).

---

### Retrieval Evaluation

Chunk quality is ultimately measured by retrieval quality. After ingesting a source, run a set of test queries and evaluate whether the returned chunks actually help the agent make good programming decisions.

#### Test Query Set

```python
RETRIEVAL_EVAL_QUERIES = [
    # Specific programming questions (should return concrete, actionable chunks)
    {
        "query": "How should I structure volume during a 4-week accumulation block?",
        "expected_topics": ["accumulation_phase", "volume_management"],
        "expected_chunk_type": ["periodization", "programming_rationale"],
        "should_contain_numbers": True,
    },
    {
        "query": "What exercises help fix an athlete who consistently misses snatches forward?",
        "expected_topics": ["fault_correction", "snatch_technique", "exercise_selection_rationale"],
        "expected_chunk_type": ["fault_correction", "methodology"],
    },
    {
        "query": "How many weeks out should I start reducing volume before a competition?",
        "expected_topics": ["competition_peaking", "volume_management"],
        "expected_chunk_type": ["periodization", "programming_rationale"],
        "should_contain_numbers": True,
    },
    {
        "query": "When should a beginner transition from learning technique to structured programming?",
        "expected_topics": ["beginner_development", "periodization_models"],
        "expected_chunk_type": ["methodology", "concept"],
    },
    {
        "query": "What is the relationship between squat strength and clean & jerk performance?",
        "expected_topics": ["squat_programming", "clean_programming"],
        "expected_chunk_type": ["concept", "biomechanics"],
    },

    # Negative / edge cases (should NOT return irrelevant chunks)
    {
        "query": "How to do a barbell curl",
        "expected_topics": [],
        "note": "Should return few or no results — curls are not in the knowledge base.",
    },
    {
        "query": "What percentage should I use for back squats?",
        "note": "Ambiguous query — results should span multiple contexts. "
                "Good retrieval returns chunks discussing both accumulation and peaking squat work.",
    },
]
```

#### Evaluation Metrics

For each test query, measure:

- **Precision@5:** Of the top 5 returned chunks, how many are actually relevant to the query?
- **Topic hit rate:** Do the returned chunks' topic tags overlap with `expected_topics`?
- **Source diversity:** Are results from multiple sources, or dominated by one book? (Diversity is generally better for programming decisions.)
- **Information density distribution:** For queries marked `should_contain_numbers`, do the returned chunks actually contain concrete prescriptions?

Target benchmarks for iteration:
- Precision@5 ≥ 0.7 (at least 3-4 of top 5 are relevant)
- Topic hit rate ≥ 0.8 (expected topics appear in results)
- No single source should dominate > 60% of results for general queries

If these benchmarks aren't met after initial ingestion, iterate on: chunk sizes for the underperforming source, topic tagging accuracy, and the contextual preamble content.

---

## Implementation Order

Follow this sequence. Each step is independently testable before moving to the next.

### Phase 1: Infrastructure & Schema (Day 1)

1. **Create project structure** — Set up the directory layout from the Updated Project Structure section. Create all `__init__.py` files, copy in `docker-compose.yml`, `requirements.txt`, `.env`.
2. **Copy `schema.sql`** — The consolidated schema file is provided and includes all DDL, indexes, and seed data (Prilepin's chart, sources, ~50 exercises, complexes, substitutions).
3. **Start Postgres** — `docker compose up -d`. The schema auto-applies on first startup.
4. **Verify schema + seed data:**
    ```bash
    psql -h localhost -U oly -d oly_programming -c "\dt"
    # Should show 11 tables: sources, prilepin_chart, exercises,
    # exercise_substitutions, exercise_complexes, percentage_schemes,
    # programming_principles, program_templates, knowledge_chunks,
    # ingestion_runs, ingestion_chunk_log

    psql -h localhost -U oly -d oly_programming -c "SELECT count(*) FROM exercises;"
    # Should be ~50

    psql -h localhost -U oly -d oly_programming -c \
      "SELECT name FROM exercises WHERE 'slow_turnover' = ANY(faults_addressed);"
    # Should return: Power Snatch, Muscle Snatch, Hang Power Snatch, Muscle Clean, etc.

    psql -h localhost -U oly -d oly_programming -c "SELECT * FROM prilepin_chart;"
    # Should return 4 rows
    ```

### Phase 2: Core Pipeline Modules (Days 2-3)

5. **Implement `config.py`** — Copy from `oly-code-reference.md`. Verify:
    ```bash
    python -c "from config import Settings; s = Settings(); print(s.embedding_model, s.embedding_dim)"
    # Should print: text-embedding-3-small 1536
    ```
    Ensure `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are set in `.env`.

6. **Implement `extractors/pdf_extractor.py`** — Copy from `oly-code-reference.md`. Test with a few pages of Everett's book:
    ```bash
    python -c "
    from extractors.pdf_extractor import PDFExtractor
    pages = PDFExtractor().extract('sources/catalyst_athletics.pdf')
    print(f'Extracted {len(pages)} pages')
    print(pages[0][:500])  # eyeball first page
    "
    ```
    Check for garbled text, figure captions mixed into prose, header/footer artifacts. Clean up post-processing as needed.

7. **Implement `processors/chunker.py`** — Copy from `oly-code-reference.md`. Test with a known chapter:
    ```bash
    python -c "
    from processors.chunker import SemanticChunker
    chunker = SemanticChunker.for_source(
        'Olympic Weightlifting: A Complete Guide for Athletes and Coaches'
    )
    test_text = open('sources/everett_chapter8.txt').read()  # extract one chapter manually
    chunks = chunker.chunk(test_text, source_title='...', author='Greg Everett')
    for c in chunks[:3]:
        print(f'Tokens: {c.token_count}, Topics: {c.topics}, Density: {c.information_density}')
        print(c.content[:200])
        print('---')
    "
    ```
    Verify: chunk sizes within profile range, preambles present, topics assigned, keep-together patterns not split.

8. **Implement `processors/classifier.py`** — Copy from `oly-code-reference.md`. Test with sample text snippets:
    ```bash
    python -c "
    from processors.classifier import ContentClassifier, ContentType
    from config import Settings
    clf = ContentClassifier(Settings())

    # Test prose
    sections = clf.classify_sections('The accumulation phase should...')
    assert sections[0].content_type == ContentType.PROSE

    # Test program template
    sections = clf.classify_sections('Monday:\n  Snatch 5x3 @ 72%\n  Back Squat 4x5 @ 75%')
    assert sections[0].content_type == ContentType.PROGRAM_TEMPLATE
    print('All classifications correct')
    "
    ```

9. **Implement `loaders/vector_loader.py`** — Copy from `oly-code-reference.md`. The OpenAI embedding client is already wired in. Test: load a few chunks and verify they appear in `knowledge_chunks` with correct metadata, `content_hash` populated, and non-zero embeddings.
    ```bash
    python -c "
    from loaders.vector_loader import VectorLoader
    from processors.chunker import Chunk
    from config import Settings
    loader = VectorLoader(Settings())
    test_chunk = Chunk(
        content='[Source: Test] Snatch technique fundamentals...',
        raw_content='Snatch technique fundamentals...',
        topics=['snatch_technique'],
        information_density='medium',
    )
    loaded = loader.load_chunks([test_chunk], source_id=6)
    print(f'Loaded {loaded} chunk(s)')
    "
    ```

10. **Implement `loaders/structured_loader.py`** — Copy from `oly-code-reference.md`. Key methods: `upsert_source()`, `load_principles()`, `load_program()`. Test each with a manual insert and verify rows in the database.

11. **Implement `processors/principle_extractor.py`** — Copy from `oly-code-reference.md`. Wire up the LLM client (Anthropic or OpenAI). Test with a known passage:
    ```
    "During the final two weeks before competition, volume should be
    reduced by 40-60% while maintaining intensity above 90%."
    ```
    Verify: extracted principle has `category='peaking'`, condition includes `weeks_out_from_competition`, recommendation includes `volume_modifier` and `intensity_floor`.

12. **Implement `pipeline.py`** — Copy from `oly-code-reference.md`. Wire everything together. Add ingestion run tracking (create run on start, update on progress, mark complete/failed).

### Phase 3: First Ingestion (Day 4)

13. **Ingest Everett's book** — Easiest source, exercises all code paths:
    ```bash
    python pipeline.py \
        --source ./sources/catalyst_athletics.pdf \
        --title "Olympic Weightlifting: A Complete Guide for Athletes and Coaches" \
        --author "Greg Everett" \
        --type book
    ```

14. **Verify ingestion results:**
    ```bash
    # Check ingestion run record
    psql -c "SELECT status, chunks_created, chunks_skipped_dedup,
             principles_extracted, programs_parsed,
             duration_seconds FROM ingestion_runs ORDER BY id DESC LIMIT 1;"

    # Spot-check chunks
    psql -c "SELECT left(content, 150), topics, information_density
             FROM knowledge_chunks ORDER BY id LIMIT 5;"

    # Spot-check principles
    psql -c "SELECT principle_name, category, rule_type, priority
             FROM programming_principles ORDER BY priority DESC LIMIT 5;"
    ```

15. **Verify embeddings** — Spot-check that stored vectors are non-zero and similarity search returns meaningful results:
    ```bash
    psql -c "SELECT id, left(content, 80),
             (embedding IS NOT NULL) as has_embedding
             FROM knowledge_chunks LIMIT 5;"
    ```

### Phase 4: Retrieval Validation (Day 5)

16. **Run retrieval eval queries** — Use the test query set from the Chunking Strategy section. For each query, call `similarity_search()` and evaluate precision, topic hit rate, and source diversity.

17. **Iterate on chunk sizing** — If retrieval quality is low, adjust the source profiles in the chunker and re-ingest. The ingestion tracking tables make it easy to compare runs.

18. **Iterate on topic tagging** — If topic hit rates are low, extend the `KEYWORD_TO_TOPIC` mapping or wire up the LLM tagging pass.

### Phase 5: Additional Sources (Week 2)

19. **Ingest Zatsiorsky** — Clean PDF, mostly prose. Tests the theory-heavy chunking profile.

20. **Ingest Takano** — Clean PDF, heavy on periodization. Good for principles extraction validation.

21. **Attempt Medvedev / Laputin** — OCR required. Add OCR fallback to `pdf_extractor.py`, apply the OCR corrections dictionary. Expect this to take longer and require manual review of extraction quality.

22. **Ingest web content** — Catalyst Athletics website programs, Stronger By Science articles. Tests the web article chunking profile.

### Phase 6: Programming Agent (Future — Separate Design Doc)

23. **Design the agent pipeline** — This doc covers ingestion only. The next document should cover:
    - Athlete data model (training logs, PRs, make/miss rates, RPE history)
    - Agent orchestration (LangGraph or similar)
    - Retrieval strategy (when to query structured tables vs vector search vs both)
    - Program generation prompts and output validation
    - Feedback loop from athlete training logs to programming adjustments

