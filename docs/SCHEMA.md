# Database Schema Documentation

Postgres 16 + pgvector. **20 tables total** across two schema files.

| Schema file | Tables | Purpose |
|-------------|--------|---------|
| `schema.sql` | 11 | Ingestion pipeline — knowledge base, exercises, Prilepin chart |
| `athlete_schema.sql` | 9 | Programming agent — athletes, programs, sessions, logs |

Connection: `postgresql://oly:oly@localhost:5432/oly_programming`

---

## Ingestion Schema

Populated by `oly-ingestion/`. Read by `oly-agent/retrieve.py`.

```mermaid
erDiagram
    sources {
        int id PK
        varchar title
        varchar author
        source_type source_type
        int credibility_score
        timestamp created_at
    }

    prilepin_chart {
        int id PK
        numeric intensity_range_low
        numeric intensity_range_high
        int reps_per_set_low
        int reps_per_set_high
        int optimal_total_reps
        int total_reps_range_low
        int total_reps_range_high
        movement_applicability movement_type
    }

    exercises {
        int id PK
        varchar name
        exercise_category category
        movement_family movement_family
        start_position start_position
        bool is_power
        bool is_muscle
        bool has_pause
        int complexity_level
        text primary_purpose
        text[] faults_addressed
        int parent_exercise_id FK
        int source_id FK
    }

    exercise_substitutions {
        int id PK
        int exercise_id FK
        int substitute_exercise_id FK
        varchar substitution_context
        text preserves_stimulus
    }

    exercise_complexes {
        int id PK
        varchar name
        jsonb exercises_ordered
        int total_reps_per_set
        text primary_purpose
        int source_id FK
    }

    percentage_schemes {
        int id PK
        varchar scheme_name
        training_phase phase
        int week_number
        int day_number
        int exercise_id FK
        int sets
        int reps
        numeric intensity_pct
        int source_id FK
    }

    programming_principles {
        int id PK
        varchar principle_name
        principle_category category
        rule_type rule_type
        jsonb condition
        jsonb recommendation
        int priority
        int source_id FK
    }

    program_templates {
        int id PK
        varchar name
        varchar athlete_level
        varchar goal
        int duration_weeks
        int sessions_per_week
        jsonb program_structure
        int source_id FK
    }

    knowledge_chunks {
        int id PK
        text content
        text raw_content
        vector embedding
        chunk_type chunk_type
        text[] topics
        varchar content_hash
        int source_id FK
    }

    ingestion_runs {
        int id PK
        int source_id FK
        ingestion_status status
        varchar file_path
        int chunks_created
        int principles_extracted
        timestamp started_at
        timestamp completed_at
    }

    ingestion_chunk_log {
        int id PK
        int ingestion_run_id FK
        int chunk_id FK
        int page_number
        varchar section_title
    }

    sources ||--o{ exercises : "source_id"
    sources ||--o{ exercise_complexes : "source_id"
    sources ||--o{ percentage_schemes : "source_id"
    sources ||--o{ programming_principles : "source_id"
    sources ||--o{ program_templates : "source_id"
    sources ||--o{ knowledge_chunks : "source_id"
    sources ||--o{ ingestion_runs : "source_id"

    exercises ||--o{ exercises : "parent_exercise_id"
    exercises ||--o{ exercise_substitutions : "exercise_id"
    exercises ||--o{ exercise_substitutions : "substitute_exercise_id"
    exercises ||--o{ percentage_schemes : "exercise_id"

    ingestion_runs ||--o{ ingestion_chunk_log : "ingestion_run_id"
    knowledge_chunks ||--o{ ingestion_chunk_log : "chunk_id"
```

### Ingestion Table Reference

| Table | Rows | Description |
|-------|------|-------------|
| `sources` | 436 | Source books and articles. Seed: 6 canonical texts. |
| `prilepin_chart` | 5 | Prilepin's intensity zones (55–65, 65–70, 70–80, 80–90, 90–100%). Structured lookup only — not in vector store. |
| `exercises` | 50+ | Full exercise taxonomy: competition lifts, variants, pulls, strength, accessory. Self-referencing hierarchy via `parent_exercise_id`. |
| `exercise_substitutions` | 10+ | Injury/equipment/fatigue substitution pairs with context. |
| `exercise_complexes` | 6 | Named multi-exercise complexes with ordered JSONB structure. |
| `percentage_schemes` | varies | Extracted percentage programs from source books (week/day/sets/reps/intensity). |
| `programming_principles` | 82 | LLM-extracted if/then rules from prose. JSONB `condition` + `recommendation` fields. |
| `program_templates` | varies | LLM-parsed program structures from books. |
| `knowledge_chunks` | 2,576 | Prose chunks with `vector(1536)` embeddings (text-embedding-3-small). HNSW index for cosine similarity search. SHA-256 dedup via `content_hash`. |
| `ingestion_runs` | per run | Pipeline execution record per source. Tracks progress, timing, and error state. |
| `ingestion_chunk_log` | per chunk | Links chunks to the ingestion run that created them. Enables rollback. |

---

## Agent Schema

Populated and queried by `oly-agent/`.

```mermaid
erDiagram
    athletes {
        int id PK
        varchar name
        athlete_level level
        numeric bodyweight_kg
        int sessions_per_week
        int session_duration_minutes
        text[] technical_faults
        text[] injuries
        timestamp created_at
    }

    athlete_maxes {
        int id PK
        int athlete_id FK
        int exercise_id FK
        numeric weight_kg
        varchar max_type
        bool is_competition_result
        date date_achieved
    }

    athlete_goals {
        int id PK
        int athlete_id FK
        goal_type goal
        date competition_date
        numeric target_snatch_kg
        numeric target_cj_kg
        bool is_active
    }

    generated_programs {
        int id PK
        int athlete_id FK
        int goal_id FK
        program_status status
        training_phase phase
        int duration_weeks
        int sessions_per_week
        jsonb athlete_snapshot
        jsonb maxes_snapshot
        jsonb generation_params
        text rationale
        jsonb outcome_summary
        timestamp created_at
    }

    program_sessions {
        int id PK
        int program_id FK
        int week_number
        int day_number
        varchar session_label
        int estimated_duration_minutes
    }

    session_exercises {
        int id PK
        int session_id FK
        int exercise_id FK
        int complex_id FK
        int exercise_order
        int sets
        int reps
        numeric intensity_pct
        numeric absolute_weight_kg
        int[] source_chunk_ids
        int[] source_principle_ids
    }

    training_logs {
        int id PK
        int athlete_id FK
        int session_id FK
        date log_date
        numeric overall_rpe
        int session_duration_minutes
    }

    training_log_exercises {
        int id PK
        int log_id FK
        int session_exercise_id FK
        int exercise_id FK
        varchar exercise_name
        numeric weight_kg
        numeric rpe
        numeric make_rate
        numeric prescribed_weight_kg
        numeric rpe_deviation
    }

    generation_log {
        int id PK
        int program_id FK
        int session_id FK
        int week_number
        int day_number
        int attempt_number
        varchar model
        int input_tokens
        int output_tokens
        numeric estimated_cost_usd
        varchar status
        text[] validation_errors
    }

    athletes ||--o{ athlete_maxes : "athlete_id"
    athletes ||--o{ athlete_goals : "athlete_id"
    athletes ||--o{ generated_programs : "athlete_id"
    athletes ||--o{ training_logs : "athlete_id"

    athlete_goals ||--o{ generated_programs : "goal_id"

    generated_programs ||--o{ program_sessions : "program_id"
    generated_programs ||--o{ generation_log : "program_id"

    program_sessions ||--o{ session_exercises : "session_id"
    program_sessions ||--o{ training_logs : "session_id"
    program_sessions ||--o{ generation_log : "session_id"

    training_logs ||--o{ training_log_exercises : "log_id"
    session_exercises ||--o{ training_log_exercises : "session_exercise_id"
```

### Agent Table Reference

| Table | Description |
|-------|-------------|
| `athletes` | Athlete profile. Technical faults and injuries drive exercise selection and substitutions. |
| `athlete_maxes` | One `current` max per athlete per exercise (partial unique index). Historical and estimated maxes also stored. |
| `athlete_goals` | Active goal drives phase selection in PLAN step. Stores competition date, target totals, and faults to address. |
| `generated_programs` | Mesocycle output. Snapshots athlete state at generation time. `outcome_summary` JSONB populated after program completion via `feedback.py`. |
| `program_sessions` | One row per training day in the program (week × day). |
| `session_exercises` | Individual exercise prescriptions within a session. `source_chunk_ids` and `source_principle_ids` trace which retrieved knowledge informed each exercise. |
| `training_logs` | Athlete's actual session record. Links to `program_sessions` for adherence tracking. |
| `training_log_exercises` | Actual sets/reps/weight logged. `make_rate`, `rpe`, and `weight_deviation_kg` drive feedback loop in `feedback.py`. |
| `generation_log` | LLM call audit trail. Token counts, cost, retry attempts, and validation errors per session. |

---

## Cross-Schema Foreign Keys

`athlete_maxes.exercise_id` → `exercises.id` (ingestion schema)
`session_exercises.exercise_id` → `exercises.id` (ingestion schema)
`session_exercises.complex_id` → `exercise_complexes.id` (ingestion schema)
`training_log_exercises.exercise_id` → `exercises.id` (ingestion schema)

---

## Enum Types

**Ingestion schema (`schema.sql`):**

| Enum | Values |
|------|--------|
| `source_type` | `book`, `article`, `website`, `video`, `research_paper`, `manual` |
| `exercise_category` | `competition`, `competition_variant`, `strength`, `pull`, `accessory`, `positional`, `complex` |
| `movement_family` | `snatch`, `clean`, `jerk`, `squat`, `pull`, `press`, `hinge`, `row`, `carry`, `core`, `plyometric` |
| `start_position` | `floor`, `hang_above_knee`, `hang_at_knee`, `hang_below_knee`, `blocks_above_knee`, `blocks_at_knee`, `blocks_below_knee`, `behind_neck`, `rack` |
| `training_phase` | `general_prep`, `accumulation`, `transmutation`, `intensification`, `realization`, `competition`, `deload`, `transition` |
| `chunk_type` | `concept`, `methodology`, `periodization`, `programming_rationale`, `biomechanics`, `case_study`, `fault_correction`, `recovery_adaptation`, `competition_strategy`, `nutrition_bodyweight` |
| `principle_category` | `volume`, `intensity`, `frequency`, `exercise_selection`, `periodization`, `peaking`, `recovery`, `technique`, `load_progression`, `deload` |
| `rule_type` | `hard_constraint`, `guideline`, `heuristic` |
| `ingestion_status` | `started`, `extracting`, `classifying`, `processing`, `loading`, `completed`, `failed`, `partial` |
| `movement_applicability` | `competition_lifts`, `squats`, `pulls`, `all` |

**Agent schema (`athlete_schema.sql`):**

| Enum | Values |
|------|--------|
| `athlete_level` | `beginner`, `intermediate`, `advanced`, `elite` |
| `biological_sex` | `male`, `female` |
| `goal_type` | `competition_prep`, `general_strength`, `technique_focus`, `pr_attempt`, `return_to_sport`, `work_capacity` |
| `program_status` | `draft`, `active`, `completed`, `abandoned`, `superseded` |

---

## Key Indexes

| Table | Index | Type | Purpose |
|-------|-------|------|---------|
| `knowledge_chunks` | `idx_chunks_embedding` | HNSW (cosine) | Vector similarity search |
| `knowledge_chunks` | `idx_chunks_topics` | GIN | Topic filtering in retrieval |
| `knowledge_chunks` | `idx_chunks_hash` | btree | SHA-256 dedup on re-ingestion |
| `exercises` | `idx_exercises_faults` | GIN | Fault-to-exercise lookup |
| `programming_principles` | `idx_principles_condition` | GIN | JSONB condition filtering |
| `program_templates` | `idx_templates_tags` | GIN | Tag-based template search |
| `athlete_maxes` | `idx_maxes_unique_current` | unique partial | One current max per athlete per exercise |
| `athlete_goals` | `idx_goals_active` | partial | Active goal lookup |
| `generated_programs` | `idx_programs_active` | partial | Active program lookup per athlete |
