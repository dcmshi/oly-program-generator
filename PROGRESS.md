# Implementation Progress

Reference: implementation order in `oly-programming-pipeline.md` § Implementation Order.

---

## Phase 1 — Infrastructure & Schema ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 1 | Create project structure (`oly-ingestion/` + all subdirs + `__init__.py` files) | ✅ Done |
| 2 | Scaffold all module files from `oly-code-reference.md` | ✅ Done |
| 2 | Infrastructure files: `docker-compose.yml`, `pyproject.toml`, `.env`, `.gitignore` | ✅ Done |
| 2 | Copy `schema.sql` into `oly-ingestion/` | ✅ Done |
| 2 | Set up `uv` project (`uv sync --extra dev`) — 40 packages, isolated `.venv` | ✅ Done |
| 3 | Start Postgres — `docker compose up -d` | ✅ Done |
| 4 | Verify schema: `\dt` → 11 tables | ✅ Done |
| 4 | Verify exercises: `count(*)` → 44 | ✅ Done |
| 4 | Verify sources: `count(*)` → 6 | ✅ Done |
| 4 | Verify Prilepin: `count(*)` → 4 rows | ✅ Done |
| 4 | Verify fault query: `'slow_turnover' = ANY(faults_addressed)` → 8 exercises | ✅ Done |

**Notes:**
- Used `uv` instead of raw `pip` to keep dependencies isolated from system Python
- `pyproject.toml` replaces `requirements.txt` as the dependency manifest
- `docker` not on PowerShell PATH — use bash terminal or add `C:\Program Files\Docker\Docker\resources\bin` to PATH

---

## Phase 2 — Core Pipeline Modules ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 5 | `config.py` — Settings dataclass, loads from `.env` | ✅ Done (verified imports) |
| 6 | `extractors/pdf_extractor.py` — PyMuPDF + pdfplumber fallback | ✅ Scaffolded (needs PDF to test) |
| 6 | `extractors/html_extractor.py` — BeautifulSoup body extraction | ✅ Scaffolded |
| 6 | `extractors/epub_extractor.py` — ebooklib chapter extraction | ✅ Scaffolded |
| 7 | `processors/chunker.py` — profiles, preambles, keep-together, topic tagging, validation | ✅ Done (14/14 tests passing) |
| 7 | `processors/ocr_corrections.py` — Soviet-era OCR correction dict | ✅ Done |
| 8 | `processors/classifier.py` — heuristic routing + LLM stub | ✅ Done |
| 8 | Wire up `_llm_classify()` with Anthropic client | ✅ Done (10/10 tests passing) |
| 9 | `loaders/vector_loader.py` — batch embed, dedup, similarity search | ✅ Done |
| 9 | Test vector_loader with live DB + test chunk | ✅ Done (6/6 tests passing) |
| 10 | `loaders/structured_loader.py` — upsert sources, principles, programs, exercises | ✅ Scaffolded |
| 10 | Test structured_loader with manual inserts | ✅ Done (7/7 tests passing) |
| 11 | `processors/principle_extractor.py` — LLM extraction with Anthropic | ✅ Done |
| 11 | Test principle extraction with known passage | ✅ Done (6/6 tests passing) |
| 12 | `pipeline.py` — wire all modules + ingestion run tracking | ✅ Done (4/4 e2e tests passing) |
| 12 | Add ingestion run tracking (create/update/complete `ingestion_runs`) | ✅ Done |

---

## Phase 3 — First Ingestion ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 13 | Ingest Everett's book (EPUB) | ✅ Done — 198 chunks, 44 principles, source_id=1 |
| 14 | Verify ingestion run record, spot-check chunks and principles | ✅ Done |
| 15 | Verify embeddings are non-zero, spot similarity search | ✅ Done |

**Notes:**
- EPUB processing required per-chapter classification (not join-all-then-classify)
- Oversized chunks (>30k chars) truncated before embedding rather than dropped
- Transaction rollback added to section error handler to prevent cascade failures
- `_parse_exercise()` stub causes exercise inserts to fail silently (Phase 4-5)

---

## Phase 4 — Retrieval Validation ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 16 | Run retrieval eval queries (`tests/test_retrieval_eval.py`) | ✅ Done — results in CHUNKING.md |
| 17 | Iterate on chunk sizing if retrieval quality is low | ✅ Done — profiles validated |
| 18 | Iterate on topic tagging (extend `KEYWORD_TO_TOPIC` or wire LLM pass) | ✅ Done — ~20 keywords added, chunk_type inference refactored |

**Notes:**
- chunk_type was always `concept` — fixed by adding `_infer_chunk_type()` with `CHUNK_TYPE_KEYWORDS` mapping in `pipeline.py`
- `competition_strategy` over-tagged (66/198) — fixed by requiring specific multi-word phrases
- Retrieval eval run after each source ingestion; results tracked in `memory/CHUNKING.md`
- Query [3] (peaking weeks) improved significantly (+0.21) after Drechsler ingestion

---

## Phase 5 — Additional Sources ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 19 | Ingest Zatsiorsky — theory-heavy profile | ✅ Done — 430 chunks, 7 principles, source_id=51 |
| 20 | Ingest Takano — principles extraction focus | ✅ Done — 218 chunks, 16 program templates, source_id=2 (ingested 2026-03-19; file obtained) |
| 20b | Ingest Drechsler (Weightlifting Encyclopedia) — theory-heavy profile | ✅ Done — 603 chunks, 6 principles, source_id=52 |
| 21 | Ingest Laputin — OCR pipeline | ✅ Done — 110 chunks, 3 principles, source_id=499 (Claude vision OCR) |
| 22 | Ingest web content — Catalyst Athletics articles | ✅ Done — 418 articles, 446 chunks, 22 principles |

**Final corpus:** 2,576 chunks, 436 sources, 82 principles. Retrieval eval completed — results in `memory/CHUNKING.md`.

**Notes:**
- Laputin is an image-only scanned PDF — implemented Claude vision API as third-stage OCR fallback in `pdf_extractor.py` instead of Tesseract (no local install required, handles unusual typefaces)
- `--vision` CLI flag added to `pipeline.py` to opt-in to vision OCR (off by default to avoid accidental API costs)
- `--max-pages N` flag added for test runs (limits pages at extraction time, not after)

---

## Phase 6 — Programming Agent ✅ COMPLETE

Design doc: `oly-programming-agent.md`. Athlete schema: `athlete_schema.sql`.

### Phase 6a — Project Restructure + Athlete Schema ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 1 | Create `shared/` and `oly-agent/` directories + `__init__.py` | ✅ Done |
| 2 | Apply `athlete_schema.sql` to DB (9 new tables) | ✅ Done |
| 3 | Verify schema: athletes, athlete_maxes, goals, programs, sessions, exercises, logs | ✅ Done |
| 4 | Seed data: David (intermediate, 89kg, 7 maxes, general_strength goal) | ✅ Done |

### Phase 6b — Models + Shared Modules + ASSESS + PLAN ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 5 | `shared/config.py` — unified Settings with agent fields | ✅ Done |
| 5 | `shared/db.py` — connection helpers (fetch_one, fetch_all, execute) | ✅ Done |
| 5 | `shared/llm.py` — Anthropic client + cost estimation | ✅ Done |
| 5 | `shared/prilepin.py` — zone lookup, rep target computation | ✅ Done |
| 6 | `oly-agent/models.py` — all dataclasses (AthleteContext, ProgramPlan, etc.) | ✅ Done |
| 6 | `oly-agent/phase_profiles.py` — PHASE_PROFILES dict + `build_weekly_targets()` | ✅ Done |
| 6 | `oly-agent/session_templates.py` — SESSION_DISTRIBUTIONS + `get_session_templates()` | ✅ Done |
| 6 | `oly-agent/weight_resolver.py` — `build_maxes_dict()`, `resolve_weights()`, etc. | ✅ Done |
| 7 | `oly-agent/assess.py` — Step 1: DB queries for athlete context | ✅ Done |
| 7 | `oly-agent/plan.py` — Step 2: phase selection + Prilepin targets | ✅ Done |
| 7 | Dry-run `orchestrator.py --athlete-id 1 --dry-run` passes | ✅ Done |

### Phase 6c — RETRIEVE + GENERATE ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 8 | `oly-agent/retrieve.py` — Step 3: fault exercises, templates, vector search | ✅ Done |
| 8 | `vector_loader.py` `similarity_search` — add `id` to SELECT | ✅ Done |
| 9 | `oly-agent/generate.py` — Step 4: prompt builder + LLM call + retry logic | ✅ Done |

### Phase 6d — VALIDATE + EXPLAIN + Orchestrator ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 10 | `oly-agent/validate.py` — Step 5: Prilepin, intensity envelope, principles | ✅ Done |
| 10 | `oly-agent/explain.py` — Step 6: program-level rationale | ✅ Done |
| 11 | `oly-agent/orchestrator.py` — main 6-step pipeline with DB persistence | ✅ Done |
| 11 | Smoke tests pass (phase_profiles, session_templates, weight_resolver, validate) | ✅ Done |

### Phase 6e — Training Logs + Feedback ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 12 | `oly-agent/feedback.py` — ProgramOutcome computation, max promotion | ✅ Done (19/19 tests passing, live smoke test confirmed) |
| 13 | Training log CLI (log a session, log an exercise set) | ✅ Done (`oly-agent/log.py`) |
| 14 | Integration test: full program generation for David | ✅ Done — program_id=3, $0.44, 16 sessions |

### Phase 6f — Warmup Sets + Documentation ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 15 | Add warmup sets (50–60%) to generation prompt | ✅ Done (`generate.py`) |
| 16 | README.md + Mermaid architecture diagram | ✅ Done |
| 17 | Update PROGRESS.md + CLAUDE.md to reflect full project state | ✅ Done |

---

## Phase 7 — OCR + Retrieval Quality ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 1 | Claude vision API as third-stage OCR fallback in `pdf_extractor.py` | ✅ Done |
| 2 | `--vision` and `--max-pages` CLI flags added to `pipeline.py` | ✅ Done |
| 3 | Laputin ingested via vision OCR — 130 pages, 110 chunks, 3 principles | ✅ Done (source_id=499) |
| 4 | Expand `KEYWORD_TO_TOPIC` with 28 Soviet/Eastern-bloc terms | ✅ Done |
| 5 | `retag_chunks.py` — re-tags all DB chunks without re-ingestion | ✅ Done |
| 6 | Applied retag across full corpus — 232 chunks enriched, 11 newly tagged | ✅ Done |
| 7 | Retrieval eval re-run — scores stable, topic filterability improved | ✅ Done |
| 8 | Medvedev ingested via vision OCR — 146 pages, 617 chunks, 1 program template | ✅ Done (source_id=501) |
| 9 | Add Medvedev abbreviation keywords (P. Sn., B. Sq., C+J, etc.) — 540 chunks retagged | ✅ Done |
| 10 | `test_feedback.py` — 19 tests + live smoke test against program 4 | ✅ Done |
| 11 | Everett *Olympic Weightlifting for Sports* ingested — 172 chunks, 11 exercises | ✅ Done (source_id=502) |
| 12 | Retrieval eval expanded from 7 to 14 queries (jerk, clean, recovery, attempt selection, multi-year, frequency, Prilepin) | ✅ Done |
| 13 | All .md files and memory updated to reflect final corpus state | ✅ Done |

---

## Phase 8 — Backlog (post-audit)

Identified via codebase audit. Grouped by priority.

### 8a — Critical Stubs ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| ~~Complete `principle_extractor.py`~~ | `oly-ingestion/processors/principle_extractor.py` | Already implemented — TODO comment was stale. Removed. |
| Implement `_parse_program_template()` | `oly-ingestion/pipeline.py` | ✅ Done — LLM call extracts week/session/exercise structure into JSONB |
| Implement `_parse_exercise()` | `oly-ingestion/pipeline.py` | ✅ Done — heuristic extraction of name, movement_family, category, primary_purpose. 13/13 tests passing (`test_parse_exercise.py`) |

### 8b — Test Coverage Gaps ✅ COMPLETE (core modules)

**oly-agent:**

| Item | File | Notes |
|------|------|-------|
| `test_assess.py` | `oly-agent/tests/test_assess.py` | ✅ 16 tests — max estimation (pure), assess() with mocked DB (3 fetch_one + 2 fetch_all calls); includes estimated-max merge test |
| `test_plan.py` | `oly-agent/tests/test_plan.py` | ✅ 20 tests — phase selection (pure), cold-start overrides, plan shape |
| `test_retrieve.py` | `oly-agent/tests/test_retrieve.py` | ✅ 10 tests — fault lookup, substitutions, Prilepin targets, vector_loader=None path |
| `test_explain.py` | `oly-agent/tests/test_explain.py` | ✅ 13 tests — prompt structure (pure), mocked LLM success + failure |
| `test_orchestrator.py` | `oly-agent/tests/test_orchestrator.py` | ✅ 12 tests — all 6 pipeline steps mocked; dry-run, cost limit, exception handling |
| `test_web_routers.py` | `oly-agent/tests/test_web_routers.py` | ✅ 21 tests — FastAPI TestClient with signed session cookies; auth redirect, CRUD routes, 404s |

**oly-ingestion:**

| Item | File | Notes |
|------|------|-------|
| `test_html_extractor.py` | `oly-ingestion/tests/test_html_extractor.py` | ✅ 12 tests — body extraction, boilerplate removal, element priority, whitespace, unicode |
| `test_pdf_extractor.py` | `oly-ingestion/tests/test_pdf_extractor.py` | ✅ 13 tests + 1 skipped — fallback chain (mocked fitz/pdfplumber), _split_page_responses pure logic; vision OCR gated with INTEGRATION_TESTS=1 |
| `test_epub_extractor.py` | `oly-ingestion/tests/test_epub_extractor.py` | ✅ 9 tests — mocked ebooklib; chapter extraction, script/style removal, empty filtering, import error |
| `test_retag_chunks.py` | `oly-ingestion/tests/test_retag_chunks.py` | ✅ 10 tests + 1 skipped — mocked psycopg2; dry-run, updates, source_id filter; live DB test gated with INTEGRATION_TESTS=1 |

### 8c — Logic / Edge Case Fixes ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| Call `estimate_missing_maxes()` in assess | `oly-agent/assess.py` | ✅ Done — called after `build_maxes_dict()`; estimated maxes merged into `ctx.maxes` |
| Guard `>3 estimated maxes` | `oly-agent/assess.py` | ✅ Done — logs warning if more than 3 maxes are estimated (program weights will be approximate) |
| ~~Guard negative `weeks_to_competition`~~ | `oly-agent/assess.py` | Already clamped: `max(0, delta.days // 7)` — no change needed |
| ~~Fix retry prompt growth~~ | `oly-agent/generate.py` | Not a bug — each retry resets to `original_prompt + one feedback block` |
| ~~Null check `week_cumulative_reps`~~ | `oly-agent/validate.py` | Parameter accepted but unused in function body — no change needed |
| Strengthen deload week guidance | `oly-agent/generate.py` | ✅ Done — adds explicit MUST NOT constraint to prompt for deload sessions |
| Validate `session_exercises` non-empty | `oly-agent/validate.py` | ✅ Done — early return with `is_valid=False` + error message if list is empty; test_validate.py now 26 tests |

### 8d — Retrieval / Knowledge Improvements ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| Unify `CHUNK_TYPE_KEYWORDS` + `KEYWORD_TO_TOPIC` | `oly-ingestion/processors/chunker.py` | ✅ Done — `CHUNK_TYPE_DEFAULT_TOPICS` dict added; applied in `chunk()` so chunk_type always seeds its own topics regardless of keyword coverage |
| Add missing keyword categories | `oly-ingestion/processors/chunker.py` | ✅ Done — RPE/auto-regulation, rest timing, exercise complexity/motor learning, accessory exercise selection |
| Enrich vector queries with athlete context | `oly-agent/retrieve.py` | ✅ Done — queries include athlete level + technical faults |
| Wire substitutions into LLM prompt | `oly-agent/generate.py` | ✅ Done — `## Injury Substitutions` block added to `build_session_prompt()` |
| Increase context snippet length | `oly-agent/generate.py` | ✅ Done — 400 → 600 chars (`SNIPPET_MAX_CHARS` constant) |
| Make vector `top_k` configurable | `oly-agent/retrieve.py` | ✅ Done — reads `settings.vector_search_top_k` (default `VECTOR_SEARCH_DEFAULT_TOP_K = 5`) |

### 8e — Refactoring / Maintainability ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| Extract exercise → intensity_reference mapping | `shared/exercise_mapping.py` (new) | ✅ Done — `EXERCISE_NAME_TO_INTENSITY_REF` + `COMP_LIFT_REFS` moved here; imported by `weight_resolver.py` and `validate.py` |
| Define all Prilepin zones exhaustively | `shared/prilepin.py` | ✅ Done — added `65-70%` zone (optimal 20, range 15-26) to close the gap; `compute_session_rep_target` fallback comment updated |
| Extract magic numbers to constants | `shared/constants.py` (new) | ✅ Done — `PRILEPIN_HARD_CAP_MULTIPLIER`, `DEFAULT_SESSION_DURATION_MINUTES`, `SESSION_DURATION_TOLERANCE`, `VECTOR_SEARCH_DEFAULT_TOP_K`, `SNIPPET_MAX_CHARS`, `MAX_PRINCIPLES_IN_PROMPT`, `WEIGHT_ROUND_INCREMENT`; wired into `validate.py`, `generate.py`, `retrieve.py` |
| Replace bare `except Exception: pass` | `orchestrator.py`, `pipeline.py`, `ingest_web.py` | ✅ Done — all three now log at DEBUG level with `logger.debug(f"... (non-fatal): {e}")` |
| Unify transaction management | `oly-ingestion/pipeline.py` | ✅ Done — `_rollback_connections()` helper method extracted; section error handler calls it instead of inlining the try/except block |

---

## Phase 9 — Post-Scan Fixes (second audit)

Identified via automated codebase scan. Grouped by priority.

### 9a — Bug Fixes (crashes / data integrity) ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| ~~`explain.py` crashes when `active_goal is None`~~ | `oly-agent/explain.py` | Already guarded — `... if athlete_context.active_goal else "general_strength"`. Scan false positive. |
| ~~`explain.py` IndexError on empty `program_sessions`~~ | `oly-agent/explain.py` | Already guarded — `program_sessions[0] if program_sessions else {}`. Scan false positive. |
| Cost limit checked after session generated, not before | `oly-agent/orchestrator.py` | ✅ Fixed — cost guard moved to top of session loop; logs session coordinates before aborting |
| `exercise_id = None` silently stored without downstream validation | `oly-agent/orchestrator.py` | ✅ Fixed — `logger.warning()` lists all unresolved exercise names after `resolve_exercise_ids()` |
| ~~Pre-check athlete existence before opening connection~~ | `oly-agent/orchestrator.py` | Non-issue — `finally` block always closes `conn` regardless of exception; no resource leak |

### 9b — Feature Completion ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| Wire feedback endpoint into web UI | `oly-agent/web/routers/program.py` | ✅ Done — `POST /program/{id}/complete` calls `compute_outcome()` + `save_outcome()`; returns `partials/outcome_summary.html` with adherence, RPE deviation, make rate, max deltas |
| Add missing max update endpoint | `oly-agent/web/routers/program.py` | ✅ Done — `POST /program/maxes/update` upserts `athlete_maxes` via partial-index `ON CONFLICT`; `maxes_table.html` partial with datalist autocomplete |
| Add program delete/supersede endpoint | `oly-agent/web/routers/program.py` | ✅ Done — `POST /program/{id}/abandon` sets status to `abandoned`; button added to `program.html` for draft/active programs |
| Dashboard maxes panel | `oly-agent/web/templates/dashboard.html` | ✅ Done — current maxes table + inline update form added to dashboard; `get_athlete_maxes()` added to dashboard query context |
| Completed program outcome display | `oly-agent/web/templates/program.html` | ✅ Done — existing `outcome_summary` JSONB rendered on program detail page for completed programs |

### 9c — Observability & Ops ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| Add logging to all web routers | `oly-agent/web/routers/` | ✅ Done — `logger = logging.getLogger(__name__)` added to all 4 routers; key actions logged (route hits, 404s, submits, outcomes) |
| Log warning when API keys missing at config load | `shared/config.py` | ✅ Done — `__post_init__` logs `WARNING` if `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is empty |
| Add start/completion timestamps to async job handler | `oly-agent/web/jobs.py` | ✅ Done — `started_at`, `completed_at`, `duration_seconds` stored in job dict; logged on submit, start, and finish/failure; duration shown in generate result UI |
| Cache `Settings` singleton — don't re-parse `.env` per request | `oly-agent/web/deps.py` | ✅ Done — module-level `_settings` singleton; `get_settings()` initialises once; `get_db()` calls `get_settings()` instead of `Settings()` directly |

### 9d — Code Quality ✅ COMPLETE

| Item | File | Notes |
|------|------|-------|
| Consolidate duplicate Settings classes | `shared/config.py` vs `oly-ingestion/config.py` | ✅ Done — `oly-ingestion/config.py` replaced with thin shim that re-exports `Settings` from `shared.config`; all 9 ingestion callers unchanged |
| Clean up `weight_resolver.py` re-export comment | `oly-agent/weight_resolver.py` | ✅ Done — removed misleading `# noqa: F401 (re-exported)` comment; import is local use only, not re-exported |
| Pre-check athlete existence before opening DB connection | `oly-agent/orchestrator.py` | ✅ Non-issue — `finally` block always closes `conn`; no resource leak; confirmed in 9a review |

### 9e — Performance & Hardening ✅ COMPLETE

| Item | Notes |
|------|-------|
| Connection pooling (`psycopg2.pool`) | ✅ Done — `ThreadedConnectionPool` in `shared/db.py`; `init_pool()` / `pooled_connection()`; pool settings (`db_pool_min=1`, `db_pool_max=10`) in `shared/config.py`; `deps.py` initialises pool lazily and uses it for all web requests |
| Rate limiting on web routes | ✅ Done — `slowapi` added to web deps; per-IP limits: generate 2/min, complete/abandon 5/min, activate 10/min, maxes update 20/min, session log 30/min, exercise log 60/min |
| Input size validation on web routes | ✅ Done — `ContentSizeLimitMiddleware` (64 KB POST body cap) in `app.py`; `update_max` uses `Annotated Form` bounds: `exercise_name` max 200 chars, `weight_kg` 0–500 kg |
| ER diagram / schema documentation | ✅ Done — `SCHEMA.md` with two Mermaid ER diagrams + table reference |

### 9f — Multi-user Scaling ✅ COMPLETE

| Item | Notes |
|------|-------|
| Redis-backed rate limiter | ✅ Done — `REDIS_URL` setting in `shared/config.py`; `deps.py` configures `slowapi` with Redis storage if set, falls back to in-memory gracefully. Add `redis>=4.0` package to enable. |
| DB query caching for static tables | ✅ Done — module-level `_exercise_id_cache` in `web/queries/program.py`; first call loads full exercises table in one query, subsequent calls are dict lookups. |
| Multi-athlete session auth | ✅ Done — `SessionMiddleware` + `AuthMiddleware` (redirects unauthenticated to `/login`, HTMX-aware via `HX-Redirect`); `bcrypt` password hashing (direct `bcrypt` library — passlib 1.7.4 is incompatible with bcrypt 5.x); login/logout routes; `get_current_athlete_id` dependency replaces hardcoded `ATHLETE_ID = 1` across all routers; `setup_auth.py` CLI to set credentials; `auth_migration.sql` to add username/password_hash columns. |
| Athlete setup / create-account page | ✅ Done — `GET/POST /setup` (public); multi-section form: account (username/password), profile (level, bodyweight, weight class, sex), training (experience years, sessions/week, duration, equipment checkboxes, technical fault checkboxes, injuries), current maxes (7 key exercises, optional), goal (type, competition date, targets); server-side validation with pre-filled error state; auto-login on creation; linked from login page. |
| Async DB driver (`asyncpg`) | ⏳ Deferred — requires rewriting all `%s` → `$N` positional placeholders in ~24 queries across 4 files, plus migrating the connection API (no cursors, different pool type). FastAPI already runs sync deps in a thread pool so the event loop is not blocked. Low priority until concurrency becomes a bottleneck. |

### 9g — Profile & Settings Page ✅ COMPLETE

| Item | Notes |
|------|-------|
| Profile page (`GET /profile`) | ✅ Done — `web/routers/profile.py` + `web/templates/profile.html`; pre-filled from DB; athlete name in nav links to `/profile` |
| Edit profile fields (`POST /profile/update`) | ✅ Done — name, email, level, biological sex, date of birth, bodyweight, height, weight class, training age, sessions/week, session duration, equipment, technical faults, injuries, notes; session name refreshed on save |
| Change password (`POST /profile/password`) | ✅ Done — requires current password verification; new password + confirm with 8-char min |
| Change username (`POST /profile/username`) | ✅ Done — uniqueness check; requires password confirmation |
| `date_of_birth` migration | ✅ Done — `DATE` column added to athletes table; replaces static `age INTEGER` in all web queries, routers, and templates; both setup and profile forms use `<input type="date">`; age can be computed dynamically from timestamp |

### 9h — Extended Athlete Dimensions ✅ COMPLETE

| Item | Notes |
|------|-------|
| `lift_emphasis` column | ✅ Done — `VARCHAR(20)` (`balanced` / `snatch_biased` / `cj_biased`); dropdown in setup + profile; injected into generation prompt so LLM weights exercise selection toward the preferred lift |
| `strength_limiters` column | ✅ Done — `TEXT[]` (6 options: `squat_limited`, `pull_limited`, `overhead_limited`, `jerk_limited`, `clean_limited`, `positional_strength`); checkboxes in setup + profile; injected into prompt to guide supplemental exercise selection |
| `competition_experience` column | ✅ Done — `VARCHAR(20)` (`none` / `local` / `national` / `international`); dropdown in setup + profile; injected into prompt to calibrate peaking aggressiveness and attempts practice |
| Prompt wiring | ✅ Done — all three fields added to `## Athlete Profile` block in `generate.py:build_session_prompt()` with inline descriptions so the LLM can act on them |

### 9i — Program UI, Maxes Management & Generation Fixes ✅ COMPLETE

| Item | Notes |
|------|-------|
| Intensity range display fix | ✅ Done — program page week summary now shows `min–max` across all session exercises (was first–last, which could show higher % on left); `fmt_pct` filter cast to `float` before `{:g}` format to strip `.00` from `Decimal` DB values |
| Warmup badge | ✅ Done — exercises with "warmup" in `selection_rationale` (case-insensitive) show a blue "Warmup" badge inline in the program exercise table; keyed on rationale not intensity threshold so deload working sets at 65% are not mislabelled |
| Dashboard maxes deduplication | ✅ Done — dashboard now uses `{% include "partials/maxes_table.html" %}` instead of duplicating the inline HTML; single source of truth |
| Estimated maxes in UI | ✅ Done — `get_athlete_maxes()` now appends estimated rows (from `assess.py:estimate_missing_maxes()`) for exercises with no recorded value; displayed greyed-out with an `est.` badge |
| Max delete | ✅ Done — `✕` delete button per recorded max row (HTMX confirm dialog); `POST /program/maxes/delete` endpoint removes the row so the agent falls back to ratio-based estimation |
| Validator warmup false-warnings | ✅ Fixed — intensity-floor warning for competition lifts now skips sets ≤65% (warmup band), eliminating non-blocking noise on every session |
| Equipment constraint in prompt | ✅ Done — `available_equipment` from athlete profile wired into `build_session_prompt()`; MUST NOT rule added when `blocks` absent from equipment list; equipment list shown in athlete profile block |
| `pgvector` added to agent venv | ✅ Done — `pgvector>=0.2.4` added to `oly-agent/pyproject.toml` so vector search works without `oly-ingestion` venv; run `uv sync` after restart to activate |

### 9j — Previous Program Awareness ✅ COMPLETE

| Item | Notes |
|------|-------|
| Phase progression | ✅ Done — `plan.py:_advance_phase()` advances along `general_prep → accumulation → intensification → realization` sequence; realization always cycles back to accumulation; gated by adherence ≥70% and make_rate ≥75%; high RPE deviation (>1.5) blocks advancement even with good make rate |
| Outcome-based volume/intensity adjustments | ✅ Done — `plan.py:_apply_outcome_adjustments()` nudges non-deload week targets: low adherence (<70%) → -10% volume, low make rate (<0.75) → -3% intensity ceiling, high RPE deviation (>1.0) → -5% volume, excellent performance → +2% intensity ceiling |
| Previous program context in LLM prompt | ✅ Done — `generate.py:build_session_prompt()` now includes `## Previous Program` block with phase, duration, adherence %, avg make rate, avg RPE deviation, trends, strength progress (max deltas), and athlete notes |
| Test coverage | ✅ Done — `test_plan.py` expanded from 20 → 35 tests covering phase progression, outcome adjustments, competition date override |

### 9k — Log Session UX & Logging Fixes ✅ COMPLETE

| Item | Notes |
|------|-------|
| Collapsible wellness fields | ✅ Done — date + overall RPE are primary; duration, bodyweight, sleep quality, stress level, notes collapsed into optional `<details>` section |
| Session edit after logging | ✅ Done — `POST /log/{session_id}` upserts; exercise section shows collapsible pre-filled session-edit panel; `update_session_log()` added to queries |
| Clickable session cards on dashboard | ✅ Done — entire session card is an `<a>` tag; logged sessions link to `/log/{session_id}` for editing |
| Logged badge links on program page | ✅ Done — "✓ Logged" badge is now an `<a>` link; sessions-completed progress bar added for active programs |
| Exercise inline edit | ✅ Done — `exercise_log_entry.html` partial; ✏ button toggles pre-filled inline form; `POST /log/{log_id}/exercise/{tle_id}` update endpoint with deviation recompute |
| Exercise delete | ✅ Done — ✕ button with hx-confirm; `DELETE /log/{log_id}/exercise/{tle_id}`; returns full section so prescribed buttons refresh |
| Prescribed exercise deduplication | ✅ Done — prescribed buttons filtered by session_exercise_id of logged exercises; buttons show sets×reps; clicking prefills sets_completed + reps_per_set; Jinja2 namespace fix for scoping bug |
| Save Session button | ✅ Done — green "Save Session ✓" at bottom of exercise section; links back to program page |
| Make rate visibility | ✅ Done — field has inline hint "(successful lifts — e.g. 80 = 4/5)"; shown in logged exercise rows with colour coding |

---

## Phase 10 — Feature Enhancements (planned)

### 10a — Training Intelligence

| Item | Priority | Notes |
|------|----------|-------|
| Exercise history view | ✅ Done | `GET /history?exercise=Snatch` — table of all logged sessions per exercise with weight, sets×reps, prescribed vs actual, RPE+deviation, make rate, notes. Summary strip: total sessions, best weight, most recent, trend (up/flat/down). Clickable from exercise names in log entries and maxes table. `web/routers/history.py` + `web/queries/history.py`. |
| Lift ratio analysis | ✅ Done | Dashboard panel: 4 ratios (snatch/C&J, back squat/snatch, front squat/clean, back squat/C&J) with visual bar gauges showing actual vs target range. Status badges (Good/Low/High) + interpretation text. `get_lift_ratios()` in `dashboard.py` queries, `partials/lift_ratios.html`. |
| PR detection on max update | ✅ Done | `upsert_athlete_max` now fetches previous value before upserting, returns `(is_pr, prev_kg)`. PR triggers an amber trophy banner in the maxes table showing the new weight and kg improvement over previous best. |
| Session readiness warning | Medium | If logged sleep ≤ 2 or stress ≥ 4, surface a soft warning in the exercise log section suggesting intensity reduction. Wellness data already captured. |
| RPE-based weight nudges | Medium | If a session exercise was logged at RPE ≥ 9.5, show a suggestion to reduce weight for that exercise in the next session. Simple flag in exercise history. |

### 10b — Progress Visibility

| Item | Priority | Notes |
|------|----------|-------|
| Weekly volume chart | ✅ Done | Chart.js grouped bar chart (Prescribed vs Logged) per week on program detail page. `get_program_volume_by_week()` in `queries/program.py` computes sets×reps×weight_kg in Python. Chart loaded via CDN, null actual values render as gaps for unlogged weeks. |
| Multi-program history table | ✅ Done | Outcome metrics (adherence %, make rate %, RPE deviation, max gains count) shown inline on program list cards for completed/abandoned programs. `get_all_programs` updated to include `outcome_summary`. |

### 10c — UX & Accessibility

| Item | Priority | Notes |
|------|----------|-------|
| Mobile layout improvements | ✅ Done | Hamburger nav, all grid-cols-2 forms collapse to 1-col on mobile, exercise log entry 2-row layout (name+delete / stats), program/history tables hide low-priority columns progressively (sm/md/lg), maxes form stacks vertically, larger touch targets on prescribed exercise buttons. |
| PDF export | ✅ Done | "Export PDF" button calls `exportPDF()` (opens all accordions + `window.print()`). `@media print` CSS hides UI chrome, restores hidden table columns, removes overflow scroll. Rationale split into sections via `parse_rationale` Jinja2 filter so it flows across pages without blank-page artifacts. Each session day and each rationale section keeps `page-break-inside: avoid`. |
| Calendar view for program | Medium | Week-grid view of sessions (Mon–Sun) as an alternative to the flat session list on the program detail page. |
| Quick log mode | ❌ Won't do | Session RPE and per-exercise RPEs serve different purposes — partial data would skew deviation stats. Full log page is the only path. |

### 10d — Data & Export

| Item | Priority | Notes |
|------|----------|-------|
| Training log CSV export | ✅ Done | `GET /export/log.csv` — flat CSV (one row per exercise) with session wellness, prescribed vs actual weights, RPE, make rate. Download link on profile page. `web/routers/export.py` + `web/queries/export.py`. |
| Program CSV export | ✅ Done | `GET /export/program/{id}.csv` — full program CSV with metadata header block + one row per exercise (week, day, session, sets/reps, weight, intensity, RPE, rest, backoff, notes). "Export CSV" button on program detail page. Filename uses sanitised program name. |
| Principle conflict detection | Low | Flag contradictory principles before generation. Requires pairwise LLM comparison. |
| A/B testing framework | Low | Compare phase/volume strategies across athletes. Needs multi-athlete data. |
| Async DB driver (`asyncpg`) | ✅ Done | Migrated all web queries to asyncpg. `web/async_db.py` added; `shared/db.py` (psycopg2) unchanged for agent pipeline. Pool lifecycle in FastAPI `lifespan` handler. JSONB columns registered via `_init_connection` codec. `get_db()` uses `async with pool.acquire() + conn.transaction()`. |

---

## Phase 11 — Post-Audit Enhancements

### 11a — Tier 1 (High value / low effort)

| Item | Priority | Status | Notes |
|------|----------|--------|-------|
| Make-rate by lift | High | ✅ Done | `feedback.py` groups make_rows by `intensity_reference` → `make_rate_by_lift` dict stored in `outcome_summary` JSONB. Program list cards show per-lift badges. `outcome_summary.html` and `program.html` both show per-lift breakdown below overall make rate. `generate.py` injects per-lift rates into the LLM previous-program prompt. |
| Session duration vs estimated | Medium | ⬜ Planned | Log actual session end time (or total duration). Compare to `estimated_duration_minutes` on program detail. Simple "X min over/under" stat in session log. |
| Video URL in exercise history | Low | ⬜ Planned | Optional `video_url` field per training log exercise entry. Displayed as thumbnail/link in history view. |

### 11b — Tier 2 (Medium effort)

| Item | Priority | Status | Notes |
|------|----------|--------|-------|
| Intensity zone distribution chart | Medium | ⬜ Planned | Chart.js stacked bar per week showing % of prescribed volume in each Prilepin zone (55-65 / 65-70 / 70-80 / 80-90 / 90+). Uses `intensity_pct` from `session_exercises`. |
| Goal progress tracker | Medium | ✅ Done | `GET /profile/goals` (POST upserts active `athlete_goals` row) added to profile page with goal type, target snatch/C&J, competition date/name, notes. Dashboard widget shows goal type badge, competition countdown, snatch + C&J progress bars (current / target / gap). `queries/dashboard.get_goal_progress()`. |
| Phase progression transparency | Medium | ✅ Done | `_compute_phase_verdict()` in `feedback.py` mirrors `plan._advance_phase` + `_apply_outcome_adjustments`. Stored as `phase_verdict` in `outcome_summary` JSONB. Rendered in both `outcome_summary.html` (HTMX on completion) and `program.html` (static on completed program detail): phase arrow, per-threshold ✓/✗ rows, next-program load adjustments block. |

### 11c — Tier 3 (Complex / lower priority)

| Item | Priority | Status | Notes |
|------|----------|--------|-------|
| Exercise complexity self-tuning | Low | ⬜ Planned | Use historical make-rate-by-lift to reduce competition lift frequency when make rate is consistently low (< 60%). Auto-suggest substituting power variants. |
| Attempt selection intelligence | Low | ⬜ Planned | For realization-phase programs, suggest opening/second/third attempt weights based on current max, competition date proximity, and historical make rates. |

---

## Remaining / Optional (pre-Phase 10)

| Task | Priority | Notes |
|------|----------|-------|
| Takano ingestion | ✅ Done | 218 chunks, 16 program templates, source_id=2. Ingested 2026-03-19 after file was obtained. |
| Projected maxes in peaking-phase weight calc | ✅ Done | `weight_resolver.apply_projected_maxes()` overrides snatch/C&J maxes with `target_snatch_kg`/`target_cj_kg` from `athlete_goals` in realization phase; only applies when target > current max (no downgrade); orchestrator computes `effective_maxes` after PLAN and uses it for `resolve_weights()` and prompt; prompt labels the section "Working Maxes" with "← target" annotation; 7 new tests in `test_weight_resolver.py` (25 total). |

---

## Phase 12 — Corpus Expansion + Agent Performance

### 12a — Corpus Expansion ✅ COMPLETE

| Item | Notes |
|------|-------|
| EPUB paragraph extraction fix | `epub_extractor.py` — `get_text(separator='\n')` produced single newlines between `<p>` tags; `_chunk_section` splits on `\n\n` so entire chapters became one chunk. Fixed by inserting `NavigableString('\n\n')` after block-level tags before `get_text(separator='')`. Everett: 198 → 587 chunks, 44 → 76 principles (source_id=1→507). |
| `test_epub_extractor.py` — 4 new paragraph-break tests | 9 → 13 tests: `test_p_tags_produce_double_newlines`, `test_heading_tags_produce_double_newlines`, `test_inline_elements_no_spurious_breaks`, `test_many_p_tags_all_preserved_as_separate_paragraphs` (core regression test) |
| Empty-string guard (three-layer) | (1) skip empty `page_text` before classifier in `pipeline.py`; (2) skip empty `section.content` before routing; (3) filter empty `chunk.content` before embedding in `vector_loader.py` — OpenAI returns 400 JSON parse error for empty strings |
| `test_vector_loader.py` — 2 new empty-content tests | 6 → 8 tests: `test_empty_content_chunks_skipped_before_embed`, `test_mixed_empty_and_valid_chunks_only_valid_embedded` |
| Israetel — *Scientific Principles of Hypertrophy Training* (EPUB) | ✅ Done — 206 chunks, 21 principles, source_id=504, programming profile |
| Starrett — *Becoming a Supple Leopard* (EPUB) | ✅ Done — 137 chunks, 16 principles, source_id=505, theory_heavy profile |
| Dan John — *Intervention* (PDF) | ✅ Done — 266 chunks, 0 principles, source_id=506, programming profile |
| `SOURCE_PROFILE_MAP` additions | Added Israetel (programming), Starrett (theory_heavy), Dan John (programming) to `chunker.py` |
| `KEYWORD_TO_TOPIC` expansion (Phase 12) | 60+ new entries for Israetel vocabulary (`hypertrophy`, `mev`, `mav`, `mrv`, `hard sets`, `reps in reserve`), Starrett vocabulary (`mobility`, `range of motion`, `soft tissue`, `hip mobility`, `ankle mobility`, `thoracic`, `dorsiflexion`), Dan John vocabulary (`loaded carry`, `farmer`, `goblet squat`, `kettlebell`, `hinge`, `gpp`, `conditioning`). `retag_chunks.py` applied — 96 newly tagged chunks. |
| Retrieval eval expanded | 14 → 22 queries; all 8 new queries hit expected topics after KEYWORD_TO_TOPIC expansion |
| **Final corpus** | **3,578 chunks · 151 principles · 439 sources** |

### 12b — Agent Performance Improvements ✅ COMPLETE

Identified via codebase audit of `assess.py`, `retrieve.py`, `generate.py`, `plan.py`, `validate.py`.

**Group A — Dead data (already loaded, not wired up):** ✅ COMPLETE

| Item | Priority | File | Notes |
|------|----------|------|-------|
| Wire `recent_logs` into generate prompt | High | `generate.py` | ✅ `## Recent Training (last 14 days)` section added — compact 1-line per entry (date, exercise, weight×sets, RPE, make rate); capped at `MAX_RECENT_LOGS_IN_PROMPT=10`; null RPE/make_rate guarded |
| Wire `template_references` into prompt | Medium | `generate.py` | ✅ `## Similar Program Templates` section added — shows name + notes only (program_structure JSON excluded to control size); capped at 2 |
| Explicit `make_rate_by_lift` instruction | High | `generate.py` | ✅ Directive line added after lift-by-lift breakdown when any lift < 75%: "→ X make rate was below 75% — reduce intensity on those lifts 3–5% below the week ceiling." |
| Prompt length logging | — | `generate.py` | ✅ Char count + token estimate logged at DEBUG per session; WARNING if > `PROMPT_LENGTH_WARN_CHARS=20_000` (~5k tokens). New constants added to `shared/constants.py`. |
| Test coverage | — | `tests/test_generate_utils.py` | ✅ 13 new tests (15 → 28 total): 5 recent_logs, 4 template_references, 4 make_rate_by_lift directive |

**Group B — Smarter retrieval:** ✅ COMPLETE

| Item | Priority | File | Notes |
|------|----------|------|-------|
| Expand vector search to all faults | Medium | `retrieve.py` | ✅ Removed `[:2]` cap — all faults searched; athletes with 4–5 faults get full fault_correction coverage |
| Include `lift_emphasis` + `strength_limiters` in vector queries | Medium | `retrieve.py` | ✅ Session template queries enriched with lift emphasis (e.g. "snatch biased lift focus") and strength limiters context; balanced adds nothing to keep queries clean |
| Strength-limiter dedicated searches | — | `retrieve.py` | ✅ One vector search per limiter (e.g. "squat strength development for intermediate weightlifter"); `_limited` suffix stripped |
| Wire `fault_correction_chunks` into prompt | — | `generate.py` | ✅ Context block leads with up to 2 fault_correction chunks when faults present, fills remaining from programming_rationale; deduplicates by ID; hard cap 4 chunks |
| Test coverage | — | `test_retrieve.py`, `test_generate_utils.py` | ✅ 9 new retrieve tests (10→19 total), 5 new generate context tests (28→33 total) |

**Group C — Prompt clarity:** ✅ COMPLETE

| Item | Priority | File | Notes |
|------|----------|------|-------|
| Explicit fault → exercise cross-reference | High | `generate.py` | ✅ `fault_block` restructured from family-grouped to fault-grouped: each fault gets its own line listing exercises that address it (`'forward_miss': Snatch Balance (…), Pause Snatch (…)`). Section header updated to prescriptive "Fault Correction Exercises (prescribe ≥1 per session)". |
| Lift ratio context in prompt | Low | `generate.py` | ✅ `## Lift Ratios` section added after `## Current Maxes`: Sn/C&J (target 77–83%), Sn/BS (target 60–67%), C&J/BS (target 75–82%). Each line shows ratio %, target range, and ✓/↑/↓ status. Lines omitted when a max is missing. |
| Test coverage | — | `tests/test_generate_utils.py` | ✅ 10 new tests (33 → 43 total): 5 fault cross-reference, 5 lift ratios |

**Group D — Validation gaps:** ✅ COMPLETE

| Item | Priority | File | Notes |
|------|----------|------|-------|
| RPE target vs intensity check | Medium | `validate.py` | ✅ Check 7 added: warning when ≥90% intensity has RPE <8.0, or 80–90% intensity has RPE <7.0 |
| Warn if zero fault-addressing exercises selected | Medium | `validate.py` | ✅ Check 8 added: warning when athlete has faults and `fault_exercise_names` provided but no prescribed exercise matches; `fault_exercise_names` wired through `generate_session_with_retries` and orchestrator |
| `strength_limiters` coverage check | Low | `validate.py` | ✅ Check 9 added: `_LIMITER_KEYWORDS` dict maps limiter key → exercise name keywords; warns per unaddressed limiter |
| Test coverage | — | `tests/test_validate.py` | ✅ 14 new tests (26 → 40 total): 4 RPE, 4 fault coverage, 6 strength limiter; also fixed pre-existing floor warning test (65% → 68%, above warmup threshold) |

---

**Running the web UI:**
```bash
cd oly-agent
uv sync --extra web
PYTHONUTF8=1 uv run uvicorn web.app:app --reload --port 8080
# Open http://localhost:8080
```

**Running the agent:**
```bash
cd oly-agent
PYTHONUTF8=1 uv run python orchestrator.py --athlete-id 1 --dry-run
PYTHONUTF8=1 uv run python orchestrator.py --athlete-id 1

# Training log
PYTHONUTF8=1 uv run python log.py show    --athlete-id 1
PYTHONUTF8=1 uv run python log.py session --athlete-id 1
PYTHONUTF8=1 uv run python log.py status  --athlete-id 1

# Tests (no DB or API keys needed)
PYTHONUTF8=1 uv run python tests/test_validate.py        # 26 tests
PYTHONUTF8=1 uv run python tests/test_phase_profiles.py  # 15 tests
PYTHONUTF8=1 uv run python tests/test_weight_resolver.py # 18 tests
PYTHONUTF8=1 uv run python tests/test_generate_utils.py  # 15 tests
PYTHONUTF8=1 uv run python tests/test_assess.py          # 16 tests
PYTHONUTF8=1 uv run python tests/test_plan.py            # 35 tests
PYTHONUTF8=1 uv run python tests/test_retrieve.py        # 10 tests
PYTHONUTF8=1 uv run python tests/test_explain.py         # 13 tests
```

**Note:** Run all commands with `PYTHONUTF8=1` on Windows to avoid cp1252 encoding errors.
