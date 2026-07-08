# TODO — 2026-07-03 Repo Audit Findings

> **Provenance / completeness (reconciled 2026-07-04):** this doc is the full port
> of the prior Fable session's 24 filed tasks (#1 consolidate report + #2–#24),
> recovered from that session's transcript because its task list did not persist.
> Every task and every sub-item of the batch tasks (#12–#16) is represented here;
> #24 (e2e verification) was completed this session. Nothing from the Fable
> session is now transcript-only.

Reconstructed from the 3-agent parallel audit (web / ingestion / agent-pipeline).
The 5 critical items were fixed in commit `519aad0`. Batch 2 fixed all 9 remaining
agent-pipeline HIGH/MEDIUM findings + W-M2 + ENV1. Batch 3 (2026-07-03) fixed **all
8 agent LOW items + all 11 refactors**, each with a regression test, ruff clean.
Batch 4 fixed the **web LOW items** (W-L4, W-L6, W-L7, W-INFO). Batch 5 fixed
**all ingestion HIGH + MEDIUM + LOW findings** that are code-testable here (mocked
suites + live empty DB). **W-L5** (per-athlete timezone) was implemented 2026-07-04
(migration `0005`). **Every bug-audit finding is now fixed.**

Remaining work (all needs the main DB machine + API keys): **#6 Catalyst re-ingest**,
**I-L11** (shared `process_section` refactor), and the **RAG / LLM / CORPUS ROADMAP**
below (#17–#23 — recovered from the prior audit: model migration, RAG improvements,
and the new-source ingestion table).

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
- [x] **W-L5** ✅ (2026-07-04) migration `0005` adds `athletes.timezone` (default UTC); new `shared.timeutil.today_in_tz()`; dashboard `_current_week` and the log-form default date use the athlete's local today; timezone is settable on the profile page. Tests in `test_web_queries`. (Residual, documented: a few peripheral `date.today()` spots — goal countdown, 14-day adherence cutoff, PR `date_achieved` — still use server-local today; cosmetic.)
- [x] **W-L6** ✅ new `_representative_reps_per_set()` gives prescribed volume the same per-set basis as actual (avg for lists, midpoint for ranges), not "first rep only". Tests `test_reps_*`.
- [x] **W-L7** ✅ `_parse_log_date` clamps out-of-window dates (future / older than `MAX_LOG_BACKFILL_DAYS`=365) to today. Tests `test_log_date_*`.
- [x] **W-INFO** ✅ `prefillExercise` reads `data-*` attributes (HTML-escaped) instead of interpolating into JS-string args. Test `test_prefill_uses_data_attributes_not_js_string`.

---

## DEFERRED (main DB machine / product decision)
- [ ] **#6 — Catalyst corpus re-ingest** (see the Ingestion H1 follow-up above).
- [~] **I-L11** — shared `process_section()` refactor (see Ingestion LOW above).

---

## RAG / LLM / CORPUS ROADMAP — recovered from the prior Fable audit (2026-07-03)

The original audit had a 4th reviewer (RAG) and enhancement recommendations that
weren't in the bug batches above. Recovered from the prior session transcript
(the task list itself didn't persist). These are **enhancements, not bugs** — all
best done on the main DB machine (need keys/live corpus). Bug-fix prereqs for
new-source ingestion (principle UNIQUE #7, resume #8, vision-OCR #9) are **now
done** (batch 5), so #23 is unblocked once the corpus DB is available.

- [ ] **#17 — LLM migration: Sonnet 5 + structured outputs + model housekeeping.** Currently `claude-sonnet-4-6` for llm/generation/explanation (`config.py:37-44`). NOT a plain string swap:
  1. **BLOCKER** — remove `temperature` from `generate.py:511` (0.3) and `explain.py:50` (0.7); non-default sampling → HTTP 400 on Sonnet 5. Drop `generation_temperature`/`explanation_temperature` from config + tests; steer via prompt.
  2. **Thinking default** — Sonnet 5 runs *adaptive* thinking when `thinking` is omitted (4.6 ran off). Our calls omit it → thinking tokens eat `max_tokens` (classifier's 128 would vanish) and `response.content[0].text` (generate:514, explain:53, classifier:256, principle_extractor:116, pipeline:489) may hit a thinking block first. Pass `thinking={"type":"disabled"}` for these structured tasks, or iterate blocks for `type=="text"` + raise max_tokens.
  3. **Tokenizer** ≈ 30% more tokens/text — re-baseline `cost_limit_per_program`. Fix `shared/llm.py:13-15`: `COST_PER_*_TOKEN` hardcodes $3/$15 regardless of model (Opus OCR already mispriced) → per-model pricing dict.
  4. **WIN** — structured outputs (`output_config={"format":{"type":"json_schema",...}}`, Sonnet 5 only) guarantee valid JSON → delete the parse-retry loop + "respond ONLY with JSON" re-prompts + the fence-strippers; fixes the malformed-output crash class by construction.
  5. Optional: classifier's 128-token label pick → `claude-haiku-4-5` ($1/$5).
  6. Verify: assert `response.model.startswith("claude-sonnet-5")`; update `test_generate_utils`/`test_explain` (expect sonnet-4-6 strings). Prompt caching not worth it at ~2,600-token prompts unless #19 widens them.
- [ ] **#18 — RAG quick fixes:** (a) `retrieve.py:104,:143` embed fault tokens with underscores intact ("early_arm_bend") while limiters/emphasis get `.replace("_"," ")` — apply the same to faults (values from `setup.py` FAULT_OPTIONS). (b) *[DONE — A-L2]* `retrieve(settings=…)`. (c) `retrieve.py:118` `[:2]` template cap — remove with #19 or document.
- [ ] **#19 — Session-specific retrieval + diversity + wider snippets** (highest-value RAG, ~½ day, no new infra). Today retrieval runs once per program (`orchestrator.py:139`) and `generate.py:251-266` collapses ~25-35 chunks to ≤4 by INSERTION ORDER, reused identically for every session, truncated to 600 chars. (a) run session-template queries for ALL templates, key results by day/movement. (b) select ≤4 for THIS template sorted by `similarity` DESC; round-robin fault chunks across faults. (c) per-source diversity cap (max 2/source_id). (d) widen `SNIPPET_MAX_CHARS` 600→~1500 (numeric prescriptions live in chunk tails; prompt headroom exists). Re-run eval after.
- [ ] **#20 — Harden retrieval eval** (PREREQ #6; gates #21/#22). `test_retrieval_eval.py` is print-only (no assertions/exit code) and calls `similarity_search` WITHOUT the production `chunk_types` filters. (a) production-parity pass. (b) graded labels → hit-rate@5 + MRR, exit non-zero below baseline. (c) expand 22→~50 queries templated from LITERAL production strings (one per FAULT_OPTION, phase×movement, limiter). (d) update `RETRIEVAL_EVAL.md`.
- [ ] **#21 — Hybrid lexical + vector search (RRF)** (PREREQ #20). Pure vector today (no tsvector/pg_trgm). (a) Alembic: `ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', raw_content)) STORED` + GIN index (no re-embed). (b) `similarity_search(hybrid=True)`: vector top-20 + lexical top-20 CTEs merged via RRF `1/(60+rank)`, `min_similarity` on the vector leg only. (c) optional soft topic boost (not hard filter). Skip HyDE/rerankers (fixed-template queries, 4-chunk budget).
- [ ] **#22 — Embedding upgrade: `text-embedding-3-large` @ dim 1536** (PREREQ #20/#21). Matryoshka truncation → keep `vector(1536)`, no schema change: pass `dimensions=1536` in `_embed_batch`/`_embed`, flip `config.py:33`. Backfill ~50-line script from stored `content` (~3.4M tokens ≈ **$0.45**); HNSW updates in place; re-run eval.
- [ ] **#23 — New source ingestion (the recovered title/author table).** Add each to `SOURCE_PROFILE_MAP` in `chunker.py` BEFORE ingesting (unknown titles fall back to the 900-token programming profile); use owned copies; update the CLAUDE.md source list + corpus totals; re-run the retrieval eval after each batch. Corpus gaps this fills: concrete Soviet loading prescriptions beyond Medvedev, competition tapering/peaking, modern evidence-based programming.

  | # | Source | Profile | Why |
  |---|--------|---------|-----|
  | 1 | **R.A. Roman — *The Training of the Weightlifter*** (Sportivny Press) | soviet | Concrete %/volume/frequency tables feeding Prilepin-style validation. |
  | 2 | **A.S. Medvedev — the not-yet-ingested volume** (check which of *A System of Multi-Year Training* / *Programming and Organization of Training* is source_id 501) | soviet | The other Medvedev volume. |
  | 3 | **Bud Charniga — translated essays** (~~sportivnypress.com~~ → Wayback Machine) | web | FREE web articles on Soviet methodology/restoration/technique. **Site defunct** (see 2026-07-07 note below) — recover from the Internet Archive. Scaffold now drafted: `ingest_web.py --site charniga`. Still the second web target after the Catalyst re-ingest. |
  | 4 | **Tommy Kono — *Weightlifting, Olympic Style* + *Championship Weightlifting*** | programming | Enriches thin `fault_correction` retrieval. |
  | 5 | **A.N. Vorobyev — *A Textbook on Weightlifting*** | theory_heavy | Soviet theory. |
  | 6 | **Verkhoshansky — *Special Strength Training: Manual for Coaches*** | theory_heavy | Fills the strength-limiter query family. |
  | 7 | **Stronger by Science (Nuckols) articles + tapering research** (Pritchard et al.; Storey & Smith 2012) | (new `research` profile?) | Modern evidence-based programming + tapering. |
  | 8 | **Bompa & Buzzichelli — *Periodization*** | programming | `periodization` chunk_type coverage. |

  ### Acquisition update — 2026-07-07 (source availability re-checked)

  **sportivnypress.com is defunct.** The domain no longer resolves (DNS `ENOTFOUND`);
  search engines still serve stale index entries. Andrew "Bud" Charniga died
  **January 2025** ([USAW obituary](https://www.usaweightlifting.org/news/2025/january/27/celebrating-the-life-of-andrew-bud-charniga)),
  and the domain lapsed since. This changes how rows 1–6 are obtained.

  **Row 3 (Charniga free essays) → Internet Archive, no OCR.** The essays were
  freely, publicly published ("viewable without password"), and are HTML — so
  they go through `ingest_web.py`, *not* the vision-OCR path. Recovery mechanics
  (scaffold drafted this session in `ingest_web.py`, `--site charniga`):
  - **Enumerate** archived URLs via the Wayback CDX API:
    `http://web.archive.org/cdx/search/cdx?url=sportivnypress.com/*&output=json&fl=original,timestamp&filter=statuscode:200&filter=mimetype:text/html`
    → dedupe to the latest capture per URL, skipping WP plumbing/taxonomy/feed/asset URLs.
  - **Fetch raw captures** with the `id_` suffix to strip the Wayback toolbar/JS:
    `http://web.archive.org/web/<timestamp>id_/<original-url>` → clean HTML for `block_text()`.
  - **DB-machine TODO before the full run:** confirm the real WordPress content
    class (scaffold guesses `.entry-content` → `.post-content` → `<article>`) against
    one live snapshot, then run `--site charniga --dry-run` to sanity-check the URL
    list, then a `--limit` smoke test, then the full run + retrieval-eval refresh.
    Progress tracked separately in `sources/charniga_progress.json`.

  **Rows 1, 2, 4, 5, 6 (the books) → buy the EPUB/Kindle; do NOT hunt PDFs to OCR.**
  Clean ebook text is better corpus input than OCR'd scans (cf. Everett/Israetel EPUBs
  vs. the vision-OCR'd Laputin/Medvedev). "Use owned copies" (table header) stands.
  Legitimately purchasable as of this check:
  - **Medvedev** — [*A System of Multi-Year Training in Weightlifting*](https://www.amazon.com/System-Multi-Year-Training-Weightlifting-Russian-ebook/dp/B08HYBSGWS) (Kindle B08HYBSGWS)
    **and** [*A Program of Multi-Year Training*](https://www.amazon.com/Program-Multi-Training-Weightlifting-Russian-ebook/dp/B08H8S4WT4) (Kindle B08H8S4WT4) — resolves row 2:
    buy whichever is **not** already `source_id=501`.
  - **Roman** — *The Training of the Weightlifter* (Charniga's Russian Weightlifting Library ebook compilation; on Amazon/Kobo/Google Play).
  - **Vorobyev** — [*A Textbook on Weightlifting*](https://www.amazon.com/textbook-weightlifting-N-Vorobyev/dp/B0007BVA9M).
  - **Verkhoshansky** — *Fundamentals of Special Strength Training in Sport* (Charniga translation; retailers/Goodreads).
  - **Charniga compilations** — [*Weightlifting Training and Technique*](https://www.kobo.com/ww/en/ebook/weightlifting-training-and-technique) (Kobo) and *…and Biomechanics*.
  - Fallback where a title is truly unsold: library / interlibrary loan or archive.org
    controlled digital lending (borrow-to-read). Reserve `--vision` OCR for scanned
    copies you actually own.
