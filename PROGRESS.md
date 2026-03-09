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
| 20 | Ingest Takano — principles extraction focus | ⏳ File not available; substituted Drechsler |
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

## Remaining / Optional

| Task | Priority | Notes |
|------|----------|-------|
| Takano ingestion | ❌ Permanently skipped | File unavailable online. Programming gap closed by *Olympic Weightlifting for Sports* (Everett) + Catalyst. |

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
| `test_orchestrator.py` | `oly-agent/tests/test_orchestrator.py` | ⏳ Deferred — requires full pipeline mock; lower value vs above |
| `test_web_routers.py` | `oly-agent/tests/test_web_routers.py` | ⏳ Deferred — FastAPI TestClient setup; lower priority than logic tests |

**oly-ingestion:**

| Item | File | Notes |
|------|------|-------|
| `test_html_extractor.py` | `oly-ingestion/tests/test_html_extractor.py` | ✅ 12 tests — body extraction, boilerplate removal, element priority, whitespace, unicode |
| `test_pdf_extractor.py` | `oly-ingestion/tests/test_pdf_extractor.py` | ⏳ Deferred — needs real PDF fixtures; vision OCR tests require API key |
| `test_epub_extractor.py` | `oly-ingestion/tests/test_epub_extractor.py` | ⏳ Deferred — needs real EPUB fixture or ebooklib mock |
| `test_retag_chunks.py` | `oly-ingestion/tests/test_retag_chunks.py` | ⏳ Deferred — needs live DB |

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

### 9d — Code Quality

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
| A/B testing framework for program strategies | Future feature |
| CSV/JSON training log export endpoint | Future feature |
| Principle conflict detection | Future feature |

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
PYTHONUTF8=1 uv run python tests/test_plan.py            # 20 tests
PYTHONUTF8=1 uv run python tests/test_retrieve.py        # 10 tests
PYTHONUTF8=1 uv run python tests/test_explain.py         # 13 tests
```

**Note:** Run all commands with `PYTHONUTF8=1` on Windows to avoid cp1252 encoding errors.
