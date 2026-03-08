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
| 21 | Ingest Laputin — OCR pipeline, table parsing | ⚠️ Skipped — image-only PDF, needs Tesseract OCR |
| 22 | Ingest web content — Catalyst Athletics articles | ✅ Done — 418 articles, 446 chunks, 22 principles |

**Final corpus:** 1,681 chunks, 433 sources. Retrieval eval completed — results in `memory/CHUNKING.md`.

---

## Phase 6 — Programming Agent 🔄 IN PROGRESS

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
| 12 | `oly-agent/feedback.py` — ProgramOutcome computation, max promotion | ✅ Done (needs live test) |
| 13 | Training log CLI (log a session, log an exercise set) | ✅ Done (`oly-agent/log.py`) |
| 14 | Integration test: full program generation for David | ✅ Done — program_id=3, $0.44, 16 sessions |

### Phase 6f — Warmup Sets + Documentation ✅ COMPLETE

| Step | Task | Status |
|------|------|--------|
| 15 | Add warmup sets (50–60%) to generation prompt | ✅ Done (`generate.py`) |
| 16 | README.md + Mermaid architecture diagram | ✅ Done |
| 17 | Update PROGRESS.md + CLAUDE.md to reflect full project state | ✅ Done |

---

## Remaining / Optional

| Task | Priority | Notes |
|------|----------|-------|
| Integration test with warmup sets (re-run generation) | High | Verifies warmup prescription in output |
| Live test `feedback.py` (mark program complete, promote maxes) | Medium | Needs logged sessions first |
| Tesseract OCR → Laputin ingestion | Low | `winget install UB-Mannheim.TesseractOCR` |
| Takano ingestion | Low | File not yet available; use `programming` profile |
| Web UI | Future | CLI is the current interface |

**Running the agent:**
```bash
cd oly-agent
PYTHONUTF8=1 "D:/oly-program-generator/oly-ingestion/.venv/Scripts/python.exe" orchestrator.py --athlete-id 1 --dry-run
PYTHONUTF8=1 "D:/oly-program-generator/oly-ingestion/.venv/Scripts/python.exe" orchestrator.py --athlete-id 1

# Training log
PYTHONUTF8=1 "D:/oly-program-generator/oly-ingestion/.venv/Scripts/python.exe" log.py show --athlete-id 1
PYTHONUTF8=1 "D:/oly-program-generator/oly-ingestion/.venv/Scripts/python.exe" log.py session --athlete-id 1
PYTHONUTF8=1 "D:/oly-program-generator/oly-ingestion/.venv/Scripts/python.exe" log.py status --athlete-id 1
```

**Note:** Run all commands with `PYTHONUTF8=1` on Windows to avoid cp1252 encoding errors.
