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

- [x] **ING-M1 — Wayback URL dedup keys on the raw `original` string** ✅ CDX query now requests `urlkey` (SURT) and dedupes on it, keeping the newest capture's original URL. Test `test_cdx_dedupes_variants_and_caps_pre2025`.
- [x] **ING-M2 — `_CHARNIGA_SKIP` misses whole classes of non-article URLs** ✅ positive `_CHARNIGA_ARTICLE_RE` requires the `/YYYY/slug/` (or `/YYYY/MM/slug/`) permalink shape. Test `test_cdx_requires_article_shaped_urls`.
- [x] **ING-M3 — "Latest capture" selects post-lapse parking pages** ✅ CDX query capped with `to=20241231`. Covered by `test_cdx_dedupes_variants_and_caps_pre2025`.
- [x] **ING-M4 — `resp.text` mojibake on captures without a charset header** ✅ `BeautifulSoup(resp.content, "lxml")` — BS4 detection honors the meta charset. Test `test_charniga_utf8_without_charset_header_no_mojibake`.
- [x] **ING-M5 — Program-parse prompt's `goal` vocabulary violates the DB CHECK** ✅ prompt lists the CHECK vocabulary; `load_program` normalizes legacy labels via `_GOAL_SYNONYMS` (unknown → general_strength, warned); pipeline counts `stats["programs"]` only when load returns an id. Tests `test_program_parse_prompt_goal_line_matches_db_check`, `test_load_program_normalizes_legacy_goal`.
- [x] **ING-M6 — `load_program` has no dedup** ✅ **REWORKED after audit2-H1**: the first version keyed on `(source_id, name)`, but names are auto-generated per source ("Program from {title}") — it would have deleted 15 of Takano's 16 distinct templates and capped every source at one template. Migration `0006` now builds a content-aware identity: unique expression index `(source_id, name, md5(program_structure::text)) NULLS NOT DISTINCT`, dedup-DELETE compares structure too, and it defensively drops the old constraint if a DB applied the first version. `load_program` conflicts on the same expression tuple; template names now include the section title when available. Tests `test_load_program_dedup`, `test_load_program_same_name_distinct_structure_both_load`. **DB machine: safe to `alembic upgrade head` (only exact duplicates are removed).**

### Infra / config

- [x] **INF-M1 — `tzdata` missing → the entire W-L5 timezone feature is silently inert on Windows** ✅ `tzdata>=2024.1` added to oly-agent deps (synced: tzdata 2026.3). Test `test_tzdata_available`.
- [x] **INF-M2 — 8 passing no-key test suites are never run by `make test`/CI** ✅ all 8 added to the Makefile lists (CI runs make test-agent/test-ingestion). Verified: full lists pass under pytest (394 agent / 142 ingestion). Meta-test `test_makefile_runs_all_no_key_suites`.
- [x] **INF-M3 — Alembic `env.py` blanket-rewrites any `:5432/` → `:5433/`** ✅ URL logic extracted to importable `migrations/db_url.py`; rewrite fires only for `@localhost:5432/`/`@127.0.0.1:5432/`; `ALEMBIC_DATABASE_URL` documented in docs/SETUP.md. Verified `alembic current` still works. Test `test_migration_url_rewrites_only_local_hosts`.
- [x] **INF-M4 — Stale migration-head docs** ✅ CLAUDE.md instructs `alembic stamp head` (with the why); 0000/0001 docstrings no longer hardcode a head; SETUP.md tree points at `alembic history`.
- [x] **INF-M5 — Root reference SQL files drifted far behind the Alembic chain** ✅ both files now carry an "⚠ OUTDATED REFERENCE ONLY — DO NOT APPLY" banner pointing at Alembic (`auth_migration.sql` was already gone).
- [x] **INF-M6 — `make reset` runs Alembic before Postgres is healthy** ✅ `up -d --wait` in the reset target. Covered by the Makefile meta-test.

## 3. Low

### Web

- [x] **WEB-L1 — Timezone is free text with no validation** ✅ `ZoneInfo(tz)` validated at POST /profile/update; unknown zones 422 with a message. Test `test_profile_update_rejects_unknown_timezone`.
- [x] **WEB-L2 — No per-athlete in-flight guard on generation enqueue** ✅ `SET NX EX 660` guard (`gen_inflight:{athlete_id}`) in `submit_generation` → `GenerationInFlightError` → 409 fragment; guard cleared when the owner's status poll sees a terminal state. Tests `test_submit_generation_rejects_concurrent`, `test_submit_generation_guard_then_enqueue`, `test_job_status_terminal_clears_inflight`, `test_generate_run_conflict_when_inflight`.
- [x] **WEB-L3 — Duplicate `training_logs` race** ✅ migration `0007` (merges legacy duplicates, partial UNIQUE on `session_id`; applied → head 0007); `create_session_log` upserts via `ON CONFLICT`; `get_existing_log` deterministic (`ORDER BY id`). Test `test_session_log_insert_upserts_on_session_conflict`. **DB-machine note: run `alembic upgrade head` there too (0006+0007).**
- [x] **WEB-L4 — `nan`/`inf`/huge floats accepted** ✅ new `web/formparse.py` (`parse_float`: finite + `< 10000` bound, `parse_int`) replaces all five copy-pasted `_float`/`_int` helpers (log_session, profile ×2, setup ×2) and the setup maxes loop; `/program/maxes/update` was already safe (`Form(gt=0, le=500)`). Tests `test_parse_float_rejects_nan_inf_huge`, `test_update_profile_nan_bodyweight_stored_as_null`.
- [x] **WEB-L5 — No CSRF tokens; sole defense is `SameSite=Lax`** ✅ `OriginCheckMiddleware`: POST/PUT/PATCH/DELETE with an Origin header mismatching Host (or `null`) → 403; no-Origin requests pass (defense-in-depth, not the only line). Tests `test_cross_origin_post_rejected`, `test_same_origin_post_allowed`.
- [x] **WEB-L6 — 64 KB body cap checks only `Content-Length`** ✅ `ContentSizeLimitMiddleware` rewritten as pure ASGI: header check + bounded pre-read of streamed chunks, replayed to the app; oversized chunked bodies → 413 before the app sees them. Test `test_chunked_body_over_cap_rejected`.
- [x] **WEB-L7 — Username-existence timing oracle at login** ✅ precomputed `_TIMING_DUMMY_HASH`; unknown usernames burn the same bcrypt verify. Test `test_login_unknown_user_still_runs_bcrypt`.
- [x] **WEB-L8 — `/admin/jobs/{id}` footer sums a nullable column** ✅ (fixed with WEB-M1) `map(attribute=…) | select | sum` drops NULL rows. Covered by `test_admin_job_detail_null_cost_renders`.
- [x] **WEB-L9 — Client-controlled `session_exercise_id`/`prescribed_*` stored verbatim** ✅ `create_exercise_log` links `session_exercise_id` only when it belongs to the log's session (else NULL); the promote path uses the stored/validated id, not the raw form value. `prescribed_*` remain self-affecting stats (accepted). Tests `test_create_exercise_log_drops_foreign_session_exercise_id`, `..._keeps_valid_session_exercise_id`.

### Agent pipeline

- [x] **AGT-L1 — Numeric-as-string LLM fields crash weight resolution** ✅ `_coerce_numeric_fields()` in `parse_llm_response` coerces sets/reps/rest/order (int) and intensity/rpe_target (float) once at parse time; garbage → None so validation flags it. Tests `test_parse_coerces_numeric_strings`, `test_parse_unparseable_numeric_becomes_none`.
- [x] **AGT-L2 — Unguarded `OutcomeSummary.model_validate` in generate** ✅ same try/except-defaults guard as plan's. Test `test_prompt_tolerates_malformed_outcome_summary`.
- [x] **AGT-L3 — `week_cumulative_reps` is threaded everywhere but never read** ✅ Check 1b: warns when the week's running comp-lift total exceeds the plan's weekly budget × `WEEKLY_REP_BUDGET_TOLERANCE` (1.25, in constants). Tests `test_weekly_budget_overshoot_warns`, `test_weekly_budget_within_tolerance_no_warning`.
- [x] **AGT-L4 — `log.py cmd_exercise` inserts NOT NULL columns from optional prompts** ✅ `_apply_notnull_defaults()` mirrors the web defaults (sets from rep entries, weight 0). Test `test_exercise_defaults_for_blank_prompts`.
- [x] **AGT-L5 — `cmd_status` gates make-rate warnings on RPE presence** ✅ WHERE now accepts either metric (`rpe IS NOT NULL OR make_rate IS NOT NULL`); AVG ignores NULLs per column. Test `test_status_query_not_gated_on_rpe`.
- [x] **AGT-L6 — `ProgramPlan.sessions_per_week` not synced to the template fallback** ✅ plan stores `len(session_templates)`. Test `test_sessions_per_week_matches_template_fallback`.
- [x] **AGT-L7 — Reported "Total cost" and the cost guard exclude the EXPLAIN step's spend** ✅ `explain()` returns `(rationale, in_tokens, out_tokens)`; orchestrator adds its cost to `cumulative_cost` and skips the call entirely (with a self-explanatory rationale) when the limit was already reached. Tests `test_explain_skipped_when_cost_limit_reached` + updated explain tests.

### Ingestion

- [x] **ING-L1 — Charniga title extraction: en-dash suffix survives; `sources.url` never populated** ✅ separator class extended to `[-|–—]`; `upsert_source(url=…)` stores the URL on insert and backfills NULLs on existing rows; the web path passes `article["url"]`. Tests `test_charniga_title_strips_endash_suffix`, `test_ingest_article_passes_url_to_source`.
- [x] **ING-L2 — Progress flushed on loop index, not success count** ✅ `successes` counter drives the every-10 flush. Test `test_progress_flush_counts_successes`.
- [x] **ING-L3 — `load_percentage_schemes` counts `ON CONFLICT DO NOTHING` skips as loaded** ✅ `loaded += cursor.rowcount`. Test `test_load_percentage_schemes_dedup_counts_rowcount` (live DB).
- [x] **ING-L4 — A failed/empty *first* window aborts all continuation scanning of an oversized program section** ✅ continuation gate no longer requires first-window weeks (`parsed.setdefault("weeks", [])`); still bounded by `MAX_EMPTY`. Test `test_first_window_empty_continuation_still_scans`.

### Infra / docs

- [x] **INF-L1 — Docs claim `DATABASE_URL` has "no localhost fallback"** ✅ docs now describe the real behavior (dev fallback + logged warning; production must set it): ARCHITECTURE.md env table + CONTRIBUTING M3 row re-marked "Mitigated".
- [x] **INF-L2 — Broken doc links** ✅ ARCHITECTURE.md → `docs/design/SECURITY.md`. (README no longer contains a bare SCALING.md link — already resolved earlier.)
- [x] **INF-L3 — `docs/CONTRIBUTING.md` pg_restore passes a host-side filename as an in-container path** ✅ stdin form.
- [x] **INF-L4 — CLAUDE.md's `uv sync --extra dev` omits `--extra web`** ✅ added.
- [x] **INF-L5 — `docs/SCHEMA.md` prilepin row count + `prilepin.py` "loaded from DB" claim** ✅ SCHEMA.md says 4 seed rows and names `shared/prilepin.py` as the runtime source (with the 65–70 band); the false docstring comment rewritten.
- [x] **INF-L6 — docker-compose: no `restart:` policy; PgBouncer on `:latest`** ✅ `restart: unless-stopped` on all three services; PgBouncer pinned to `edoburu/pgbouncer:v1.25.2-p0` (newest published tag; stack verified healthy on it).
- [x] **INF-L7 — Ruff unpinned in both lint entry points** ✅ pinned to 0.15.22 (`RUFF_VERSION` in Makefile, mirrored in ci.yml).
- [x] **INF-L8 — `LOG_FORMAT`/`LOG_LEVEL` env vars override explicit constructor args** ✅ blank-default + `or` resolution (arg > env > default), matching every other field. Test `test_log_env_does_not_override_explicit_args`.
- [x] **INF-L9 — The committed placeholder `SECRET_KEY` passes validation silently** ✅ known placeholder rejected with a warning; a random key replaces it. Test `test_placeholder_secret_key_rejected`.
- [x] **INF-L10 — Duplicate/conflicting `ebooklib` bounds** ✅ vestigial `epub` extra deleted.

## 4. Addendum — 2026-07-16 fresh pass (jobs/worker, auth, shared modules)

Second-pass sweep of areas the 07-08 audit didn't dig into (`feedback.py`,
`retrieve.py`, `vector_loader.py`, `web/jobs.py`/`worker.py`, `shared/config`/
`timeutil`/`llm`, `web/auth.py`). Three new findings:

- [x] **WEB-M7 — Passwords >72 bytes → unhandled ValueError → 500 on login, setup, and password change** ✅ new `auth.password_too_long()`; `verify_password` fails closed (no stored hash can match) so login/username-confirm return 401/422; setup + password-change validate with a "72 bytes" message before hashing. Tests `test_verify_password_over_72_bytes_returns_false`, `test_login_long_password_401_not_500`, `test_setup_long_password_422_with_message`, `test_profile_password_change_long_new_password_no_500`.
- [x] **WEB-M8 — ARQ `job_timeout=600` cannot actually stop a generation** ✅ `orchestrator.run(deadline=…)` (monotonic) checked between sessions — aborts cleanly with a "# Generation Aborted — Time Limit" rationale; worker passes `job_timeout − 30s` margin and uses `get_running_loop()`. Tests `test_deadline_exceeded_aborts_and_marks_draft`, `test_worker_passes_deadline_to_orchestrator`.
- [x] **AGT-L8 — Null/empty `exercise_name` makes the retry hint suggest every exercise** ✅ `close` computed only for non-empty names. Test `test_validate_blank_name_no_suggestions`.

Clean on this pass: `feedback.py`, `retrieve.py` (only the known roadmap items
#18/#19), `vector_loader.similarity_search` (filter/param ordering correct),
`jobs.py` ownership check (W-L4 fix holds), `resolve_redis_dsn`, `shared/llm.py`
(pricing hardcode = roadmap #17.3), `shared/timeutil.py` (INF-M1 is the known gap).

## 5. Audit 2 — 2026-07-17 post-fix verification pass (3 parallel agents) — ALL FIXED

Fresh 3-track audit run immediately after the fix campaign landed, focused on
regressions introduced by the fixes themselves. 1 HIGH, 7 MEDIUM, 18 LOW filed;
all verified, fixed with red-first tests, in commits `026f667`, `c6af397`,
`174bb33`, and the web batch.

- [x] **H1 (ingestion) — migration 0006 would have DESTROYED distinct templates**: names are auto-generated per source, so the `(source_id, name)` dedup key would have collapsed Takano's 16 templates into 1 on the corpus DB. Reworked to a content-aware identity (`md5(program_structure)` in the key) BEFORE any corpus DB applied it; names now include section titles. ✅ `026f667`
- [x] **M (ingestion ×3)**: month archives passed the article regex; port-qualified CDX originals were dropped; `sources.url` never actually disambiguated same-titled essays. ✅ `c6af397`
- [x] **M (agent ×2)**: `exercise_order` NOT NULL wasn't mirrored in Check 0 (coerced-None killed paid runs at save); log.py CLI deviation math crashed on Decimal and ran after the NOT-NULL defaults. ✅ `174bb33`
- [x] **M (web ×2)**: max-promotion re-parsed weight with bare `float()` — `nan` bypassed the WEB-L4 guard into `athlete_maxes`; `_safe_back` was bypassable via TAB/LF/CR smuggling. ✅ web batch
- [x] **L (18 across tracks)**: Catalyst transient-failure permanence, continuation KeyError, athlete_level CHECK, db_url host forms, `make up --wait`, null-dims guard, bool/fractional coercion, warmup reps in volume accounting, unreturnable pct error, prompt/rationale sessions-per-week, cmd_status sample sizes, stale-goal projection, NULL-source dedup, parse_int bounds + CHECK-range guards, profile re-render input loss, in-flight guard leak on enqueue failure. ✅ all in the three audit2 commits

Clean under scrutiny (all three agents): middleware order + body-cap replay, Origin check, 0007 dup-merge SQL + partial-index inference, guard TTL ordering, explain tuple consumers, cost accounting, CDX parsing, `_get_with_retry` semantics, options.py template wiring, Makefile/CI/compose pins.

## 6. Audit 3 — 2026-07-17 fix-the-fixes pass (2 agents, scoped to the audit2 diff) — ALL FIXED

Third pass over ONLY the audit2 fix commits. 1 HIGH, 3 MEDIUM, 6 LOW — every one
a bug **in an audit2 fix itself** (regression or incomplete fix). All fixed
red-first in one commit.

- [x] **H1 — the profile re-render fix 500'd on its own target path**: merging raw form strings into the template context crashed `athlete.date_of_birth.isoformat()` whenever a DOB was submitted (the form always pre-fills it). The fix's test omitted exactly that field. ✅ DOB parsed to a date before merging; test now submits one.
- [x] **M1 — warmup exclusion used the 65% sub-floor cutoff, silently deleting the entire 55–65 Prilepin zone**: 120 reps @62% passed with zero errors on low-intensity weeks; zone "55-65" became dead code. ✅ new `WARMUP_VOLUME_EXCLUSION_PCT = 60` (the mandated warmup band only); test pins 62% counting.
- [x] **M2 — `parse_int` bounds never applied at `sessions_per_week`** (profile + setup; CHECK 1..14) — the exact 500 the commit claimed fixed. ✅ `lo=1, hi=14` at both call sites.
- [x] **M (ingestion) — slug disambiguation could STILL violate UNIQUE(title, author)** (repeated slug across years, 300-char boundary, CDX url-variant drift), and the exception aborted the entire ingest run. ✅ collision re-check + deterministic url-hash fallback; ingest loop now isolates per-URL failures (rollback + continue).
- [x] **L (6)** — rpe-deviation warning gated on the wrong count (`COUNT(rpe)` vs the averaged `rpe_deviation`); `reps_per_set` entries unbounded into INT[] overflow (web ×2 + CLI); guard leak on `CancelledError` + unguarded cleanup delete; explicit-null template dims bypassing inference; first-window null `week_number` TypeError; 403 treated as permanent (WAF false-drop). ✅ all fixed.

Clean under scrutiny (audit3 agents): reworked 0006 verified live against pg16 (NULLS NOT DISTINCT arbiter inference, dedup DELETE semantics, downgrade from both states), `_CHARNIGA_ARTICLE_RE` 17-case probe matrix, fetch_article tuple contract, db_url 11-URL matrix, max-test-session/Check-0 interaction, `_safe_back` after the control-char fix, promotion parse_float consistency.

## 7. Audit 4 — 2026-07-18 pre-ingestion pass (code review + DB-machine rehearsal) — ALL FIXED

Two agents: a fix-the-fixes review of the audit3 diff, and an operational
rehearsal of the DB-machine runbook (real migration round-trip over
corpus-shaped seed data + live Catalyst/Wayback dry-run probes).

- [x] **Code review of the audit3 diff: CLEAN — zero findings at any severity.** Every fix verified, every new test confirmed to flip red pre-fix. The four-pass severity convergence held.
- [x] **Rehearsal F1 (HIGH) — CDX enumeration failed live with a Wayback 503, no retry, and 0 URLs masqueraded as a completed run** ✅ `collect_charniga_urls` goes through `_get_with_retry` (params support added); Wayback URLs switched to https (plain http failed live where https succeeded first try); a 0-URL collection now aborts loudly (`SystemExit(1)`) instead of "Nothing to ingest". Tests `test_cdx_503_is_retried`, `test_wayback_urls_use_https`.
- [x] **Rehearsal F7 (LOW) — 6 numeric WP shortlinks (/2014/439/) leaked through the article regex** ✅ lookahead widened to any all-numeric final segment; hyphenated numeric slugs still pass. Test `test_article_regex_rejects_numeric_shortlinks`.
- [x] **`"weeks": null` template loss (flagged by the code-review pass as pre-existing)** ✅ non-list `weeks` dropped after the first parse. Test `test_explicit_null_weeks_no_crash`.
- [x] **Rehearsal PASSES (the airtight part):** migration round-trip over 16 distinct same-named templates + 2 exact dupes → all 16 survived, dupes collapsed (and the v1 constraint was directly proven un-appliable on that data); Catalyst crawl live: 428 URLs, selectors/pagination intact; Charniga CDX: 215 unique essays, content selectors confirmed against a live snapshot, mojibake fix verified end-to-end; Catalyst delete-SQL semantics verified live (cascade to chunk_log, url backfill on re-ingest, principle dedup).
- [x] **Runbook shipped**: `docs/DB-MACHINE-RUNBOOK.md` — numbered, with expected outcomes, backup step, branch-rename fixup (incl. `--prune` + single-branch contingency), and the delete-before-Charniga ordering constraint.

## 8. Audit 5 — 2026-07-18 full-repo sweep of least-audited surfaces (3 agents) — FIXED (2 deferred-low)

Deliberately aimed at the corners the first four passes skipped (dashboard/program
queries, extractors/processors, phase/session data, migrations 0000–0005). Real
substance: 2 HIGH (both live-proven), 5 MEDIUM, plus LOWs.

### Web (commit 9e409c5)
- [x] **web-H1 — dashboard 500 + max-upsert rollback**: `get_athlete_maxes` still unpacked `estimate_missing_maxes` as `(kg, source)` tuples; A-R8 changed the contract to `{ref: float}` and this caller was missed. Any athlete with a snatch/C&J max got a 500 on the dashboard, and max upserts rolled back (never persisted). Suite was green because router tests mock this function. Fixed + real-boundary test.
- [x] **web-H2 — ARQ worker dead on startup**: `WorkerSettings.redis_settings` was a `@classmethod`; arq reads `__dict__` verbatim and passed the classmethod object to `Worker()`, which crashed on `.host`. Web generation was dead end-to-end. Now a plain attribute; meta-test asserts `get_kwargs()` yields a real `RedisSettings`.
- [ ] **web M1 / L1–L8 — DEFERRED to a follow-up** (dashboard make-rate warning gating; status-machine transition guards; enum-field 422; admin cosmetics; login `?error` whitelist). Filed for the next pass — none are data-loss or auth-bypass; see the web report.

### Ingestion (commit 02a47c0)
- [x] **ing-H1 — curated seed `faults_addressed` wiped**: `load_exercise` ON CONFLICT DO UPDATE overwrote curated fault mappings with the heuristic parser's `[]`. COALESCE/CASE now preserves non-empty curated fields. **DB-machine spot-check before re-ingest** (source #8 Everett OWS may have already collided): `SELECT name FROM exercises WHERE faults_addressed = '{}' AND category <> 'competition';`
- [x] **ing-M1** `'variation'` (invalid enum) normalized → competition_variant; stats count real inserts. **ing-M2** classifier LLM fallback made reachable (was dead code at a flat 0.80) — signal-free prose stays confident, only weak-signal ambiguity drops to 0.55. **ing-M3** per-row SAVEPOINTs in load_principles/load_percentage_schemes. **ing-M4** `block_text` (html+epub) inserts br/td/th/div/table/tr separators — was mashing program lines/tables into single tokens (**materially improves the pending re-ingest**). **ing-L1** OCR %-corrections fire now.
- [ ] **ing M5/M6, L2–L6 — DEFERRED** (vision-OCR batch tolerance + spend caching; VARCHAR(300) title truncation; severity aggregation; keyword-boundary false positives; max-pages; infer fallback). Filed; none block the re-ingest except a nice-to-have on M6 truncation.

### Agent pipeline (commit pending)
- [x] **agent-M1** `athlete_snapshot` no longer persists `password_hash`/`username`/`is_admin` into every program row (credential retention/leak). **agent-M3** fault retrieval covers jerk+squat families (the selectable `dip_forward` fault was unreachable and disabled Check 8). **agent-M4** `source_principle_ids` sanitized at parse (a `"P-3"` element IntegrityError'd the save after all LLM spend). **agent-L3** snapshot uses recorded-only maxes (estimates no longer read as strength progress). **agent-L4** clean/jerk estimable (were resolving to NULL kg). **agent-L5** 1-week all-deload realization skips the work-up-to-100% max test.
- [x] **agent-M2 (= web L1) status-machine guards — folded into the deferred web follow-up** (SQL WHERE-status filters + 409). Re-completing an old program re-stamps `updated_at` and can win the previous-program pick — the AGT-H2 class through a side door.
- [ ] **agent-L1 (per-week Prilepin block) + L2 (deepcopy accessors) — DEFERRED**: L1 is validation-retry churn (extra paid calls), not a correctness bug; L2 has no live trigger (nothing mutates the shared module constants today). Documented for a follow-up.

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
