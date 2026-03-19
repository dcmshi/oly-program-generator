"""Baseline — full athlete/agent schema as of initial deployment.

Revision ID: 0001_baseline
Revises: 0000_ingestion_schema
Create Date: 2026-03-16

This migration captures the complete athlete/agent schema including all
columns added in later phases (username, password_hash, date_of_birth,
lift_emphasis, strength_limiters, competition_experience).

Ingestion tables are now managed by 0000_ingestion_schema (the new root).

----
EXISTING DATABASE: stamp both migrations to mark them as already applied:
    uv run alembic stamp 0002_athlete_cost_limit

FRESH DATABASE: run only:
    uv run alembic upgrade head
----
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = "0000_ingestion_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enum types ──────────────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE athlete_level AS ENUM ('beginner','intermediate','advanced','elite');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE biological_sex AS ENUM ('male','female');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE goal_type AS ENUM (
                'competition_prep','general_strength','technique_focus',
                'pr_attempt','return_to_sport','work_capacity'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE program_status AS ENUM (
                'draft','active','completed','abandoned','superseded'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)

    # ── Athletes ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS athletes (
            id                      SERIAL PRIMARY KEY,
            name                    VARCHAR(200) NOT NULL,
            username                VARCHAR(100) UNIQUE,
            password_hash           TEXT,
            email                   VARCHAR(300),
            level                   athlete_level NOT NULL,
            biological_sex          biological_sex,

            bodyweight_kg           NUMERIC(5,1),
            height_cm               NUMERIC(5,1),
            age                     INT,
            date_of_birth           DATE,
            weight_class            VARCHAR(20),

            training_age_years      NUMERIC(4,1),
            sessions_per_week       INT DEFAULT 4
                                        CHECK (sessions_per_week BETWEEN 1 AND 14),
            session_duration_minutes INT DEFAULT 90,
            available_equipment     TEXT[],

            injuries                TEXT[],
            technical_faults        TEXT[],
            exercise_preferences    JSONB DEFAULT '{}',

            lift_emphasis           VARCHAR(20) DEFAULT 'balanced',
            strength_limiters       TEXT[] DEFAULT '{}',
            competition_experience  VARCHAR(20) DEFAULT 'none',

            notes                   TEXT,
            created_at              TIMESTAMP DEFAULT NOW(),
            updated_at              TIMESTAMP DEFAULT NOW()
        )
    """)

    # ── Athlete Maxes ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS athlete_maxes (
            id                      SERIAL PRIMARY KEY,
            athlete_id              INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            exercise_id             INT NOT NULL REFERENCES exercises(id),

            weight_kg               NUMERIC(6,1) NOT NULL,
            is_competition_result   BOOLEAN DEFAULT FALSE,
            rpe                     NUMERIC(3,1),
            date_achieved           DATE NOT NULL,

            max_type                VARCHAR(20) NOT NULL DEFAULT 'current'
                                        CHECK (max_type IN ('current','historical','estimated')),
            notes                   TEXT,
            created_at              TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_maxes_unique_current
            ON athlete_maxes (athlete_id, exercise_id)
            WHERE max_type = 'current'
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_maxes_athlete ON athlete_maxes (athlete_id)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_maxes_current
            ON athlete_maxes (athlete_id, max_type)
            WHERE max_type = 'current'
    """)

    # ── Athlete Goals ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS athlete_goals (
            id                  SERIAL PRIMARY KEY,
            athlete_id          INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            goal                goal_type NOT NULL,

            competition_date    DATE,
            competition_name    VARCHAR(200),
            target_total_kg     NUMERIC(6,1),
            target_snatch_kg    NUMERIC(6,1),
            target_cj_kg        NUMERIC(6,1),
            target_faults       TEXT[],

            priority            INT DEFAULT 1 CHECK (priority BETWEEN 1 AND 5),
            is_active           BOOLEAN DEFAULT TRUE,
            notes               TEXT,
            created_at          TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_goals_athlete ON athlete_goals (athlete_id)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_goals_active
            ON athlete_goals (athlete_id, is_active)
            WHERE is_active = TRUE
    """)

    # ── Generated Programs ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS generated_programs (
            id                      SERIAL PRIMARY KEY,
            athlete_id              INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,

            name                    VARCHAR(300),
            status                  program_status NOT NULL DEFAULT 'draft',
            phase                   training_phase NOT NULL,
            duration_weeks          INT NOT NULL CHECK (duration_weeks BETWEEN 1 AND 16),
            sessions_per_week       INT NOT NULL,
            start_date              DATE,
            end_date                DATE,

            goal_id                 INT REFERENCES athlete_goals(id),

            athlete_snapshot        JSONB NOT NULL,
            maxes_snapshot          JSONB NOT NULL,
            generation_params       JSONB NOT NULL,
            knowledge_sources_used  JSONB,

            rationale               TEXT,
            outcome_summary         JSONB,

            created_at              TIMESTAMP DEFAULT NOW(),
            updated_at              TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_programs_athlete ON generated_programs (athlete_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_programs_status ON generated_programs (status)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_programs_active
            ON generated_programs (athlete_id, status)
            WHERE status = 'active'
    """)

    # ── Program Sessions ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS program_sessions (
            id                          SERIAL PRIMARY KEY,
            program_id                  INT NOT NULL REFERENCES generated_programs(id) ON DELETE CASCADE,
            week_number                 INT NOT NULL,
            day_number                  INT NOT NULL,
            session_label               VARCHAR(200),

            estimated_duration_minutes  INT,
            session_rpe_target          NUMERIC(3,1),
            focus_area                  VARCHAR(100),
            notes                       TEXT,

            UNIQUE (program_id, week_number, day_number)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_sessions_program ON program_sessions (program_id)")

    # ── Session Exercises ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS session_exercises (
            id                      SERIAL PRIMARY KEY,
            session_id              INT NOT NULL REFERENCES program_sessions(id) ON DELETE CASCADE,
            exercise_order          INT NOT NULL,

            exercise_id             INT REFERENCES exercises(id),
            complex_id              INT REFERENCES exercise_complexes(id),
            exercise_name           VARCHAR(200) NOT NULL,

            sets                    INT NOT NULL CHECK (sets >= 1),
            reps                    INT NOT NULL CHECK (reps >= 1),
            intensity_pct           NUMERIC(5,2)
                                        CHECK (intensity_pct IS NULL OR
                                               (intensity_pct > 0 AND intensity_pct <= 120)),
            intensity_reference     VARCHAR(100),
            absolute_weight_kg      NUMERIC(6,1),

            rpe_target              NUMERIC(3,1),
            tempo                   VARCHAR(20),
            rest_seconds            INT,
            backoff_sets            INT DEFAULT 0,
            backoff_intensity_pct   NUMERIC(5,2),
            is_max_attempt          BOOLEAN DEFAULT FALSE,

            selection_rationale     TEXT,
            source_principle_ids    INT[],
            source_chunk_ids        INT[],
            notes                   TEXT,

            UNIQUE (session_id, exercise_order)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_exercises_session ON session_exercises (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_exercises_exercise ON session_exercises (exercise_id)")

    # ── Training Logs ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS training_logs (
            id                          SERIAL PRIMARY KEY,
            athlete_id                  INT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            session_id                  INT REFERENCES program_sessions(id),
            log_date                    DATE NOT NULL,

            overall_rpe                 NUMERIC(3,1),
            session_duration_minutes    INT,
            bodyweight_kg               NUMERIC(5,1),
            sleep_quality               INT CHECK (sleep_quality IS NULL OR sleep_quality BETWEEN 1 AND 5),
            stress_level                INT CHECK (stress_level IS NULL OR stress_level BETWEEN 1 AND 5),
            athlete_notes               TEXT,

            created_at                  TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_logs_athlete ON training_logs (athlete_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_logs_date ON training_logs (athlete_id, log_date)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_logs_session ON training_logs (session_id)")

    # ── Training Log Exercises ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS training_log_exercises (
            id                      SERIAL PRIMARY KEY,
            log_id                  INT NOT NULL REFERENCES training_logs(id) ON DELETE CASCADE,

            session_exercise_id     INT REFERENCES session_exercises(id),
            exercise_id             INT REFERENCES exercises(id),
            exercise_name           VARCHAR(200) NOT NULL,
            sets_completed          INT NOT NULL,
            reps_per_set            INT[],
            weight_kg               NUMERIC(6,1) NOT NULL,

            rpe                     NUMERIC(3,1),
            make_rate               NUMERIC(3,2),
            technical_notes         TEXT,
            video_url               VARCHAR(500),

            prescribed_weight_kg    NUMERIC(6,1),
            weight_deviation_kg     NUMERIC(6,1),
            rpe_deviation           NUMERIC(3,1),

            created_at              TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_log_exercises_log ON training_log_exercises (log_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_log_exercises_exercise ON training_log_exercises (exercise_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_log_exercises_prescribed ON training_log_exercises (session_exercise_id)")

    # ── Generation Log ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS generation_log (
            id                  SERIAL PRIMARY KEY,
            program_id          INT NOT NULL REFERENCES generated_programs(id) ON DELETE CASCADE,
            session_id          INT REFERENCES program_sessions(id),

            week_number         INT NOT NULL,
            day_number          INT NOT NULL,
            attempt_number      INT NOT NULL DEFAULT 1,

            model               VARCHAR(100) NOT NULL,
            prompt_text         TEXT NOT NULL,
            raw_response        TEXT,
            parsed_response     JSONB,

            input_tokens        INT,
            output_tokens       INT,
            estimated_cost_usd  NUMERIC(8,4),

            status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                                    CHECK (status IN
                                        ('pending','success','parse_error',
                                         'validation_error','failed')),
            validation_errors   TEXT[],
            error_message       TEXT,

            created_at          TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_generation_log_program ON generation_log (program_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_generation_log_session ON generation_log (week_number, day_number)")


def downgrade() -> None:
    # Drop in reverse FK order
    op.execute("DROP TABLE IF EXISTS generation_log CASCADE")
    op.execute("DROP TABLE IF EXISTS training_log_exercises CASCADE")
    op.execute("DROP TABLE IF EXISTS training_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS session_exercises CASCADE")
    op.execute("DROP TABLE IF EXISTS program_sessions CASCADE")
    op.execute("DROP TABLE IF EXISTS generated_programs CASCADE")
    op.execute("DROP TABLE IF EXISTS athlete_goals CASCADE")
    op.execute("DROP TABLE IF EXISTS athlete_maxes CASCADE")
    op.execute("DROP TABLE IF EXISTS athletes CASCADE")
    op.execute("DROP TYPE IF EXISTS program_status")
    op.execute("DROP TYPE IF EXISTS goal_type")
    op.execute("DROP TYPE IF EXISTS biological_sex")
    op.execute("DROP TYPE IF EXISTS athlete_level")
