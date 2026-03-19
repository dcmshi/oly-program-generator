# Setup & Operations Guide

> Quick reference for running, testing, and maintaining the system locally.

---

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `pip install uv`
- Docker Desktop (for Postgres + PgBouncer + Redis)
- `OPENAI_API_KEY` (embeddings) and `ANTHROPIC_API_KEY` (LLM)
- `make` — on Windows: `winget install GnuWin32.Make` or use Git Bash with make from the Git SDK

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
make sync     # uv sync for both subsystems
make up       # docker compose up -d (Postgres + PgBouncer + Redis)
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

| Process | Role |
|---------|------|
| Docker (Postgres) | Stores knowledge corpus, athlete profiles, programs, training logs |
| Docker (Redis) | Queues generation jobs between the web server and worker |
| uvicorn (web server) | Serves the UI, handles auth, reads/writes DB, enqueues generation jobs |
| ARQ worker | Polls Redis for jobs, runs the 6-step agent pipeline, stores result |

---

## Running Ingestion

If you only want to ingest source material (no web UI needed):

```bash
make up   # Postgres only needed; Redis is not required for ingestion

cd oly-ingestion

# EPUB / PDF book
PYTHONUTF8=1 uv run python pipeline.py \
  --source "./sources/book.epub" --title "Title" --author "Author" --type book

# PDF with vision OCR fallback (scanned / image-only PDFs)
PYTHONUTF8=1 uv run python pipeline.py \
  --source "./sources/book.pdf" --title "Title" --author "Author" --type book --vision

# Catalyst Athletics web articles
PYTHONUTF8=1 uv run python ingest_web.py
```

> The `make` targets set `PYTHONUTF8=1` automatically. When running `uv run` directly on Windows, prefix it manually.

---

## Running the Agent via CLI

To generate programs or log sessions without the web UI:

```bash
cd oly-agent

# Generate a program
uv run python orchestrator.py --athlete-id 1 --dry-run  # ASSESS + PLAN only
uv run python orchestrator.py --athlete-id 1            # full generation

# Training log
uv run python log.py show    --athlete-id 1   # view current week
uv run python log.py session --athlete-id 1   # log a session (interactive)
uv run python log.py status  --athlete-id 1   # RPE / make-rate warnings
uv run python log.py history --athlete-id 1   # recent session history
```

CLI generation only needs Postgres (no Redis, no web server, no ARQ worker).

---

## Running Tests

```bash
make test              # all no-key/no-DB tests (both subsystems)
make test-agent        # oly-agent unit + web router tests
make test-ingestion    # oly-ingestion unit tests
make coverage          # coverage report for both subsystems
```

Tests that need a live DB or API keys are not included in `make test` — run them directly after `make up`:

```bash
cd oly-agent     && uv run pytest tests/test_feedback.py
cd oly-ingestion && uv run pytest tests/test_structured_loader.py
cd oly-ingestion && uv run pytest tests/test_vector_loader.py       # OPENAI_API_KEY
cd oly-ingestion && uv run pytest tests/test_principle_extractor.py # ANTHROPIC_API_KEY
```

---

## Database Backup & Restore

Backups are stored in `backups/` (gitignored). The custom format (`-Fc`) is compressed and supports selective restore.

### Create a backup

```bash
docker exec oly-postgres pg_dump -U oly -d oly_programming -Fc > backups/oly_programming_$(date +%Y-%m-%d).dump
```

### Restore after data loss

```bash
docker exec -i oly-postgres pg_restore -U oly -d oly_programming --clean --if-exists < backups/oly_programming_2026-03-19.dump
```

### Full recovery (Docker volume wiped)

```bash
# 1. Recreate schema
make up && make migrate

# 2. Restore data on top
docker exec -i oly-postgres pg_restore -U oly -d oly_programming --clean --if-exists < backups/oly_programming_2026-03-19.dump
```

---

## Granting Admin Access

The `/admin/jobs` page is restricted to athletes with `is_admin = true` (athlete id=1 is seeded as admin). To grant access to another user:

```sql
UPDATE athletes SET is_admin = true WHERE username = 'someone';
```

The flag takes effect on next login.

---

## Project Structure

```
oly-program-generator/
├── README.md
├── Makefile                         # Common dev tasks: make web, make test, make up …
├── CLAUDE.md                        # Claude Code project instructions
├── ARCHITECTURE.md                  # Service architecture + Mermaid diagrams
├── schema.sql                       # Ingestion schema DDL (seed data included)
├── athlete_schema.sql               # Athlete / program schema DDL
├── docs/
│   ├── SETUP.md                     # This file — setup, ingestion, CLI, tests, backup
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
│   ├── pyproject.toml
│   ├── docker-compose.yml           # Postgres + PgBouncer + Redis
│   ├── pipeline.py                  # EPUB / PDF ingestion orchestrator
│   ├── ingest_web.py                # Web article ingestion (Catalyst Athletics)
│   ├── extractors/                  # pdf_extractor · epub_extractor · html_extractor
│   ├── processors/                  # chunker · classifier · principle_extractor
│   ├── loaders/                     # vector_loader · structured_loader
│   └── tests/
│
└── oly-agent/                       # Programming agent + web UI
    ├── pyproject.toml
    ├── orchestrator.py              # Main pipeline runner (CLI entry point)
    ├── assess.py / plan.py / retrieve.py / generate.py / validate.py / explain.py
    ├── models.py · phase_profiles.py · session_templates.py · weight_resolver.py
    ├── feedback.py · log.py
    ├── migrations/                  # Alembic migrations (0000–0003)
    ├── tests/                       # 275 unit tests (no DB/API needed for make test)
    └── web/                         # FastAPI web UI
        ├── app.py                   # Application factory + middleware + Jinja2 filters
        ├── routers/                 # auth · setup · dashboard · program · log_session
        │                            # generate · export · history · profile · admin
        ├── queries/                 # Async DB query modules (one per router)
        └── templates/               # Jinja2 templates + HTMX partials
```
