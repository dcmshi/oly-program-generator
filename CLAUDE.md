# Olympic Weightlifting Program Generator — CLAUDE.md

## Project Layout

```
D:\oly-program-generator\
├── CLAUDE.md                        # this file
├── README.md                        # project overview + Mermaid architecture diagram
├── PROGRESS.md                      # implementation progress tracker
├── oly-programming-pipeline.md      # ingestion pipeline design doc
├── oly-programming-agent.md         # agent design doc
├── oly-code-reference.md            # reference implementations by module
├── schema.sql                       # ingestion schema DDL + seed data
├── athlete_schema.sql               # athlete/program/log schema DDL
│
├── shared/                          # shared modules (used by both subsystems)
│   ├── config.py                    # unified Settings dataclass — reads .env from multiple locations
│   ├── db.py                        # get_connection, fetch_one, fetch_all, execute, execute_returning
│   ├── llm.py                       # create_llm_client, estimate_cost
│   └── prilepin.py                  # get_prilepin_zone, get_prilepin_data, compute_session_rep_target
│
├── oly-agent/                       # programming agent (Phase 6)
│   ├── orchestrator.py              # main pipeline: --athlete-id N [--dry-run]
│   ├── assess.py                    # Step 1: athlete context from DB
│   ├── plan.py                      # Step 2: phase selection + weekly targets
│   ├── retrieve.py                  # Step 3: fault exercises + templates + vector search
│   ├── generate.py                  # Step 4: prompt builder + LLM + retries
│   ├── validate.py                  # Step 5: Prilepin / intensity / principle checks
│   ├── explain.py                   # Step 6: program rationale (LLM)
│   ├── models.py                    # dataclasses (AthleteContext, ProgramPlan, …)
│   ├── phase_profiles.py            # PHASE_PROFILES + build_weekly_targets()
│   ├── session_templates.py         # SESSION_DISTRIBUTIONS + get_session_templates()
│   ├── weight_resolver.py           # resolve_weights, resolve_exercise_ids
│   ├── feedback.py                  # ProgramOutcome computation + max promotion
│   └── log.py                       # training log CLI (show/session/exercise/status/history)
│
└── oly-ingestion/                   # ingestion pipeline (Phases 1–5)
    ├── pyproject.toml               # uv project file
    ├── .venv/                       # managed by uv (never touch manually)
    ├── docker-compose.yml
    ├── schema.sql                   # copy of ../schema.sql (auto-applied by Docker)
    ├── .env                         # API keys — DO NOT COMMIT
    ├── pipeline.py                  # EPUB/PDF ingestion orchestrator
    ├── ingest_web.py                # web article ingestion (Catalyst Athletics)
    ├── extractors/
    │   ├── pdf_extractor.py         # PyMuPDF → pdfplumber → Claude vision OCR fallback chain
    │   ├── html_extractor.py        # BeautifulSoup body extraction
    │   └── epub_extractor.py        # ebooklib chapter-by-chapter
    ├── processors/
    │   ├── chunker.py               # profile-aware chunking + topic tagging
    │   ├── classifier.py            # heuristic routing + LLM fallback (<0.6 confidence)
    │   ├── principle_extractor.py   # LLM extraction of if-then rules
    │   └── ocr_corrections.py       # Soviet-era OCR correction dict
    ├── loaders/
    │   ├── vector_loader.py         # batch embed + SHA-256 dedup + similarity_search
    │   └── structured_loader.py     # upsert sources, principles, exercises
    ├── sources/                     # PDFs go here (gitignored)
    ├── retag_chunks.py              # re-tag existing DB chunks after KEYWORD_TO_TOPIC changes
    └── tests/
        ├── test_chunker.py          # 14 tests — no API keys needed
        ├── test_classifier.py       # 10 tests (6 heuristic + 4 LLM)
        ├── test_vector_loader.py    # 6 tests — needs live DB + OPENAI_API_KEY
        ├── test_structured_loader.py # 7 tests — needs live DB
        ├── test_principle_extractor.py # 6 tests — needs ANTHROPIC_API_KEY
        ├── test_pipeline.py         # 4 e2e tests — needs both keys
        └── test_retrieval_eval.py   # retrieval quality eval (both keys)
```

## How to Run Things

**Always prefix with `PYTHONUTF8=1` on Windows** to avoid cp1252 encoding errors with Unicode output.

Each subsystem manages its own venv via `uv`. Use `uv run` from the respective directory — never call the interpreter directly by path.

```bash
# ── Install / sync dependencies ──────────────────────────────────────────
cd oly-ingestion && uv sync --extra dev
cd oly-agent    && uv sync --extra dev

# ── Tests (no API keys needed) ──────────────────────────────────────────
cd oly-ingestion
PYTHONUTF8=1 uv run python tests/test_chunker.py          # 14 tests
PYTHONUTF8=1 uv run python tests/test_classifier.py       # 6 heuristic tests
PYTHONUTF8=1 uv run python tests/test_classifier.py --llm # + 4 LLM tests (needs ANTHROPIC_API_KEY)

# ── Tests (need live DB, no API keys) ───────────────────────────────────
PYTHONUTF8=1 uv run python tests/test_structured_loader.py  # 7 tests

# ── Tests (need live DB + API keys) ─────────────────────────────────────
PYTHONUTF8=1 uv run python tests/test_vector_loader.py       # 6 tests (OPENAI_API_KEY)
PYTHONUTF8=1 uv run python tests/test_principle_extractor.py # 6 tests (ANTHROPIC_API_KEY)
PYTHONUTF8=1 uv run python tests/test_pipeline.py            # 4 e2e tests (both keys)
PYTHONUTF8=1 uv run python tests/test_retrieval_eval.py      # retrieval quality (both keys)

# ── Ingestion ────────────────────────────────────────────────────────────
cd oly-ingestion

# EPUB / PDF (text-based)
PYTHONUTF8=1 uv run python pipeline.py \
  --source "./sources/book.epub" --title "Title" --author "Author" --type book

# PDF with vision OCR fallback (image-only / scanned PDFs)
PYTHONUTF8=1 uv run python pipeline.py \
  --source "./sources/book.pdf" --title "Title" --author "Author" --type book --vision

# Test first N pages only (avoid full OCR cost during testing)
PYTHONUTF8=1 uv run python pipeline.py ... --vision --max-pages 10

# Re-tag all DB chunks after updating KEYWORD_TO_TOPIC (no re-embedding needed)
PYTHONUTF8=1 uv run python retag_chunks.py                  # all chunks
PYTHONUTF8=1 uv run python retag_chunks.py --source-id 499  # single source
PYTHONUTF8=1 uv run python retag_chunks.py --dry-run        # preview only

# Web articles (Catalyst Athletics)
PYTHONUTF8=1 uv run python ingest_web.py                          # all priority categories
PYTHONUTF8=1 uv run python ingest_web.py --categories technique   # specific category
PYTHONUTF8=1 uv run python ingest_web.py --limit 20               # cap for testing
PYTHONUTF8=1 uv run python ingest_web.py --dry-run                # collect URLs only
# Progress tracked in sources/catalyst_progress.json — re-run resumes from where it left off

# ── Programming Agent ────────────────────────────────────────────────────
cd oly-agent

PYTHONUTF8=1 uv run python orchestrator.py --athlete-id 1 --dry-run  # ASSESS + PLAN only
PYTHONUTF8=1 uv run python orchestrator.py --athlete-id 1            # full generation

# ── Training Log CLI ─────────────────────────────────────────────────────
PYTHONUTF8=1 uv run python log.py show     --athlete-id 1            # current week's sessions
PYTHONUTF8=1 uv run python log.py session  --athlete-id 1            # log a session (interactive)
PYTHONUTF8=1 uv run python log.py exercise --log-id 5                # add exercise details
PYTHONUTF8=1 uv run python log.py status   --athlete-id 1            # RPE + make-rate warnings
PYTHONUTF8=1 uv run python log.py history  --athlete-id 1 --weeks 2  # recent history

# ── Agent Tests (no DB or API keys needed) ───────────────────────────────
PYTHONUTF8=1 uv run python tests/test_validate.py        # 25 tests
PYTHONUTF8=1 uv run python tests/test_phase_profiles.py  # 15 tests
PYTHONUTF8=1 uv run python tests/test_weight_resolver.py # 18 tests
PYTHONUTF8=1 uv run python tests/test_generate_utils.py  # 15 tests
```

## Docker / Database

```bash
# Start Postgres (from oly-ingestion/)
docker compose up -d

# Stop
docker compose down

# Connect directly (no -it flag on Windows — use without TTY)
docker exec oly-postgres psql -U oly -d oly_programming -c "\dt"

# Quick spot-check after ingestion
docker exec oly-postgres psql -U oly -d oly_programming -c "
  SELECT source_id, count(*) as chunks FROM knowledge_chunks GROUP BY source_id;
  SELECT source_id, count(*) as principles FROM programming_principles GROUP BY source_id;
  SELECT id, status, chunks_created FROM ingestion_runs ORDER BY id DESC LIMIT 5;
"

# Reset DB (drops all data — re-applies schema.sql on next up)
docker compose down -v && docker compose up -d
```

Connection string: `postgresql://oly:oly@localhost:5432/oly_programming`

## API Keys

Set in `oly-ingestion/.env`:
```
OPENAI_API_KEY=sk-...        # required for embeddings (vector_loader)
ANTHROPIC_API_KEY=sk-ant-... # required for LLM tasks (principle_extractor, classifier LLM fallback)
DATABASE_URL=postgresql://oly:oly@localhost:5432/oly_programming
```

- `OPENAI_API_KEY` — needed by `loaders/vector_loader.py` (embeddings via text-embedding-3-small)
- `ANTHROPIC_API_KEY` — needed by `processors/principle_extractor.py` and the `_llm_classify()` stub in `processors/classifier.py`
- Neither key is needed to run tests for chunker, classifier heuristics, or structured_loader

## Key Architecture

Content is **routed before chunking** — the classifier sends each section to exactly one path:

| Content type | Destination |
|---|---|
| Prose / rationale | `chunker` → `vector_loader` → `knowledge_chunks` |
| Tables / programs | `structured_loader` → structured tables |
| If/then rules | `principle_extractor` → `programming_principles` |
| Mixed (both) | Both vector store AND principle extraction |

Chunk dedup uses SHA-256 of `raw_content` (without preamble) stored in `content_hash`.
Re-running pipeline on the same source skips already-ingested chunks before the embedding API call.

## Stubbed / Incomplete

| File | What's stubbed | Phase to implement |
|---|---|---|
| `pipeline.py` | `_parse_program_template()` returns shell dict | Phase 4–5 |
| `pipeline.py` | `_parse_exercise()` returns empty dict — exercises always fail to insert | Phase 4–5 |

## Known Pipeline Behaviours & Gotchas

- **EPUB chapters are processed individually** — do NOT join all pages into one string before classifying. Each EPUB document item is already a logical section. `pipeline.py` now iterates `pages` and classifies each one separately.
- **Oversized chunks are truncated at embedding time** — `vector_loader` truncates texts >30k chars before sending to OpenAI (limit is 8192 tokens ≈ 32k chars). A warning is logged; the chunk is still stored with a partial embedding.
- **Transaction rollback on section error** — the section processing loop calls `conn.rollback()` on both connections after any exception, preventing the "transaction is aborted" cascade.
- **Dedup is content-hash global** — the same text appearing in two different sources will only be embedded once. The second source's run shows `chunks_created=0` for those sections.
- **`_llm_classify()` fires when heuristic confidence < 0.6** — short sections (<50 words) score 0.60 (just at threshold), so they won't trigger LLM. Only genuinely ambiguous mid-length sections fall through.
- **`docker exec` on Windows** — drop the `-it` flag (no TTY). Use `docker exec oly-postgres psql ...` not `docker exec -it`.
- **classifier `chunk_type` is a Postgres enum** — filter with `chunk_type::text = ANY(...)` not `chunk_type = ANY(...)`.
- **PDF extractor fallback chain** — PyMuPDF → pdfplumber → Claude vision OCR. pdfplumber is tried when PyMuPDF extracts <100 total chars. Vision OCR is tried when pdfplumber also returns <100 chars AND `--vision` flag was passed (opt-in to avoid accidental API costs). Pass `--max-pages N` to limit OCR to the first N pages during testing.
- **Web scraper progress** — `ingest_web.py` saves ingested URLs to `sources/catalyst_progress.json`. Safe to interrupt and re-run; already-ingested URLs are skipped. Catalyst-specific selector is `div.sub_page_main_area_half_container_left`.

## Source Ingestion Order

1. Everett (EPUB) — ✅ Done (198 chunks, 44 principles, source_id=1)
2. Zatsiorsky (PDF) — ✅ Done (430 chunks, 7 principles, source_id=51, theory_heavy profile)
3. Drechsler / Weightlifting Encyclopedia (PDF) — ✅ Done (603 chunks, 6 principles, source_id=52, theory_heavy profile)
4. Catalyst Athletics articles (web) — ✅ Done (418 articles, 446 chunks, 22 principles)
5. Laputin (PDF, vision OCR) — ✅ Done (110 chunks, 3 principles, source_id=499, soviet profile, `--vision` flag)
6. Takano — ⏳ File not available yet; use `programming` profile when obtained
7. Medvedev — ✅ Done (617 chunks, 0 principles, source_id=501, soviet profile + vision OCR)
8. Everett — *Olympic Weightlifting for Sports* — ✅ Done (172 chunks, 11 exercises, source_id=502, programming profile)
   - Added to address programming gap left by unavailable Takano book

**Total corpus:** 2,576 chunks · 82 principles · 436 sources

## Chunk Sizing Reference

| Source profile | Chunk size | Overlap |
|---|---|---|
| Theory-heavy (Zatsiorsky) | 1100 tokens | 250 |
| Programming-focused (Everett, Takano) | 900 tokens | 200 |
| Soviet data-heavy (Medvedev) | 700 tokens | 150 |
| Web article | 500–1100 (dynamic) | 100–250 |
