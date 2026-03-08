-- ============================================================
-- Olympic Weightlifting Programming — Athlete Data Model
-- ============================================================
-- Run AFTER the Phase 1-5 schema (schema.sql).
-- Depends on: exercises, exercise_complexes, training_phase enum
--
-- Usage: psql -h localhost -U oly -d oly_programming -f athlete_schema.sql
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- ENUM TYPES
-- ────────────────────────────────────────────────────────────

CREATE TYPE athlete_level AS ENUM (
    'beginner',
    'intermediate',
    'advanced',
    'elite'
);

CREATE TYPE biological_sex AS ENUM ('male', 'female');

CREATE TYPE goal_type AS ENUM (
    'competition_prep',
    'general_strength',
    'technique_focus',
    'pr_attempt',
    'return_to_sport',
    'work_capacity'
);

CREATE TYPE program_status AS ENUM (
    'draft',
    'active',
    'completed',
    'abandoned',
    'superseded'
);


-- ────────────────────────────────────────────────────────────
-- TABLES
-- ────────────────────────────────────────────────────────────

-- ── Athletes ────────────────────────────────────────────────

CREATE TABLE athletes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(300),
    level athlete_level NOT NULL,
    biological_sex biological_sex,

    bodyweight_kg NUMERIC(5,1),
    height_cm NUMERIC(5,1),
    age INT,
    weight_class VARCHAR(20),

    training_age_years NUMERIC(4,1),
    sessions_per_week INT DEFAULT 4
        CHECK (sessions_per_week BETWEEN 1 AND 14),
    session_duration_minutes INT DEFAULT 90,
    available_equipment TEXT[],

    injuries TEXT[],
    technical_faults TEXT[],
    exercise_preferences JSONB DEFAULT '{}',

    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);


-- ── Athlete Maxes ───────────────────────────────────────────

CREATE TABLE athlete_maxes (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    exercise_id INT NOT NULL REFERENCES exercises(id),

    weight_kg NUMERIC(6,1) NOT NULL,
    is_competition_result BOOLEAN DEFAULT FALSE,
    rpe NUMERIC(3,1),
    date_achieved DATE NOT NULL,

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

CREATE TABLE athlete_goals (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    goal goal_type NOT NULL,

    competition_date DATE,
    competition_name VARCHAR(200),
    target_total_kg NUMERIC(6,1),

    target_snatch_kg NUMERIC(6,1),
    target_cj_kg NUMERIC(6,1),

    target_faults TEXT[],

    priority INT DEFAULT 1
        CHECK (priority BETWEEN 1 AND 5),
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_goals_athlete ON athlete_goals (athlete_id);
CREATE INDEX idx_goals_active ON athlete_goals (athlete_id, is_active)
    WHERE is_active = TRUE;


-- ── Generated Programs ──────────────────────────────────────

CREATE TABLE generated_programs (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,

    name VARCHAR(300),
    status program_status NOT NULL DEFAULT 'draft',
    phase training_phase NOT NULL,
    duration_weeks INT NOT NULL CHECK (duration_weeks BETWEEN 1 AND 16),
    sessions_per_week INT NOT NULL,
    start_date DATE,
    end_date DATE,

    goal_id INT REFERENCES athlete_goals(id),

    athlete_snapshot JSONB NOT NULL,
    maxes_snapshot JSONB NOT NULL,
    generation_params JSONB NOT NULL,
    knowledge_sources_used JSONB,

    rationale TEXT,
    outcome_summary JSONB,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_programs_athlete ON generated_programs (athlete_id);
CREATE INDEX idx_programs_status ON generated_programs (status);
CREATE INDEX idx_programs_active ON generated_programs (athlete_id, status)
    WHERE status = 'active';


-- ── Program Sessions ────────────────────────────────────────

CREATE TABLE program_sessions (
    id SERIAL PRIMARY KEY,
    program_id INT NOT NULL REFERENCES generated_programs(id) ON DELETE CASCADE,
    week_number INT NOT NULL,
    day_number INT NOT NULL,
    session_label VARCHAR(200),

    estimated_duration_minutes INT,
    session_rpe_target NUMERIC(3,1),
    focus_area VARCHAR(100),

    notes TEXT,

    UNIQUE(program_id, week_number, day_number)
);

CREATE INDEX idx_sessions_program ON program_sessions (program_id);


-- ── Session Exercises ───────────────────────────────────────

CREATE TABLE session_exercises (
    id SERIAL PRIMARY KEY,
    session_id INT NOT NULL REFERENCES program_sessions(id) ON DELETE CASCADE,
    exercise_order INT NOT NULL,

    exercise_id INT REFERENCES exercises(id),
    complex_id INT REFERENCES exercise_complexes(id),
    exercise_name VARCHAR(200) NOT NULL,

    sets INT NOT NULL CHECK (sets >= 1),
    reps INT NOT NULL CHECK (reps >= 1),
    intensity_pct NUMERIC(5,2)
        CHECK (intensity_pct IS NULL OR (intensity_pct > 0 AND intensity_pct <= 120)),
    intensity_reference VARCHAR(100),
    absolute_weight_kg NUMERIC(6,1),

    rpe_target NUMERIC(3,1),
    tempo VARCHAR(20),
    rest_seconds INT,
    backoff_sets INT DEFAULT 0,
    backoff_intensity_pct NUMERIC(5,2),
    is_max_attempt BOOLEAN DEFAULT FALSE,

    selection_rationale TEXT,
    source_principle_ids INT[],
    source_chunk_ids INT[],

    notes TEXT,

    UNIQUE(session_id, exercise_order)
);

CREATE INDEX idx_session_exercises_session ON session_exercises (session_id);
CREATE INDEX idx_session_exercises_exercise ON session_exercises (exercise_id);


-- ── Training Logs ───────────────────────────────────────────

CREATE TABLE training_logs (
    id SERIAL PRIMARY KEY,
    athlete_id INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    session_id INT REFERENCES program_sessions(id),
    log_date DATE NOT NULL,

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


-- ── Training Log Exercises ──────────────────────────────────

CREATE TABLE training_log_exercises (
    id SERIAL PRIMARY KEY,
    log_id INT NOT NULL REFERENCES training_logs(id) ON DELETE CASCADE,

    session_exercise_id INT REFERENCES session_exercises(id),

    exercise_id INT REFERENCES exercises(id),
    exercise_name VARCHAR(200) NOT NULL,
    sets_completed INT NOT NULL,
    reps_per_set INT[],
    weight_kg NUMERIC(6,1) NOT NULL,

    rpe NUMERIC(3,1),
    make_rate NUMERIC(3,2),
    technical_notes TEXT,
    video_url VARCHAR(500),

    prescribed_weight_kg NUMERIC(6,1),
    weight_deviation_kg NUMERIC(6,1),
    rpe_deviation NUMERIC(3,1),

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_log_exercises_log ON training_log_exercises (log_id);
CREATE INDEX idx_log_exercises_exercise ON training_log_exercises (exercise_id);
CREATE INDEX idx_log_exercises_prescribed ON training_log_exercises (session_exercise_id);


-- ── Generation Log ──────────────────────────────────────────

CREATE TABLE generation_log (
    id SERIAL PRIMARY KEY,
    program_id INT NOT NULL REFERENCES generated_programs(id) ON DELETE CASCADE,
    session_id INT REFERENCES program_sessions(id),

    week_number INT NOT NULL,
    day_number INT NOT NULL,
    attempt_number INT NOT NULL DEFAULT 1,

    model VARCHAR(100) NOT NULL,
    prompt_text TEXT NOT NULL,
    raw_response TEXT,
    parsed_response JSONB,

    input_tokens INT,
    output_tokens INT,
    estimated_cost_usd NUMERIC(8,4),

    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'success', 'parse_error', 'validation_error', 'failed')),
    validation_errors TEXT[],
    error_message TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_generation_log_program ON generation_log (program_id);
CREATE INDEX idx_generation_log_session ON generation_log (week_number, day_number);


-- ────────────────────────────────────────────────────────────
-- SEED DATA — Test Athlete
-- ────────────────────────────────────────────────────────────

INSERT INTO athletes
    (name, level, biological_sex, bodyweight_kg, age, weight_class,
     training_age_years, sessions_per_week, session_duration_minutes,
     available_equipment, injuries, technical_faults, notes)
VALUES (
    'David', 'intermediate', 'male', 89, NULL, '89',
    3.0, 4, 90,
    '{"barbell", "squat_rack", "blocks", "straps"}',
    '{}',
    '{"forward_balance_off_floor", "slow_turnover"}',
    'Training at 646 Weightlifting. Remote coaching with Paul Medeiros.'
);

-- Seed maxes (example — update with real numbers)
INSERT INTO athlete_maxes (athlete_id, exercise_id, weight_kg, max_type, date_achieved, notes)
SELECT
    1,  -- David (athlete_id = 1)
    e.id,
    m.weight_kg,
    'current',
    CURRENT_DATE,
    'Initial seed'
FROM (VALUES
    ('Snatch', 100.0),
    ('Clean & Jerk', 125.0),
    ('Back Squat', 160.0),
    ('Front Squat', 140.0),
    ('Snatch Pull', 120.0),
    ('Clean Pull', 150.0),
    ('Push Press', 95.0)
) AS m(exercise_name, weight_kg)
JOIN exercises e ON e.name = m.exercise_name;

-- Seed goal
INSERT INTO athlete_goals
    (athlete_id, goal, target_snatch_kg, target_cj_kg, priority, is_active, notes)
VALUES (
    1,
    'general_strength',
    105,
    130,
    1,
    TRUE,
    'Off-season base building. No competition target yet.'
);
