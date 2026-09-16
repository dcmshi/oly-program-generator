# Handoff — model bump (Sonnet 4.6 → Claude 5 family) + dogfooding

Written 2026-09-15 mid-session so the next session can resume without the transcript.
Update or delete this file when the items below are done. Standing constraints from the
user: **no subagents**; one commit per fix/feature with tests, gated on the pytest exit code
(never on a pipe); run the Makefile's `AGENT_TESTS` / `INGESTION_TESTS` lists with
`PYTHONUTF8=1 uv run pytest …` from the subsystem dir (`make` is not on PATH in Git Bash);
lint with `uvx ruff@0.15.22 check .`. Org policy: never suggest signing up for a new hosted
vendor without Fellow Security approval; never print API keys.

## Agreed order of work (user: "let's do the dogfood items first before reingestion")

| # | Item | State |
|---|------|-------|
| 1 | `light_model` role (Haiku 4.5 for classifier fallback / `--contextualize` / relabel / golden grading) | **done** `c355552` |
| 2 | Claude 5-safe request/response surface + per-model pricing | **done** `97f54a3` |
| 3 | Model-baseline tool: Sonnet 4.6 vs Sonnet 5 (± thinking/effort) on the real pipeline | **next** — design below, nothing written yet |
| 4 | DOG-1: import the real General Strength CSV as a completed program, then generate the next block for athlete `dshi` (id=1) and review | design below, nothing written yet |
| 5 | Run the baseline (item 3) against athlete 1 **after** DOG-1 so the "Previous Program" prompt block is real; decide whether `DEFAULT_LLM_MODEL` / `generation_model` move to `claude-sonnet-5`; record numbers in `TODO.md` + `docs/RETRIEVAL_EVAL.md` | pending |
| 6 | DOG-2: Catalyst re-crawl test (`ingest_web.py --dry-run`, `--limit 5`, full run; measure chunks/article) | pending, see TODO §11 |
| 7 | TODO item: evaluate other open providers (user named "kimi-k3" and "flm-5.3" — ids unverified; any new hosted vendor needs Fellow Security approval, or run open weights locally) | pending — add to TODO §11 |
| 8 | Only then: the 7-PDF re-ingest + corpus-DB ops in `docs/DB-MACHINE-RUNBOOK.md` §8b/§9 (`--contextualize --context-model` now defaults to `light_model`), `relabel_chunk_types.py`, `retag_chunks.py`, `eval.build_golden`, `eval.run_eval --update-baseline` | pending |

## What landed in items 1–2 (so you don't re-derive it)

- `shared/config.py`: `llm_model` / `light_model` / `generation_model` / `explanation_model`
  (env `LLM_MODEL`, `LIGHT_MODEL`, `GENERATION_MODEL`, `EXPLANATION_MODEL`; defaults
  `claude-sonnet-4-6` / `claude-haiku-4-5-20251001`), plus `generation_thinking` /
  `generation_effort` / `explanation_thinking` / `explanation_effort` (env
  `GENERATION_THINKING` …; blank = model default) and `explanation_max_tokens` (1024).
- `shared/llm.py`: `MODEL_PRICING_PER_MTOK` (Sonnet 5 $2/$10, Sonnet 4.6 $3/$15, Haiku 4.5
  $1/$5, Opus 4.x/5 $5/$25, Fable $10/$50; longest-prefix match, unknown → Sonnet 4.6 rates
  with one warning), `estimate_cost(in, out, model, cache_read_tokens=, cache_creation_tokens=)`,
  `message_text(response)` (text blocks only; raises `LLMRefusal` on an empty refusal),
  `sampling_kwargs(model, temperature)` (drops `temperature` on Sonnet 5 / Opus 4.7+ / Opus 5 /
  Fable), `thinking_kwargs(model, mode, effort)` (4.6+ only; `disabled` is a no-op where thinking
  is already off; validates values), `usage_tokens(usage)`, `light_model_for(settings, explicit)`.
- Every `content[0].text` reader now goes through `message_text`. `generate.py` / `explain.py`
  build their request kwargs from the helpers; `GenerationResult` carries `cache_read_tokens` /
  `cache_creation_tokens`; the orchestrator's cost guard and `generation_log.estimated_cost_usd`
  price at the role's model.
- Facts from the claude-api skill that drove this: Sonnet 5 rejects non-default
  `temperature`/`top_p`/`top_k` and `budget_tokens` (400); omitting `thinking` runs adaptive on
  Sonnet 5 / Opus 5 (off on 4.6); effort via `output_config={"effort": …}`; Sonnet 5's tokenizer
  yields ~30% more tokens for the same text, so compare **cost**, not token counts; `max_tokens`
  may need headroom on v5 (thinking tokens count against it).
- API key check (2026-09-15): both keys work; models visible: claude-fable-5, claude-fable-5-1,
  claude-haiku-4-5-20251001, claude-opus-4-5-20251101, claude-opus-4-6/4-7/4-8, claude-opus-5,
  claude-sonnet-4-5-20250929, claude-sonnet-4-6, claude-sonnet-5; OpenAI text-embedding-3-small/large.

## Item 3 design — `oly-agent/eval/model_baseline.py` (+ `orchestrator.run(max_sessions=)`)

- `orchestrator.run(athlete_id, settings, dry_run=False, deadline=None, max_sessions=None)`:
  before generating each session, `if max_sessions is not None and len(all_sessions_data) >= max_sessions: capped = True; break` (break both loops); skip the max-test session when capped;
  still run EXPLAIN (it is part of the per-program cost); prefix the rationale with
  `# Partial Program — Session Cap …`; the program stays `draft`. Test in
  `tests/test_orchestrator.py` with a 4-session plan (`_two_session_plan()` style fixture,
  `ProgramPlan(... session_templates=[_session_template(1..4)])`) asserting
  `mocks["generate"].call_count == 2` and that an `execute` call writes a rationale containing
  "Session Cap".
- CLI: `PYTHONUTF8=1 uv run python -m eval.model_baseline --athlete-id 1 --sessions 8 --config claude-sonnet-4-6 --config claude-sonnet-5 --config claude-sonnet-5:disabled:low [--delete]`.
  Each `--config` is `model[:thinking[:effort]]` → `Settings(generation_model=m, explanation_model=m, generation_thinking=t, generation_effort=e, explanation_thinking=t, explanation_effort=e)`.
- Per config: monkeypatch `generate.create_message_with_retries` and `explain.create_message_with_retries`
  with a recorder that wraps the real function and captures per call: model, elapsed seconds,
  `usage_tokens(response.usage)`, `stop_reason`, cost via `estimate_cost(..., cache tokens)`. This is
  the only place cache tokens and latency are visible (generation_log stores in/out tokens only).
  Then `program_id = orchestrator.run(athlete_id, settings, max_sessions=N)`; read
  `generation_log` for that program: attempts per (week, day), final status per session, counts of
  `parse_error` / `validation_error` / `failed`, `array_length(validation_errors,1)`.
- Report: markdown table (config | sessions | attempts | first-try OK | parse err | validation err |
  in/out/cache-read tokens | cost | wall s | mean call s | cost per session) printed and written as
  JSON to `oly-agent/eval/model_baseline_<UTC ts>.json` (commit the JSON; no prompts in it).
  Rename each program `name || ' [baseline: <config>]'` so it can be reviewed in the UI; `--delete`
  removes them (NULL `training_logs.session_id` first — none exist for fresh drafts — then delete
  `generated_programs`, which cascades).
- Tests `tests/test_model_baseline.py` (no DB/LLM): `parse_config` (incl. bad thinking/effort →
  ValueError), `summarize_log_rows` on synthetic rows, `render_table`. Add to Makefile `AGENT_TESTS`
  after `tests/test_eval_harness.py` (edit the Makefile with a Python line-based insert — `sed`
  inserts literal `\n` on this machine).
- Also record thinking/effort in `generated_programs.generation_params` (orchestrator writes
  `{"model", "temperature", "top_k"}` today).

## Item 4 design — `oly-agent/import_program_csv.py` (DOG-1)

Source: `C:\Users\Shibi\Desktop\General Strength - 3 Block_11W - P2 General Strength.csv`
(400 rows, 19 columns). Verified layout:

- `GENERAL STRENGTH  BLOCK n` (block header) → `Week N - <CYCLE NAME>` (week header; **the file's
  week numbers restart/skip per block — 1,2,3 / 4,2,3,4 / 8,2,3,11 — number weeks sequentially by
  occurrence, 1..11**; cycle names: DEFICIT / VELOCITY / GENERAL CYCLE) → `Day n` header rows
  (`Day 3 ` has a trailing space; columns `%,Reps,%,Reps,…,Total Reps,Avg Intensity %,Volume`;
  use `header.index("Total Reps")` to bound the pairs) → one row per exercise → a totals row
  (first cell empty: total reps, avg intensity, volume) → week summary rows (`Week Intensity Avg`).
- Pairs `(pct, reps)`: reps cell forms `3`, `3x3` (sets×reps), `3+3` (complex, one set),
  `4x3+3`, `2x2+2+2`, `6x1+1`, `3x1+2`, `8x2`, `10x1`, `1RM` (→ 1 rep, `is_max_attempt`).
  Regex: `^(?:(\d+)x)?(\d+(?:\+\d+)*)(?:-\d+)?(?:/s)?$`; `reps` = first `+` part (primary
  movement), keep the full notation in `notes` when it has `+`. Unloaded rows put `5x5` / `3x5` /
  `3x12-15` / `4x8/s` / `2x6` / `3x10` in the **pct** column (Box Jumps, Vertical/Broad Jumps,
  Jumping Squats, Back Extensions, Bulgarian Split Squats, BB/Pendlay Rows) → sets×reps, no
  intensity. `Core,,,,` has no prescription → store sets=1, reps=1, notes "unspecified".
- `intensity_pct` CHECK is `≤ 120`; the file has `125` (Clean Deadlift 6" Block) → clamp to 120
  and put the true value in `notes`. `Muscle Snatch (% of MSN 1rm)` / `Stiff Leg Deadlift (% of CL)`
  say their reference in the name.
- One `session_exercises` row **per (exercise, pair)** — that is the generator's own convention
  (warm-up ramp rows at 50–60% are separate rows; `is_warmup_set` keys off comp lift ≤ 60%).
  `exercise_order` sequential within the session. `intensity_reference` = the max the % refers to,
  pipeline-style: snatch family (Snatch, Power/Muscle/Hang/No Foot/Block Snatch, Snatch
  Extension/Pull to Hip/Deadlift/Grip DL/RDL, SN Balance, +OH SQ complexes) → `snatch`; clean family
  (Clean, Power/Block Clean, Clean Extension/Pull to Hip/Deadlift, Mid Grip DL, SLDL (% of CL)) →
  `clean`; any clean complex ending in a jerk (`Clean + Jerk`, `Power Clean + Jerk`, `Clean Deadlift +
  Clean + Jerk`, `Power Clean + FSQ + Jerk`) → `clean_and_jerk`; Jerk from Rack / Power Jerk / Jerk
  Dips → `jerk`; Push Press / BTN Push Press → `push_press`; Back Squat variants (60s rest, Close
  Stance, Partial 1/4) → `back_squat`; Front Squat variants (1+1/4) → `front_squat`; unloaded → `None`.
  Athlete 1 current maxes: Snatch 70, Clean 92, Clean & Jerk 92, Front Squat 110, Back Squat 150 (no
  push_press/jerk max → `absolute_weight_kg` NULL there; `resolve_weights` from `weight_resolver`
  does the rounding and warns).
- `exercise_id` via an explicit alias map onto the 53-row catalogue (ids: Snatch 1, Power Snatch 2,
  Power Snatch from Blocks 3, Hang Snatch (above knee) 4, Muscle Snatch 7, No Feet Snatch 11, Snatch
  Balance 12, Overhead Squat 14, Snatch Pull 15, Snatch Pull from Deficit 16, Snatch Deadlift 19,
  Clean 21, Power Clean 22, Clean Pull 28, Clean Deadlift 31, Jerk 32, Power Jerk 33, Push Press 38,
  Back Squat 39, Front Squat 40, Clean & Jerk 44, Deadlift 58); complexes take the primary lift's id
  and keep the full name in `exercise_name`; anything else `exercise_id NULL` (the generator's recent-log
  block is keyed by `exercise_name`, so NULL ids are harmless). Print unmapped names in `--dry-run`.
- Program row: `athlete_id 1`, name `General Strength — 3 Block / 11W (imported)`, `phase`
  `--phase` default `general_prep` (enum `training_phase`: general_prep, accumulation,
  transmutation, intensification, realization, competition, deload, transition), `duration_weeks 11`
  (CHECK ≤ 16), `sessions_per_week 4`, `--start-date` default today − 77 days, `end_date` today,
  `athlete_snapshot` via `orchestrator._build_athlete_snapshot(athlete_row)`, `maxes_snapshot`
  `{to_intensity_ref(name): kg}` from `athlete_maxes` (`max_type='current'`), `generation_params`
  `{"source": "csv_import", "file": <basename>}`, `rationale` describing the block structure. NOT NULL:
  `athlete_snapshot`, `maxes_snapshot`, `generation_params`, `session_exercises.sets/reps` (CHECK ≥ 1).
- `program_sessions`: `session_label` `"<Cycle Title> · Day n"`, `focus_area` = first comp-lift
  family in the day, `estimated_duration_minutes` via `orchestrator._estimate_duration`, `notes`
  with the CSV totals row.
- `--log-as-completed`: per session insert `training_logs(athlete_id, session_id, log_date =
  start + 7*(week-1) + day_offset)` and per exercise row `training_log_exercises(log_id,
  session_exercise_id, exercise_id, exercise_name, sets_completed=sets, reps_per_set=[reps]
  (shorthand form the feedback SQL understands), weight_kg=absolute_weight_kg or 0 (NOT NULL),
  prescribed_weight_kg, weight_deviation_kg=0, make_rate=--make-rate (default 1.0) on comp-lift
  refs, rpe NULL)`; then `feedback.compute_outcome(program_id, 1, conn)` + `save_outcome` (sets
  `status='completed'`, `end_date`, `outcome_summary`). Without the flag insert with
  `status='completed'` and no outcome (plan.py tolerates a missing `outcome_summary`).
- `--dry-run` prints the parsed weeks/days/rows + alias resolution; `--replace` deletes a previous
  import of the same file (delete `training_log_exercises` → `training_logs` for the program's
  sessions, then the program; cascades cover sessions/exercises/generation_log).
- Tests `tests/test_import_program_csv.py` (no DB): reps-cell parser table, a 2-week inline CSV →
  structure, alias/intensity-ref resolution, the 125 → 120 clamp, log-date arithmetic. Add to
  Makefile `AGENT_TESTS`.
- Then: `PYTHONUTF8=1 uv run python import_program_csv.py --file "<csv>" --athlete-id 1 --dry-run`,
  real run with `--log-as-completed`, then `PYTHONUTF8=1 uv run python orchestrator.py --athlete-id 1`
  (or the web UI with the worker running) and review per TODO DOG-1 (phase progression, Previous
  Program block, exercise selection vs the real block, Prilepin volume vs the CSV per-day totals,
  retrieved context per session). Local DB today: athlete 1 (`dshi`, intermediate, 4/wk, goal
  general_strength, fault slow_turnover) has one draft program (id 5, accumulation, 2026-03-10) and
  no completed programs.

## Tooling pitfalls (this machine / this harness)

- The Bash tool mangles quoted heredocs that contain `\\` **or** many single quotes (bash reports
  "unexpected EOF while looking for matching `''"). Write patch scripts and test fragments with the
  Write tool into the scratchpad dir and run them with `python <script>`; append fragments with
  `cat >>` or a Python splice. Memory notes: `feedback-bash-heredoc-backslashes.md`.
- Insert new tests **before** a file's `if __name__ == "__main__":` runner block so both pytest and
  the script-runner mode see them.
- `docker exec oly-postgres psql -U oly -d oly_programming -At -c "…"` for quick DB checks (no `-it`).
