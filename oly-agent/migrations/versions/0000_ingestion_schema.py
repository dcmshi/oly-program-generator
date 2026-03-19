"""Ingestion schema — full schema.sql content folded into Alembic.

Revision ID: 0000_ingestion_schema
Revises:
Create Date: 2026-03-19

Captures the complete ingestion pipeline schema: pgvector extension, all enum
types, 11 ingestion tables (sources, exercises, prilepin_chart, etc.), indexes
(including the HNSW vector index), and all seed data (Prilepin chart, exercise
taxonomy, exercise substitutions, exercise complexes).

This migration is the new root of the chain:
    0000_ingestion_schema → 0001_baseline → 0002_athlete_cost_limit

EXISTING DATABASE (schema.sql was applied via Docker init):
    Your alembic_version already shows 0002_athlete_cost_limit, which is still
    the head. No action needed — alembic upgrade head is a no-op for you.

FRESH DATABASE:
    docker compose up -d
    cd oly-agent && uv run alembic upgrade head
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0000_ingestion_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extension ────────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── Enum types ───────────────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE source_type AS ENUM (
                'book', 'article', 'website', 'video', 'research_paper', 'manual'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE movement_applicability AS ENUM (
                'competition_lifts', 'squats', 'pulls', 'all'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE exercise_category AS ENUM (
                'competition', 'competition_variant', 'strength',
                'pull', 'accessory', 'positional', 'complex'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE movement_family AS ENUM (
                'snatch', 'clean', 'jerk',
                'squat', 'pull', 'press',
                'hinge', 'row', 'carry',
                'core', 'plyometric'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE start_position AS ENUM (
                'floor', 'hang_above_knee', 'hang_at_knee', 'hang_below_knee',
                'blocks_above_knee', 'blocks_at_knee', 'blocks_below_knee',
                'behind_neck', 'rack'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE training_phase AS ENUM (
                'general_prep', 'accumulation', 'transmutation', 'intensification',
                'realization', 'competition', 'deload', 'transition'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE principle_category AS ENUM (
                'volume', 'intensity', 'frequency', 'exercise_selection',
                'periodization', 'peaking', 'recovery', 'technique',
                'load_progression', 'deload'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE rule_type AS ENUM (
                'hard_constraint', 'guideline', 'heuristic'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE chunk_type AS ENUM (
                'concept', 'methodology', 'periodization', 'programming_rationale',
                'biomechanics', 'case_study', 'fault_correction',
                'recovery_adaptation', 'competition_strategy', 'nutrition_bodyweight'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE ingestion_status AS ENUM (
                'started', 'extracting', 'classifying', 'processing',
                'loading', 'completed', 'failed', 'partial'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)

    # ── Tables ───────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id                  SERIAL PRIMARY KEY,
            title               VARCHAR(300) NOT NULL,
            author              VARCHAR(200),
            source_type         source_type NOT NULL,
            publisher           VARCHAR(200),
            publication_year    INT,
            isbn                VARCHAR(20),
            url                 VARCHAR(500),
            credibility_score   INT DEFAULT 5 CHECK (credibility_score BETWEEN 1 AND 10),
            notes               TEXT,
            created_at          TIMESTAMP DEFAULT NOW(),
            UNIQUE(title, author)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS prilepin_chart (
            id                      SERIAL PRIMARY KEY,
            intensity_range_low     NUMERIC(5,2) NOT NULL
                                        CHECK (intensity_range_low >= 0 AND intensity_range_low <= 100),
            intensity_range_high    NUMERIC(5,2) NOT NULL
                                        CHECK (intensity_range_high > intensity_range_low
                                               AND intensity_range_high <= 100),
            reps_per_set_low        INT NOT NULL CHECK (reps_per_set_low >= 1),
            reps_per_set_high       INT NOT NULL CHECK (reps_per_set_high >= reps_per_set_low),
            optimal_total_reps      INT NOT NULL,
            total_reps_range_low    INT NOT NULL,
            total_reps_range_high   INT NOT NULL
                                        CHECK (total_reps_range_high >= total_reps_range_low),
            movement_type           movement_applicability NOT NULL DEFAULT 'competition_lifts',
            notes                   TEXT
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS exercises (
            id                      SERIAL PRIMARY KEY,
            name                    VARCHAR(150) NOT NULL UNIQUE,
            category                exercise_category NOT NULL,
            movement_family         movement_family NOT NULL,

            start_position          start_position,
            is_power                BOOLEAN DEFAULT FALSE,
            is_muscle               BOOLEAN DEFAULT FALSE,
            has_pause               BOOLEAN DEFAULT FALSE,
            pause_position          VARCHAR(100),
            tempo_prescription      VARCHAR(50),
            is_no_feet              BOOLEAN DEFAULT FALSE,
            is_no_hook              BOOLEAN DEFAULT FALSE,

            parent_exercise_id      INT REFERENCES exercises(id),

            complexity_level        INT DEFAULT 1 CHECK (complexity_level BETWEEN 1 AND 5),
            primary_purpose         TEXT,
            secondary_purposes      TEXT[],

            faults_addressed        TEXT[],

            typical_sets_low        INT,
            typical_sets_high       INT,
            typical_reps_low        INT,
            typical_reps_high       INT,
            typical_intensity_low   NUMERIC(5,2),
            typical_intensity_high  NUMERIC(5,2),
            typical_rest_seconds    INT,

            equipment_required      TEXT[],
            cues                    TEXT[],
            video_reference_url     VARCHAR(500),
            source_id               INT REFERENCES sources(id),

            created_at              TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS exercise_substitutions (
            id                      SERIAL PRIMARY KEY,
            exercise_id             INT NOT NULL REFERENCES exercises(id),
            substitute_exercise_id  INT NOT NULL REFERENCES exercises(id),
            substitution_context    VARCHAR(100) NOT NULL,
            preserves_stimulus      TEXT,
            notes                   TEXT,
            CHECK (exercise_id != substitute_exercise_id),
            UNIQUE(exercise_id, substitute_exercise_id, substitution_context)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS exercise_complexes (
            id                      SERIAL PRIMARY KEY,
            name                    VARCHAR(200) NOT NULL,
            exercises_ordered        JSONB NOT NULL,
            total_reps_per_set      INT NOT NULL,
            primary_purpose         TEXT,
            typical_intensity_low   NUMERIC(5,2),
            typical_intensity_high  NUMERIC(5,2),
            intensity_reference     VARCHAR(100),
            source_id               INT REFERENCES sources(id),
            notes                   TEXT
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS percentage_schemes (
            id                      SERIAL PRIMARY KEY,
            scheme_name             VARCHAR(200) NOT NULL,
            source_id               INT REFERENCES sources(id),
            phase                   training_phase NOT NULL,
            block_number            INT,
            week_number             INT NOT NULL,
            day_number              INT NOT NULL,
            exercise_order          INT DEFAULT 1,
            exercise_id             INT REFERENCES exercises(id),
            movement_family         movement_family,
            sets                    INT NOT NULL CHECK (sets >= 1),
            reps                    INT NOT NULL CHECK (reps >= 1),
            intensity_pct           NUMERIC(5,2) NOT NULL
                                        CHECK (intensity_pct > 0 AND intensity_pct <= 120),
            intensity_reference     VARCHAR(100) DEFAULT 'competition_lift',
            rpe_target              NUMERIC(3,1)
                                        CHECK (rpe_target IS NULL OR
                                               (rpe_target >= 5.0 AND rpe_target <= 10.0)),
            tempo                   VARCHAR(20),
            rest_seconds            INT CHECK (rest_seconds IS NULL OR rest_seconds >= 0),
            backoff_sets            INT DEFAULT 0,
            backoff_intensity_pct   NUMERIC(5,2),
            max_attempts            BOOLEAN DEFAULT FALSE,
            notes                   TEXT,
            UNIQUE(scheme_name, week_number, day_number, exercise_order)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS programming_principles (
            id              SERIAL PRIMARY KEY,
            principle_name  VARCHAR(300) NOT NULL,
            source_id       INT REFERENCES sources(id),
            category        principle_category NOT NULL,
            rule_type       rule_type NOT NULL,
            condition       JSONB NOT NULL DEFAULT '{}',
            recommendation  JSONB NOT NULL DEFAULT '{}',
            rationale       TEXT,
            priority        INT DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
            conflicts_with  INT[],
            created_at      TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS program_templates (
            id                      SERIAL PRIMARY KEY,
            name                    VARCHAR(300) NOT NULL,
            source_id               INT REFERENCES sources(id),
            athlete_level           VARCHAR(50) NOT NULL
                                        CHECK (athlete_level IN
                                            ('beginner', 'intermediate', 'advanced', 'elite', 'any')),
            goal                    VARCHAR(100) NOT NULL
                                        CHECK (goal IN (
                                            'general_strength', 'competition_prep', 'technique_focus',
                                            'hypertrophy', 'work_capacity', 'peaking', 'return_to_sport'
                                        )),
            duration_weeks          INT NOT NULL CHECK (duration_weeks >= 1),
            sessions_per_week       INT NOT NULL CHECK (sessions_per_week BETWEEN 1 AND 14),
            phases_included         training_phase[],
            periodization_model     VARCHAR(100),
            program_structure       JSONB NOT NULL,
            expected_outcomes       JSONB,
            notes                   TEXT,
            tags                    TEXT[],
            created_at              TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id                              SERIAL PRIMARY KEY,
            content                         TEXT NOT NULL,
            raw_content                     TEXT NOT NULL,
            embedding                       vector(1536),
            source_id                       INT REFERENCES sources(id),
            chapter                         VARCHAR(300),
            section                         VARCHAR(300),
            page_range                      VARCHAR(50),
            chunk_type                      chunk_type NOT NULL,
            topics                          TEXT[] NOT NULL DEFAULT '{}',
            athlete_level_relevance         VARCHAR(50)
                                                CHECK (athlete_level_relevance IS NULL OR
                                                       athlete_level_relevance IN
                                                       ('beginner','intermediate','advanced','elite','all')),
            information_density             VARCHAR(20) DEFAULT 'medium'
                                                CHECK (information_density IN ('low','medium','high')),
            contains_specific_numbers       BOOLEAN DEFAULT FALSE,
            content_hash                    VARCHAR(64) NOT NULL UNIQUE,
            created_at                      TIMESTAMP DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            id                      SERIAL PRIMARY KEY,
            source_id               INT NOT NULL REFERENCES sources(id),
            status                  ingestion_status NOT NULL DEFAULT 'started',
            file_path               VARCHAR(500) NOT NULL,
            file_hash               VARCHAR(64),
            total_pages             INT,
            pages_processed         INT DEFAULT 0,
            chunks_created          INT DEFAULT 0,
            chunks_skipped_dedup    INT DEFAULT 0,
            chunks_quarantined      INT DEFAULT 0,
            principles_extracted    INT DEFAULT 0,
            programs_parsed         INT DEFAULT 0,
            exercises_created       INT DEFAULT 0,
            tables_parsed           INT DEFAULT 0,
            last_processed_page     INT DEFAULT 0,
            checkpoint_data         JSONB,
            started_at              TIMESTAMP DEFAULT NOW(),
            completed_at            TIMESTAMP,
            duration_seconds        NUMERIC(10,2),
            error_message           TEXT,
            error_details           JSONB,
            config_snapshot         JSONB
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_chunk_log (
            id                  SERIAL PRIMARY KEY,
            ingestion_run_id    INT NOT NULL REFERENCES ingestion_runs(id) ON DELETE CASCADE,
            chunk_id            INT NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
            page_number         INT,
            section_title       VARCHAR(300),
            classification      VARCHAR(50),
            created_at          TIMESTAMP DEFAULT NOW()
        )
    """)

    # ── Indexes ──────────────────────────────────────────────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS idx_exercises_family ON exercises (movement_family)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_exercises_category ON exercises (category)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_exercises_faults ON exercises USING GIN (faults_addressed)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_exercises_parent ON exercises (parent_exercise_id)")

    op.execute("CREATE INDEX IF NOT EXISTS idx_schemes_phase ON percentage_schemes (phase)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_schemes_week ON percentage_schemes (scheme_name, week_number)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_schemes_exercise ON percentage_schemes (exercise_id)")

    op.execute("CREATE INDEX IF NOT EXISTS idx_principles_category ON programming_principles (category)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_principles_rule_type ON programming_principles (rule_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_principles_condition ON programming_principles USING GIN (condition)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_principles_priority ON programming_principles (priority DESC)")

    op.execute("CREATE INDEX IF NOT EXISTS idx_templates_level ON program_templates (athlete_level)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_templates_goal ON program_templates (goal)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_templates_tags ON program_templates USING GIN (tags)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_templates_phases ON program_templates USING GIN (phases_included)")

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON knowledge_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_type ON knowledge_chunks (chunk_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_topics ON knowledge_chunks USING GIN (topics)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON knowledge_chunks (source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_level ON knowledge_chunks (athlete_level_relevance)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunks_hash ON knowledge_chunks (content_hash)")

    op.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source ON ingestion_runs (source_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_runs_status ON ingestion_runs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_chunk_log_run ON ingestion_chunk_log (ingestion_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_chunk_log_chunk ON ingestion_chunk_log (chunk_id)")

    # ── Seed data ────────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO prilepin_chart
            (intensity_range_low, intensity_range_high, reps_per_set_low, reps_per_set_high,
             optimal_total_reps, total_reps_range_low, total_reps_range_high, movement_type, notes)
        VALUES
            (55, 65, 3, 6, 24, 18, 30, 'competition_lifts', 'Technique / speed work zone'),
            (70, 80, 3, 6, 18, 12, 24, 'competition_lifts', 'Primary working zone for volume accumulation'),
            (80, 90, 2, 4, 15, 10, 20, 'competition_lifts', 'Intensification zone'),
            (90, 100, 1, 2,  7,  4, 10, 'competition_lifts', 'Realization / peaking zone')
    """)

    op.execute("""
        INSERT INTO sources (title, author, source_type, publication_year, credibility_score)
        VALUES
            ('Olympic Weightlifting: A Complete Guide for Athletes and Coaches',
             'Greg Everett', 'book', 2009, 9),
            ('Weightlifting Programming: A Winning Coach''s Guide',
             'Bob Takano', 'book', 2012, 8),
            ('Managing the Training of Weightlifters',
             'Nikolai Laputin, Valentin Oleshko', 'book', 1982, 8),
            ('Science and Practice of Strength Training',
             'Vladimir Zatsiorsky', 'book', 1995, 9),
            ('A System of Multi-Year Training in Weightlifting',
             'Alexei Medvedev', 'book', 1986, 8),
            ('Exercise Taxonomy', 'Manual', 'manual', 2025, 10)
        ON CONFLICT (title, author) DO NOTHING
    """)

    # Snatch family
    op.execute("""
        INSERT INTO exercises
            (name, category, movement_family, start_position, is_power, is_muscle, has_pause,
             pause_position, is_no_feet, complexity_level, primary_purpose, secondary_purposes,
             faults_addressed, typical_sets_low, typical_sets_high, typical_reps_low, typical_reps_high,
             typical_intensity_low, typical_intensity_high, typical_rest_seconds,
             equipment_required, cues, source_id)
        VALUES
            ('Snatch', 'competition', 'snatch', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Competition lift — full expression of snatch technique and strength', NULL,
             '{}', 3, 8, 1, 3, 70, 100, 120,
             '{"barbell"}', '{"drive through the floor","elbows high and outside","fast turnover","punch the ceiling"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Power Snatch', 'competition_variant', 'snatch', 'floor', TRUE, FALSE, FALSE,
             NULL, FALSE, 2, 'Pulling height and turnover speed — must receive above parallel',
             '{"speed development","lighter training day option"}',
             '{"slow_turnover","bar_crashing","not_finishing_pull"}',
             3, 6, 1, 3, 65, 85, 90,
             '{"barbell"}', '{"finish the pull","fast elbows","punch up not press out"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Power Snatch from Blocks (knee)', 'competition_variant', 'snatch', 'blocks_at_knee', TRUE, FALSE, FALSE,
             NULL, FALSE, 2, 'Explosive extension from mid-thigh without first pull fatigue',
             '{"extension timing practice"}',
             '{"not_finishing_pull","slow_turnover"}',
             3, 5, 1, 3, 60, 80, 90,
             '{"barbell","blocks"}', '{"patient off blocks","drive through","fast feet"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Hang Snatch (above knee)', 'competition_variant', 'snatch', 'hang_above_knee', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Extension timing and hip contact — eliminates first pull variables',
             '{"positional awareness","timing development"}',
             '{"forward_balance_off_floor","hips_rising_fast","no_hip_contact"}',
             3, 5, 2, 3, 65, 82, 90,
             '{"barbell"}', '{"load the hamstrings","sweep back","patient through the middle"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Hang Snatch (below knee)', 'competition_variant', 'snatch', 'hang_below_knee', FALSE, FALSE, FALSE,
             NULL, FALSE, 3, 'Full pull mechanics from a static position — tests positioning under load',
             '{"back strength","positional discipline"}',
             '{"lost_back_tightness","hips_rising_fast","forward_balance_off_floor"}',
             3, 5, 1, 3, 60, 78, 90,
             '{"barbell"}', '{"chest up","knuckles down","push through the floor"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Hang Power Snatch (above knee)', 'competition_variant', 'snatch', 'hang_above_knee', TRUE, FALSE, FALSE,
             NULL, FALSE, 2, 'Speed and aggression through the middle with abbreviated receiving position',
             '{"warm-up movement","speed work"}',
             '{"slow_turnover","not_finishing_pull","passive_hip_extension"}',
             3, 5, 2, 3, 55, 75, 60,
             '{"barbell"}', '{"be violent","snap the hips","fast turnover"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Muscle Snatch', 'accessory', 'snatch', 'floor', FALSE, TRUE, FALSE,
             NULL, FALSE, 2, 'Turnover strength and bar path awareness — no rebend of knees in receiving',
             '{"warm-up","turnover patterning"}',
             '{"slow_turnover","early_arm_bend","bar_crashing"}',
             3, 4, 3, 5, 40, 60, 60,
             '{"barbell"}', '{"elbows high and outside","keep pulling","smooth turnover"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Muscle Snatch from Hang', 'accessory', 'snatch', 'hang_above_knee', FALSE, TRUE, FALSE,
             NULL, FALSE, 2, 'Isolated turnover practice from shortened pull',
             '{"warm-up","turnover patterning"}',
             '{"slow_turnover","early_arm_bend"}',
             3, 4, 3, 5, 35, 55, 60,
             '{"barbell"}', '{"keep the bar close","high elbows","no press out"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Pause Snatch (at knee)', 'positional', 'snatch', 'floor', FALSE, FALSE, TRUE,
             'knee', FALSE, 3, 'Reinforce position over the knee — exposes balance and back strength issues',
             '{"positional strength","tempo development"}',
             '{"hips_rising_fast","forward_balance_off_floor","lost_back_tightness"}',
             3, 5, 1, 3, 60, 78, 90,
             '{"barbell"}', '{"cover the bar","chest over","patient off the floor"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Pause Snatch (2 inches off floor)', 'positional', 'snatch', 'floor', FALSE, FALSE, TRUE,
             '2 inches off floor', FALSE, 3, 'First pull discipline — exposes weak positions off the floor',
             '{"starting position strength","patience"}',
             '{"hips_rising_fast","forward_balance_off_floor","chest_dropping"}',
             3, 5, 1, 3, 58, 75, 90,
             '{"barbell"}', '{"push the floor away","chest up","stay over the bar"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('No Feet Snatch', 'positional', 'snatch', 'floor', FALSE, FALSE, FALSE,
             NULL, TRUE, 3, 'Footwork discipline — forces vertical extension without lateral foot movement',
             '{"balance refinement","extension mechanics"}',
             '{"jumping_forward","jumping_backward","inconsistent_foot_placement"}',
             3, 5, 1, 3, 60, 80, 90,
             '{"barbell"}', '{"drive straight up","flat feet","pull yourself under"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Snatch Balance', 'accessory', 'snatch', 'behind_neck', FALSE, FALSE, FALSE,
             NULL, FALSE, 3, 'Overhead receiving position confidence, speed, and positional strength',
             '{"overhead stability","speed under"}',
             '{"soft_receiving_position","missed_lockout","fear_of_overhead_position"}',
             3, 5, 1, 3, 60, 95, 90,
             '{"barbell"}', '{"fast feet","punch and hold","active shoulders"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Pressing Snatch Balance', 'accessory', 'snatch', 'behind_neck', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Slow-tempo overhead position familiarization — builds confidence in the bottom',
             '{"overhead mobility","position strength"}',
             '{"soft_receiving_position","missed_lockout","fear_of_overhead_position"}',
             3, 4, 3, 5, 40, 60, 60,
             '{"barbell"}', '{"press and sit simultaneously","stay tight","active shoulders"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Overhead Squat', 'accessory', 'snatch', 'behind_neck', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Overhead position strength and mobility in full depth',
             '{"mobility development","core stability"}',
             '{"soft_receiving_position","knee_cave_in_recovery","lost_overhead_position"}',
             3, 5, 2, 5, 50, 80, 90,
             '{"barbell"}', '{"push up into the bar","elbows locked","knees out"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Snatch Pull', 'pull', 'snatch', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'First and second pull strength — reinforces positions and timing without turnover',
             '{"back strength","pull mechanics"}',
             '{"hips_rising_fast","early_arm_bend","lost_back_tightness"}',
             3, 5, 2, 5, 85, 110, 90,
             '{"barbell","straps"}', '{"same positions as the snatch","drive through the whole foot","finish tall"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Snatch Pull from Deficit', 'pull', 'snatch', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 3, 'Extended range of motion in the first pull — builds strength off the floor',
             '{"first pull strength","back strength"}',
             '{"hips_rising_fast","weak_off_floor","chest_dropping"}',
             3, 4, 2, 4, 80, 100, 90,
             '{"barbell","straps","deficit_platform"}', '{"stay over the bar longer","patient","push the floor"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Halting Snatch Deadlift (at knee)', 'pull', 'snatch', 'floor', FALSE, FALSE, TRUE,
             'knee', FALSE, 2, 'Positional strength and back engagement in the first pull to knee',
             '{"first pull strength","position reinforcement"}',
             '{"hips_rising_fast","lost_back_tightness","forward_balance_off_floor"}',
             3, 4, 3, 5, 80, 100, 90,
             '{"barbell","straps"}', '{"identical position to the snatch","cover the bar","hold 2-3 seconds"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Snatch High Pull', 'pull', 'snatch', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Full extension with arm follow-through — bridges the gap between pull and turnover',
             '{"pull height","aggression"}',
             '{"not_finishing_pull","passive_hip_extension","early_arm_bend"}',
             3, 5, 2, 4, 75, 95, 90,
             '{"barbell"}', '{"elbows high and outside","finish the pull first","violent extension"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Snatch Deadlift', 'strength', 'snatch', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'Snatch-grip pulling strength and position reinforcement with heavier loads',
             '{"general back strength","grip strength"}',
             '{"lost_back_tightness","weak_off_floor"}',
             3, 5, 3, 5, 90, 115, 120,
             '{"barbell","straps"}', '{"same back angle as snatch","knuckles down","control the descent"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Snatch Push Press', 'accessory', 'snatch', 'behind_neck', FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'Overhead strength in the snatch receiving position',
             '{"lockout strength","overhead stability"}',
             '{"missed_lockout","soft_receiving_position"}',
             3, 4, 3, 5, 50, 75, 90,
             '{"barbell"}', '{"big dip","press through","lock and hold"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy'))
        ON CONFLICT (name) DO NOTHING
    """)

    # Clean family
    op.execute("""
        INSERT INTO exercises
            (name, category, movement_family, start_position, is_power, is_muscle, has_pause,
             pause_position, is_no_feet, complexity_level, primary_purpose, secondary_purposes,
             faults_addressed, typical_sets_low, typical_sets_high, typical_reps_low, typical_reps_high,
             typical_intensity_low, typical_intensity_high, typical_rest_seconds,
             equipment_required, cues, source_id)
        VALUES
            ('Clean', 'competition', 'clean', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Competition lift — full expression of clean technique and strength', NULL,
             '{}', 3, 8, 1, 3, 70, 100, 120,
             '{"barbell"}', '{"drive through the floor","elbows fast","meet the bar in the squat"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Power Clean', 'competition_variant', 'clean', 'floor', TRUE, FALSE, FALSE,
             NULL, FALSE, 2, 'Pulling height and turnover speed — receive above parallel',
             '{"speed development","lighter training day option"}',
             '{"slow_turnover","bar_crashing","not_finishing_pull"}',
             3, 6, 1, 3, 65, 85, 90,
             '{"barbell"}', '{"finish the pull","elbows through fast","strong rack"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Hang Clean (above knee)', 'competition_variant', 'clean', 'hang_above_knee', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Extension timing — eliminates first pull variables',
             '{"positional awareness","timing development"}',
             '{"forward_balance_off_floor","hips_rising_fast","no_hip_contact"}',
             3, 5, 1, 3, 65, 85, 90,
             '{"barbell"}', '{"load the hamstrings","sweep to the hip","fast elbows"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Hang Clean (below knee)', 'competition_variant', 'clean', 'hang_below_knee', FALSE, FALSE, FALSE,
             NULL, FALSE, 3, 'Full pull mechanics from static hang — positional discipline under load',
             '{"back strength","patience off the floor"}',
             '{"lost_back_tightness","hips_rising_fast","forward_balance_off_floor"}',
             3, 5, 1, 3, 60, 80, 90,
             '{"barbell"}', '{"chest up","push through the floor","stay over"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Hang Power Clean (above knee)', 'competition_variant', 'clean', 'hang_above_knee', TRUE, FALSE, FALSE,
             NULL, FALSE, 2, 'Speed and aggression from the hip with abbreviated catch',
             '{"warm-up","speed work"}',
             '{"slow_turnover","not_finishing_pull","passive_hip_extension"}',
             3, 5, 2, 3, 55, 78, 60,
             '{"barbell"}', '{"be aggressive","snap through","elbows fast"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Muscle Clean', 'accessory', 'clean', 'floor', FALSE, TRUE, FALSE,
             NULL, FALSE, 2, 'Turnover strength and rack position practice — no rebend',
             '{"warm-up","rack position strength"}',
             '{"slow_turnover","loose_rack_position","early_arm_bend"}',
             3, 4, 3, 5, 40, 60, 60,
             '{"barbell"}', '{"elbows high and around","keep pulling","fast rack"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Pause Clean (at knee)', 'positional', 'clean', 'floor', FALSE, FALSE, TRUE,
             'knee', FALSE, 3, 'Reinforce position over the knee under load',
             '{"positional strength","first pull discipline"}',
             '{"hips_rising_fast","forward_balance_off_floor","lost_back_tightness"}',
             3, 5, 1, 3, 60, 80, 90,
             '{"barbell"}', '{"cover the bar","chest over","3 second pause"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Clean Pull', 'pull', 'clean', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'Pull strength and position reinforcement without the turnover',
             '{"back strength","pull mechanics"}',
             '{"hips_rising_fast","early_arm_bend","lost_back_tightness"}',
             3, 5, 2, 5, 85, 110, 90,
             '{"barbell","straps"}', '{"same positions as the clean","finish tall","shrug at the top"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Clean High Pull', 'pull', 'clean', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Full extension with arm follow-through',
             '{"pull height","aggression"}',
             '{"not_finishing_pull","passive_hip_extension"}',
             3, 5, 2, 4, 75, 95, 90,
             '{"barbell"}', '{"elbows high","finish first","violent extension"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Halting Clean Deadlift (at knee)', 'pull', 'clean', 'floor', FALSE, FALSE, TRUE,
             'knee', FALSE, 2, 'First pull strength and back engagement to knee height',
             '{"first pull strength","position reinforcement"}',
             '{"hips_rising_fast","lost_back_tightness"}',
             3, 4, 3, 5, 80, 105, 90,
             '{"barbell","straps"}', '{"identical position to the clean","hold 2-3 seconds"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Clean Deadlift', 'strength', 'clean', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'Clean-grip pulling strength with heavier loads',
             '{"general back strength","grip strength"}',
             '{"lost_back_tightness","weak_off_floor"}',
             3, 5, 3, 5, 90, 120, 120,
             '{"barbell","straps"}', '{"same back angle as clean","control the descent"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy'))
        ON CONFLICT (name) DO NOTHING
    """)

    # Jerk family
    op.execute("""
        INSERT INTO exercises
            (name, category, movement_family, start_position, is_power, is_muscle, has_pause,
             pause_position, is_no_feet, complexity_level, primary_purpose, secondary_purposes,
             faults_addressed, typical_sets_low, typical_sets_high, typical_reps_low, typical_reps_high,
             typical_intensity_low, typical_intensity_high, typical_rest_seconds,
             equipment_required, cues, source_id)
        VALUES
            ('Jerk', 'competition', 'jerk', 'rack', FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Competition lift — drive from the rack to overhead in a split or squat receiving',
             NULL,
             '{}', 3, 6, 1, 3, 70, 100, 120,
             '{"barbell"}', '{"straight dip","drive through the bar","fast split","punch and hold"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Power Jerk', 'competition_variant', 'jerk', 'rack', TRUE, FALSE, FALSE,
             NULL, FALSE, 2, 'Overhead drive strength — receive in partial squat, no split',
             '{"drive power","overhead strength"}',
             '{"short_dip_drive","forward_drive","soft_lockout"}',
             3, 5, 1, 3, 65, 90, 90,
             '{"barbell"}', '{"long dip","straight up","aggressive lockout"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Push Jerk', 'competition_variant', 'jerk', 'rack', TRUE, FALSE, FALSE,
             NULL, FALSE, 2, 'Overhead drive with active push-under — transitions between push press and split jerk',
             '{"drive timing","push-under aggressiveness"}',
             '{"passive_receiving","pressing_out","forward_drive"}',
             3, 5, 1, 3, 60, 85, 90,
             '{"barbell"}', '{"dip and drive","push yourself under","lock hard"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Jerk from Behind Neck', 'competition_variant', 'jerk', 'behind_neck', FALSE, FALSE, FALSE,
             NULL, FALSE, 3, 'Overhead position and drive without front rack limitations',
             '{"drive mechanics","overhead position"}',
             '{"forward_drive","soft_lockout","dip_forward"}',
             3, 5, 1, 3, 60, 90, 90,
             '{"barbell"}', '{"straight dip","drive to lock","flat feet in dip"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Pause Jerk (in dip)', 'positional', 'jerk', 'rack', FALSE, FALSE, TRUE,
             'dip', FALSE, 3, 'Dip position strength and discipline — eliminates momentum cheating',
             '{"dip position awareness","leg drive"}',
             '{"dip_forward","short_dip_drive","uneven_dip"}',
             3, 5, 1, 3, 55, 82, 90,
             '{"barbell"}', '{"2-3 second pause","straight dip","flat feet","drive from dead stop"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Jerk Recovery', 'accessory', 'jerk', 'rack', FALSE, FALSE, FALSE,
             NULL, FALSE, 3, 'Overhead positional strength in the split — build confidence with heavy loads overhead',
             '{"split position strength","overhead stability"}',
             '{"soft_lockout","unstable_split","fear_of_heavy_overhead"}',
             3, 5, 1, 2, 90, 110, 120,
             '{"barbell","jerk_blocks"}', '{"lock out first","step up strong","hold 3 seconds"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Push Press', 'accessory', 'jerk', 'rack', FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'Overhead pressing strength with leg drive — builds jerk drive power',
             '{"general overhead strength","dip-drive practice"}',
             '{"weak_drive","pressing_out","soft_lockout"}',
             3, 5, 3, 5, 60, 85, 90,
             '{"barbell"}', '{"big dip","drive hard","lock out"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy'))
        ON CONFLICT (name) DO NOTHING
    """)

    # Squat family + Clean & Jerk
    op.execute("""
        INSERT INTO exercises
            (name, category, movement_family, start_position, is_power, is_muscle, has_pause,
             pause_position, is_no_feet, complexity_level, primary_purpose, secondary_purposes,
             faults_addressed, typical_sets_low, typical_sets_high, typical_reps_low, typical_reps_high,
             typical_intensity_low, typical_intensity_high, typical_rest_seconds,
             equipment_required, cues, source_id)
        VALUES
            ('Back Squat', 'strength', 'squat', NULL, FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'Primary lower body strength — foundation for all pulling and receiving positions',
             '{"general strength","hypertrophy"}',
             '{"weak_legs","knee_cave_in_recovery"}',
             3, 6, 1, 8, 65, 100, 120,
             '{"barbell","squat_rack"}', '{"chest up","knees out","drive through the floor","full depth"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Front Squat', 'strength', 'squat', 'rack', FALSE, FALSE, FALSE,
             NULL, FALSE, 1, 'Clean receiving position strength — mirrors bottom of the clean',
             '{"clean recovery strength","core strength"}',
             '{"soft_receiving_position","knee_cave_in_recovery","collapsed_torso_in_clean"}',
             3, 6, 1, 5, 65, 100, 120,
             '{"barbell","squat_rack"}', '{"elbows up","chest up","sit between the legs","drive out of the hole"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Pause Back Squat (2s in hole)', 'strength', 'squat', NULL, FALSE, FALSE, TRUE,
             'bottom', FALSE, 2, 'Positional strength and control in the deepest receiving position',
             '{"bottom position strength","reactive strength"}',
             '{"soft_receiving_position","knee_cave_in_recovery","bouncing_out_of_bottom"}',
             3, 5, 2, 5, 65, 85, 120,
             '{"barbell","squat_rack"}', '{"2 second pause","stay tight","knees out","controlled descent"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Pause Front Squat (2s in hole)', 'strength', 'squat', 'rack', FALSE, FALSE, TRUE,
             'bottom', FALSE, 2, 'Clean recovery strength from a dead stop — no bounce',
             '{"bottom position strength","core stability"}',
             '{"collapsed_torso_in_clean","soft_receiving_position"}',
             3, 5, 2, 5, 60, 82, 120,
             '{"barbell","squat_rack"}', '{"elbows up the whole time","2 second pause","drive up with the chest"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Tempo Back Squat (3-1-X-0)', 'strength', 'squat', NULL, FALSE, FALSE, FALSE,
             NULL, FALSE, 2, 'Eccentric control and positional awareness under time-based constraints',
             '{"muscle development","control"}',
             '{"bouncing_out_of_bottom","losing_position_under_fatigue"}',
             3, 5, 3, 6, 60, 78, 120,
             '{"barbell","squat_rack"}', '{"3 seconds down","pause at bottom","explode up"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy')),

            ('Clean & Jerk', 'competition', 'clean', 'floor', FALSE, FALSE, FALSE,
             NULL, FALSE, 3, 'Competition lift — full clean followed immediately by jerk',
             NULL,
             '{}', 2, 6, 1, 2, 70, 100, 150,
             '{"barbell"}', '{"smooth clean","reset for the jerk","confident dip and drive"}',
             (SELECT id FROM sources WHERE title = 'Exercise Taxonomy'))
        ON CONFLICT (name) DO NOTHING
    """)

    # Exercise complexes
    op.execute("""
        INSERT INTO exercise_complexes
            (name, exercises_ordered, total_reps_per_set, primary_purpose,
             typical_intensity_low, typical_intensity_high, intensity_reference, notes)
        VALUES
            ('Clean + Front Squat + Jerk',
             '[{"exercise_name": "Clean", "reps": 1}, {"exercise_name": "Front Squat", "reps": 1}, {"exercise_name": "Jerk", "reps": 1}]',
             3, 'Full competition movement pattern with squat reinforcement between clean and jerk',
             65, 85, 'clean_and_jerk', 'Classic complex. Intensity limited by jerk.'),

            ('Snatch + Overhead Squat',
             '[{"exercise_name": "Snatch", "reps": 1}, {"exercise_name": "Overhead Squat", "reps": 2}]',
             3, 'Receiving position reinforcement — extra time overhead under load',
             60, 80, 'snatch', 'Good for athletes who need overhead confidence.'),

            ('Clean Pull + Clean',
             '[{"exercise_name": "Clean Pull", "reps": 1}, {"exercise_name": "Clean", "reps": 1}]',
             2, 'Position reinforcement — pull primes the correct positions for the clean',
             65, 82, 'clean', 'Pull should mirror clean positions exactly.'),

            ('Snatch Pull + Hang Snatch (above knee)',
             '[{"exercise_name": "Snatch Pull", "reps": 1}, {"exercise_name": "Hang Snatch (above knee)", "reps": 1}]',
             2, 'Pull pattern followed by hang lift to reinforce positions through the middle',
             60, 78, 'snatch', 'Great for athletes who lose position above the knee.'),

            ('Power Clean + Push Jerk',
             '[{"exercise_name": "Power Clean", "reps": 1}, {"exercise_name": "Push Jerk", "reps": 1}]',
             2, 'Speed and power variant of the clean & jerk for lighter days',
             55, 75, 'clean_and_jerk', 'Good for deload weeks or technique days.'),

            ('Hang Snatch (below knee) + Hang Snatch (above knee) + Snatch',
             '[{"exercise_name": "Hang Snatch (below knee)", "reps": 1}, {"exercise_name": "Hang Snatch (above knee)", "reps": 1}, {"exercise_name": "Snatch", "reps": 1}]',
             3, 'Segmented snatch complex — builds each position sequentially',
             55, 72, 'snatch', 'Excellent for technique development. Do not rush between reps.')
    """)

    # Parent exercise hierarchy
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Snatch')
        WHERE name IN ('Power Snatch', 'Hang Snatch (above knee)', 'Hang Snatch (below knee)',
                       'Pause Snatch (at knee)', 'Pause Snatch (2 inches off floor)',
                       'No Feet Snatch', 'Muscle Snatch')
          AND parent_exercise_id IS NULL
    """)
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Power Snatch')
        WHERE name IN ('Hang Power Snatch (above knee)', 'Power Snatch from Blocks (knee)')
          AND parent_exercise_id IS NULL
    """)
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Muscle Snatch')
        WHERE name = 'Muscle Snatch from Hang' AND parent_exercise_id IS NULL
    """)
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Clean')
        WHERE name IN ('Power Clean', 'Hang Clean (above knee)', 'Hang Clean (below knee)',
                       'Pause Clean (at knee)', 'Muscle Clean', 'Clean & Jerk')
          AND parent_exercise_id IS NULL
    """)
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Power Clean')
        WHERE name = 'Hang Power Clean (above knee)' AND parent_exercise_id IS NULL
    """)
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Jerk')
        WHERE name IN ('Power Jerk', 'Push Jerk', 'Jerk from Behind Neck',
                       'Pause Jerk (in dip)', 'Jerk Recovery')
          AND parent_exercise_id IS NULL
    """)
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Back Squat')
        WHERE name IN ('Pause Back Squat (2s in hole)', 'Tempo Back Squat (3-1-X-0)')
          AND parent_exercise_id IS NULL
    """)
    op.execute("""
        UPDATE exercises SET parent_exercise_id = (SELECT id FROM exercises WHERE name = 'Front Squat')
        WHERE name = 'Pause Front Squat (2s in hole)' AND parent_exercise_id IS NULL
    """)

    # Exercise substitutions
    op.execute("""
        INSERT INTO exercise_substitutions
            (exercise_id, substitute_exercise_id, substitution_context, preserves_stimulus, notes)
        SELECT e1.id, e2.id, sub.context, sub.preserves, sub.notes
        FROM (VALUES
            ('Snatch', 'Hang Snatch (above knee)', 'injury_modification',
             'Extension and turnover without first pull loading on the lower back',
             'Use when athlete has lower back soreness from pulling off the floor'),
            ('Clean', 'Hang Clean (above knee)', 'injury_modification',
             'Extension and turnover without first pull loading',
             'Use when athlete has lower back soreness from pulling off the floor'),
            ('Back Squat', 'Front Squat', 'injury_modification',
             'Leg strength with less spinal compression',
             'Front squat loads ~15-20% less than back squat, adjust percentages accordingly'),
            ('Snatch', 'Power Snatch', 'beginner_regression',
             'Full snatch pattern without requiring full depth overhead squat',
             'Transition to full snatch as overhead squat mobility improves'),
            ('Clean', 'Power Clean', 'beginner_regression',
             'Clean pattern without requiring full depth front squat catch',
             'Transition to full clean as front rack mobility and squat depth improve'),
            ('Snatch Balance', 'Pressing Snatch Balance', 'beginner_regression',
             'Overhead position work with slower, more controlled movement',
             'Progress to snatch balance once athlete is confident overhead'),
            ('Power Snatch from Blocks (knee)', 'Hang Snatch (above knee)', 'equipment_limitation',
             'Similar start position and extension mechanics',
             'Hang requires more isometric hold but similar pulling mechanics'),
            ('Snatch', 'Power Snatch', 'fatigue_management',
             'Snatch pattern with less overall systemic demand',
             'Power variants for deload weeks or high-frequency programs'),
            ('Clean', 'Power Clean', 'fatigue_management',
             'Clean pattern with less overall systemic demand',
             'Power variants for deload weeks or high-frequency programs'),
            ('Back Squat', 'Pause Back Squat (2s in hole)', 'fatigue_management',
             'Leg strength with less absolute load due to pause demand',
             'Pause squats at 70% provide similar stimulus to regular squats at 80-85%')
        ) AS sub(e1_name, e2_name, context, preserves, notes)
        JOIN exercises e1 ON e1.name = sub.e1_name
        JOIN exercises e2 ON e2.name = sub.e2_name
        WHERE NOT EXISTS (
            SELECT 1 FROM exercise_substitutions es
            WHERE es.exercise_id = e1.id
              AND es.substitute_exercise_id = e2.id
              AND es.substitution_context = sub.context
        )
    """)


def downgrade() -> None:
    # Drop in reverse FK order
    op.execute("DROP TABLE IF EXISTS ingestion_chunk_log CASCADE")
    op.execute("DROP TABLE IF EXISTS ingestion_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks CASCADE")
    op.execute("DROP TABLE IF EXISTS program_templates CASCADE")
    op.execute("DROP TABLE IF EXISTS programming_principles CASCADE")
    op.execute("DROP TABLE IF EXISTS percentage_schemes CASCADE")
    op.execute("DROP TABLE IF EXISTS exercise_complexes CASCADE")
    op.execute("DROP TABLE IF EXISTS exercise_substitutions CASCADE")
    op.execute("DROP TABLE IF EXISTS exercises CASCADE")
    op.execute("DROP TABLE IF EXISTS prilepin_chart CASCADE")
    op.execute("DROP TABLE IF EXISTS sources CASCADE")
    op.execute("DROP TYPE IF EXISTS ingestion_status")
    op.execute("DROP TYPE IF EXISTS chunk_type")
    op.execute("DROP TYPE IF EXISTS rule_type")
    op.execute("DROP TYPE IF EXISTS principle_category")
    op.execute("DROP TYPE IF EXISTS training_phase")
    op.execute("DROP TYPE IF EXISTS start_position")
    op.execute("DROP TYPE IF EXISTS movement_family")
    op.execute("DROP TYPE IF EXISTS exercise_category")
    op.execute("DROP TYPE IF EXISTS movement_applicability")
    op.execute("DROP TYPE IF EXISTS source_type")
    op.execute("DROP EXTENSION IF EXISTS vector")
