# Setup & Operations Guide

> Quick reference for running, testing, and maintaining the system locally.

---

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `pip install uv`
- Docker Desktop (for Postgres + PgBouncer + Redis)
- `OPENAI_API_KEY` (embeddings) and one LLM provider key: `LLM_PROVIDER=openrouter` + `OPENROUTER_API_KEY` (the dev default since 2026-09-20 — Claude and the open models through one account; no Message Batches, so `--batch` runs synchronously) or `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (Claude only, Batches available). Optional `TYPESAFE_API_KEY` for Jev label/score decisions. Model roles and the provider notes are in `.env.example`.
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

> **Migrations against a non-local database:** `make migrate` rewrites
> `localhost:5432` → `:5433` to bypass the local PgBouncer (transaction pooling
> is incompatible with DDL). For any remote/production database, set
> `ALEMBIC_DATABASE_URL` to a **direct** Postgres URL (not through a
> transaction-mode pooler) — it passes through untouched.

### 3. Ports

| Port | Service | Used by |
|------|---------|---------|
| 5432 | **PgBouncer** (transaction pooling) | `DATABASE_URL` — all application + ingestion traffic |
| 5433 | **Postgres direct** | psql, Alembic DDL, debugging |
| 6379 | Redis | ARQ job queue |

Connection strings: `postgresql://oly:oly@localhost:5432/oly_programming` (app)
and `postgresql://oly:oly@localhost:5433/oly_programming` (direct).

---

## Database Operations

```bash
# Connect (no -it flag on Windows — there is no TTY)
docker exec oly-postgres psql -U oly -d oly_programming -c "\dt"

# Spot-check after an ingestion run
docker exec oly-postgres psql -U oly -d oly_programming -c "
  SELECT source_id, count(*) AS chunks FROM knowledge_chunks GROUP BY source_id;
  SELECT source_id, count(*) AS principles FROM programming_principles GROUP BY source_id;
  SELECT id, status, chunks_created FROM ingestion_runs ORDER BY id DESC LIMIT 5;
"

make reset   # down -v, up, migrate — drops all data and rebuilds
```

**Existing database that predates Alembic:** mark every migration as applied
without running it with `cd oly-agent && uv run alembic stamp head`. Always
stamp `head`, never a specific revision — a partial stamp goes stale as the
chain grows and then dies mid-upgrade on non-idempotent DDL.

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

### Useful flags

| Flag | Applies to | Effect |
|------|-----------|--------|
| `--vision` | `pipeline.py` | Enables the Claude vision OCR fallback for image-only PDFs (opt-in — it costs money) |
| `--max-pages N` | `pipeline.py` | Limits extraction to the first N pages — use when testing an OCR run |
| `--no-ocr-cache` | `pipeline.py` | Ignore `sources/.ocr_cache/` and transcribe every page again; by default vision-OCR text is cached per file hash + model, so a re-ingest of an unchanged scanned PDF costs no OCR (ING-M5) |
| `--classifier jev` | `pipeline.py` | Routes sections with one calibrated Jev `Choice` each instead of heuristics + LLM fallback (JEV-1c; beat the heuristic 13:4 on adjudicated disagreements); needs `TYPESAFE_API_KEY`, ~$0.002 per book |
| `--judge jev` | `relabel_chunk_types.py` | Labels through TypeSafe's Jev instead of the light model: one calibrated `Choice` per passage, ~185 ms and ~$0.19 for the corpus; needs `TYPESAFE_API_KEY`. Re-freeze the golden set / baseline after applying it (labels feed the chunk-type preference boost) |
| `--batch` | `pipeline.py`, `relabel_chunk_types.py` | Sends principle extraction, vision OCR (and the relabel calls) through the Message Batches API at half price. Principle sections are queued and flushed as one batch after the section loop; a batch takes minutes to hours, so not for smoke tests (COST-1) |
| `--categories technique` | `ingest_web.py` | Restrict to one category instead of all priority categories |
| `--site charniga` | `ingest_web.py` | Crawl Charniga via the Wayback CDX index instead of Catalyst |
| `--site urls --url-file sources/url_lists/<name>.json` | `ingest_web.py` | Ingest a curated URL list from any site (SBS, JTS, Pendlay); generic WordPress-style extraction, progress in `sources/urls_progress.json` |
| `--limit 20` | `ingest_web.py` | Cap article count for a smoke test |
| `--dry-run` | `ingest_web.py` | Collect URLs only, ingest nothing |

Web ingestion records completed URLs in `sources/catalyst_progress.json` (and
`charniga_progress.json`). Runs are safe to interrupt — a re-run resumes and
skips what is already stored. Delete the progress file to force a full re-ingest.

### Re-tagging existing chunks

After changing `KEYWORD_TO_TOPIC` in the chunker, re-tag stored chunks in place —
no re-embedding and no API cost:

```bash
PYTHONUTF8=1 uv run python retag_chunks.py               # all chunks
PYTHONUTF8=1 uv run python retag_chunks.py --source-id 499
PYTHONUTF8=1 uv run python retag_chunks.py --dry-run     # preview only
```

---

## Running the Agent via CLI

To generate programs or log sessions without the web UI:

```bash
cd oly-agent

# Generate a program
uv run python orchestrator.py --athlete-id 1 --dry-run  # ASSESS + PLAN only
uv run python orchestrator.py --athlete-id 1            # full generation

# Training log
uv run python log.py show     --athlete-id 1          # view current week
uv run python log.py session  --athlete-id 1          # log a session (interactive)
uv run python log.py exercise --log-id 5              # add exercise details to a logged session
uv run python log.py status   --athlete-id 1          # RPE / make-rate warnings
uv run python log.py history  --athlete-id 1 --weeks 2  # recent session history
```

CLI generation only needs Postgres (no Redis, no web server, no ARQ worker).

---

## Frontend Assets

Nothing is loaded from a CDN — Tailwind, htmx, Chart.js and both webfonts are all
served from `oly-agent/web/static/`. The generated files are **committed**, so a
clone runs and deploys without npm or network access, and neither target below is
part of setup.

```bash
make css      # compile web/static/tailwind.css from web/tailwind/  (needs npm)
make fonts    # refetch the self-hosted woff2 files + regenerate fonts.css
```

Run `make css` after using a Tailwind utility class that no template used before —
the compiler only emits what it finds in the content globs, so a brand-new class
silently has no styles until you rebuild.

The colour palette lives in `web/tailwind/tailwind.config.js`, defined by role:
`paper` (surfaces), `line` (borders), `ink` (text on those surfaces), `navy` (nav
and primary buttons, with its own light text shades for use *on* navy), and
`canvas` (page background). Tailwind's default `gray` is deliberately left
unremapped and unused — a stray `text-gray-500` renders visibly cool against the
warm theme, and a test fails on any `-gray-` utility.
`web/tailwind/build_palette.py` regenerates the accent tints and audits the
contrast of every fill/text pair the templates use.

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
cd oly-ingestion && uv run pytest tests/test_pipeline.py            # both keys, e2e
cd oly-ingestion && uv run pytest tests/test_retrieval_eval.py      # both keys, quality baseline
```

A few tests inside otherwise-mocked files need real API calls or a live DB.
They are gated behind `INTEGRATION_TESTS=1` and reported as SKIP otherwise:

```bash
cd oly-ingestion
INTEGRATION_TESTS=1 uv run pytest tests/test_pdf_extractor.py   # + vision OCR test
INTEGRATION_TESTS=1 uv run pytest tests/test_retag_chunks.py    # + live DB test

cd oly-agent
INTEGRATION_TESTS=1 uv run pytest tests/test_orchestrator.py tests/test_web_routers.py
```

The canonical list of which suites run without a DB or keys is `AGENT_TESTS` /
`INGESTION_TESTS` in the root `Makefile` — prefer that over any list in prose.

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
├── CLAUDE.md                        # Claude Code project instructions + invariants
├── ARCHITECTURE.md                  # Service architecture + Mermaid diagrams
├── TODO.md                          # Current audit findings and their status
├── schema.sql                       # Ingestion schema DDL (reference; managed by Alembic)
├── athlete_schema.sql               # Athlete / program schema DDL (reference; managed by Alembic)
├── docs/
│   ├── SETUP.md                     # This file — setup, DB ops, ingestion, CLI, tests, backup
│   ├── CONTRIBUTING.md              # Security audit, scaling checklist, test coverage
│   ├── SCHEMA.md                    # ER diagrams + table reference (20 tables)
│   ├── CORPUS.md                    # Ingested + planned sources, chunk-size profiles
│   ├── RETRIEVAL_EVAL.md            # Retrieval quality baseline scores
│   ├── DB-MACHINE-RUNBOOK.md        # Pending ops on the corpus DB machine
│   └── design/                      # Historical build docs (pipeline, agent, code reference)
│
├── shared/                          # Shared modules (imported by both subsystems)
│   ├── config.py                    # Unified Settings dataclass (reads .env)
│   ├── constants.py                 # Project-wide numeric constants
│   ├── db.py                        # psycopg2 fetch_one / fetch_all / execute helpers
│   ├── exercise_mapping.py          # EXERCISE_NAME_TO_INTENSITY_REF + COMP_LIFT_REFS
│   ├── formulas.py · timeutil.py    # Derived metrics · timezone-aware "today"
│   ├── llm.py                       # Anthropic client + cost estimation
│   └── prilepin.py                  # Zone lookup + per-session rep targets
│
├── oly-ingestion/                   # Ingestion pipeline
│   ├── pyproject.toml
│   ├── docker-compose.yml           # Postgres + PgBouncer + Redis
│   ├── pipeline.py                  # EPUB / PDF ingestion orchestrator
│   ├── ingest_web.py                # Web article ingestion (Catalyst · Charniga · curated URL lists)
│   ├── retag_chunks.py              # Re-tag stored chunks after KEYWORD_TO_TOPIC changes
│   ├── extractors/                  # pdf_extractor · epub_extractor · html_extractor · jats_extractor (Europe PMC) · ocr_cache
│   ├── processors/                  # chunker · classifier · principle_extractor · ocr_corrections
│   ├── loaders/                     # vector_loader · structured_loader
│   ├── sources/                     # Source files + crawl progress JSON (gitignored)
│   └── tests/
│
└── oly-agent/                       # Programming agent + web UI
    ├── pyproject.toml
    ├── orchestrator.py              # Main pipeline runner (CLI entry point)
    ├── assess.py / plan.py / retrieve.py / generate.py / validate.py / explain.py
    ├── models.py · schemas.py · phase_profiles.py · phase_progression.py
    ├── session_templates.py · weight_resolver.py · feedback.py · log.py · setup_auth.py
    ├── migrations/                  # Alembic migrations (see `alembic history` for the chain)
    ├── tests/                       # Unit tests (no DB/API needed for make test)
    └── web/                         # FastAPI web UI
        ├── app.py                   # Application factory + middleware + Jinja2 filters
        ├── async_db.py              # asyncpg pool (web-only async DB layer)
        ├── worker.py · jobs.py      # ARQ worker entry point + job handler
        ├── auth.py · deps.py        # bcrypt auth · settings/db/limiter dependencies
        ├── formparse.py · options.py · logging_config.py
        ├── routers/                 # auth · setup · dashboard · program · log_session
        │                            # generate · export · history · profile · admin · health
        ├── queries/                 # Async DB query modules (one per router)
        ├── static/                  # favicon
        └── templates/               # Jinja2 templates + HTMX partials
```
