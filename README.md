# Olympic Weightlifting Program Generator

An AI-powered training program generator for Olympic weightlifting. Ingests coaching literature into a hybrid vector + structured database, then generates personalised mesocycle programs grounded in Prilepin's chart, extracted programming principles, and athlete-specific maxes and goals.

---

## Architecture

```mermaid
flowchart TB
    subgraph Sources["📚 Source Material"]
        PDF[PDF Books]
        EPUB[EPUB Books]
        WEB[Web Articles]
    end

    subgraph Ingestion["Ingestion Pipeline  oly-ingestion/"]
        EXT["Extractors\npdf / epub / html"]
        CLASS["Classifier\nheuristic + LLM fallback"]
        CHUNK["Chunker\nprofile-aware sizing"]
        PE["Principle Extractor\nClaude LLM"]
        VL["Vector Loader\nOpenAI text-embedding-3-small"]
        SL["Structured Loader\nupsert tables"]
    end

    subgraph DB["🗄  Postgres 16 + pgvector"]
        KC[("knowledge_chunks\n1,681 chunks · embeddings")]
        PP[("programming_principles\n79 extracted rules")]
        EX[("exercises · 50+\nsubstitutions · complexes")]
        PC[("prilepin_chart\n4 intensity zones")]
        AP[("athletes · maxes\ngoals · logs")]
        GP[("generated_programs")]
        PS[("program_sessions\nsession_exercises")]
        TL[("training_logs\nlog_exercises")]
    end

    subgraph SharedPkg["shared/"]
        CFG["config.py\nSettings dataclass"]
        DBSH["db.py\nconnection helpers"]
        LLM["llm.py\nAnthropic client"]
        PRI["prilepin.py\nzone lookup + rep targets"]
    end

    subgraph Agent["🤖 Programming Agent  oly-agent/"]
        ORCH["orchestrator.py"]
        ASSESS["1 · ASSESS\nassess.py"]
        PLAN["2 · PLAN\nplan.py"]
        RETRIEVE["3 · RETRIEVE\nretrieve.py"]
        GENERATE["4 · GENERATE\ngenerate.py"]
        VALIDATE["5 · VALIDATE\nvalidate.py"]
        EXPLAIN["6 · EXPLAIN\nexplain.py"]
    end

    subgraph UI["🌐 Web UI  oly-agent/web/"]
        AUTH["Login / Setup\nsession auth · bcrypt · multi-athlete"]
        DASH["Dashboard\ncurrent week · adherence · warnings · lift ratios"]
        PROG["Program view\nweek accordions · exercise tables · CSV export"]
        LOGUI["Log session\nprescribed vs actual · prefill · PR detection"]
        GEN["Generate\nbackground job · HTMX polling"]
        PROF["Profile / Settings\nedit athlete fields · change password · data export"]
        HIST["Exercise History\nper-exercise log · trend · back navigation"]
    end

    subgraph CLI["💻 CLI"]
        LOG["log.py\nshow · session · exercise\nstatus · history"]
        FEEDBACK["feedback.py\noutcome + max promotion"]
    end

    CLAUDE(["Claude claude-sonnet-4-6"])

    Sources --> EXT
    EXT --> CLASS
    CLASS -->|prose| CHUNK --> VL --> KC
    CLASS -->|if-then rules| PE --> PP
    CLASS -->|tables| SL --> EX & PC

    ORCH --> ASSESS & PLAN & RETRIEVE & GENERATE & VALIDATE & EXPLAIN
    ASSESS --> AP
    PLAN --> PC & PP
    RETRIEVE --> KC & EX & PP
    GENERATE <--> CLAUDE
    EXPLAIN <--> CLAUDE
    ORCH --> GP & PS

    UI --> DB
    UI -.->|triggers| ORCH
    CLI --> TL & PS
    FEEDBACK --> GP
```

---

## Project Structure

> Full database schema with ER diagrams: [SCHEMA.md](SCHEMA.md)

```
oly-program-generator/
├── README.md
├── CLAUDE.md                        # Claude Code project instructions
├── PROGRESS.md                      # Implementation progress tracker
├── schema.sql                       # Ingestion schema DDL (seed data included)
├── athlete_schema.sql               # Athlete / program schema DDL
├── SCHEMA.md                        # ER diagrams + table reference (20 tables)
├── oly-programming-pipeline.md      # Ingestion pipeline design doc
├── oly-programming-agent.md         # Agent design doc
│
├── shared/                          # Shared modules (imported by both subsystems)
│   ├── config.py                    # Unified Settings dataclass (reads .env)
│   ├── db.py                        # fetch_one / fetch_all / execute helpers
│   ├── llm.py                       # Anthropic client + cost estimation
│   └── prilepin.py                  # Zone lookup + per-session rep targets
│
├── oly-ingestion/                   # Ingestion pipeline
│   ├── pyproject.toml               # uv project (managed venv)
│   ├── docker-compose.yml           # Postgres + pgvector
│   ├── schema.sql                   # Copy of ../schema.sql (auto-applied by Docker)
│   ├── pipeline.py                  # EPUB / PDF ingestion orchestrator
│   ├── ingest_web.py                # Web article ingestion (Catalyst Athletics)
│   ├── extractors/
│   │   ├── pdf_extractor.py         # PyMuPDF + pdfplumber fallback
│   │   ├── epub_extractor.py        # ebooklib chapter-by-chapter
│   │   └── html_extractor.py        # BeautifulSoup body extraction
│   ├── processors/
│   │   ├── chunker.py               # Profile-aware chunking + topic tagging
│   │   ├── classifier.py            # Heuristic routing + LLM fallback
│   │   ├── principle_extractor.py   # LLM extraction of if-then rules
│   │   └── ocr_corrections.py       # Soviet-era OCR correction dict
│   ├── loaders/
│   │   ├── vector_loader.py         # Batch embed + dedup + similarity search
│   │   └── structured_loader.py     # Upsert sources / principles / exercises
│   └── tests/
│       ├── test_chunker.py          # 14 tests — no API keys needed
│       ├── test_classifier.py       # 10 tests (6 heuristic + 4 LLM)
│       ├── test_vector_loader.py    # 6 tests — needs live DB + OPENAI_API_KEY
│       ├── test_structured_loader.py # 7 tests — needs live DB
│       ├── test_principle_extractor.py # 6 tests — needs ANTHROPIC_API_KEY
│       ├── test_pipeline.py         # 4 e2e tests — needs both keys
│       └── test_retrieval_eval.py   # Retrieval quality eval
│
└── oly-agent/                       # Programming agent + web UI
    ├── pyproject.toml               # uv project (web, dev extras)
    ├── orchestrator.py              # Main pipeline runner (CLI entry point)
    ├── assess.py                    # Step 1: DB queries for athlete context
    ├── plan.py                      # Step 2: Phase selection + Prilepin targets
    ├── retrieve.py                  # Step 3: Fault exercises + vector search
    ├── generate.py                  # Step 4: Prompt builder + LLM call + retries
    ├── validate.py                  # Step 5: Prilepin / intensity / principle checks
    ├── explain.py                   # Step 6: Program-level rationale
    ├── models.py                    # Dataclasses (AthleteContext, ProgramPlan, …)
    ├── phase_profiles.py            # PHASE_PROFILES + build_weekly_targets()
    ├── session_templates.py         # SESSION_DISTRIBUTIONS + get_session_templates()
    ├── weight_resolver.py           # resolve_weights() + exercise ID lookup
    ├── feedback.py                  # ProgramOutcome computation + max promotion
    ├── log.py                       # Training log CLI
    ├── tests/                       # Unit tests (no DB/API needed)
    │   ├── test_validate.py         # 25 tests — all 6 validation checks
    │   ├── test_phase_profiles.py   # 15 tests — weekly target computation
    │   ├── test_weight_resolver.py  # 18 tests — weight resolution + ID lookup
    │   └── test_generate_utils.py   # 15 tests — JSON parsing + name validation
    └── web/                         # FastAPI web UI
        ├── app.py                   # Application factory + middleware + Jinja2 filters
        ├── auth.py                  # bcrypt helpers + get_current_athlete_id dependency
        ├── deps.py                  # get_db (pooled) + slowapi limiter + settings singleton
        ├── jobs.py                  # Background thread queue for generation
        ├── routers/
        │   ├── auth.py              # GET/POST /login, POST /logout
        │   ├── setup.py             # GET/POST /setup (account creation wizard)
        │   ├── profile.py           # GET /profile, POST /profile/update|password|username
        │   ├── dashboard.py         # GET / (dashboard + lift ratios)
        │   ├── program.py           # GET/POST /program (list, detail, activate, complete, abandon, maxes)
        │   ├── log_session.py       # GET/POST /log/{session_id}, POST/DELETE /log/{log_id}/exercise/{tle_id}
        │   ├── generate.py          # GET /generate, POST /generate/run, GET /generate/status/{id}
        │   ├── export.py            # GET /export/log.csv, GET /export/program/{id}.csv
        │   └── history.py           # GET /history?exercise=... (per-exercise training history)
        ├── queries/
        │   ├── dashboard.py         # active program, week sessions, adherence, warnings, lift_ratios
        │   ├── program.py           # program list/detail, maxes upsert (PR detection)
        │   ├── setup.py             # username_taken, create_athlete/maxes/goal
        │   ├── profile.py           # get_athlete, update_profile/password/username
        │   ├── log_session.py       # session log create/update, exercise log CRUD, max promotion
        │   ├── export.py            # get_program_for_export, get_full_training_log
        │   └── history.py           # get_exercise_history, compute_history_summary
        └── templates/               # Jinja2 templates + HTMX partials
```

---

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `pip install uv`
- Docker Desktop (for Postgres + pgvector)
- API keys: `OPENAI_API_KEY` (embeddings) and `ANTHROPIC_API_KEY` (LLM)

---

## Quick Start

### 1. Start the database

```bash
cd oly-ingestion
docker compose up -d
```

### 2. Install dependencies

```bash
cd oly-ingestion
uv sync --extra dev

cd ../oly-agent
uv sync --extra web --extra dev
```

### 3. Configure API keys

Create `oly-ingestion/.env`:
```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://oly:oly@localhost:5432/oly_programming
```

### 4. Ingest source material

```bash
cd oly-ingestion

# EPUB / PDF book
PYTHONUTF8=1 uv run python pipeline.py \
  --source "./sources/book.epub" --title "Title" --author "Author" --type book

# Catalyst Athletics web articles
PYTHONUTF8=1 uv run python ingest_web.py
```

### 5. Start the web UI

```bash
cd oly-agent
PYTHONUTF8=1 uv run uvicorn web.app:app --reload --port 8080
```

Open `http://localhost:8080`. Create an account at `/setup` or log in at `/login`. The UI provides:
- **Dashboard** — current week's sessions, logged/unlogged status, adherence, warnings, current maxes, lift ratio analysis panel
- **Programs** — all programs with phase/status badges; week accordions with full exercise tables; activate / complete / abandon; CSV export
- **Log session** — two-phase form: session RPE/details, then exercise-by-exercise with click-to-prefill from prescribed weights; inline add/edit/delete; PR banner on new max
- **Exercise history** — per-exercise training log with trend indicator; accessible from any logged exercise name link
- **Generate** — triggers the agent in a background thread and polls for completion via HTMX
- **Profile** — edit all athlete fields (bodyweight, lift emphasis, strength limiters, competition experience, etc.); change password/username; full training log CSV export

### 6. Generate a program (CLI alternative)

```bash
cd oly-agent
PYTHONUTF8=1 uv run python orchestrator.py --athlete-id 1 --dry-run  # ASSESS + PLAN only
PYTHONUTF8=1 uv run python orchestrator.py --athlete-id 1             # full generation
```

### 7. Log training sessions (CLI alternative)

```bash
cd oly-agent
PYTHONUTF8=1 uv run python log.py show    --athlete-id 1   # view current week
PYTHONUTF8=1 uv run python log.py session --athlete-id 1   # log a session (interactive)
PYTHONUTF8=1 uv run python log.py status  --athlete-id 1   # RPE / make-rate warnings
PYTHONUTF8=1 uv run python log.py history --athlete-id 1   # recent session history
```

### 8. Run agent tests

```bash
cd oly-agent
PYTHONUTF8=1 uv run python tests/test_validate.py        # 25 tests
PYTHONUTF8=1 uv run python tests/test_phase_profiles.py  # 15 tests
PYTHONUTF8=1 uv run python tests/test_weight_resolver.py # 18 tests
PYTHONUTF8=1 uv run python tests/test_generate_utils.py  # 15 tests
```

> **Windows note:** Always prefix commands with `PYTHONUTF8=1` to avoid cp1252 encoding errors.

---

## Web UI

The agent ships with a browser interface built on **FastAPI + HTMX + Jinja2** — no npm, no build step.

| Page | URL | Description |
|------|-----|-------------|
| Login | `/login` | Username + bcrypt password auth; session cookie via `SessionMiddleware` |
| Create account | `/setup` | Multi-section wizard: account, profile, training config, current maxes, goal |
| Dashboard | `/` | Current week's sessions with logged/unlogged status, adherence bar, active warnings, current maxes, lift ratio analysis panel |
| Programs | `/program` | All generated programs with phase and status badges |
| Program detail | `/program/{id}` | Week accordions with full exercise tables (weight, intensity, RPE, rest), rationale, activate / complete / abandon; CSV export button |
| Log session | `/log/{session_id}` | Session header form + per-exercise logging with click-to-prefill; inline add/edit/delete; PR banner on new personal best |
| Exercise history | `/history?exercise=` | Full per-exercise training history: sets/reps/weight/RPE/deviation per session, trend indicator, back-to-session navigation |
| Generate | `/generate` | Triggers the 6-step agent pipeline in a background thread; polls every 3 s via HTMX until complete |
| Profile | `/profile` | Edit athlete fields (name, bodyweight, lift emphasis, strength limiters, competition experience, etc.); change password; change username; training log CSV download |
| Export log | `/export/log.csv` | Download full training log as CSV (streamed, one row per exercise entry) |
| Export program | `/export/program/{id}.csv` | Download a specific program as CSV with metadata header block + exercise rows |

**Stack:** FastAPI · Jinja2 templates · HTMX (no page reloads) · Tailwind CSS via CDN · Google Fonts (Barlow Condensed + DM Sans) · slowapi rate limiting · `ThreadedConnectionPool`

---

## Knowledge Corpus

| Source | Format | Chunks | Principles |
|--------|--------|--------|------------|
| Everett — *Olympic Weightlifting* | EPUB | 198 | 44 |
| Zatsiorsky — *Science and Practice of Strength Training* | PDF | 430 | 7 |
| Drechsler — *Weightlifting Encyclopedia* | PDF | 603 | 6 |
| Catalyst Athletics articles | Web (HTML) | 446 | 22 |
| Laputin — *Managing the Training of Weightlifters* | PDF (vision OCR) | 110 | 3 |
| Medvedev — *A Program of Multi-Year Training in Weightlifting* | PDF (vision OCR) | 617 | 0 |
| Everett — *Olympic Weightlifting for Sports* | PDF | 172 | 0 |
| **Total** | | **2,576** | **82** |

---

## Agent Pipeline

Each program generation runs 6 steps:

| Step | Module | What it does |
|------|--------|-------------|
| 1 · ASSESS | `assess.py` | Load athlete profile, maxes, goals, recent training history |
| 2 · PLAN | `plan.py` | Select phase + duration; build weekly intensity/volume targets |
| 3 · RETRIEVE | `retrieve.py` | Fetch fault-targeted exercises, program templates, relevant knowledge chunks |
| 4 · GENERATE | `generate.py` | One LLM call per session; retries on parse/validation failures |
| 5 · VALIDATE | `validate.py` | Prilepin per-session volume, intensity envelope, reps/set, avoid list |
| 6 · EXPLAIN | `explain.py` | One LLM call for program-level rationale |

A 4-week, 4-session/week program = 16 sessions × ~1–2 LLM calls + 1 explain call ≈ **$0.40–0.50** at current Claude pricing.

---

## Programming Model

- **Prilepin's chart** enforces per-session rep targets per intensity zone. Hard cap at 1.5× range ceiling to account for multiple snatch variations all referencing the same max.
- **Phase profiles**: accumulation (4 wk), intensification (4 wk), realization (3 wk), general prep (5 wk). Intensity and volume adjust automatically for beginner / intermediate / elite.
- **Cold-start defaults**: no prior program → intensity capped at 80%, max 4 weeks, max exercise complexity 3.
- **Warmup sets**: 2–3 sets at 50–60% prescribed before every competition lift or heavy pull.
- **Cost limit**: `$1.00` per program by default (configurable in `Settings`).
