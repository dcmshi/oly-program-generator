# TODO — 2026-07-08 Repo Audit Findings

Full 4-track audit (agent pipeline / web / ingestion / infra+docs), run after the
2026-07-03 audit closed out. Every item below was verified against the code at the
cited line before filing. Prefixes: **AGT** agent pipeline · **WEB** web layer ·
**ING** ingestion · **INF** infra/config/docs. Work order: top to bottom.

## 1. High

- [x] **WEB-H1 — IDOR: unscoped `get_exercise_log_entry` leaks other athletes' log rows** ✅ query now takes `log_id` and scopes `WHERE id AND log_id`; both router call sites pass the ownership-checked log_id. Test `test_get_exercise_log_entry_scoped_by_log_id`.
  - `POST /log/{own_log_id}/exercise/{B_tle_id}` with another athlete's sequential `tle_id`: the scoped UPDATE no-ops, but the read-back fetches athlete B's row and renders it (exercise, weights, RPE, `technical_notes`) into the partial; line 182's fetch also feeds B's `session_exercise_id` into `maybe_promote_max` for A. Fix: join through `training_logs` and scope by `log_id` + `athlete_id`.
- [x] **WEB-H2 — `GET /setup` 500s: `form.getlist()` called on a plain dict** ✅ template uses `form.get(...) or []`; the error re-render injects `raw_form.getlist("strength_limiters")` so multi-select picks survive. Tests `test_setup_page_get_renders`, `test_setup_validation_error_rerenders_422`, `test_setup_rerender_preserves_strength_limiters`.
  - Jinja raises `UndefinedError: 'dict object' has no attribute 'getlist'` on both the initial GET and the validation-error re-render — account creation is broken through the UI. No router test covers GET /setup, so the suite is green.
- [x] **WEB-H3 — Date form fields bound to asyncpg DATE params as raw strings → 500** ✅ module-level `_date()` parser in `queries/setup.py` + `queries/profile.py`, applied to all four paths (DOB in create/update, competition_date in both upsert branches). Tests `test_create_athlete_dob_string_becomes_date` + 4 more.
  - asyncpg requires `datetime.date` objects (`DataError: expected a datetime.date instance, got 'str'`). Any submit of `POST /profile/update` with a DOB (the form pre-fills it), `POST /profile/goals` with a competition date, or setup with a DOB → unhandled 500. `queries/setup.py:110` already has a `_date()` parser for `create_goal` — apply it to the other three paths.
- [x] **AGT-H1 — Max-test day collides with the session-template fallback → IntegrityError kills a fully-paid run** ✅ new `orchestrator.compute_max_test_day()` derives the day from `max(template day_numbers) + 1`. Tests `test_max_test_day_*`.
  - Athlete with `sessions_per_week=2` (setup allows `min="1"`) in a max-test phase: templates store days 1–3, `max_test_day=3` violates `program_sessions UNIQUE(program_id, week_number, day_number)` → outer except → run returns None after all LLM cost is spent. Fix: derive from `max(t.day_number for t in session_templates) + 1`.
- [x] **AGT-H2 — "Previous program" ordered by `end_date`, which no code path ever writes** ✅ both halves: `assess.py` orders by `updated_at DESC`, and `feedback.save_outcome` now stamps `end_date = CURRENT_DATE` at completion. Tests `test_previous_program_ordered_by_updated_at`, `test_save_outcome_stamps_end_date`.
  - Every completed program has `end_date=NULL`, so `ORDER BY end_date DESC LIMIT 1` returns an arbitrary row once an athlete has ≥2 completed programs — phase progression, load adjustments, and the prompt's "Previous Program" block silently run off the wrong program. Fix: `ORDER BY updated_at DESC`, or write `end_date` in `feedback.save_outcome()`.
- [x] **ING-H1 — Transient Wayback failures permanently marked as ingested** ✅ new `_wayback_get()` (3 attempts, exponential backoff, 429/5xx/timeouts retryable); `fetch_charniga_snapshot` returns `(article, permanent_skip)`; the ingest loop only persists permanent failures (404/empty doc) — transient failures and <200-char extractions stay pending. Tests `test_charniga_*` (5).
  - Any `fetch_charniga_snapshot` failure — 429/5xx/timeout from web.archive.org, or a wrong content selector yielding <200 chars — lands in the `article is None` branch, which writes the URL to `charniga_progress.json` forever. A rate-limited or bad-selector run silently discards the corpus. Fix: distinguish permanent (404, no content element) from transient failures; only persist permanent ones; add retry/backoff for Wayback.

## 2. Medium

### Web

- [x] **WEB-M1 — `/admin/jobs` 500s on every request** ✅ admin router uses the app's shared `templates` (imported in-handler like every other router). Tests `test_admin_jobs_page_renders`, `test_admin_job_detail_null_cost_renders`, `test_admin_jobs_403_for_non_admin`.
- [x] **WEB-M2 — `_parse_log_date` clamps against *server* today, defeating W-L5** ✅ `_parse_log_date`/`create_session_log`/`update_session_log` take `today=`; the submit router passes `today_in_tz(athlete tz)`. Tests `test_log_date_clamps_against_passed_today_not_server`, `test_create_session_log_threads_today_through`.
- [x] **WEB-M3 — Profile checkbox vocabularies diverge from setup's → silent data loss on every profile save** ✅ canonical lists moved to new `web/options.py` (equipment/faults/limiters/max-exercises), registered as Jinja globals in `app.py`; setup + profile templates loop over the globals; `routers/setup.py` re-exports for back-compat. Test `test_profile_renders_canonical_fault_options`.
- [x] **WEB-M4 — Open-redirect bypass in `_safe_back`** ✅ rejects `//` and `/\` prefixes. Test `test_safe_back_rejects_protocol_relative_urls`.
- [x] **WEB-M5 — Blank sets/weight in the exercise log form → NOT NULL violation → 500** ✅ create/update default sets to the rep-entry count (else 1) and weight to 0 (bodyweight); sets input marked `required` client-side. Tests `test_*_exercise_log_defaults_blank_sets_and_weight`.
- [x] **WEB-M6 — CSV export + history silently drop logs unlinked by program deletion** ✅ LEFT JOINs from `training_logs` outward in both queries; history shows `(deleted program)` / `—` for unlinked rows. Tests `test_full_training_log_uses_left_joins`, `test_exercise_history_uses_left_joins`.

### Agent pipeline

- [x] **AGT-M1 — Cold-start intensity cap inverts floor/ceiling** ✅ cold-start branch clamps `intensity_floor = min(floor, ceiling_cap)`. Test `test_cold_start_floor_never_exceeds_ceiling`.
- [x] **AGT-M2 — Past `competition_date` clamps to `weeks_to_competition=0` → perpetual 1-week realization** ✅ past dates now read as no-competition (None) with a warning log. Tests `test_past_competition_date_treated_as_none`, `test_future_competition_date_still_counts` (old clamp-to-zero test updated to the new contract).
- [x] **AGT-M3 — `"selection_rationale": null` crashes the run after validation passes** ✅ `str(ex.get(...) or "").lower()`. Test `test_attach_chunk_ids_null_rationale_no_crash`.
- [x] **AGT-M4 — Validated exercises can still violate `session_exercises` DB constraints → IntegrityError aborts instead of retrying** ✅ new Check 0 in `validate_session` mirrors the DB: sets/reps integer ≥1, `intensity_pct` in (0, 120] (supersedes the A-L4 warn-only above 120; supramax ≤120 still allowed), unique `exercise_order`. 5 new tests (`test_null_sets_is_error` etc.); the A-L4 absurd-intensity test updated to expect an error.

### Ingestion (Charniga scaffold + pipeline)

- [ ] **ING-M1 — Wayback URL dedup keys on the raw `original` string** (`ingest_web.py:285-296`): `http://`, `https://`, `www.`, `:80`, and trailing-slash variants of one essay each become separate pending URLs — separate fetches, ingestion runs, and paid principle-extraction calls. Key on the CDX `urlkey` (SURT) or a normalized URL.
- [ ] **ING-M2 — `_CHARNIGA_SKIP` misses whole classes of non-article URLs** (`ingest_web.py:119-124`): the homepage, bare date archives (`/2016/`), static WP pages (`/about/`, `/shop/`), and `comment-page-N` pagination all pass; the `<article>`/`soup.body` fallbacks then ingest nav/teaser soup as articles. Require the `/YYYY/slug/` article shape (or tighten the skip list).
- [ ] **ING-M3 — "Latest capture" selects post-lapse parking pages** (`ingest_web.py:291-294`): the domain died Jan 2025 and parking pages answer 200 `text/html` for every path, so the newest capture of a real essay can be junk (short junk → permanently marked ingested via ING-H1). Cap CDX with `to=20241231` or prefer the latest pre-2025 capture.
- [ ] **ING-M4 — `resp.text` mojibake on captures without a charset header** (`ingest_web.py:311`): requests defaults `text/*` to ISO-8859-1, so UTF-8 quotes/em-dashes in old captures become `â€œ` in every embedded chunk. Pass `resp.content` and let BeautifulSoup honor the meta charset.
- [ ] **ING-M5 — Program-parse prompt's `goal` vocabulary violates the DB CHECK** (`pipeline.py:107` instructs `technique|accumulation|intensification`; the constraint allows `technique_focus|hypertrophy|work_capacity|peaking|return_to_sport|general_strength|competition_prep`): the LLM's correct answer triggers a CHECK violation, `load_program` rolls back and returns None (template silently lost), and `pipeline.py:357-358` still counts `stats["programs"] += 1`. Align the vocabularies and check `goal` in the validation guard; count only on success.
- [ ] **ING-M6 — `load_program` has no dedup** (`structured_loader.py:121-139`, plain INSERT; `program_templates` has no UNIQUE): re-running the pipeline on the same source — documented as safe — duplicates every template (Takano: 16 → 32), and the every-10-sections resume checkpoint re-parses (paid) and re-inserts any template in the redo window. Add a `UNIQUE(source_id, name)` + `ON CONFLICT`.

### Infra / config

- [ ] **INF-M1 — `tzdata` missing → the entire W-L5 timezone feature is silently inert on Windows** (`oly-agent/pyproject.toml` has no `tzdata`; `shared/timeutil.py:19-24` swallows `ZoneInfoNotFoundError` → UTC): verified live in this venv — `ZoneInfo('America/New_York')` raises. Every athlete timezone falls back to UTC with no warning, and the W-L5 tests pass anyway because they compare against the same fallback. One-line fix: add `tzdata>=2024.1` (harmless on Linux). Related LOW: WEB-L1 below (validate the free-text field).
- [ ] **INF-M2 — 8 passing no-key test suites are never run by `make test`/CI** (`Makefile:76-98`): missing agent suites `test_config`, `test_formulas`, `test_phase_progression`, `test_log`, `test_web_queries`; missing ingestion suites `test_ingest_web`, `test_llm_helpers`, `test_vector_loader_units` — i.e. all the batch-3/4/5 regression tests. A regression in exactly the audited code merges green. Add them to the two lists.
- [ ] **INF-M3 — Alembic `env.py` blanket-rewrites any `:5432/` → `:5433/`** (`migrations/env.py:48-49`): `make migrate` against any non-compose DB on the standard port silently connects to port 5433 on that host. Only rewrite for `localhost`/`127.0.0.1`, and document `ALEMBIC_DATABASE_URL` in the production docs (currently only in the module docstring).
- [ ] **INF-M4 — Stale migration-head docs: following CLAUDE.md's `alembic stamp 0002_athlete_cost_limit` then `upgrade head` fails** (`CLAUDE.md` Docker section; head is `0005`; `0003` has no `IF NOT EXISTS` so an existing DB with `is_admin` dies with `DuplicateColumn` mid-chain). Change the instruction to `alembic stamp head`; also fix `docs/SETUP.md` "(0000–0003)" and the stale "0002 is head" comments in `0000`/`0001`.
- [ ] **INF-M5 — Root reference SQL files drifted far behind the Alembic chain; `auth_migration.sql` orphaned** (`athlete_schema.sql` athletes table lacks `username`, `password_hash`, `date_of_birth`, `lift_emphasis`, `strength_limiters`, `competition_experience`, `cost_limit_usd`, `is_admin`, `timezone`; file headers still present psql as a setup path): building from the SQL files yields a DB the app crashes on. Delete `auth_migration.sql` and regenerate or clearly mark the reference files OUTDATED.
- [ ] **INF-M6 — `make reset` runs Alembic before Postgres is healthy** (`Makefile:54-57`): after `down -v`, initdb takes seconds; `alembic upgrade head` gets connection-refused nearly every time. Use `docker compose up -d --wait` (healthchecks already exist).

## 3. Low

### Web

- [ ] **WEB-L1 — Timezone is free text with no validation** (`routers/profile.py:56`, `profile.html:130`): typos are saved and silently ignored forever by the UTC fallback. Validate with `ZoneInfo(tz)` at POST time or use a `<select>`.
- [ ] **WEB-L2 — No per-athlete in-flight guard on generation enqueue** (`routers/generate.py:39`): double-click / repeated posts queue N serial ~$0.50 jobs producing duplicate drafts; the UI only polls the newest job.
- [ ] **WEB-L3 — Duplicate `training_logs` race** (`routers/log_session.py:73-80` check-then-insert; no `UNIQUE(session_id)`; `get_existing_log` has no ORDER BY): double-submit → two log rows → adherence >100%, duplicate dashboard cards, edits hit an arbitrary row.
- [ ] **WEB-L4 — `nan`/`inf`/huge floats accepted** (`queries/log_session.py:13-17`, `queries/profile.py:100-110`, `routers/setup.py:146`): `weight_kg=nan` on a max attempt passes the truthiness/comparison gates and stores NaN into `athlete_maxes` (poisoning all future weight resolution); `1e6` overflows NUMERIC → 500. Add `math.isfinite` + range checks.
- [ ] **WEB-L5 — No CSRF tokens; sole defense is `SameSite=Lax`** (`app.py:115-120`). Defense-in-depth gap on all state-changing POSTs, not currently exploitable from third-party sites in modern browsers.
- [ ] **WEB-L6 — 64 KB body cap checks only `Content-Length`** (`app.py:69-72`): a chunked POST bypasses it and `request.form()` buffers unbounded (authenticated memory-DoS).
- [ ] **WEB-L7 — Username-existence timing oracle at login** (`routers/auth.py:38`): unknown usernames skip the ~100 ms bcrypt check. Marginal (setup discloses taken usernames anyway); equalize with a dummy hash.
- [x] **WEB-L8 — `/admin/jobs/{id}` footer sums a nullable column** ✅ (fixed with WEB-M1) `map(attribute=…) | select | sum` drops NULL rows. Covered by `test_admin_job_detail_null_cost_renders`.
- [ ] **WEB-L9 — Client-controlled `session_exercise_id`/`prescribed_*` stored verbatim** (`queries/log_session.py:281,285,310`; `maybe_promote_max` lookup at `:226-231` also unscoped): cross-tenant FK references possible and deviation/make-rate stats fabricable. Self-affecting integrity, not disclosure.

### Agent pipeline

- [ ] **AGT-L1 — Numeric-as-string LLM fields crash weight resolution** (`weight_resolver.py:84`): `"intensity_pct": "75"` passes validation (which coerces via `float()`) then `"75" / 100` raises TypeError in the orchestrator → whole-run abort. Coerce once at parse time.
- [ ] **AGT-L2 — Unguarded `OutcomeSummary.model_validate` in generate** (`generate.py:321` vs the guarded twins at `plan.py:162,213`): an outcome dict plan tolerates with defaults aborts the run at the first prompt build. Add the same try/except.
- [ ] **AGT-L3 — `week_cumulative_reps` is threaded everywhere but never read** (`validate.py:49`, only other mention is the docstring): the weekly Prilepin check promised by the module header is never enforced — 80 reps/week in the 80–90 zone passes silently. Implement the check or delete the dead parameter + docstring claims.
- [ ] **AGT-L4 — `log.py cmd_exercise` inserts NOT NULL columns from optional prompts** (`log.py:317,328`, INSERT at `:343-359` unguarded): pressing Enter at "Sets completed" or "Weight" → constraint violation traceback, entry lost (A-L5 wrapped only `cmd_session`). Mark required or wrap like A-L5.
- [ ] **AGT-L5 — `cmd_status` gates make-rate warnings on RPE presence** (`log.py:443` `AND tle.rpe IS NOT NULL` in the same query that computes `AVG(make_rate)`): make-rate-only rows are excluded, so the <70% warning can never fire for them. Split the filters per metric.
- [ ] **AGT-L6 — `ProgramPlan.sessions_per_week` not synced to the template fallback** (`plan.py:128` vs `session_templates.py:140-143`): athlete at 6/week gets a 5-day program stored as "6 days/wk" (and at 1–2/week this escalates to AGT-H1).
- [ ] **AGT-L7 — Reported "Total cost" and the cost guard exclude the EXPLAIN step's spend** (`orchestrator.py:237,365-369`; `explain.py:54` computes but only logs locally): `total_cost_usd` telemetry understates true spend by the explain call(s), and explain fires even when generation landed at the limit.

### Ingestion

- [ ] **ING-L1 — Charniga title extraction: en-dash suffix survives, URL becomes title on fallback, constant author + `UNIQUE(title, author)` can merge distinct pages into one source** (`ingest_web.py:321,353`; `structured_loader.py:33-35`): strip `–`/`&#8211;` separators too, and populate `sources.url` to disambiguate.
- [ ] **ING-L2 — Progress flushed on loop index, not success count** (`ingest_web.py:570-573` `if i % 10 == 0` where `i` counts all pending incl. failures): a crash loses up to 9 successful ingests from the progress file (re-fetched and re-paid next run) while failures are flushed immediately — persistence priority inverted.
- [ ] **ING-L3 — `load_percentage_schemes` counts `ON CONFLICT DO NOTHING` skips as loaded** (`structured_loader.py:200,215`): same class as the fixed I-L2; use `cursor.rowcount`.
- [ ] **ING-L4 — A failed/empty *first* window aborts all continuation scanning of an oversized program section** (`pipeline.py:523` `if len(content) > CHUNK_SIZE and parsed.get("weeks")`): a 20k-char section opening with 5k of prose legitimately parses to `{}` → the remaining 15k of weeks is never scanned. Let the continuation loop start even when the first window is empty (bounded by `MAX_EMPTY`).

### Infra / docs

- [ ] **INF-L1 — Docs claim `DATABASE_URL` has "no localhost fallback"; `shared/config.py:93` still falls back with only a warning** (`ARCHITECTURE.md:222`, `docs/CONTRIBUTING.md:26` says "✅ Fixed"): a deploy missing the var silently talks to localhost. Fail fast or fix the docs.
- [ ] **INF-L2 — Broken doc links**: `ARCHITECTURE.md:229` → `SECURITY.md` (actual: `docs/design/SECURITY.md`); README's bare `SCALING.md` likewise.
- [ ] **INF-L3 — `docs/CONTRIBUTING.md:101` pg_restore passes a host-side filename as an in-container path** — fails as written; use the `< file` stdin form used at line 105.
- [ ] **INF-L4 — CLAUDE.md's `uv sync --extra dev` omits `--extra web`**, so its own documented uvicorn/web-router-test commands fail after following it (Makefile `sync` is correct).
- [ ] **INF-L5 — `docs/SCHEMA.md:161` claims 5 `prilepin_chart` rows incl. 65–70; seed has 4, and `prilepin.py:10`'s "loaded from DB at startup" is false** (nothing reads the table at runtime).
- [ ] **INF-L6 — docker-compose: no `restart:` policy on any service; PgBouncer pinned to `:latest`** — an OOM-killed service stays down; an upstream release changes behavior with no repo diff.
- [ ] **INF-L7 — Ruff unpinned in both lint entry points** (`Makefile:103`, `ci.yml:18` `uvx ruff check .`): a new ruff release can break CI with zero repo changes. Pin `uvx ruff@<version>`.
- [ ] **INF-L8 — `LOG_FORMAT`/`LOG_LEVEL` env vars override explicit constructor args** (`shared/config.py:112-113`), opposite of every other Settings field's precedence.
- [ ] **INF-L9 — The committed placeholder `SECRET_KEY` passes validation silently** (`.env.example:31`; `config.py:115-122` warns only when empty): a copied-but-unedited .env signs sessions with a public string. Reject the known placeholder (or warn on it).
- [ ] **INF-L10 — Duplicate/conflicting `ebooklib` bounds** (`oly-ingestion/pyproject.toml:21` pins `>=0.20,<1`; the vestigial `epub` extra at `:26-28` declares `>=0.18` unbounded). Delete the extra.

## 4. Addendum — 2026-07-16 fresh pass (jobs/worker, auth, shared modules)

Second-pass sweep of areas the 07-08 audit didn't dig into (`feedback.py`,
`retrieve.py`, `vector_loader.py`, `web/jobs.py`/`worker.py`, `shared/config`/
`timeutil`/`llm`, `web/auth.py`). Three new findings:

- [x] **WEB-M7 — Passwords >72 bytes → unhandled ValueError → 500 on login, setup, and password change** ✅ new `auth.password_too_long()`; `verify_password` fails closed (no stored hash can match) so login/username-confirm return 401/422; setup + password-change validate with a "72 bytes" message before hashing. Tests `test_verify_password_over_72_bytes_returns_false`, `test_login_long_password_401_not_500`, `test_setup_long_password_422_with_message`, `test_profile_password_change_long_new_password_no_500`.
- [x] **WEB-M8 — ARQ `job_timeout=600` cannot actually stop a generation** ✅ `orchestrator.run(deadline=…)` (monotonic) checked between sessions — aborts cleanly with a "# Generation Aborted — Time Limit" rationale; worker passes `job_timeout − 30s` margin and uses `get_running_loop()`. Tests `test_deadline_exceeded_aborts_and_marks_draft`, `test_worker_passes_deadline_to_orchestrator`.
- [ ] **AGT-L8 — Null/empty `exercise_name` makes the retry hint suggest every exercise** (`generate.py:115-118`): `name=""` → `"" in n.lower()` is True for every catalogue name, so the correction prompt says "Did you mean: <first 3 alphabetical>?" for a blank name. Guard the `close` computation on a non-empty name. Cosmetic — the entry still fails validation.

Clean on this pass: `feedback.py`, `retrieve.py` (only the known roadmap items
#18/#19), `vector_loader.similarity_search` (filter/param ordering correct),
`jobs.py` ownership check (W-L4 fix holds), `resolve_redis_dsn`, `shared/llm.py`
(pricing hardcode = roadmap #17.3), `shared/timeutil.py` (INF-M1 is the known gap).

## Notes / non-findings from this pass

- Charniga articles never consult `SOURCE_PROFILE_MAP` — the web path sizes chunks via `for_web_article(word_count)`. Not a bug, but CLAUDE.md's "add to SOURCE_PROFILE_MAP first" rule is a no-op for `--site charniga`; keep in mind for the DB-machine run.
- CDX parsing itself is correct (header row skipped, field order matches `fl=`, repeated `filter` params ANDed, lexicographic timestamp compare valid); the Catalyst path is regression-free from the progress-file parameterization.
- Clean under scrutiny: asyncpg placeholder/JSONB/transaction usage, program/export/history/dashboard ownership scoping (except WEB-H1/WEB-L9), ARQ status-poll ownership, template autoescaping (no `|safe`), secrets scan of tracked files, migration chain 0000→0005 integrity, CI cache config.

---

# Archive — 2026-06-12 Repo Audit Findings (all closed)

Work order: top to bottom. Check items off as they land.

## 1. High — authorization gaps (web layer)

- [x] **Session logging ownership checks** (`oly-agent/web/routers/log_session.py`)
  - `GET /log/{session_id}` never verifies the session belongs to the logged-in athlete
  - `POST /log/{session_id}` same — accepts any session ID
  - `POST /log/{log_id}/exercise` and `POST /log/{log_id}/exercise/{tle_id}` never verify log ownership
  - `DELETE /log/{log_id}/exercise/{tle_id}` has no `athlete_id` dependency at all
  - Fix: add `get_current_athlete_id` where missing; compare `session["athlete_id"]` / `log["athlete_id"]` and 404 on mismatch
- [x] **Program activate missing ownership check** (`oly-agent/web/routers/program.py:52`, `queries/program.py:344`)
  - Router never fetches the program to check ownership; query's second UPDATE is `WHERE id = $1` with no athlete scoping
- [x] **Program complete missing ownership check** (`oly-agent/web/routers/program.py:69`)
  - Fetches program, checks existence, never compares `program["athlete_id"]` to session athlete
- [x] **Scope `abandon_program()` by athlete_id** (`queries/program.py:209`) — router checks ownership; add scoping to the query for defense-in-depth

## 2. Medium — portfolio/devex

- [x] **CI**: add `.github/workflows/ci.yml` running the no-key/no-DB test suites for both subsystems + ruff; add badge to README
- [x] **Linting**: add ruff config to both `pyproject.toml`s, fix any findings, add `make lint` target

## 3. Medium — robustness

- [x] **Anthropic retry/backoff in ingestion** — `processors/principle_extractor.py`, `processors/classifier.py`, `pipeline.py:_llm_call` call `messages.create` bare; add shared retry helper (exponential backoff on rate-limit/overload/timeout)
- [x] **`explain.py` retry + cost logging** — failures currently return a placeholder with no retry and no token logging
- [x] **Consolidate phase-advancement thresholds** — adherence 70%, make-rate 75%, RPE-dev 1.5/1.0, excellent 90%/85% duplicated between `plan.py` and `feedback.py`; move to `shared/constants.py`
- [x] **Orchestrator partial-failure handling** (`orchestrator.py`) — failed session generation stores an empty session and continues silently; mark program status accordingly and log clearly
- [x] **Security headers middleware** (`web/app.py`) — X-Content-Type-Options, X-Frame-Options, Referrer-Policy (+ HSTS when HTTPS_ONLY)
- [x] **Custom error pages** — 404/500 templates + exception handlers in `web/app.py`

## 4. Low priority / polish

- [x] **Prilepin zone boundary overlap** (`shared/prilepin.py:10-16`) — 65 and 70 each match two zones; make boundaries exclusive
- [x] **Floor-clamp `intensity_ceiling`** (`plan.py:267`) — clamps at 100 but not at 0
- [x] **Fix dedup stats math** (`loaders/structured_loader.py:365`) — `chunks_skipped_dedup` derived from values that don't track dedup; have `vector_loader.load_chunks()` return the skipped count
- [x] **Pin dependency major versions** — both `pyproject.toml`s use bare `>=`; add upper bounds for anthropic/openai/fastapi etc.
- [x] **Pagination on unbounded lists** — program list + exercise history have no LIMIT

## Discarded during verification (false positives)

- `history.py` trend division-by-zero — guarded by `len(weights) >= 4`, slice always non-empty
- docker-compose missing `version:` key — obsolete in Compose v2, intentionally omitted
