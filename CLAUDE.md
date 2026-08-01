# Olympic Weightlifting Program Generator — CLAUDE.md

Two subsystems around one Postgres database:

- **`oly-ingestion/`** — books / PDFs / web articles → extract → classify → chunk + embed → `knowledge_chunks` and structured tables. Sync psycopg2, CLI only.
- **`oly-agent/`** — the 6-step generation pipeline (ASSESS → PLAN → RETRIEVE → GENERATE → VALIDATE → EXPLAIN), plus `web/`, a FastAPI + HTMX UI with an ARQ background worker. Pipeline is sync psycopg2; `web/` is async asyncpg.
- **`shared/`** — config, DB helpers, LLM client, Prilepin math, constants, exercise mapping. Imported by both.

## Where things are documented

Read these rather than re-deriving them; update them when the underlying thing changes.

| Doc | Contents |
|---|---|
| `docs/SETUP.md` | Setup, ports, DB ops, ingestion + CLI commands, tests, backup/restore, project tree |
| `ARCHITECTURE.md` | Service diagrams, generation sequence, deployment, production env vars |
| `docs/SCHEMA.md` | ER diagrams + table reference (20 tables) |
| `docs/CORPUS.md` | Ingested sources, chunk-size profiles, `SOURCE_PROFILE_MAP`, and the planned-additions table (what to ingest next, how to obtain each, and the substitute if that route fails) |
| `docs/RETRIEVAL_EVAL.md` | Retrieval-quality baseline — re-run and update after any corpus or retrieval change |
| `docs/CONTRIBUTING.md` | Security audit, scaling checklist, coverage |
| `docs/DB-MACHINE-RUNBOOK.md` | Pending ops that must run on the corpus DB machine |
| `TODO.md` | Current audit findings and their status |
| `docs/design/` | Historical build docs — read-only reference |

---

## Running Things

**Use the root `Makefile`** — it exports `PYTHONUTF8=1` for every recipe.

```bash
make up        # Postgres + PgBouncer + Redis
make migrate   # alembic upgrade head
make web       # uvicorn :8080
make worker    # ARQ worker (separate process — the web UI cannot generate without it)
make test      # all no-key/no-DB tests
make lint      # ruff (pinned version)
```

- Each subsystem has its own `uv` venv. Run `uv run` from that subsystem's directory; never invoke an interpreter by path.
- Running `uv run` directly on Windows: prefix with `PYTHONUTF8=1` or you get cp1252 encoding errors.
- `docker exec` on Windows: drop `-it`, there is no TTY. `docker exec oly-postgres psql …`
- Ports: **5432 = PgBouncer** (all app + ingestion traffic), **5433 = Postgres direct** (psql, Alembic DDL), 6379 = Redis.
- API keys live in `oly-ingestion/.env` (gitignored; template in `.env.example`). `OPENAI_API_KEY` → embeddings, `ANTHROPIC_API_KEY` → LLM, plus `SECRET_KEY`, `POSTGRES_PASSWORD`, `DATABASE_URL`. `shared/config.py` loads the file for both subsystems.
- The Makefile's `AGENT_TESTS` / `INGESTION_TESTS` lists are the source of truth for which suites need no DB or keys. Don't keep test names or counts in prose — they drift.

---

## Content Routing (ingestion)

Content is **routed before chunking** — the classifier sends each section down exactly one path:

| Content type | Destination |
|---|---|
| Prose / rationale | `chunker` → `vector_loader` → `knowledge_chunks` |
| Tables / programs | `structured_loader` → structured tables |
| If/then rules | `principle_extractor` → `programming_principles` |
| Mixed | Both vector store and principle extraction |

---

## Invariants

### Module boundaries

- **The agent pipeline must never import from `web/`.** `orchestrator.py` and everything it imports run standalone without FastAPI installed. The single coupling point is `web/worker.py`, which imports `orchestrator` — keep it unidirectional.
- **`COMP_LIFT_REFS` and `EXERCISE_NAME_TO_INTENSITY_REF` live in `shared/exercise_mapping.py`.** Import them; never redefine inline.
- **Numeric constants live in `shared/constants.py`.** Prilepin cap multiplier, session duration, `top_k`, snippet length, rounding increment, similarity threshold, phase-advancement thresholds — import, don't hardcode.

### Database

- **Two drivers coexist.** `shared/db.py` (psycopg2) serves the agent pipeline, `feedback.py`, and ingestion. `web/async_db.py` (asyncpg) is web-only. Never hand an asyncpg connection to psycopg2 code or vice versa. The one deliberate crossover is `complete_program()` in `queries/program.py`, which opens a dedicated psycopg2 connection because `feedback.py` uses psycopg2 internals.
- **asyncpg placeholders are `$1`, `$2`, …** — not `%s`. Dynamic `IN` clauses use `= ANY($1::int[])` with a Python list as one argument.
- **asyncpg has no `conn.commit()`.** Use `async with conn.transaction():` or the `get_db()` dependency, which wraps it. Commit on clean exit and rollback on exception are automatic.
- **asyncpg needs JSONB codecs registered** (`init=_init_connection` in `async_db.py`), otherwise JSONB columns arrive as raw strings and templates raise `AttributeError: 'str' has no attribute 'adherence_pct'`.
- **`chunk_type` is a Postgres enum** — filter with `chunk_type::text = ANY(...)`, not `chunk_type = ANY(...)`.
- **PgBouncer:** `statement_cache_size=0` is required in `init_async_pool()` (asyncpg's prepared-statement cache doesn't survive transaction pooling). `AUTH_TYPE` must be `scram-sha-256` — `md5`/`trust` make PgBouncer hash the password internally, leaving it unable to complete the SCRAM handshake Postgres 16 requires. The healthcheck must pass `-d ${POSTGRES_DB}`; bare `pg_isready` targets a database named after the user, which PgBouncer won't route.
- **Program deletion:** `training_logs.session_id` and `training_log_exercises.session_exercise_id` have no `ON DELETE CASCADE` and must be NULLed explicitly first. See `delete_program()` in `queries/program.py`.
- **`date_of_birth` replaced `age` on `athletes`.** The `age` column still exists but is never written. Compute age from `date_of_birth`; don't reintroduce `age` into INSERT/UPDATE.
- **`lift_emphasis`, `strength_limiters`, `competition_experience`** must be included in every profile SELECT/UPDATE and setup INSERT — `generate.py:build_session_prompt()` injects all three into the LLM prompt.

### Agent pipeline

- **Prilepin zones cover 55–100%**, including the 65–70% transition band. `get_prilepin_zone()` returns `None` only below 55%; the fallback in `compute_session_rep_target` handles that deload case alone.
- **`plan._advance_phase()`** walks `general_prep → accumulation → intensification → realization`, and realization cycles back to accumulation. Gated on adherence ≥ 70% and make rate ≥ 75%; RPE deviation > 1.5 blocks advancement. `_apply_outcome_adjustments()` nudges volume/intensity on non-deload weeks from the previous `outcome_summary`.
- **`feedback._compute_phase_verdict()` mirrors `plan._advance_phase` + `_apply_outcome_adjustments` exactly.** Both read their thresholds from `shared/constants.py` — change one and you must change the other.
- **`outcome_summary` (JSONB) fields**, all written by `feedback.save_outcome()` and read by `plan.py`, `generate.py`, and templates: `adherence_pct`, `avg_rpe_deviation`, `avg_make_rate`, `make_rate_by_lift`, `avg_weekly_reps`, `rpe_trend`, `make_rate_trend`, `maxes_delta`, `athlete_feedback`, `phase_verdict`. psycopg2 returns it as a dict already — pass it as a parsed dict, don't re-parse.
- **Prompt length budget:** worst case is ~10,500 chars against `PROMPT_LENGTH_WARN_CHARS = 20,000`. The two dominant sections are Available Exercises (~78 chars each) and 4 × 600-char knowledge chunks. If the exercise catalogue passes ~100 entries, add `MAX_EXERCISES_IN_PROMPT` to `shared/constants.py` and slice `retrieval_context.available_exercises` at the `ex_lines` loop in `generate.py`.

### Ingestion pipeline

- **Add every new source title to `SOURCE_PROFILE_MAP` (`processors/chunker.py`) before ingesting it.** Matching is substring-on-title; an unrecognised title silently falls back to the 900-token `programming` profile. Profiles are listed in `docs/CORPUS.md`. This applies to books only — the web path (`ingest_web.py`, both Catalyst and Charniga) sizes chunks dynamically via `for_web_article(word_count)` and never consults the map.
- **Never call `get_text(separator='\n')` on content headed for the chunker.** Both `epub_extractor.py` and `html_extractor.block_text()` insert `NavigableString('\n\n')` after block tags (`p`, `h1`–`h6`, `li`) before `get_text(separator='')`. `_chunk_section` splits on `\n\n`, so single newlines collapse an entire chapter into one oversized chunk (a 146k-char chapter → 1 chunk instead of ~47).
- **EPUB chapters are classified individually.** Each EPUB document item is already a logical section — don't join pages into one string before classifying.
- **Dedup is global by content hash** (SHA-256 of `raw_content`). The same text in two sources is embedded once, so the second source's run reports `chunks_created=0` for those sections. Dedup happens before the embedding call.
- **`vector_loader` guards the embedding call:** empty chunks are skipped (OpenAI 400s on empty strings) and texts over 30k chars are truncated (8192-token limit) with a warning; the chunk is still stored.
- **Section-level errors call `_rollback_connections()`** to reset both loader connections — without it, every later section fails with "transaction is aborted". `ingest_web.py` has an inline equivalent (no class structure).
- **`_llm_classify()` fires only below 0.6 confidence.** Short sections (<50 words) score 0.60 and stay heuristic; plain narrative scores 0.80. Only genuinely ambiguous mid-length sections — a weak signal that cleared no category — score 0.55 and reach the LLM.
- **PDF extraction falls back** PyMuPDF → pdfplumber (under 100 chars extracted) → Claude vision OCR (still under 100 chars **and** `--vision` passed; opt-in because it costs money).
- **Program templates parse incrementally.** `_parse_program_template()` splits sections over 5,000 chars into 5,000/500 overlapping chunks: the first uses `_PROGRAM_PARSE_PROMPT`, the rest `_PROGRAM_CONTINUATION_PROMPT` (weeks with `week_number > last_seen`), deduped through a `seen_weeks` set. `max_tokens=4096` on all program parse calls.
- **`structured_loader.load_program()` validates before INSERT** — `duration_weeks >= 1`, `sessions_per_week` in `[1, 14]` — and infers both from the parsed weeks when the LLM returns 0. It warns and returns `None` rather than tripping a DB check constraint.

### Web UI

- **CSS is compiled and committed, not loaded from a CDN.** `web/static/tailwind.css` is built from `web/tailwind/` by `make css`; every JS/font asset is vendored under `web/static/`. Adding a utility class a template didn't already use means rebuilding, or it won't exist in the output.
- **The palette lives in `web/tailwind/tailwind.config.js`, by role.** Use `paper`/`line`/`ink`/`navy` (and `canvas`), never Tailwind's default `gray` — it is intentionally left cool and unremapped, so a stray `text-gray-500` renders visibly off-theme. `ink` shades are for cream surfaces and `navy-100`/`navy-200` for text on the navy nav; conflating them is what took the nav to 2.58:1. `web/tailwind/build_palette.py` regenerates the accent tints and audits contrast.
- **Do not add `passlib`.** passlib 1.7.4 is incompatible with bcrypt 5.x — `web/auth.py` wraps the `bcrypt` library directly (`bcrypt.hashpw` / `bcrypt.checkpw`).
- **Middleware order:** `add_middleware` wraps in reverse, so `SessionMiddleware` must be added *after* `AuthMiddleware` to run outermost. The session has to be populated before the auth guard reads it.
- **HTMX auth expiry:** `AuthMiddleware` checks the `HX-Request` header and returns an `HX-Redirect` response header with status 200 instead of a 302, so HTMX does a full-page redirect rather than swapping the login page into a fragment.
- **Jinja `{% set %}` is loop-scoped.** To accumulate across iterations use `{% set ns = namespace(key=[]) %}` and `ns.key = ns.key + [item]` — see `exercise_log_section.html`.
- **Custom Jinja filters are registered in `web/app.py`:** `urlencode` (`quote_plus`, not a Jinja builtin) and `parse_rationale` (splits the rationale on `#` headings into `{heading, body}` dicts, which is also what makes per-section page breaks work in PDF export).
- **`exercise_log_entry.html` needs `session_id` from both call sites** — included from `exercise_log_section.html` (set before the loop) and rendered directly by `update_exercise_log` (passed explicitly). Keep both paths in sync.
- **`upsert_athlete_max()` returns `(is_pr, prev_kg)`** — always destructure; the program router feeds `is_pr` to the maxes partial for the PR banner.
- **PDF export is browser print.** `exportPDF()` opens every `<details>` before `window.print()`; `@media print` CSS handles chrome (`print:hidden`), restores responsive-hidden columns (`print:table-cell`), and sets `page-break-inside: avoid`.
- **Responsive conventions:** forms use `grid grid-cols-1 sm:grid-cols-2`; tables hide columns progressively with `hidden sm:table-cell` / `md:` / `lg:`; the exercise log entry toggles a mobile and desktop variant with `hidden sm:flex` / `sm:hidden`.

### Tests

- **Router tests authenticate with signed session cookies, not middleware patching.** `BaseHTTPMiddleware` captures `self.dispatch_func = self.dispatch` at construction, so `patch.object(AuthMiddleware, "dispatch", …)` has no effect after the app is built. Build the cookie with `itsdangerous.TimestampSigner(secret).sign(base64(json(session)))` and set it on the TestClient jar; `get_settings().secret_key` gives the live key post-init.
- **The `get_db` override must be `async def _db_override(): yield mock_conn`.** A sync generator won't satisfy async dependency injection.
- **The asyncpg pool is created in the FastAPI `lifespan` handler** inside a try/except so `TestClient` works with no Postgres running. Tests override `get_db`, so `get_async_pool()` is never called.
- **`INTEGRATION_TESTS=1` gates tests needing real APIs or a live DB** inside otherwise-mocked files, via `_integration_only()` raising `_Skip`. Used in `test_pdf_extractor.py`, `test_retag_chunks.py`, `test_orchestrator.py`, and `test_web_routers.py`.
