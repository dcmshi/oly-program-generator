# Olympic Weightlifting Program Generator

An AI-powered training program generator for Olympic weightlifting. Ingests coaching literature into a hybrid vector + structured database, then generates personalised mesocycle programs grounded in Prilepin's chart, extracted programming principles, and athlete-specific maxes and goals.

> Full service and deployment architecture: [ARCHITECTURE.md](ARCHITECTURE.md)

---

## Demo

> **Add a short walkthrough here** — a GIF or screen recording (~60–90 s) showing: account setup → dashboard → program generation → session logging → exercise history.
>
> Tools: [LICEcap](https://www.cockos.com/licecap/) (Windows/macOS) or [Peek](https://github.com/phw/peek) (Linux) for GIF; [Loom](https://www.loom.com) or [OBS](https://obsproject.com) for video. Export at 800–1000 px wide and commit as `screenshots/demo.gif` or link to a YouTube/Loom URL.

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
        EXT["Extractors<br/>pdf / epub / html"]
        CLASS["Classifier<br/>heuristic + LLM fallback"]
        CHUNK["Chunker<br/>profile-aware sizing"]
        PE["Principle Extractor<br/>Claude LLM"]
        VL["Vector Loader<br/>OpenAI text-embedding-3-small"]
        SL["Structured Loader<br/>upsert tables"]
    end

    subgraph DB["🗄  Postgres 16 + pgvector"]
        KC[("knowledge_chunks<br/>3,796 chunks · embeddings")]
        PP[("programming_principles<br/>167 extracted rules")]
        EX[("exercises · 50+<br/>substitutions · complexes")]
        PC[("prilepin_chart<br/>4 intensity zones")]
        AP[("athletes · maxes<br/>goals · logs")]
        GP[("generated_programs")]
        PS[("program_sessions<br/>session_exercises")]
        TL[("training_logs<br/>log_exercises")]
    end

    subgraph SharedPkg["shared/"]
        CFG["config.py<br/>Settings dataclass"]
        DBSH["db.py<br/>connection helpers"]
        LLM["llm.py<br/>Anthropic client"]
        PRI["prilepin.py<br/>zone lookup + rep targets"]
    end

    subgraph Agent["🤖 Programming Agent  oly-agent/"]
        ORCH["orchestrator.py"]
        ASSESS["1 · ASSESS<br/>assess.py"]
        PLAN["2 · PLAN<br/>plan.py"]
        RETRIEVE["3 · RETRIEVE<br/>retrieve.py"]
        GENERATE["4 · GENERATE<br/>generate.py"]
        VALIDATE["5 · VALIDATE<br/>validate.py"]
        EXPLAIN["6 · EXPLAIN<br/>explain.py"]
    end

    subgraph UI["🌐 Web UI  oly-agent/web/"]
        AUTH["Login / Setup<br/>session auth · bcrypt · multi-athlete"]
        DASH["Dashboard<br/>current week · adherence · warnings · lift ratios"]
        PROG["Program view<br/>week accordions · exercise tables · CSV export"]
        LOGUI["Log session<br/>prescribed vs actual · prefill · PR detection"]
        GEN["Generate<br/>background job · HTMX polling"]
        PROF["Profile / Settings<br/>edit athlete fields · change password · data export"]
        HIST["Exercise History<br/>per-exercise log · trend · back navigation"]
    end

    subgraph CLI["💻 CLI"]
        LOG["log.py<br/>show · session · exercise<br/>status · history"]
        FEEDBACK["feedback.py<br/>outcome + max promotion"]
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

> _If the diagram above doesn't render, see the static PNG: [docs/arch-main.png](docs/arch-main.png)_

---

## Screenshots

**Login**
![Login](screenshots/01-login.png)

**Dashboard** — current week sessions, goal progress, current maxes, lift ratio analysis
![Dashboard](screenshots/02-dashboard.png)

**Program detail** — week accordions, exercise tables with weights / intensity / RPE
![Program detail](screenshots/04-program-detail.png)

**Session logging** — prescribed exercises prefilled, log actual sets/reps/weight/RPE
![Session log](screenshots/05-session-log.png)

**Exercise history** — per-exercise trend across all logged sessions
![Exercise history](screenshots/06-history.png)

**Generate** — triggers the 6-step agent pipeline as a background job
![Generate](screenshots/07-generate.png)

---

## Project Structure

> Full database schema with ER diagrams: [docs/SCHEMA.md](docs/SCHEMA.md)

```
oly-program-generator/
├── README.md
├── Makefile                         # Common dev tasks: make web, make test, make up …
├── CLAUDE.md                        # Claude Code project instructions
├── ARCHITECTURE.md                  # Service architecture + Mermaid diagrams
├── schema.sql                       # Ingestion schema DDL (seed data included)
├── athlete_schema.sql               # Athlete / program schema DDL
├── docs/
│   ├── CONTRIBUTING.md              # Security audit, scaling checklist, test coverage
│   ├── SCHEMA.md                    # ER diagrams + table reference (20 tables)
│   ├── RETRIEVAL_EVAL.md            # Retrieval quality baseline scores
│   └── design/                      # Historical build docs (pipeline, agent, code reference)
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
│       ├── test_vector_loader.py    # 8 tests — needs live DB + OPENAI_API_KEY
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
    │   ├── test_validate.py         # 40 tests — all 6 validation checks
    │   ├── test_phase_profiles.py   # 15 tests — weekly target computation
    │   ├── test_weight_resolver.py  # 25 tests — weight resolution + ID lookup
    │   ├── test_generate_utils.py   # 43 tests — JSON parsing + name validation
    │   ├── test_assess.py           # 16 tests
    │   ├── test_plan.py             # 35 tests
    │   ├── test_retrieve.py         # 19 tests
    │   ├── test_explain.py          # 13 tests
    │   ├── test_orchestrator.py     # 12 tests (all 6 steps mocked)
    │   ├── test_feedback.py         # 19 tests (live DB)
    │   └── test_web_routers.py      # 21 tests (signed session cookies, mocked queries)
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
- Docker Desktop (for Postgres + Redis)
- API keys: `OPENAI_API_KEY` (embeddings) and `ANTHROPIC_API_KEY` (LLM)
- `make` — available in most shells; on Windows install via `winget install GnuWin32.Make` or use Git Bash with make from the Git SDK

---

## Local Setup

### 1. Configure environment

```bash
cd oly-ingestion
cp .env.example .env
```

Edit `.env` and fill in real values — at minimum `POSTGRES_PASSWORD`, `SECRET_KEY`, and the API keys. The file is gitignored and never committed. `shared/config.py` loads it automatically for both subsystems.

### 2. Install dependencies + start infrastructure

```bash
make sync   # uv sync for both subsystems
make up     # docker compose up -d (Postgres + PgBouncer + Redis)
make migrate  # alembic upgrade head (creates all tables + seed data)
```

---

## Running the Web UI

The web UI requires **three processes** running simultaneously. Open three terminals:

```bash
make up      # Terminal 1: infrastructure (if not already running)
make web     # Terminal 2: uvicorn on :8080 (--reload)
make worker  # Terminal 3: ARQ background worker
```

Open `http://localhost:8080`. Create an account at `/setup` or log in at `/login`.

The web server and ARQ worker are **separate processes** — both connect to the same Redis and Postgres. The worker can be restarted independently without affecting the web server or any open sessions.

**What each process does:**

| Process | Role |
|---------|------|
| Docker (Postgres) | Stores knowledge corpus, athlete profiles, programs, training logs |
| Docker (Redis) | Queues generation jobs between the web server and worker |
| uvicorn (web server) | Serves the UI, handles auth, reads/writes DB, enqueues generation jobs |
| ARQ worker | Polls Redis for jobs, runs the 6-step agent pipeline, stores result |

---

## Running Ingestion Only

If you only want to ingest source material (no web UI needed):

```bash
make up   # Postgres only needed; Redis is not required for ingestion

cd oly-ingestion

# EPUB / PDF book
uv run python pipeline.py \
  --source "./sources/book.epub" --title "Title" --author "Author" --type book

# PDF with vision OCR fallback (scanned / image-only PDFs)
uv run python pipeline.py \
  --source "./sources/book.pdf" --title "Title" --author "Author" --type book --vision

# Catalyst Athletics web articles
uv run python ingest_web.py
```

> On Windows outside of `make`, prefix with `PYTHONUTF8=1` to avoid cp1252 errors.

---

## Running the Agent via CLI

To generate programs or log sessions without the web UI:

```bash
cd oly-agent

# Generate a program
uv run python orchestrator.py --athlete-id 1 --dry-run  # ASSESS + PLAN only
uv run python orchestrator.py --athlete-id 1             # full generation

# Training log
uv run python log.py show    --athlete-id 1   # view current week
uv run python log.py session --athlete-id 1   # log a session (interactive)
uv run python log.py status  --athlete-id 1   # RPE / make-rate warnings
uv run python log.py history --athlete-id 1   # recent session history
```

CLI generation only needs Postgres (no Redis, no web server, no ARQ worker).

---

## Database Backup & Restore

Backups are stored in `backups/` (gitignored). The custom format (`-Fc`) is compressed and supports selective restore.

### Create a backup

```bash
docker exec oly-postgres pg_dump -U oly -d oly_programming -Fc > backups/oly_programming_$(date +%Y-%m-%d).dump
```

### Restore after data loss (DB intact, data lost)

If the container is running but data was lost:

```bash
docker exec -i oly-postgres pg_restore -U oly -d oly_programming --clean --if-exists < backups/oly_programming_2026-03-19.dump
```

### Full recovery (Docker wiped — `docker compose down -v`)

If the volume was destroyed entirely, recreate the schema first, then restore:

```bash
# 1. Start fresh and apply all migrations (creates tables + seed data)
make up && make migrate

# 2. Restore data on top
docker exec -i oly-postgres pg_restore -U oly -d oly_programming --clean --if-exists < backups/oly_programming_2026-03-19.dump
```

`--clean --if-exists` drops and recreates objects from the dump rather than erroring on tables that schema.sql already created.

---

## Running Tests

```bash
make test              # all no-key/no-DB tests (both subsystems)
make test-agent        # oly-agent unit + web router tests
make test-ingestion    # oly-ingestion unit tests

make coverage          # full coverage report for both subsystems
```

Tests that need a live DB (`test_feedback.py`, `test_structured_loader.py`) or API keys (`test_vector_loader.py`, `test_principle_extractor.py`, `test_pipeline.py`, `test_retrieval_eval.py`) are not included in `make test` — run them directly after `make up`:

```bash
cd oly-agent    && uv run pytest tests/test_feedback.py
cd oly-ingestion && uv run pytest tests/test_structured_loader.py
```

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
| Everett — *Olympic Weightlifting* | EPUB | 587 | 76 |
| Zatsiorsky — *Science and Practice of Strength Training* | PDF | 430 | 7 |
| Drechsler — *Weightlifting Encyclopedia* | PDF | 603 | 6 |
| Catalyst Athletics articles | Web (HTML) | 446 | 22 |
| Laputin — *Managing the Training of Weightlifters* | PDF (vision OCR) | 110 | 3 |
| Medvedev — *A Program of Multi-Year Training in Weightlifting* | PDF (vision OCR) | 617 | 0 |
| Everett — *Olympic Weightlifting for Sports* | PDF | 172 | 0 |
| Israetel — *Scientific Principles of Hypertrophy Training* | EPUB | 206 | 21 |
| Starrett — *Becoming a Supple Leopard* | EPUB | 137 | 16 |
| Dan John — *Intervention* | PDF | 266 | 0 |
| Takano — *Weightlifting Programming: A Winning Coach's Guide* | PDF | 218 | 0 |
| **Total** | | **3,796** | **151** |

> Retrieval quality baseline scores: [docs/RETRIEVAL_EVAL.md](docs/RETRIEVAL_EVAL.md)

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
