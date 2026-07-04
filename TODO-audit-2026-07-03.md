# TODO — 2026-07-03 Repo Audit Findings

Reconstructed from the 3-agent parallel audit (web / ingestion / agent-pipeline).
The 5 critical items were fixed in commit `519aad0`. Batch 2 fixed all 9 remaining
agent-pipeline HIGH/MEDIUM findings + W-M2 + ENV1. Batch 3 (2026-07-03) fixed **all
8 agent LOW items + all 11 refactors**, each with a regression test, ruff clean.
Batch 4 fixed the **web LOW items** (W-L4, W-L6, W-L7, W-INFO), leaving only
**W-L5** (server-tz week math) deferred as a product decision (needs a tz column).
Batch 5 fixed **all ingestion HIGH + MEDIUM + LOW findings** that are code-testable
here (mocked suites + live empty DB), leaving only **I-L11** (shared
`process_section` refactor) deferred to the DB machine.
**Every audit finding is now either fixed or explicitly deferred.** Remaining
deferrals: **#6 Catalyst re-ingest**, **I-L11**, **W-L5** — all documented below /
inline. Only end-to-end embedding/vision verification needs the main DB machine.

Legend: `[x]` done · `[ ]` open · `[~]` code done, follow-up deferred · `[-]` non-finding

---

## DONE (commit 519aad0 — verified e2e 2026-07-03 after Docker install)

- [x] **Web H1 — IDOR `DELETE /program/{id}`**: added ownership pre-check + athlete-scoped UPDATEs. Regression test `test_program_delete_404_for_unowned`.
- [x] **Agent H1 — `maxes_delta` always empty**: `to_intensity_ref()` normalizes the snapshot↔current key spaces. Verified live: old lookup `{}` → fixed `{"Snatch":5.0,"Clean & Jerk":5.0}`.
- [x] **Agent H3 — realization repeat**: RPE-block guard now exempts realization→accumulation.
- [x] **Agent M1 — `Phase: WeekTarget` prompt**: `build_session_prompt` takes the plan's phase.
- [~] **Ingestion H1 — HTML/web single-newline chunking**: `html_extractor.block_text()` ported the EPUB fix. **Follow-up deferred (task #6):** delete old ~446 Catalyst chunks, clear `sources/catalyst_progress.json`, re-run `ingest_web.py` (~$1–2), re-run `test_retrieval_eval.py`, update `docs/RETRIEVAL_EVAL.md`. **Must run on the main DB machine.**

## Verification status (2026-07-03, this machine)

Docker up · migrations → head `0003` (21 tables) · all no-key suites pass (agent + ingestion) · live-DB `test_structured_loader` 7/7 · `test_feedback` 19/19 · web boots on asyncpg+PgBouncer, `/login` 200, `/health` 200 with `127.0.0.1` Redis.

---

## NEW — found during 2026-07-03 verification

- [x] **ENV1 — `.env.example` ships `REDIS_URL=redis://localhost:6379`, which breaks ARQ on Windows.** ✅ Fixed: new `web.jobs.resolve_redis_dsn()` rewrites any `localhost` DSN → `127.0.0.1` (preserving scheme/userinfo/port/path); used by both `init_arq_pool` and `worker.WorkerSettings.redis_settings`; `.env.example` default updated. Verified e2e: app boots with `localhost` DSN, ARQ inits immediately, `/health` 200. Unit test `test_resolve_redis_dsn_forces_ipv4_localhost`.

---

## AGENT PIPELINE (oly-agent) — open

### HIGH
- [x] **A-H2 — "Remaining session rep budget" collapses to 0 after day 1** ✅ `generate.py` now computes `remaining_weekly_reps = week_target.total_competition_lift_reps − cumulative_comp_reps` and relabels the prompt line "Remaining weekly rep budget". Test `test_remaining_budget_is_weekly_not_session`.

### MEDIUM
- [x] **A-M2 — Cost guard undercounts retry tokens** ✅ `generate_session_with_retries` accumulates `total_input/output_tokens` across every attempt and returns them on both success and failure. Tests `test_generate_tokens_accumulate_across_retries`, `test_generate_failed_reports_accumulated_tokens`.
- [x] **A-M3 — RPE/make-rate trends computed on unordered SQL** ✅ both queries now `ORDER BY ps.week_number, ps.day_number, se.exercise_order`. Live-DB test `test_rpe_trend_uses_chronological_order` (verified fails without the fix).
- [x] **A-M4 — `make_rate_trend` can never leave "stable"** ✅ `_compute_trend` takes a `threshold` param; make-rate uses `MAKE_RATE_TREND_THRESHOLD=0.07` (constants). Tests `test_make_rate_decline_detected_with_small_threshold`, `test_make_rate_small_wobble_stays_stable`, `test_default_rpe_threshold_hides_make_rate_moves`.
- [x] **A-M5 — `avg_weekly_reps` computes sets × array-length, not reps** ✅ SQL now `CASE array_length==1 → sets×reps[1] ELSE SUM(unnest)`. Live-DB test `test_avg_weekly_reps_counts_actual_reps`.
- [x] **A-M6 — NULL `exercise_preferences` crashes the whole run** ✅ `(… or {}).get("avoid", [])`. Test `test_prompt_handles_null_exercise_preferences`.
- [x] **A-M7 — Malformed-but-parseable LLM output crashes instead of retrying** ✅ `parse_llm_response` rejects non-dict items via `_is_exercise_list` (→ ValueError → retry); `validate_exercise_names` uses `str(ex.get("exercise_name") or "")`. Tests `test_parse_list_of_strings_raises`, `test_parse_list_of_scalars_raises`, `test_validate_null_exercise_name_no_crash`, `test_generate_session_list_of_strings_retries`.
- [x] **A-M8 — `weeks_to_competition == 0` reported as "no competition date"** ✅ `explain.py` now branches on `is None` (0 → "competition this week"). Test `test_prompt_shows_competition_this_week_when_zero`.
- [x] **A-M9 — Cold-start truncation drops the deload week for general_prep** ✅ caps duration then re-calls `build_weekly_targets` (keeps peak + deload). Test `test_cold_start_general_prep_keeps_deload` (verified fails without the fix).

### LOW — all fixed 2026-07-03 (batch 3), each with a regression test
- [x] **A-L1** — Cost-limit abort now writes a "# Generation Aborted — Cost Limit" rationale via `_mark_program_draft(reason=…)` so the truncated draft explains itself. Test `test_cost_limit_abort_writes_rationale`.
- [x] **A-L2** — `orchestrator.retrieve(…, settings=settings)` so `vector_search_top_k` is honored. Test `test_retrieve_called_with_settings`.
- [x] **A-L3** — `assess.py` uses `sessions_per_week or 4`. Test `test_assess_null_sessions_per_week_defaults_to_4`.
- [x] **A-L4** — Check 2 hard-errors only `COMP_LIFT_REFS`; non-comp lifts warn above `SUPRAMAX_INTENSITY_WARN_PCT` (120). Tests `test_supramaximal_pull_allowed_above_ceiling`, `test_non_comp_lift_absurd_intensity_warns`.
- [x] **A-L5** — new `_validate_session_link()` only links a listed session id; log insert wrapped in try/except+rollback. Tests in `test_log.py`.
- [x] **A-L6** — Documented: max-test intentionally uses CURRENT maxes (attempt a new PR). Test `test_max_test_session_uses_current_maxes`.
- [x] **A-L7** — Extend branch only sets `deload_week` when the profile has a deload. Test `test_extended_no_deload_phase_stays_deload_free`.
- [x] **A-L8** — `cost_limit_usd` uses `is not None` so an explicit 0 is honored. Test `test_cost_limit_usd_zero_is_honored`.

### Refactors — all done 2026-07-03 (batch 3)
- [x] **A-R1/R2/R10** — new `shared/formulas.py` (`round_kg`, `estimate_session_minutes`) replaces 3 rounding copies + 2 duration copies; constants `SECONDS_PER_SET`, `DEFAULT_REST_SECONDS`, `MIN_SESSION_DURATION_MINUTES`, `WARMUP_INTENSITY_CUTOFF_PCT`. Test `test_formulas.py`.
- [x] **A-R5/R6** — new `oly-agent/phase_progression.py` (`decide_next_phase`, `compute_load_adjustments`, `PHASE_SEQUENCE`) is the single source of truth for `plan._advance_phase` + `feedback._compute_phase_verdict` (kills the A-H3 drift); dead code in `plan.plan()` removed. Test `test_phase_progression.py`.
- [x] **A-R3** — `prilepin.py` uses `MIN_SESSION_REPS` + `PRILEPIN_ZONES[0]["optimal"]`, no bare `24`/`return 6`.
- [x] **A-R4** — `feedback.py` make-rate query uses `= ANY(%s)` with `list(COMP_LIFT_REFS)`.
- [x] **A-R7** — `models.py` `fault_exercises` docstring corrected (keyed by movement family).
- [x] **A-R8** — `estimate_missing_maxes` returns `dict[str, float]`; caller + tests updated.
- [x] **A-R9** — `Settings.__post_init__` no longer mkdirs; new `ensure_working_dirs()` called from ingestion entry points. Test `test_config.py`.
- [x] **A-R11** — `attach_source_chunk_ids` caps at `MAX_SOURCE_CHUNKS_PER_EXERCISE` (3), order preserved. Tests in `test_weight_resolver.py`.

---

## INGESTION (oly-ingestion) — batch 5 (2026-07-03)

Code-testable fixes done on this machine (mocked suites + live empty DB). Full
end-to-end embedding/vision verification + the #6 Catalyst re-ingest remain for
the main DB machine.

### HIGH
- [x] **I-H2** ✅ vision OCR `max_tokens`→8192, `stop_reason` checked, mismatch keeps parsed pages (pad/truncate). Tests in `test_pdf_extractor`.
- [x] **I-H3** ✅ migration `0004` adds `UNIQUE(source_id, principle_name)` (dedup-safe); loader targets it + counts by rowcount. Test `test_load_principles_dedup`.

### MEDIUM
- [x] **I-M1** ✅ `find_resumable_run` returns `(id, sections_done)`; pipeline skips processed sections; checkpoint stores a count. Test updated.
- [x] **I-M2** ✅ TABLE sections chunk as prose when unparsed (pipeline) and web path chunks all non-PRINCIPLE content. Test `test_table_section_chunked_not_dropped`.
- [x] **I-M3** ✅ `retag_chunks.compute_topics` re-applies `CHUNK_TYPE_DEFAULT_TOPICS`. Tests in `test_retag_chunks`.
- [x] **I-M4** ✅ `ingest_article` returns a success flag; only successful URLs are marked ingested. Tests in `test_ingest_web`.
- [x] **I-M5** ✅ `last_skipped_count` set on every return path.
- [x] **I-M6** ✅ vision OCR via `create_message_with_retries`, model from settings.
- [x] **I-M7** ✅ PyMuPDF/pdfplumber stages fall through on exceptions. Tests in `test_pdf_extractor`.
- [x] **I-M8** ✅ `principle_extractor` windows large sections (dedup by name); classifier's 3k sample documented as intentional. Tests in `test_llm_helpers`.
- [x] **I-M9** ✅ single `= ANY(%s)` existing-hash lookup + intra-batch `set` dedup (`_partition_new_chunks`). Tests `test_vector_loader_units`.

### LOW
- [x] **I-L1** continuation tolerates `MAX_EMPTY=2` prose windows before stopping. **I-L2** `load_principles` counts by rowcount. **I-L3** `apply_ocr_corrections` wired for soviet-profile sources + no-op entry removed. **I-L4** embed retry on typed OpenAI errors. **I-L5** `load_json` opens `encoding="utf-8"`. **I-L6** `_would_split_pattern` uses `finditer`. **I-L7** section-break patterns capture the full heading line. **I-L8** shared `shared.llm.parse_llm_json` replaces 3 fence-strippers. **I-L9** = A-R9 (done). **I-L10** `_CATALYST_PAGE_SIZE` constant + step-agnostic next-page detection. (Tests: `test_chunker`, `test_llm_helpers`, `test_structured_loader`, `test_ingest_web`.)
- [~] **I-L11 — extract a shared `process_section()`** for `pipeline.py`↔`ingest_web.py`. **Deferred**: a large refactor of the core ingestion path in both entry points; full verification needs live keys/DB, so it's best done on the main DB machine alongside the re-ingest rather than blind here. (The web path skipping `validate_chunk` — the reason I-H1 went unnoticed — is noted for that work.)

---

## WEB LAYER (oly-agent/web) — open

### MEDIUM
- [x] **W-M2 — CSV formula injection in both exports** ✅ new `_csv_safe()` prefixes any str cell starting with `= + - @ \t \r` with `'` (numeric cells untouched); applied to every data row + program name. Tests `test_csv_safe_neutralizes_and_preserves`, `test_export_log_csv_neutralizes_formula_injection`.
- [-] **W-M3 — maxes update/delete** — NON-FINDING (correctly `WHERE athlete_id=$1`, parameterized). Noted for completeness.

### LOW — batch 4 (2026-07-03)
- [x] **W-L4** ✅ `get_job_status` now reads the owner from the job's own embedded args (`info().args[0]`), set atomically at enqueue — the racy `job_owner:{id}` side key is gone. Tests in `test_web_queries.py`.
- [~] **W-L5** — server-local `date.today()` for week math (`dashboard.py`). **Deferred**: a real fix needs an athlete-timezone column (schema migration) + conversion, and the audit rates it minor for single-user-per-account usage. Flagged for a product decision rather than a disproportionate migration.
- [x] **W-L6** ✅ new `_representative_reps_per_set()` gives prescribed volume the same per-set basis as actual (avg for lists, midpoint for ranges), not "first rep only". Tests `test_reps_*`.
- [x] **W-L7** ✅ `_parse_log_date` clamps out-of-window dates (future / older than `MAX_LOG_BACKFILL_DAYS`=365) to today. Tests `test_log_date_*`.
- [x] **W-INFO** ✅ `prefillExercise` reads `data-*` attributes (HTML-escaped) instead of interpolating into JS-string args. Test `test_prefill_uses_data_attributes_not_js_string`.

---

## DEFERRED (main DB machine / product decision)
- [ ] **#6 — Catalyst corpus re-ingest** (see the Ingestion H1 follow-up above).
- [~] **I-L11** — shared `process_section()` refactor (see Ingestion LOW above).
- [~] **W-L5** — per-athlete timezone for week math (needs a tz column; product decision).
