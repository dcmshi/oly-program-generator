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
- [x] **web M1 / L1, L3, L5–L8 — FIXED** (commit after 3ab0554): M1 dashboard warnings accept either metric with per-metric sample gates; L1/agent-M2 status-machine guards (activate requires draft, complete requires active → 409, + SQL status filters); L3 in-flight guard stamps the job_id and releases only on match (a stale poll no longer frees a newer job's guard); L5 admin `validation_errors` joined then char-truncated; L6 admin "last error" is the latest not lexicographic MAX; L7 profile enum values validated → 422 not 500; L8 login `?error` mapped to a whitelist. **L2 (two-actives race) and L4 (admin sys.path) accepted-low**: L2 impact is contained by `get_active_program`'s deterministic ORDER BY (a partial unique index risks turning races into 500s); L4's program.py path-inserts were moved to module scope with H1.

### Ingestion (commit 02a47c0)
- [x] **ing-H1 — curated seed `faults_addressed` wiped**: `load_exercise` ON CONFLICT DO UPDATE overwrote curated fault mappings with the heuristic parser's `[]`. COALESCE/CASE now preserves non-empty curated fields. **DB-machine spot-check before re-ingest** (source #8 Everett OWS may have already collided): `SELECT name FROM exercises WHERE faults_addressed = '{}' AND category <> 'competition';`
- [x] **ing-M1** `'variation'` (invalid enum) normalized → competition_variant; stats count real inserts. **ing-M2** classifier LLM fallback made reachable (was dead code at a flat 0.80) — signal-free prose stays confident, only weak-signal ambiguity drops to 0.55. **ing-M3** per-row SAVEPOINTs in load_principles/load_percentage_schemes. **ing-M4** `block_text` (html+epub) inserts br/td/th/div/table/tr separators — was mashing program lines/tables into single tokens (**materially improves the pending re-ingest**). **ing-L1** OCR %-corrections fire now.
- [ ] **ing M5/M6, L2–L6 — DEFERRED** (vision-OCR batch tolerance + spend caching; VARCHAR(300) title truncation; severity aggregation; keyword-boundary false positives; max-pages; infer fallback). Filed; none block the re-ingest except a nice-to-have on M6 truncation.

### Agent pipeline (commit pending)
- [x] **agent-M1** `athlete_snapshot` no longer persists `password_hash`/`username`/`is_admin` into every program row (credential retention/leak). **agent-M3** fault retrieval covers jerk+squat families (the selectable `dip_forward` fault was unreachable and disabled Check 8). **agent-M4** `source_principle_ids` sanitized at parse (a `"P-3"` element IntegrityError'd the save after all LLM spend). **agent-L3** snapshot uses recorded-only maxes (estimates no longer read as strength progress). **agent-L4** clean/jerk estimable (were resolving to NULL kg). **agent-L5** 1-week all-deload realization skips the work-up-to-100% max test.
- [x] **agent-M2 (= web L1) status-machine guards — folded into the deferred web follow-up** (SQL WHERE-status filters + 409). Re-completing an old program re-stamps `updated_at` and can win the previous-program pick — the AGT-H2 class through a side door.
- [ ] **agent-L1 (per-week Prilepin block) + L2 (deepcopy accessors) — DEFERRED**: L1 is validation-retry churn (extra paid calls), not a correctness bug; L2 has no live trigger (nothing mutates the shared module constants today). Documented for a follow-up.

## 9. Audit 6 — 2026-07-25 inline pass (single reviewer, whole repo) — ALL FIXED

Sixth pass, run inline (no subagents) over the unchanged post-audit5 tree
(HEAD `4a26d3c`). Every previous section above was re-read first to avoid
re-filing. Result: **1 MEDIUM, 8 LOW** — consistent with the severity
convergence after five passes. Verified green on this machine with the full
stack up (`make up`): **481/481 agent tests** (incl. `test_web_routers` +
`test_feedback` against live Redis/Postgres), **164+2 ingestion no-key**,
**17/17 live-DB `test_structured_loader`**, alembic at head 0007 (single
head), ruff 0.15.22 clean. Bonus live proof of the MEDIUM below: with
`REDIS_URL` set but Redis down, 12 `test_web_routers` tests fail with
`redis.exceptions.ConnectionError` — the exact request-path 500 it describes.

### Medium

- [x] **WEB-M9 — Redis outage 500s every rate-limited route (login included).** ✅
  Confirmed live first: a `Limiter(storage_uri="redis://127.0.0.1:6399")`
  constructs without raising, and the first request to a limited route dies
  with `ConnectionError`. Fixed with slowapi's own
  `in_memory_fallback_enabled=True` rather than the proposed startup ping —
  the ping only covers boot-time outages (a mid-run Redis failure still 500s
  every route until restart), while the flag marks the storage dead on the
  first error, re-evaluates **the same limits** against per-process counters,
  and auto-recovers to shared counting when Redis returns. Verified end to
  end: with Redis down the route serves 200, 200, then 429 at the 2/minute
  cap. Test `test_limiter_serves_requests_when_redis_is_down`.

### Low

- [x] **WEB-L10 — `goal` enum unvalidated on the two goal-write paths** ✅
  Canonical `GOAL_OPTIONS`/`SEX_OPTIONS` (+ `VALID_GOALS`/`VALID_SEXES`) added
  to `web/options.py` — the DB enum vocabulary from migration 0001 — and
  validated at `POST /profile/goals` (422 re-render with a message) and
  `POST /setup` (goal_type + biological_sex join the errors list, no athlete
  created). **Found while fixing:** the two goal selects had *diverged* like
  WEB-M3 — profile offered 3 of the 6 enum values, setup 5, and `pr_attempt`
  none — so opening /profile with a `work_capacity` goal and pressing Save
  silently rewrote it to `general_strength`. Both templates now loop the
  `goal_options` Jinja global. Tests `test_profile_goals_rejects_unknown_goal`,
  `test_profile_goals_accepts_full_db_vocabulary`,
  `test_profile_renders_all_goal_options`, `test_setup_rejects_unknown_goal_type`,
  `test_setup_rejects_unknown_biological_sex`.
- [x] **WEB-L11 — failed generation misreported as "Dry run complete".** ✅
  `get_job_status` now reads `dry_run` from the job's own enqueue payload
  (`info.kwargs`, the same source the ownership check already trusts — so
  results queued before this change are classified correctly too) and returns
  `failed` when a non-dry run completes with `program_id=None`. Tests
  `test_job_status_failed_real_run_is_not_done`, `test_job_status_dry_run_still_done`,
  `test_job_status_successful_run_still_done`.
- [x] **WEB-L12 — `create_exercise_log` doesn't bound `exercise_name`.** ✅ New
  `web/formparse.parse_text(v, max_len, default)` companion to
  `parse_float`/`parse_int`; the name is truncated to
  `EXERCISE_NAME_MAX_CHARS` (new in `shared/constants.py`, mirroring the
  VARCHAR widths) and a blank falls back to "Unnamed exercise" instead of
  storing a junk NOT NULL row. Tests `test_parse_text_bounds_and_defaults`,
  `test_create_exercise_log_bounds_exercise_name`.
- [x] **AGT-L9 — Check 0 doesn't mirror `rpe_target`/`intensity_reference`
  column bounds.** ✅ Check 0 now also mirrors `rpe_target` ≤ `MAX_RPE_TARGET`
  (NUMERIC(3,1)) and `intensity_reference` ≤ 100 chars — **plus
  `exercise_name`**, which had the same gap and no catalogue check anywhere
  else to catch it: a null/blank name (NOT NULL) or a >200-char name
  IntegrityError'd at `_save_session` after the whole program was paid for.
  5 tests (`test_rpe_target_over_column_range_is_error`,
  `test_overlong_intensity_reference_is_error`, `test_overlong_exercise_name_is_error`,
  `test_blank_exercise_name_is_error`, `test_normal_rpe_target_still_valid`).
- [x] **AGT-L10 — `explain()` annotated `-> str` but returns a 3-tuple.** ✅
  Now `-> tuple[str, int, int]`, matching the docstring and both return paths.
- [x] **AGT-L11 — `log.py session`/`status` ignore program status.** ✅ New
  `log._current_program(conn, athlete_id, columns)` — active first, newest as
  fallback — is the single selector for all three commands (`show` kept its
  behaviour, `session`/`status` now match it). Tests
  `test_current_program_prefers_active`, `test_current_program_falls_back_to_newest`,
  `test_session_and_status_pick_the_active_program`.
- [x] **ING-L7 — `load_prilepin_rows` full-connection rollback per bad row** ✅
  SAVEPOINT/RELEASE/ROLLBACK TO per row, matching the two loaders fixed in
  audit5-M3. Proven live against pg16 before the fix: a 4-row batch with one
  CHECK violation stored **1** of 3 valid rows while reporting `loaded=3`;
  after, 3 stored and the count matches. Test
  `test_load_prilepin_savepoint_keeps_valid_rows` (live DB, now 18/18).
- [x] **INF-L11 — CLAUDE.md test counts are stale.** ✅ Recounted from
  `pytest --collect-only` (agent 476 across 16 suites, ingestion 166 across
  12) and corrected in both the file tree and the run sections. Also added the
  suites the docs never listed at all (agent: web_queries/schemas/config/
  formulas/phase_progression/log; ingestion: html_extractor/ingest_web/
  parse_exercise/pipeline_unit/structured_loader_unit/llm_helpers/
  vector_loader_units), each section now labelled as exactly what
  `make test-agent` / `make test-ingestion` runs, with a note that
  `test_feedback.py` (24) needs a live DB.

### Fix-campaign verification (2026-07-25)

All 9 fixed red-first: every test above was confirmed failing against the
pre-fix tree (WEB-M9 with the real `redis.exceptions.ConnectionError`, ING-L7
with 1-of-3 rows surviving on the live DB) before the change landed. Full
green after: **476 agent no-key** (16 suites), **164 + 2 skipped ingestion
no-key** (12 suites), **18/18 live-DB `test_structured_loader`**, **24/24
live-DB `test_feedback`**, ruff 0.15.22 clean. Two findings grew during
verification — the WEB-L10 goal-select divergence and the `exercise_name` gap
in AGT-L9's Check 0 — both noted inline above.

### Discarded during verification (audit 6)

- `exercise_log_section.html` with `session=None` (log unlinked by program
  deletion while the page is open): Jinja guards `session and session.exercises`;
  renders degraded (broken `/log/` post target), no 500. Contrived path.
- `upsert_athlete_max` read-then-upsert PR race: benign (same-athlete,
  last-write-wins on a self-reported max).
- `_parse_table` / `load_json` stat counting (`len(rows)` vs actual inserts):
  stats-only nit, no data impact.
- Warmup boundary: prompt's 50–60% band vs `WARMUP_VOLUME_EXCLUSION_PCT=60` —
  consistent by design (audit3-M1).
- `_split_on_sections` `re.match` vs `re.MULTILINE` anchoring: correct as-is.
- Clean under scrutiny: `generate.py` retry/token accounting, `orchestrator`
  deadline+cost guards, `feedback.py` outcome math, `jobs.py` in-flight guard
  lifecycle, middleware stack ordering, all router ownership scoping,
  `structured_loader.upsert_source` disambiguation, `ingest_web.py`
  transient/permanent split, `pipeline.py` continuation scanning, chunker
  boundary math, migration chain 0000→0007 (verified live in audit 4).

## 10. Audit 7 — 2026-07-31 frontend & design pass — ALL FIXED

Scope: `oly-agent/web/templates/**` plus the router/template contract in
`web/{app,options}.py` and `routers/{program,log_session,generate,setup,history}.py`
— the surface every earlier audit treated as secondary. Result: **5 HIGH,
10 MEDIUM, 6 LOW**, each fixed in its own commit with regression tests. Verified
green on this machine: **552 agent** + **164+2 ingestion** no-key tests, ruff
0.15.22 clean, and the rendered pages checked in a browser (the CSS work is
visual, so tests alone weren't evidence).

Regression coverage lives in `tests/test_web_routers.py` (rendered-page and
router behaviour) and `tests/test_web_queries.py` (template-source invariants,
palette contrast, dead-asset checks).

### High

- [x] **FE-H1** — the complete response emitted a second `<span id="status-badge">`
  while the header still read "active"; now one badge, swapped `hx-swap-oob`.
- [x] **FE-H2** — completing a program replaced `#program-actions` and took Export
  CSV/PDF with it; complete now fills a dedicated `#outcome-area`.
- [x] **FE-H3** — htmx doesn't swap on 4xx/5xx and nothing listened, so a 409
  double-submit, any 429, or a 500 mid-render did *nothing* visible. Global
  `htmx:responseError`/`htmx:sendError` toast in `base.html`.
- [x] **FE-H4** — Activate/Abandon swapped only the badge, leaving both buttons
  live; and `abandon` was the one status endpoint with no guard, so it would flip
  a *completed* program and strand the outcome just computed for it. Buttons moved
  to `partials/program_actions.html`, keyed off status; abandon 409s.
- [x] **FE-H5** — `get_adherence` didn't clamp, so over-logging rendered
  `width: 112%` on a track with no `overflow-hidden`.

### Medium

- [x] **FE-M1** — `{% block extra_js %}` was never declared in `base.html`, so
  Jinja silently dropped it. A test now walks every base-extending template.
- [x] **FE-M2** — deleted `exercise_logged_row.html` (unreferenced), the
  `[x-cloak]` rule (Alpine is never loaded) and the unused `.htmx-indicator` CSS.
- [x] **FE-M3** — no busy state on any action. Styles htmx's own `.htmx-request`
  instead of per-template markup. **Follow-up:** the selector matched only
  `[type="submit"]`, so a typeless submit button would have been missed → now
  `:not([type="button"])`, with a test walking every `hx-*` trigger.
- [x] **FE-M4** — "Save Session ✓" was an `<a href>`; everything was already
  persisted. Renamed to "Done — back to program".
- [x] **FE-M5** — dropped the hardcoded "16 LLM calls / ~5 min / ~$0.50"; added
  `jobs.get_inflight_job_id()` so returning to `/generate` resumes polling.
- [x] **FE-M6** — the program-delete ✕ was hover-only, i.e. invisible on touch.
- [x] **FE-M7** — setup and profile collected the same data three ways: free-text
  vs select weight class (with a wrong-format hint), four disagreeing numeric
  bounds, and `equip_<val>`/`fault_<val>` vs multi-value names. Now one shared
  select partial, `FIELD_BOUNDS` in `web/options.py`, and consistent field names.
- [x] **FE-M8** — the exercise row toggled its edit form from a bare
  `<div onclick>`; keyboard users couldn't open it at all.
- [x] **FE-M9** — labels sat as bare siblings of their inputs everywhere except
  `login.html`. `for`/`id` throughout; per-row ids in `exercise_log_entry.html`.
- [x] **FE-M10** — the hamburger had no `aria-expanded`, and the menu closed on
  neither outside click nor Escape.

### Low

- [x] **FE-L1** — `text-gray-400`/`500` were remapped to 2.7:1 and 4.4:1 on the
  warm card. See the FE-L2 note below: this fix was correct for cards and *wrong*
  for the nav, which FE-L2 then resolved properly.
- [x] **FE-L2** — the theme was ~40 `!important` overrides on Tailwind's gray
  scale, copy-pasted into four templates as different subsets. Now a role-based
  palette (`paper`/`line`/`ink`/`navy`/`canvas`) in `web/tailwind/tailwind.config.js`;
  the compiled CSS contains **no** `!important`. `static/theme.css` is gone — it
  was served raw so `theme()` never resolved — and its component classes moved
  into the build's `input.css`, outside `@layer components` because Tailwind
  tree-shakes that layer and `.htmx-request` only exists at runtime.
- [x] **FE-L3** — every page pulled from `cdn.tailwindcss.com` (a dev-only
  in-browser compiler), unpkg, jsdelivr and Google Fonts. All vendored under
  `web/static/`; `make css` / `make fonts` regenerate, outputs committed.
- [x] **FE-L4** — ~90 lines of outcome-card markup duplicated between
  `program.html` and the partial, already drifted. One `partials/outcome_card.html`,
  rendering from both the dataclass and the stored JSONB.
- [x] **FE-L5** — the "Warmup" badge substring-matched `selection_rationale`, so
  it fired on "not a warmup priority". Now `shared/exercise_mapping.is_warmup_set()`,
  reusing the ≤`WARMUP_VOLUME_EXCLUSION_PCT` rule `validate.py` already applies.
- [x] **FE-L6** — the Logout form rendered regardless of session state.

### Two findings worth remembering

- **A single remapped token can't serve two backgrounds.** `text-gray-400` meant
  "faint metadata on a cream card" in most templates and "light link on the navy
  nav" in `base.html`. FE-L1 darkened it for the cards and thereby took the nav's
  profile link and hamburger from 5.18:1 to **2.58:1** — a regression introduced
  and then caught inside the same audit, because the FE-L1 contrast test only
  checked light surfaces. FE-L2's split (`ink-*` vs `navy-100/200`) is the fix, and
  the test now covers on-navy pairs too.
- **Neither webfont had ever loaded.** The Google Fonts URL the templates built
  was a malformed two-axis DM Sans request (`opsz,wght@9..40,300;400;500;600`),
  which Google answers with a 400 — so every page had been falling back to the
  generic sans-serif. Self-hosting fixed the typography as a side effect.

### Verified non-issues (don't re-file)

- `_safe_back` open-redirect handling in `routers/history.py` — correctly rejects
  protocol-relative, `://` and control-char variants.
- CSRF posture — `OriginCheckMiddleware` + `SameSite=Lax` + 64 KB body cap.
- Goal-progress and lift-ratio gauges already clamp pct to 0–100.
- `prefillExercise` uses `data-*` attributes, not JS-string interpolation.
- HTMX auth expiry returns `HX-Redirect`, so mid-interaction expiry redirects cleanly.

## 11. Audit 8 — 2026-09-15 RAG / ingestion / vector-DB best-practice pass (inline, no subagents) — OPEN

Scope: the retrieval-quality path the seven bug audits never covered — extractors →
classifier → chunker → embeddings → `knowledge_chunks` / pgvector → `retrieve.py`
→ `generate.py` prompt assembly, plus `programming_principles` selection in
`plan.py` and `tests/test_retrieval_eval.py`. Compared against current RAG
practice; the reasoning, reference model, and measurement queries are in
[docs/RAG_RESEARCH.md](docs/RAG_RESEARCH.md). Every number below was **measured**,
not inferred: two probes over the local PDFs (real chunker + classifier splitter,
no keys) and SQL against the local corpus copy (3,368 chunks · 161 principles ·
pgvector 0.8.2). Both no-key suites green at audit time (agent + ingestion, 2
skips). Roadmap items #17–#23 (`TODO-audit-2026-07-03.md`) are cross-referenced,
not re-filed. Work order: **H5 same day → H1 (one re-ingest) → H4 → M6 → the rest
on numbers.**

### High

- [x] **RAG-H1 — PDF sources are chunked by page, not by paragraph (50% of the corpus; a further 18% is 269-char fragments)** ✅ code landed; **re-ingest of the 7 PDF sources still pending on the corpus DB machine (runbook §8b)**. `_extract_with_pymupdf` now uses `get_text("blocks")` (text blocks joined with `\n\n`, image blocks dropped, line-end hyphenation repaired; pdfplumber fallback uses `layout=True`); new `extractors/page_text.py` (`dehyphenate`, `strip_running_heads` — recurring edge lines + folios, `join_pages` — re-attaches sentences/words split by the break); `pipeline._prepare_pdf_pages()` joins PDF pages into one document before classification; new `processors/sectioning.py` caps classifier sections at `CLASSIFY_SECTION_MAX_CHARS` (8k, paragraph boundaries) and folds weak-heading fragments under `MIN_SECTION_CHARS` back into their neighbour with the heading line restored (chunker `_split_on_sections` applies the same merge). Re-measured on the same PDFs: Takano 229 → 161 chunks, mean 590 est. tokens (was 344), single-paragraph 18/161 (was 100%), 3 chunks equal a page (was 190); Zatsiorsky mean 810 (was 485), single-paragraph 6/178; Dan John 0/102; chapter metadata on 71/72, 102/107, 44/46 sections (was ~3%). 24 new tests across `test_page_text.py` (new), `test_pdf_extractor.py`, `test_classifier.py`, `test_chunker.py`, `test_pipeline_unit.py`. CLAUDE.md invariants, CORPUS.md note, runbook §8b. — `pdf_extractor._extract_with_pymupdf` (`:115`) uses `get_text("text")`, which separates blocks with a single `\n`; `SemanticChunker._chunk_section` (`chunker.py:658`) splits paragraphs on `\n\n`; `pipeline.ingest` (`:313`) classifies and chunks **each page in isolation**. Corpus measurement: Drechsler 603, Zatsiorsky 430, Dan John 266, Takano 229, Everett-for-Sports 172 chunks are **100% single-paragraph** (avg 1.0 paragraphs/chunk; EPUB sources average 11–17); Medvedev (vision OCR) is 617 chunks averaging **269 chars**. Page probe (Takano 211 pp / Zatsiorsky 200 pp / Dan John 200 pp): 100% of pages have zero blank-line breaks, 83–90% become exactly one chunk, est. tokens/chunk 279–485 against 900/1,100 targets, 33–72% of pages end mid-sentence, 7–19% of chunks are < 50 tokens; running heads/folios ("Science and Practice of Strength Training", `20:108:280`, `part 2`, `111`) are embedded as text; `chapter` is empty on 3,252/3,368 chunks and `section` on 3,267, so the preamble is `[Source | Author]` for 97% of the corpus. Keep-together, overlap and profile sizing never engage. Same bug class as the EPUB fix (198 → 587 Everett chunks) — the PDF path was never checked. **Fix:** extract with `get_text("blocks")` (or PyMuPDF4LLM Markdown — same vendor, emits `#` headings the chunker already splits on), join blocks with `\n\n` and pages into one document before classification (as EPUB chapters already are), strip lines recurring on > 30% of pages and bare folios, de-hyphenate line ends, carry `page_number` into the never-written `page_range` column; for the vision path merge sub-100-token paragraphs into neighbours. Fix probe: 30 joined Zatsiorsky pages → 27 chunks at mean 1,039 tok (target 1,100), 30 Takano pages → 25 chunks at 796 (target 900), zero fragments. **Needs a re-ingest of all 7 PDF sources on the corpus DB machine** (~$1–2) — add a runbook step. Tests: a 3-page fake document must yield a chunk spanning pages; a page-break inside a sentence must not split a chunk; running-head line removed.
- [x] **RAG-H2 — Production retrieval hard-filters on `chunk_type`, a first-match substring label, and `concept` is 61% of the corpus** ✅ (1) `similarity_search(preferred_chunk_types=…)`: a similarity-ranked candidate pool (`max(top_k × VECTOR_SEARCH_CANDIDATE_MULTIPLIER, VECTOR_SEARCH_MIN_CANDIDATES)`, index-friendly) re-ranked with `CHUNK_TYPE_PREFERENCE_BOOST`; rows carry `score` + raw `similarity`; `retrieve.py` uses it everywhere (`methodology` dropped); the hard `chunk_types=` stays available but is no longer used in production. (2) `_infer_chunk_type` now scans title **and** content with word-boundary matchers (`title or content` skipped the body of every titled section — i.e. nearly all of them after RAG-H1; `miss` fired on *permission*), and `periodization` (with accumulation/intensification/realization/deload/taper/peaking added) is tested before `recovery_adaptation`. (3) `relabel_chunk_types.py` — batched LLM relabel with confidence threshold, `--dry-run`, `--source-id`, `--limit`; **run pending on the corpus DB (a few dollars with a Haiku-class model)**. Live: a fault-correction query now returns 10 rows across 4 types with 4 boosted (the old filter returned only `fault_correction`). Tests: `test_preferred_chunk_types_reranks_a_candidate_pool_instead_of_filtering`, `test_hard_chunk_types_filter_still_available`, `test_preferred_chunk_types_boosts_without_excluding` (live), 4 `test_retrieve` tests (no `chunk_types=` anywhere, preferences per path), 4 `_infer_chunk_type` tests, `test_relabel_chunk_types.py` (6, incl. enum mirror). CLAUDE.md invariant; RETRIEVAL_EVAL O1/O3 corrected. Production-parity eval → RAG-M6. — `pipeline._infer_chunk_type` (`:427`) probes `title or content[:800]` against `CHUNK_TYPE_KEYWORDS` (`:67`) in dict order; `retrieve.py:130/148/168` then filter session queries to `programming_rationale, periodization`, fault queries to `fault_correction`, limiter queries add `methodology`. Measured: concept 2,048 (60.8%), recovery_adaptation 380, programming_rationale 363, fault_correction 189, periodization 134, nutrition 130, biomechanics 123, **competition_strategy 1**, methodology/case_study **0** (the inference never emits them — dead filter value). Session generation can therefore reach **497 of 3,368 chunks (15%)**. Of chunks mentioning *deload*, **0 of 32** are reachable; *accumulation* **3 of 97** (77 sit in `recovery_adaptation`, which is tested before `periodization` and matches the word "adaptation"); *taper/peaking* 10 of 43; *Prilepin* 0 of 3. `RETRIEVAL_EVAL.md` never shows this because the eval searches unfiltered, and its O1/O3 notes describe the filter as a defence. **Fix (in order):** production-parity eval first (RAG-M6); replace the hard filter with a score term or apply type preference at rerank; relabel `chunk_type` with a batched small-model call storing a confidence (a few dollars), keeping the heuristic as fallback — at minimum reorder `CHUNK_TYPE_KEYWORDS` so `periodization` precedes `recovery_adaptation` and drop `methodology` from the limiter filter. Test: an eval query about deloads must return ≥ 1 chunk under production settings.
- [x] **RAG-H3 — Principle selection evaluates 2 of 12 extracted condition keys and drops array-valued phases** ✅ `plan._load_principles` now pre-filters with `condition->'phase' @> to_jsonb(%s::text)` (matches string **and** array phases) and `LIMIT MAX_PRINCIPLE_CANDIDATES` (50) as a superset; new `principle_matcher.py` (`compare` for `lte/gte/lt/gt/eq/between`, list membership, scalars; `condition_matches` over the 8 schema keys — unknown facts are *not* satisfied, schema-drift keys are ignored; `build_session_state` per week/day: weeks-out decremented per week, movement family = the day's primary, make rate from the previous outcome, RPE from recent logs; `select_principles`). The orchestrator selects principles **per session** and passes the same list to the prompt (`build_session_prompt(active_principles=…)`), `generate_session_with_retries` and `validate_session`, so a snatch-only rule no longer reaches C&J days and a `weeks_out ≤ 2` taper rule no longer fires 12 weeks out. Tests: `test_principle_matcher.py` (18), `test_load_principles_sql_matches_array_phases_and_caps_candidates`, `test_prompt_uses_per_session_principles_when_given` (+ fallback), `test_principles_selected_per_session_by_movement_family` (orchestrator, generate + validate + prompt all narrowed). Schema-drift whitelist → RAG-L9. — `plan._load_principles` (`:264–281`) filters `condition->>'phase' = %s` and `athlete_level`; `principle_extractor.EXTRACTION_PROMPT` (`:49`) explicitly allows `phase` as an array, and `->>` on a JSON array returns its text form → never equal (2/161 rules silently excluded). Measured condition keys: `movement_family` **60**, `athlete_level` 25, `phase` 25, `weeks_out_from_competition` 9, plus `recent_make_rate`, `rpe_average_last_week`, `week_of_block`, `training_age_years` (1 each) and four keys outside the prompt schema (`athlete_characteristics`, `exercise_type`, `delay_minutes`, `training_focus`) — **73 of 161 principles carry a condition nobody checks.** A snatch-only rule is shown as an "Active Principle" on C&J days and its `max_exercises_per_session` / `competition_lifts_first` is enforced by `validate.py` Check 5; a `weeks_out ≤ 2` taper rule is served in week 1 of a 12-week-out block. **Fix:** `(condition->'phase' @> to_jsonb(%s::text) OR condition->>'phase' = %s)`; a `condition_matches(condition, state)` helper over `lte/gte/lt/gt/eq/between` for weeks-out, week-of-block, movement family (session primary), level, and previous-outcome make rate / RPE; select principles **per session** (the orchestrator already has the template + week in hand). Tests per operator + an array-phase fixture. Schema drift → RAG-L9 / #17.4.
- [x] **RAG-H4 — Retrieval runs once per program and every session prompt shows the same four 600-char snippets (absorbs roadmap #19 a–d, #18c)** ✅ `retrieve.retrieve_session_context()` runs per session from the orchestrator: `build_session_query()` (primary + supporting movements, phase, intensity band rounded to `INTENSITY_BAND_WIDTH_PCT`, level, deload flag, faults/emphasis/limiters with underscores humanised) cached per program by query string (4-week × 4-day = ~4–8 searches, not 16 or 2); `compose_session_context()` takes ≤ `MAX_FAULT_CHUNKS_IN_CONTEXT` fault chunks round-robined across faults (chunks now carry which fault surfaced them), then the session's chunks **by score**, deduped, ≤ `MAX_CHUNKS_PER_SOURCE_IN_CONTEXT` per source, ≤ `MAX_CONTEXT_CHUNKS`. `build_session_prompt(context_chunks=…)` shows them labelled `[C1|type]…` with `SNIPPET_MAX_CHARS` 600 → **1,500** and asks the model to cite labels in `selection_rationale` (feeds RAG-M5); `attach_source_chunk_ids` now traces against the session's chunks. `retrieve()` no longer issues the `[:2]` program-level session queries. Tests: 8 in `test_retrieve` (query contents + humanised tokens, per-template queries + band cache, no-loader, score order/cap, fault round-robin, no-fault, per-source cap + dedupe; two old session tests re-targeted), 4 in `test_generate_utils` (order + labels, cap/empty, tail of a >600-char chunk visible, citation instruction), 1 in `test_orchestrator` (per-session call, shared cache, `context_chunks` + trace wiring). CLAUDE.md invariant + prompt budget updated (~14k chars worst case). — `retrieve.py:121` queries only `session_templates[:2]` (day 3/4 templates get no retrieval); results accumulate in insertion order; `generate.py:339–360` takes ≤ 2 fault chunks then fills to 4 **by insertion order, not similarity**, and shows `raw_content[:SNIPPET_MAX_CHARS]` = 600 chars of chunks that average 2,100–4,900 chars — the head (preamble + topic sentence), while prescriptions sit in the tail. Retrieval unit ≠ display unit: no chunking or embedding improvement can reach a generated program until this is fixed. **Fix:** one query per session template from `(primary_movement, secondary_movements, phase, week intensity band)`; top-4 by `similarity` with a per-`source_id` cap of 2; round-robin fault chunks across faults; show the whole chunk or ≥ 1,500 chars; raise `PROMPT_LENGTH_WARN_CHARS`; label chunks `[C1]…[C4]` (feeds RAG-M5). Tests: day-3 template triggers a query; context differs between two sessions; ordering by similarity.
- [x] **RAG-H5 — Filtered HNSW returns nothing once the planner uses the index (latent; one `SET LOCAL` away)** ✅ `VectorLoader._apply_hnsw_query_settings()` runs `set_config('hnsw.iterative_scan', 'relaxed_order', true)` + `set_config('hnsw.ef_search', '100', true)` (constants `HNSW_ITERATIVE_SCAN` / `HNSW_EF_SEARCH`) before every search, behind a SAVEPOINT so pre-0.8 pgvector degrades to a warning. Proven red-first on the local corpus with the index forced: **19/20** filtered queries short of exact counts (16 empty) → **20/20** match. Tests `test_similarity_search_sets_iterative_scan_and_ef_search_before_select`, `test_similarity_search_survives_missing_hnsw_gucs` (mocked), `test_filtered_search_with_index_forced_matches_exact_counts` (live DB, no key). CLAUDE.md invariant + CONTRIBUTING S5 note added. — pgvector HNSW collects `hnsw.ef_search` (40) candidates *then* applies WHERE; every production query filters on `chunk_type` and the `min_similarity` predicate. Measured with the index forced (`enable_seqscan=off`), 60 query vectors, filter `fault_correction`, `LIMIT 5`: **`iterative_scan=off` (default): 60/60 short of 5, 46/60 empty, mean 0.32 rows; `relaxed_order`: 1/60 short, mean 4.92.** Today `EXPLAIN` shows Seq Scan + Sort (exact) — results are correct only because the HNSW index is **idle** at 3.4k rows (`docs/CONTRIBUTING.md` S5 "done" is built-not-used); the plan flips as the corpus grows (Catalyst re-ingest ≈ ×2, Charniga, #23) and 77% of fault queries then silently return "(none retrieved)". **Fix:** in `VectorLoader.similarity_search`, same transaction: `SET LOCAL hnsw.iterative_scan = 'relaxed_order'; SET LOCAL hnsw.ef_search = 100;` (guarded for older pgvector); pin the image (RAG-L6); drop the redundant index (RAG-L7); later, partial HNSW indexes per `chunk_type`. Test (live DB, `INTEGRATION_TESTS=1`): force the index and assert `len(results) == top_k` for a filtered query.

### Medium

- [x] **RAG-M1 — No lexical channel (= roadmap #21)** ✅ migration `0009_chunk_tsv_context_prefix`: `tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(context_prefix,'') || ' ' || raw_content)) STORED` + GIN `idx_chunks_tsv` (+ the `context_prefix` column RAG-M3 will fill — added now because a generated expression can't be altered later); applied locally (3,368 rows; `prilepin` 3 hits, `deload` 50). `similarity_search(hybrid=True)`: vector leg (with `min_similarity`) and lexical leg (`ts_rank_cd` over an OR-of-terms `to_tsquery`, same metadata filters) each top `HYBRID_CANDIDATES_PER_LEG`, `FULL OUTER JOIN`ed and fused by RRF (`RRF_K`), chunk_type preference re-scaled to `CHUNK_TYPE_PREFERENCE_BOOST_RRF`; rows carry `similarity`, `lex_score`, `rrf`, `score`. `retrieve.py` passes `HYBRID_SEARCH_ENABLED` on all three paths. **Honest measurement:** `ts_rank_cd` has no IDF, so an OR query lets boilerplate terms swamp rare ones — on the "Prilepin table optimal reps per set" probe hybrid surfaced 1/5 Prilepin chunks vs 2/5 dense-only until a domain stoplist (`_LEXICAL_STOPLIST`: exercise/session/intensity/reps/sets/…) was added; now 2/5 vs 2/5. Whether hybrid helps on the production query set is exactly what RAG-M6's golden eval must decide; if not, the follow-ups are IDF weighting (per-term `df` from the GIN index) or a BM25 extension. Tests: `test_lexical_tsquery_ors_deduped_alphanumeric_terms`, `test_hybrid_search_fuses_two_legs_with_rrf`, `test_hybrid_falls_back_to_dense_when_query_has_no_terms`, `test_hybrid_off_by_default_keeps_dense_sql`, `test_hybrid_surfaces_exact_term_chunks` (live), `test_all_production_searches_pass_hybrid_flag`. SCHEMA.md + CLAUDE.md updated. — pure dense search; exercise names, `%/reps` notation, Soviet abbreviations and "Prilepin" (eval Q12 at 0.46–0.50) are matched only through embeddings. Alembic: `tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', raw_content || ' ' || coalesce(context_prefix,''))) STORED` + GIN (no re-embed) — index `raw_content`, **not** `content`, or the preamble's source titles dominate; `similarity_search(hybrid=True)`: vector top-20 + lexical top-20 CTEs fused by RRF `1/(60+rank)`, `min_similarity` on the vector leg only; soft `chunk_type` / `topics` boosts here rather than hard filters. Accept on RAG-M6 numbers.
- [x] **RAG-M2 — Word-count token estimate + no oversized-paragraph split** ✅ new `processors/tokens.py` (`count_tokens` / `truncate_to_tokens` via tiktoken `cl100k_base`, words × `TOKENS_PER_WORD_FALLBACK` when the encoder can't load; `tiktoken` added to `oly-ingestion` deps). Measured: the notation `(85%/4)4 20:108:280 70%/3x3 75%/3x2` is **24** tokens vs the old estimate of **5**. `SemanticChunker._estimate_tokens` delegates to it; new `_split_long_paragraph` breaks a paragraph over the profile size into sentence groups (whitespace split for a single punctuation-less giant "sentence"); `vector_loader` caps embedding input at `EMBED_MAX_TOKENS` (8,191) by tokens instead of 30k chars. Tests `test_tokens.py` (6: notation ratio, offline fallback, truncate, sentence split respects size + boundaries, giant-sentence split lossless, token-based embed cap). CLAUDE.md + CORPUS.md note (re-ingested sources will report different chunk counts). — `_estimate_tokens` was `len(split()) × 1.3` (`chunker.py:742`); notation like `(85%/4)4 20:108:280` tokenises at 2–4× the word count, so "900-token" Soviet chunks can be 1,500+ real tokens while prose under-fills. `_chunk_section` never splits a paragraph larger than `chunk_size` — masked today because PDF paragraphs are pages; after RAG-H1 real long paragraphs become single oversized chunks and `vector_loader` (`EMBED_CHAR_LIMIT`, `:131`) embeds only their head. **Fix:** `tiktoken` `cl100k_base` (the `text-embedding-3-*` tokenizer; OpenAI package) for sizing and the 8,191-token cap; sentence-level fallback split above target; keep the word estimate as the test fallback. Do with RAG-H1.
- [x] **RAG-M3 — Static, mostly-empty preamble where contextual retrieval is standard** ✅ code landed; **the paid pass runs with the RAG-H1 re-ingest (runbook §8b, `--contextualize --context-model claude-haiku-4-5-20251001`)**. New `processors/contextualizer.py`: the enclosing section goes in a cached system block (`cache_control: ephemeral`), one ≤60-word call per chunk; `apply_context` inserts the text between preamble and body of `content` (embedded) and into `metadata["context_prefix"]` → `knowledge_chunks.context_prefix` (column from migration 0009, already inside the generated `tsv`); `raw_content`/hash/prompt text untouched; per-chunk failures leave the chunk as-is. `pipeline.py --contextualize [--context-model]` and `ingest_web.py --contextualize [--context-model]`. Tests `test_contextualizer.py` (6: prompt blocks, insertion + raw untouched, blank ignored, cached system block + failure isolation, pipeline wiring on/off, insert column). CLAUDE.md invariant; runbook §8b. — `_build_preamble` (`chunker.py:599`) emits `[Source|Author]\n[Chapter|Section]`, and 97% of chunks have neither chapter nor section (RAG-H1). Anthropic's contextual-retrieval result (−35% retrieval failures alone, −49% with BM25, −67% with reranking) comes from a 50–100-token LLM description of the chunk *within its document*, prepended before embedding and lexical indexing. One short Haiku-class call per chunk with the section as cached context ≈ a few dollars once; store in a new `context_prefix` column so `content` stays reconstructable. **Do inside the RAG-H1 re-ingest** so the corpus is embedded once; feeds RAG-M1's tsvector.
- [x] **RAG-M4 — Program templates (Path B) reach the prompt as name + notes only** ✅ `generate.render_template_reference()` renders the template week matching the current `week_number` (else the latest earlier week, else week 1) as `Name (week N): Day: Ex sets×reps@pct, …; Day: …`, capped at `MAX_TEMPLATE_CHARS_IN_PROMPT` (700) for `MAX_TEMPLATES_IN_PROMPT` (2) templates; JSON-string structures parsed; missing/malformed structure falls back to `name — notes`. `percentage_schemes` / `exercise_complexes` remain unread by the agent — left for the corpus-content work (they have no rows the prompt would use today). Tests: 5 in `test_generate_utils` (matching week, week fallback, malformed → name/notes, JSON string + cap, end-to-end prompt). — `generate.py:447–455` printed `name — notes`; `program_templates.program_structure` (17 templates, 16 Takano, parsed by the incremental LLM parser) is never rendered; `percentage_schemes` and `exercise_complexes` have **no reader in `oly-agent/`**. Render the week matching `week_number` compactly (`D1: Snatch 5×2@80, Back Squat 4×4@82 …`, capped) or stop paying to parse them. Test: template structure appears in the prompt when a template matches.
- [ ] **RAG-M5 — Traceability is nominal; retrieval set is not logged** — `weight_resolver.attach_source_chunk_ids` (`:147–181`) attaches the same first-3 `programming_rationale` ids to every exercise (+ fault ids when the rationale contains `fault|address|correct|fix`), so `source_chunk_ids` cannot say which chunk informed which exercise; `generation_log` stores the prompt text but not `(chunk_id, similarity, query)`. README roadmap #5 (boost chunks cited in successful programs) would learn from noise. **Fix:** log the per-call retrieval set as JSONB in `generation_log`; ask the model to cite `[Cn]` indices in `selection_rationale` (RAG-H4 labels; trivial with #17.4 structured outputs) and derive `source_chunk_ids` from the citations.
- [ ] **RAG-M6 — Eval is print-only, non-parity, unlabelled, not in CI (extends roadmap #20)** — `test_retrieval_eval.py` never asserts, has no exit code, calls `similarity_search` **without the production `chunk_types` filter** and **with** `require_numbers` (production never passes it); no relevance labels → no recall@k / MRR / nDCG; the design doc's source-diversity metric was never built; nothing generation-side. **Fix:** golden set of ~50 production-shaped queries (one per `FAULT_OPTIONS` value, phase × primary movement, one per limiter, + the current 22) → top-20 each → LLM-graded 0–2 → skimmed → frozen `tests/eval/golden.json`; report recall@5, MRR, source diversity under production settings; fail under `INTEGRATION_TESTS=1` below the `RETRIEVAL_EVAL.md` baseline; add validator-retry rate + citation coverage from `generation_log` as the generation-side check. **Gate for RAG-H2 step 2/3, M1, M3 and #22.**
- [ ] **RAG-M7 — Dead / never-populated chunk metadata** — measured: `athlete_level_relevance` set on **0** chunks (nothing populates `chunk.metadata`; its filter branch + `idx_chunks_level` are dead), `page_range` set on 0, `topics` empty on 268, `information_density` / `contains_specific_numbers` / `topics=` **never read by `retrieve.py`** — `KEYWORD_TO_TOPIC` (~230 entries), `retag_chunks.py` and the GIN index have no production consumer. Decide: wire `topics` + density in as soft boosts after RAG-M1 (first switch `keyword_tag` to word-boundary matching — `rir`→*requiring*, `miss`→*mission*, `peak`→*speak*, `position`→everything; deferred in audit 5), or retire the tagger and drop the columns/indexes.
- [x] **RAG-M8 — No embedding-model versioning on `knowledge_chunks`** ✅ migration `0008_chunk_embedding_model`: `embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small'` + `embedded_at` (backfilled from `created_at`) + btree index — applied locally (3,368 rows tagged). `VectorLoader` writes the model on insert, `similarity_search` filters `embedding_model = settings.embedding_model`, and `_embed`/`_embed_batch` pass `dimensions=settings.embedding_dim` for `text-embedding-3-*` (what makes #22's `-large @ 1536` a settings flip). New `oly-ingestion/reembed.py` (`--dry-run`, `--source-id`, `--limit`, `--all`; refuses if the API's vector width ≠ `embedding_dim`). Tests `test_reembed.py` (6). SCHEMA.md + CLAUDE.md updated. — only `ingestion_runs.config_snapshot` recorded the model; a re-embed (#22 `text-embedding-3-large` @ 1536, or RAG-M3) leaves mixed vector spaces indistinguishable. Add `embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small'` + `embedded_at`, backfill, assert in `similarity_search`, and ship `reembed.py` (batches from stored `content`). Prerequisite for #22 and RAG-M3.

### Low

- [ ] **RAG-L1 — Fault tokens embedded with underscores (= #18a)** — `retrieve.py:143` embeds `correcting early_arm_bend …`; limiters/emphasis already `.replace("_", " ")`. One line + test.
- [ ] **RAG-L2 — No query-embedding cache** — retrieval strings are fixed templates; an LRU or `query_embeddings(text_hash, model, embedding)` table removes 5–12 OpenAI calls per program and makes the eval deterministic offline.
- [ ] **RAG-L3 — Prompt order defeats caching** — static blocks (Available Exercises ~2.3k chars, Principles, Context, Templates) sit *after* the per-session blocks in `build_session_prompt`, so the cacheable prefix across the 16 calls is a few hundred tokens. Reorder static-first + `cache_control` on the last static block. (#17.6 judged caching not worth it at 2.6k tokens; the reorder plus RAG-H4's wider context changes that.)
- [ ] **RAG-L4 — Retrieved text is injected with no untrusted-data frame** — "## Programming Context" pastes web/Wayback-scraped text verbatim; delimit it and state once that it is reference material, not instructions. Catalogue check + Checks 0–9 bound the blast radius.
- [ ] **RAG-L5 — Cross-source dedup drops provenance** — global `sha256(raw_content)` credits text shared by Everett's two books or a Catalyst reprint to whichever source ingested first (documented as intentional). Either hash `(source_id, raw_content)` or add a `chunk_sources` join table; matters when reading `source_id` in the eval.
- [ ] **RAG-L6 — `pgvector/pgvector:pg16` floats the extension minor** (resolves to 0.8.2 locally) — pin to an explicit `0.8.x-pg16` tag as INF-L6 did for PgBouncer, so RAG-H5's `iterative_scan` presence is a known quantity.
- [ ] **RAG-L7 — `idx_chunks_hash` duplicates `knowledge_chunks_content_hash_key`** (migration 0000 `:388`; both present) — drop in the next migration.
- [ ] **RAG-L8 — `section_title` inserted untruncated into `VARCHAR(300)`** — a long line matching `^(?:Week|Phase|Block|Cycle)\s+\d+.*$` raises `StringDataRightTruncation` in `load_chunks` and the section-level handler drops the whole section. Truncate at insert (same class as the deferred audit5-M6 title case).
- [ ] **RAG-L9 — Principle extraction emits condition keys outside its own schema** (`athlete_characteristics`, `exercise_type`, `delay_minutes`, `training_focus` in the corpus) — unenforceable rules; closed by #17.4 structured outputs, or a key whitelist in `PrincipleExtractor._extract_window` meanwhile.
- [ ] **RAG-L10 — Docs drift** — `docs/SCHEMA.md` 2,576 chunks / 82 principles; `README.md` "167 extracted rules", "275 unit tests" (×2); `RETRIEVAL_EVAL.md` O1/O3 rely on the chunk_type filter as a defence (RAG-H2); `docs/CONTRIBUTING.md` S5 "done" while the index is idle (RAG-H5); local corpus copy 3,368 / 161 vs documented 3,796 / 151 — reconcile which DB the docs describe. Fix alongside the next eval run.

### Dogfooding (requested 2026-09-15)

- [ ] **DOG-1 — Import the last real General Strength block, then generate the next block against it and review the output.** Source: `C:\Users\Shibi\Desktop\General Strength - 3 Block_11W - P2 General Strength.csv` (399 rows; 3 blocks / 11 weeks; layout per week: `Week N - <cycle name>` → `Day N,%,Reps,%,Reps,…,Total Reps,Avg Intensity %,Volume` → one row per exercise with up to four `(pct, reps)` pairs where `reps` may be `3x3` set notation and unloaded rows are `5x5`-style, then a per-day totals row). Plan: (1) a small `oly-agent/import_program_csv.py` — parse block/week/day headers and the pct/reps pairs into `generated_programs` (status `completed`, phase `general_prep`/`accumulation`, `duration_weeks` from the file) + `program_sessions` + `session_exercises`, with an explicit exercise-name alias map onto the `exercises` catalogue (e.g. "Snatch Extension from Deficit" → deficit snatch pull; unmapped names inserted with `exercise_id NULL`), `--dry-run` printing the parsed structure first; (2) `--log-as-completed` to create `training_logs` at the prescribed loads so `feedback.save_outcome()` can compute a real `outcome_summary` (adherence/make-rate/RPE fields; RPE can be left NULL), or log the actual results by hand for the last block if they exist; (3) generate the next program for athlete `dshi` (id=1) via the UI or `orchestrator.py --athlete-id 1` and review: phase progression from the imported block, "Previous Program" prompt block, exercise selection vs. the real block, Prilepin volume vs. the CSV's per-day totals, and the retrieved context per session (after RAG-H4). Record findings back here.

- [ ] **DOG-2 — Re-run the Catalyst Athletics crawl to test ingestion/chunking on the live site (https://www.catalystathletics.com/articles/).** The re-ingest itself is already runbook §5–7 / TODO-audit-2026-07-03 #6 (needs both keys, ~$1–2); this item is the *test* of the scraper + the post-RAG-H1 chunker on web articles. Live probe on 2026-09-15 (no keys): articles index HTTP 200 with 13 section links (crawler's section ids 17/13/18/14/19/10 all still present); category page `?start=0` returns 31 article links with the `start=` pagination link intact; `fetch_article` still finds `div.sub_page_main_area_half_container_left` and extracts title/author/body with real paragraph breaks (sample article: 1,680 chars, 67 paragraphs). So the site structure is **unchanged** — the pending run is a chunk-quality test, not a scraper rewrite. Steps: `ingest_web.py --dry-run` (expect ~428 URLs, compare with the 418 rows in `sources`), `--limit 5` smoke, then full run per runbook; measure chunks/article, avg paragraphs/chunk, and re-run the eval. Also fix RAG-L11 first so every article's first chunk isn't boilerplate.
- [ ] **RAG-L11 — Catalyst article body starts with page boilerplate.** The extracted text opens with the title, author, date and "See Related Articles" lines before the first paragraph (`fetch_article` extracts title/author from those lines but leaves them in `text`), so the first chunk of every Catalyst article embeds `Podcasts with Greg Everett | Greg Everett | January 23, 2015 | See Related Articles | …`. Strip the leading lines that equal the extracted title/author, a date, or "See Related Articles" before chunking; add a unit test in `test_ingest_web.py`. Do before DOG-2.

### Clean under scrutiny (don't re-file)

Hash dedup before embedding with the intra-batch guard; typed retry on embedding
calls; empty/oversize guards (0 chunks over the 30k-char limit today); per-section
rollback + resume; principle window scanning + `UNIQUE(source_id, principle_name)`;
per-row savepoints; EPUB/HTML block separators (EPUB sources average 11–17
paragraphs/chunk — that fix worked); classifier LLM-fallback reachability;
`min_similarity` in SQL before `LIMIT`; retrieval failures degrade to a warning;
HNSW build params sane for the row count; `VectorLoader` import boundary
(`orchestrator.py:67`) is the documented one-way coupling.

### Explicitly not recommended

GraphRAG, HyDE / query rewriting (fixed-template queries), a separate vector DB,
a LangChain / LlamaIndex rewrite, or any new third-party processing vendor — every
item above stays with the two API vendors already in use plus Postgres-native and
local libraries.

## Notes / non-findings from audit 5 (2026-07-18)

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
