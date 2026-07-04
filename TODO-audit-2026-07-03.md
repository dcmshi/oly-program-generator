# TODO — 2026-07-03 Repo Audit Findings

Reconstructed from the 3-agent parallel audit (web / ingestion / agent-pipeline).
The 5 critical items were fixed in commit `519aad0`. Batch 2 fixed all 9 remaining
agent-pipeline HIGH/MEDIUM findings + W-M2 + ENV1. Batch 3 (2026-07-03) fixed **all
8 agent LOW items + all 11 refactors**, each with a regression test, ruff clean.
Batch 4 fixed the **web LOW items** (W-L4, W-L6, W-L7, W-INFO), leaving only
**W-L5** (server-tz week math) deferred as a product decision (needs a tz column).
**The entire agent-pipeline + web-layer sections of this audit are now closed.**
Still open: the **ingestion findings** (mostly bite only during ingestion → main
DB machine) and the deferred **Catalyst re-ingest**.

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

## INGESTION (oly-ingestion) — open  (most only bite when actually ingesting → main DB machine)

### HIGH
- [ ] **I-H2 — Vision OCR: `max_tokens=4096` too small, `stop_reason` unchecked, mismatch fallback blanks pages** (`pdf_extractor.py:190-220`). Dense 5-page batches truncate; `_split_page_responses` count-mismatch returns `[raw,"",…]` losing pages 2–5 and embedding `=== Page N ===` markers. Fix: check `stop_reason`, raise `max_tokens` (~8192), keep parsed sections on mismatch, shrink batch.
- [ ] **I-H3 — `ON CONFLICT DO NOTHING` inert on `programming_principles`** (`structured_loader.py:74-91`; no UNIQUE in `schema.sql:276-281`). Every reprocess/resume duplicates principles. Fix: add `UNIQUE(source_id, principle_name)` via Alembic, or dedup on content hash.

### MEDIUM
- [ ] **I-M1 — "Resume" reprocesses the whole document** (`pipeline.py:244-255,357-361`; `structured_loader.py:395-413`). `last_processed_page` recorded but discarded → repays all LLM/OCR cost + (with I-H3) duplicates principles. Fix: return & honor `last_processed_page`, or drop the "resumable" claim.
- [ ] **I-M2 — TABLE sections silently dropped** (`pipeline.py:550-565`; `classifier.py:120-125`). `structured_data` never populated → `_parse_table` loads 0 rows; `ingest_web.py:243-269` drops TABLE/PROGRAM/EXERCISE branches with no log. Fix: parse tables (or fall back to chunking as prose) + add missing web branches.
- [ ] **I-M3 — `retag_chunks.py` strips chunk_type baseline topics** (`retag_chunks.py:43`). Recomputes from `keyword_tag` alone, dropping `CHUNK_TYPE_DEFAULT_TOPICS`. Fix: SELECT `chunk_type` too and union defaults, mirroring the chunker.
- [ ] **I-M4 — Failed article permanently marked ingested** (`ingest_web.py:294-298,388`). `fail_run` swallows the error but the URL is still added to progress → never retried. Fix: only add URL on success.
- [ ] **I-M5 — `last_skipped_count` not set on early-return paths** (`vector_loader.py:69,87-99,176`). Dedup stats read 0 exactly when everything was deduped. Fix: set before both early returns.
- [ ] **I-M6 — Vision OCR bypasses retry wrapper, hardcodes Opus** (`pdf_extractor.py:190-193`). Direct `messages.create(model="claude-opus-4-6")`. Fix: route via `shared.llm.create_message_with_retries`, model from settings.
- [ ] **I-M7 — PDF fallback chain only triggers on low text, not exceptions** (`pdf_extractor.py:49-76`). A raising PyMuPDF aborts before pdfplumber. Fix: wrap each stage in try/except.
- [ ] **I-M8 — Silent input truncation loses principles** (`principle_extractor.py:103` `[:8000]`, `classifier.py:235` `[:3000]`). 50k–146k-char chapters scanned only at the head. Fix: log + window large PRINCIPLE/MIXED sections.
- [ ] **I-M9 — Dedup pre-check is N+1 and misses intra-call dups** (`vector_loader.py:73-82`). One SELECT/chunk; two identical chunks in one call both pass → UNIQUE violation aborts the section after paying for embeddings. Fix: single `= ANY(%s)` SELECT + local `set` dedup before embedding.

### LOW
- [ ] **I-L1** program-continuation `break` on first empty window drops later weeks (`pipeline.py:517-518`). **I-L2** stats over-count on failure paths (`pipeline.py:344-346`, `structured_loader.py:93,213`). **I-L3** `apply_ocr_corrections` is dead code — Soviet sources ingested uncorrected (`ocr_corrections.py:61`). **I-L4** OpenAI embed retry matches only `"rate"` substring (`vector_loader.py:207-213`). **I-L5** `load_json` opens without `encoding="utf-8"` (`structured_loader.py:269`). **I-L6** `_would_split_pattern` checks only the first match (`chunker.py:691-697`). **I-L7** chunker section titles are the bare marker not heading text (`chunker.py:480-486`). **I-L8** triplicated fragile LLM-JSON fence-stripping, no reparse retry (`pipeline.py:489`, `classifier.py:256`, `principle_extractor.py:116`). **I-L9** `Settings()` mkdir side effects (dup of A-R9). **I-L10** section indices stored in page columns; `collect_category_urls` hardcodes `page_size=10` (`ingest_web.py:120-124`). **I-L11** `pipeline.py`↔`ingest_web.py` duplication; web path skips `validate_chunk` (why I-H1 went unnoticed) → extract shared `process_section()`.

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

## DEFERRED (main DB machine)
- [ ] **#6 — Catalyst corpus re-ingest** (see the Ingestion H1 follow-up above).
