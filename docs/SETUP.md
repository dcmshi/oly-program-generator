# Setup & Operations Guide

> Quick reference for running, testing, and maintaining the system locally.

---

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `pip install uv`
- Docker Desktop (for Postgres + PgBouncer + Redis)
- `OPENAI_API_KEY` (embeddings) and one LLM provider key: `LLM_PROVIDER=openrouter` + `OPENROUTER_API_KEY` (the code default since 2026-09-22 — Kimi K3 / DeepSeek V4.1 Flash / GLM-5.3 Flash by role, Claude also reachable through the same account; no Message Batches, so `--batch` runs synchronously) or `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (Claude Sonnet 5 / Haiku 4.5 defaults, Batches available). The no-key test suites pin `LLM_PROVIDER=anthropic` themselves, and the live-API tests below need `ANTHROPIC_API_KEY` for the same reason. Optional `TYPESAFE_API_KEY` for Jev label/score decisions. Model roles and the provider notes are in `.env.example`.
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

### Exporting a program for someone outside the app

`cd oly-agent && PYTHONUTF8=1 uv run python -m eval.program_export <program_id> --pdf --out ~/Desktop`
writes a standalone `program_<id>.html` and, with Chrome installed, `program_<id>.pdf` (headless
print): maxes on file, the rationale, every session as a table (sets × reps, kg, % of which max, RPE,
rest), warm-ups greyed, and a weekly-loading summary. No login needed — it reads the database. The
in-app **Export PDF** button is the browser-print equivalent.

### Reading the log

A book ingest logs three stage banners with elapsed time (`── 1/3 Extract text … done in 13m 02s`,
`2/3 Classify sections`, `3/3 Route sections`) and one line per section
(`[section 12/48 · 25%] mixed 'Chapter 3' (3,412 ch) · chunks=40 principles=120 · elapsed 4m · ETA ~12m`);
vision OCR logs every 5-page group and any pages still blank after the retry. `ingest_web.py` logs
`[article i/n]` the same way. If you pipe the output through `grep`, add `--line-buffered` or the
file stays empty until the process exits.

### Useful flags

| Flag | Applies to | Effect |
|------|-----------|--------|
| `--vision` | `pipeline.py` | Enables the vision OCR fallback (on `LLM_MODEL`) for image-only PDFs (opt-in — it costs money) |
| `--max-pages N` | `pipeline.py` | Limits extraction to the first N pages — use when testing an OCR run |
| `--ocr-postcorrect` | `pipeline.py` | OCR-QA: pages still garbled after the multi-view check go to the light model for guarded OCR error correction (kept only if the garbled share drops and the length stays within ±10 %); off by default |
| `--no-principle-audit` | `pipeline.py`, `ingest_web.py` | Skip the end-of-ingest pass that strips principle numbers the source text never states (PRIN-AUDIT) |
| `--no-quarantine` | `pipeline.py`, `ingest_web.py` | Skip the Jev junk pass that runs over the new source's chunks at the end of every ingest (per article on the web path) |
| `--force-vision` | `pipeline.py` | With `--vision`: ignore the PDF's text layer and OCR every page — for old scans whose embedded OCR is one block per line (paragraphs lost) or spaced digits |
| `--no-ocr-cache` | `pipeline.py` | Ignore `sources/.ocr_cache/` and transcribe every page again; by default vision-OCR text is cached per file hash + model, so a re-ingest of an unchanged scanned PDF costs no OCR (ING-M5) |
| `--classifier jev` | `pipeline.py` | Routes sections with one calibrated Jev `Choice` each instead of heuristics + LLM fallback (JEV-1c; beat the heuristic 13:4 on adjudicated disagreements); needs `TYPESAFE_API_KEY`, ~$0.002 per book |
| `--judge jev` | `relabel_chunk_types.py` | Labels through TypeSafe's Jev instead of the light model: one calibrated `Choice` per passage, ~185 ms and ~$0.19 for the corpus; needs `TYPESAFE_API_KEY`. Re-freeze the golden set / baseline after applying it (labels feed the chunk-type preference boost) |
| `--batch` | `pipeline.py`, `relabel_chunk_types.py` | Sends principle extraction, vision OCR (and the relabel calls) through the Message Batches API at half price — `LLM_PROVIDER=anthropic` only; under OpenRouter it degrades to synchronous with a warning. Principle sections are queued and flushed as one batch after the section loop; a batch takes minutes to hours, so not for smoke tests (COST-1) |
| `--contextualize` | `pipeline.py`, `ingest_web.py` | Writes an LLM retrieval-context prefix (1–2 sentences) into each chunk before embedding and into `knowledge_chunks.context_prefix`; one short call per chunk, opt-in (RAG-M3) |
| `--context-model MODEL` | `pipeline.py`, `ingest_web.py` | Model for `--contextualize` (default: the light model) |
| `--categories technique` | `ingest_web.py` | Restrict to one category instead of all priority categories |
| `--site charniga` | `ingest_web.py` | Crawl Charniga via the Wayback CDX index instead of Catalyst |
| `--site urls --url-file sources/url_lists/<name>.json` | `ingest_web.py` | Ingest a curated URL list from any site (SBS, JTS, Pendlay); generic WordPress-style extraction, progress in `sources/urls_progress.json` |
| `--limit 20` | `ingest_web.py` | Cap article count for a smoke test |
| `--dry-run` | `ingest_web.py` | Collect URLs only, ingest nothing |
| `--delay SECONDS` | `ingest_web.py` | Pause between article requests (default 1.0) |

Web ingestion records completed URLs in `sources/catalyst_progress.json` (and
`charniga_progress.json`). Runs are safe to interrupt — a re-run resumes and
skips what is already stored. Delete the progress file to force a full re-ingest.

### Importing Kobo purchases

Kobo's download button yields Adobe-DRM `.acsm` tickets, not EPUBs. `kobo_import.py check | authorize | fulfil | export | verify` drives Calibre + the DeACSM and DeDRM plugins end to end — see [KOBO-IMPORT.md](KOBO-IMPORT.md) for the plugin list and pitfalls.

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

Tracked files only (`git ls-files`); tests, templates and data files are summarised per directory.

```
oly-program-generator/
├── README.md · LICENSE
├── Makefile                         # Common dev tasks: make up / migrate / web / worker / test / lint / coverage / css
├── CLAUDE.md                        # Claude Code project instructions + invariants
├── ARCHITECTURE.md                  # Service architecture + Mermaid diagrams, production env vars
├── TODO.md                          # Current audit findings and their status
├── TODO-audit-2026-07-03.md         # Older audit + roadmap #17–#23
├── ruff.toml                        # Lint config (ruff version pinned in the Makefile)
├── schema.sql · athlete_schema.sql · auth_migration.sql
│                                    # Pre-Alembic DDL, reference only — the migrations are the source of truth
├── .github/workflows/ci.yml         # CI: lint + the no-key test suites
├── .claude/skills/kobo-import/      # Claude Code skill wrapping kobo_import.py
├── screenshots/                     # README screenshots
├── docs/
│   ├── SETUP.md                     # This file — setup, DB ops, ingestion, CLI, tests, backup
│   ├── SCHEMA.md                    # ER diagrams + table reference (21 tables)
│   ├── CORPUS.md                    # Ingested + planned sources, chunk-size profiles, SOURCE_PROFILE_MAP
│   ├── RETRIEVAL_EVAL.md            # Retrieval-quality baseline and the eval gate
│   ├── RAG_RESEARCH.md              # RAG / ingestion / vector-DB review and remediation plan (RAG-* items)
│   ├── PROGRAMMING_ASSUMPTIONS.md   # Audit of hard-coded programming decisions (PLAN-1 / PLAN-2)
│   ├── KOBO-IMPORT.md               # Kobo purchase → clean EPUB (Calibre + DeACSM + DeDRM)
│   ├── CONTRIBUTING.md              # Security audit, scaling checklist, coverage
│   ├── DB-MACHINE-RUNBOOK.md        # Historical ops replay list (everything in it has run)
│   ├── arch-*.png                   # Static renders of the Mermaid diagrams
│   └── design/                      # Historical build docs (pipeline, agent, code reference, security, scaling)
│
├── shared/                          # Imported by both subsystems
│   ├── config.py                    # Settings dataclass (reads oly-ingestion/.env); provider-aware model defaults
│   ├── constants.py                 # Project-wide numeric constants
│   ├── db.py                        # psycopg2 fetch_one / fetch_all / execute helpers
│   ├── llm.py                       # LLM client (Anthropic SDK → Anthropic or OpenRouter), request helpers, batches, cost
│   ├── schema_enums.py              # Enum values for the structured-output schemas (mirrored against the migrations)
│   ├── exercise_mapping.py          # EXERCISE_NAME_TO_INTENSITY_REF + COMP_LIFT_REFS
│   ├── formulas.py · timeutil.py    # Derived metrics · timezone-aware "today"
│   └── prilepin.py                  # Zone lookup + per-session rep targets
│
├── oly-ingestion/                   # Ingestion pipeline (CLI only)
│   ├── pyproject.toml · uv.lock · requirements.txt
│   ├── .env.example                 # Template for the gitignored .env (keys, provider, model roles)
│   ├── docker-compose.yml           # Postgres + PgBouncer + Redis
│   ├── config.py                    # Shim re-exporting shared/config.py
│   ├── pipeline.py                  # EPUB / PDF / article ingestion orchestrator
│   ├── ingest_web.py                # Web ingestion (Catalyst · Charniga via Wayback · curated URL lists)
│   ├── kobo_import.py               # Kobo .acsm → DRM-free EPUB via Calibre (docs/KOBO-IMPORT.md)
│   ├── retag_chunks.py              # Re-tag stored chunks after KEYWORD_TO_TOPIC changes
│   ├── relabel_chunk_types.py       # Re-label chunk_type with the light model or Jev
│   ├── reembed.py                   # Re-embed chunks under a new embedding model (RAG-M8 / EMBED-1)
│   ├── quarantine_chunks.py         # Jev junk pass — marks non-content chunks quarantined
│   ├── dedupe_principles.py         # Marks restated principles duplicate_of their canonical twin
│   ├── principle_audit.py           # PRIN-AUDIT — strips principle numbers the source never states
│   ├── principle_model_compare.py   # Head-to-head principle extraction across models (MODEL-2)
│   ├── ocr_audit.py                 # Re-runs the OCR-QA checks on cached OCR output
│   ├── eval_queries.py              # Original hand-written retrieval queries
│   ├── schema.sql                   # Pre-Alembic ingestion DDL (reference only)
│   ├── extractors/                  # pdf_extractor (PyMuPDF → pdfplumber → vision OCR) · epub_extractor ·
│   │                                # html_extractor · jats_extractor (Europe PMC) · ocr_cache · page_text
│   ├── processors/
│   │   ├── classifier.py            # Heuristic (+ LLM / Jev) section routing
│   │   ├── sectioning.py            # Section splitting, capping and fragment merging before classification
│   │   ├── section_processor.py     # Per-section routing shared by both entry points
│   │   ├── chunker.py               # Profile-aware chunking, SOURCE_PROFILE_MAP, topic tagging
│   │   ├── tokens.py                # tiktoken token counting
│   │   ├── contextualizer.py        # --contextualize retrieval-context prefixes
│   │   ├── principle_extractor.py   # LLM if/then rule extraction (schema-constrained)
│   │   ├── ocr_quality.py           # OCR-QA gate (suspect signals + multi-view agreement)
│   │   ├── ocr_corrections.py       # OCR correction dictionary for Soviet-era sources
│   │   ├── jev_judge.py             # Label / score decisions through Jev (TypeSafe)
│   │   └── progress.py              # Stage banners, per-section progress and ETA
│   ├── loaders/
│   │   ├── vector_loader.py         # Chunk insert + dedup + hybrid similarity_search
│   │   ├── embedders.py             # Embedder interface + openai / openai_compat providers
│   │   ├── local_embedder.py        # sentence-transformers provider
│   │   └── structured_loader.py     # Exercises, templates, principles → structured tables
│   ├── sources/                     # Source files + progress JSON (gitignored), except:
│   │   └── url_lists/               # Curated URL lists for --site urls (sbs · jts · pendlay · extras) + one-off scripts
│   └── tests/
│
└── oly-agent/                       # Programming agent + web UI
    ├── pyproject.toml · uv.lock · alembic.ini
    ├── orchestrator.py              # Main pipeline runner (CLI entry point)
    ├── assess.py / plan.py / retrieve.py / generate.py / validate.py / explain.py
    ├── principle_matcher.py         # Evaluates principle conditions against one session
    ├── weight_resolver.py           # LLM output → DB-ready values, [Cn] citations → source_chunk_ids
    ├── models.py · schemas.py       # Pipeline dataclasses · Pydantic models for JSONB columns
    ├── phase_profiles.py · phase_progression.py · session_templates.py
    ├── feedback.py · log.py         # Outcome summary + max promotion · training-log CLI
    ├── import_program_csv.py        # Import a coach-written program CSV as a completed program
    ├── setup_auth.py
    ├── eval/                        # Retrieval + model evaluation harness
    │   ├── build_golden.py · run_eval.py · metrics.py · queries.py
    │   │                            # Golden set → golden.json, gated run vs baseline.json
    │   ├── judge_agreement.py · judge_adjudicate.py   # Judge selection (JUDGE-1) + per-judge golden_*.json
    │   ├── model_baseline.py        # Generation-model baseline runs (model_baseline_*.json)
    │   ├── program_diff.py          # Side-by-side program comparison
    │   └── program_export.py        # Standalone HTML / PDF export of a program
    ├── migrations/                  # Alembic env + versions/ (0000 → 0017; `alembic history` for the chain)
    ├── tests/                       # Unit + router tests (the Makefile lists which need no DB/keys)
    └── web/                         # FastAPI web UI
        ├── app.py                   # Application factory + middleware + Jinja2 filters
        ├── async_db.py              # asyncpg pool (web-only async DB layer)
        ├── worker.py · jobs.py      # ARQ worker entry point + job handler
        ├── auth.py · deps.py        # bcrypt auth · settings/db/limiter dependencies
        ├── formparse.py · options.py · logging_config.py
        ├── routers/                 # auth · setup · dashboard · program · log_session
        │                            # generate · export · history · profile · admin · health
        ├── queries/                 # Async DB query modules (one per router)
        ├── tailwind/                # Tailwind source (input.css, tailwind.config.js palette, package.json),
        │                            # build_palette.py (tints + contrast audit), build_fonts.py
        ├── static/                  # Committed build output: tailwind.css, fonts.css + fonts/*.woff2,
        │                            # vendor/ (htmx, Chart.js), favicon.svg
        └── templates/               # Jinja2 templates + HTMX partials
```
