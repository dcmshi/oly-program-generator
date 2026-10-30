# Agentic planning and on-demand retrieval — revised design

First draft by Kimi K3 (2026-10-28); reviewed and revised 2026-10-30. The work items are **AGENT-0a … AGENT-3** at the top of `TODO.md`. The first draft proposed one sequential ReAct loop for the whole program; this revision keeps its findings, its tool-safety rules, its transactional-repair rule and its reproducibility concerns, and replaces the runtime with a staged plan that fits the worker deadline and the cost guard, and measures before it adds model autonomy.

## What the repo pass found (unchanged, verified 2026-10-30)

- Retrieval is per session but automatic: `orchestrator._session_chunks()` (`oly-agent/orchestrator.py:914`) calls `retrieve.retrieve_session_context()` (`oly-agent/retrieve.py:301`) with a code-built query before the session call. The model cannot ask a question, inspect results or refine the query.
- The model fills sessions inside a fully predetermined plan. `plan.plan()` fixes every week's targets and the shared session templates; weeks 2..N see week 1's same-day session (`block_templates`, ≤ `BLOCK_TEMPLATE_MAX_CHARS`), not the previous week, so they can run concurrently (`_run_weeks`, `orchestrator.py:742`, AUD-4).
- Validation covers one session plus the running weekly totals (`validate.validate_session`, `oly-agent/validate.py:480`). Nothing checks the program as a whole.
- EXPLAIN is a rationale, not an evaluation, and sees the weekly targets plus the first 4 exercises of Week 1 Day 1 (`explain._build_explain_prompt`, `oly-agent/explain.py:145`).

## Review of the first draft

What it got right and this revision keeps: code stays in charge of phase, block length, weekly intensity / volume envelope, deload, coach limits, complexity, allowed exercises, weight resolution and validation; the model chooses only inside those limits. No LangGraph, no subagents. Model-callable tools are read-only, never take SQL, table names, filters or URLs, go through `VectorLoader.similarity_search()` with its embedding-model, quarantine, similarity-floor and soft `chunk_type` rules, and render chunks through `shared.excerpt.focused_excerpt()` as delimited untrusted data. A revision is kept only if every hard check passes and it adds no new failure; the best validated candidate is saved, not the last. Agentic behaviour ships opt-in.

What changed, and why:

1. **It could not finish inside the worker deadline or the cost guard.** The ARQ job is `JOB_TIMEOUT_SECONDS` = 600 s with a 30 s margin (`web/worker.py:29`); `cost_limit_per_program` = $1.00. Sol at adaptive + medium is 24.7 s and $0.011 per session call (MODEL-3, `model_baseline_20261011T215736Z.json`). Sequential generation already overran the deadline at 24 sessions (6 min 40 s at Kimi's 16.7 s/call — the reason AUD-4 exists). The draft made weeks sequential *and* made each session several calls. Estimate for a 24-session block: ~75 calls (24 × ~2.5 design + search turns, plus week designs, brief, evaluation, one revision round) ≈ 30 min, cost near or over the $1.00 guard against ≈ $0.26 today. An estimate, not a measurement — but an order of magnitude past the deadline either way.
2. **The problem was not shown to exist.** Dense retrieval scores nDCG@5 0.796 on the golden set and the listwise rerank's +0.02 sat inside run-to-run noise (`docs/RETRIEVAL_EVAL.md`); Sol is 8/8 first-try with 93–95 % of exercises citing a chunk and comp-lift reps within a mean |Δ| of 2.1 of the target. Nothing measured shows a model-written query beating `build_session_query` (template movements + phase + intensity band + level + faults/emphasis/limiters). The missing measurement is program quality as a coach judges it — which the draft put last.
3. **Model-issued queries are invisible to the retrieval eval.** The golden set grades the code-built session queries; ungraded chunks score 0, so `run_eval` cannot score an agent's searches.
4. **One action schema breaks the structured-output rules.** ~11 actions in one schema with "per-action required fields enforced in Python" means mostly-optional properties; the API caps a schema at 24 optional properties summed over the whole thing and requires a plain top-level object (STRUCT-1). Native tool calling (one strict tool per action) is the supported mechanism; it needs a live check through OpenRouter's Anthropic-compatible endpoint, where `require_parameters: true` then also filters on tool support.
5. **Prompt caching.** "One continuing interaction" and "compact state views supplied per turn" pull against each other: re-rendered views move the cached prefix, an append-only conversation grows with every search. The static-first prefix (`PROMPT_STATIC_DYNAMIC_MARKER`, `generate.prompt_content_blocks`) works because each call is fresh with a stable prefix; keep that shape.
6. **Self-graded evidence.** `sufficient / refine / unavailable / not_needed` would come from the generator itself — the pattern the OCR rule bans ("never ask the model to grade its own transcription"). The checkable part is the claim → excerpt mapping: the excerpt must occur in the cited chunk and its numbers must match.
7. **An uncalibrated reviewer could drive revisions.** "Accept when no new blocking findings" makes the reviewer judge its own repair. Until coach labels exist, only deterministic audit failures trigger a revision.
8. **Resume.** RESUME-1 is a plan snapshot plus saved sessions. Resuming mid-loop with evidence snapshots and candidate versions is far more state; the revised Stage 1 artifact rides in the existing snapshot.
9. **Thread safety (for any per-session tool loop).** `VectorLoader` holds a psycopg2 connection and the session query cache is a plain dict, so retrieval is main-thread only (AUD-4). A tool loop inside concurrent week workers needs a per-worker loader or a locked search service — the draft avoided this only by going sequential.

## Revised architecture

Four stages. Each is useful on its own and gates the next; nothing past Stage 0 is on by default until its gate passes.

### Stage 0 — measure, plus two free fixes (AGENT-0a/b/c)

No new LLM calls in the pipeline.

- **`audit_program()` (AGENT-0a)** — new `oly-agent/program_audit.py`, deterministic, run after the last session and before EXPLAIN on every program, and as `python -m eval.program_audit <program_id>` over existing programs for a baseline. Checks:
  - completeness: every scheduled (week, day) slot has a non-empty session;
  - weekly comp-lift reps vs the week's target (`volume_tables.session_rep_target` summed), and intensity ceiling / floor per week vs `WeekTarget`;
  - block shape: the realised weekly volume / top-intensity series follows the plan's direction (ramp, deload lower than the week before);
  - exercise drift: the primary-slot exercise for the same day changing across weeks without the template asking for it (program 29's Jerk → Push Jerk → Jerk from Behind Neck);
  - coach constraints (intensity cap, avoid list, sessions per week) on the realised sessions;
  - citation integrity: every `source_chunk_ids` entry was in that attempt's `generation_log.retrieval_set`.

  Findings are `error` / `warning` / `info` with (week, day, exercise) and a reason. The report goes in `generation_params.audit` (no migration). It never fails a program and never costs a paid retry in Stage 0.
- **EXPLAIN sees the whole program (AGENT-0b)** — replace the Week 1 Day 1 sample with a compact one-line-per-session view (`name sets×reps@%` for the main exercises) capped by a new `EXPLAIN_PROGRAM_VIEW_MAX_CHARS`, plus the audit summary, so the rationale can't claim a progression the audit contradicts.
- **Coach rubric and labelled set (AGENT-0c)** — a written rubric (progression across the block, exercise selection vs the athlete's faults and limiters, volume / intensity fit for level and phase, variety vs drift, session feasibility, warm-up and deload handling), each criterion scored 1–5 with a free-text note. Coach Paul grades the EXAMPLES programs plus a few legacy drafts through the share page; grades live in `oly-agent/eval/coach_labels.json` keyed by program id. This is the only ground truth any later stage is gated on, and it needs a person — it is the long pole.

**Gate to Stage 1:** the audit baseline over the existing programs shows which failures are real (drift, block shape, volume) and the first coach grades say where programs fall short. If neither points at exercise selection or block coherence, Stage 1 is not the next thing to build.

### Stage 1 — design the block first, then generate sessions concurrently (AGENT-1)

The main architectural change. Planning ahead is the right shape here because the outer plan is already fixed by code: the decisions left to the model are exercise selection and progression inside known weekly envelopes, which can be decided for the whole block at once. That is where active retrieval earns its cost — once per program, not once per session.

- **Program design call.** New `oly-agent/program_design.py`: `design_program(g) -> ProgramDesign | None`, one bounded tool loop before any session is generated.
  - Input: the static prompt prefix (athlete, maxes, history, catalogue, avoid list — the same text the session prompt caches), the plan (phase, every `WeekTarget`, session templates, strength curve, coach constraints), and the previous block's outcome summary.
  - Tools (native tool calling, strict schemas, read-only): `search_knowledge(query, purpose)`, `read_chunks(chunk_ids, query)`, `lookup_templates(filters from a closed enum)`, and the terminal `submit_design`. Caps: `PROGRAM_DESIGN_MAX_TURNS`, `PROGRAM_DESIGN_MAX_SEARCHES` (start at 4), result size via `SNIPPET_MAX_CHARS`. Searches run on the main thread before any worker starts, so the AUD-4 thread rule holds.
  - Output (Pydantic, mirrored as the `submit_design` schema): `brief` {intent, progression notes, risks}; `weeks[]` → `days[]` → `slots[]` {role: primary / secondary / strength / accessory, exercise_name, progression_note}; `evidence[]` {label, chunk_id, claim, excerpt}.
  - If the tool check fails (see AGENT-1 prerequisite), the fallback is two structured-output calls — "propose up to N queries" → code runs them → "submit design" — with no open loop.
- **Deterministic `validate_design()`.** Week and day counts equal the plan; every exercise is in this athlete's catalogue (complexity, equipment, avoid list, injuries); each day's primary slot is in the template's primary movement family; the design carries no numbers that override a `WeekTarget`; each evidence excerpt occurs in its chunk (whitespace-normalised) and its numbers appear in the excerpt. One repair turn on failure; a second failure drops the design and generation runs the legacy path. A design problem never fails the program.
- **Generation uses the design.** `block_templates` becomes keyed by (week, day) and is rendered from the design as `## Block Plan (Week W, Day N)` below the static marker. Because no week depends on another, week 1 no longer has to run alone first: all weeks go through `_run_weeks_concurrently`, which is *faster* than today. Session retrieval stays `retrieve_session_context` (the gated, golden-set-measured path); the design's evidence chunks for that day may join the session's candidate pool, measured separately before it is kept. `validate_session` adds a *warning* (never a paid retry) when a session leaves a slot the design fixed.
- **Persistence and resume.** The design is stored in `generation_params.resume.design` through `_plan_snapshot` / `_apply_plan_snapshot`, so a resumed run reuses it and pays nothing. Its call is a `generation_log` row; the migration for a `stage` column is deferred until Stage 2 needs it — until then the row is identified by `week_number = 0`, or the migration lands with AGENT-1 if a null week breaks a consumer (check `eval/model_baseline.py`, `web/queries/admin.py` and resume spend accounting).
- **Guards.** The design call goes through `_SpendGuard` and the deadline like any session; `FlexUnavailable` propagates as everywhere else (it is a `BaseException` and an abort the user chose — never swallow it into a legacy fallback).
- **Prompt goldens.** With the design off, every prompt stays byte-identical to `tests/fixtures/prompt_golden_*.txt`; add a golden for the design-on prompt.
- **Setting.** `PROGRAM_DESIGN_ENABLED` (default `False`) in `shared/config.py`, numeric caps in `shared/constants.py`, documented in `oly-ingestion/.env.example`; `eval/model_baseline.py --program-design` runs both arms.

Expected cost: 3–6 calls once per program (≈ $0.03–0.08 at Sol medium, an estimate) and less wall-clock than today because week 1 stops being serial.

**Gate to default-on:** on the same athletes, the audit's drift and block-shape findings drop, coach grades are at least equal on every rubric criterion, first-try validity stays 8/8, the design passes `validate_design` first-try on ≥ 90 % of runs, and cost per program rises ≤ $0.10.

### Stage 2 — one repair round driven by the audit (AGENT-2)

- A program whose audit has `error` findings gets one round: regenerate only the implicated sessions — plus later sessions of the same day when the finding is about progression — through the existing `_generate_session`, with the finding appended to the dynamic tail (the cached prefix is untouched). The candidate is built beside the original.
- Re-run the full audit on the candidate. Keep it only if it has no `error` findings and no finding the original didn't have; otherwise keep the original. Record both in a new `generated_programs.evaluation` JSONB (migration 0022, with `generation_log.stage`) and surface unresolved findings in the rationale and on the program page.
- **An LLM reviewer exists only in the eval for now.** `eval/program_review.py` runs a rubric-scored, schema-constrained review (a different model family from the generator, as with `judge_model`), and its per-criterion scores are compared with `coach_labels.json` the way `judge_agreement` compared judges. It enters the pipeline — as advisory findings, then possibly as a repair trigger — only after its agreement with the coach is measured and acceptable.

### Stage 3 — session-level tool loop, only if Stage 1 leaves a measured gap (AGENT-3)

Build this only if, after Stage 1, the coach grades or the audit show sessions failing for lack of evidence (e.g. a fault or limiter with no supporting chunk), not by default.

- Triggered, not universal: when the composed session context has no fault / limiter chunk for an athlete who has them, or when validation fails for a reason evidence could fix. Otherwise the session call is unchanged.
- One fresh call per session with the cached static prefix and a small tool loop inside it (≤ 2 searches), never one conversation across sessions.
- Retrieval from worker threads needs a per-worker `VectorLoader` (its own connection and query cache) or a locked search service; the `ContextDiversityState` rotation stays on the main thread.
- Scoring: model-issued queries need graded chunks, so extend the golden set with the queries the loop actually issued (`build_golden --extend`) before reading any retrieval metric.

## Cross-cutting requirements (from the first draft, kept)

- **Citations need support, not just existence.** For consequential choices store claim + excerpt + chunk id, verified deterministically as in `validate_design`. A choice may be marked as coach rule or heuristic instead of forcing a citation. Applies to the design in Stage 1; the session prompt's `[Cn]` citations keep their current contract.
- **Provenance.** Add the chunk's content hash (the dedup SHA-256 already computed) and the retriever settings to each `retrieval_set` entry, plus prompt and model versions on the program record, so a run can be read after a re-ingest. Cheap; do it with AGENT-1.
- **Budget exhaustion** produces the legacy path or a resumable draft, never a reported clean audit or review.
- **Retrieval metrics and program evaluation stay separate.** `run_eval` remains the retriever gate; the audit and coach labels judge programs.

## Literature

- **[ReAct](https://arxiv.org/abs/2210.03629)** / **[IRCoT](https://arxiv.org/abs/2212.10509)** — interleaved reasoning and retrieval; the model for Stage 1's bounded loop and Stage 3's triggered one.
- **[ReWOO](https://arxiv.org/abs/2305.18323)** — the first draft dismissed planning up front because programming decisions depend on evidence. At program level they mostly don't: code fixes the envelope, and Stage 1 searches while drafting the design. Its token-efficiency argument is why the design is made once and reused by every session.
- **[FLARE](https://aclanthology.org/2023.emnlp-main.495/)** / **[Adaptive-RAG](https://aclanthology.org/2024.naacl-long.389/)** — retrieve only when needed; the basis for Stage 3's triggers.
- **[CRAG](https://arxiv.org/abs/2401.15884)** — a retrieval-quality gate with corrective action; here the gate is deterministic (excerpt and number checks), not self-graded.
- **[Self-RAG](https://arxiv.org/abs/2310.11511)** — conceptual only; no trained reflection tokens.
- **[Reflexion](https://arxiv.org/abs/2303.11366)** — bounded feedback-driven revision; Stage 2 uses deterministic feedback and an accept/reject rule instead of trusting self-feedback.
- **[Search-R1](https://arxiv.org/abs/2503.09516)** — trained search policies; out of scope, and a reminder that prompted search behaviour has to be measured.
- **[RAGChecker](https://arxiv.org/abs/2408.08067)** — separate retrieval and generation diagnostics.
- **[Judging LLM-as-a-Judge](https://arxiv.org/abs/2306.05685)** / **[LLM Evaluators Recognize and Favor Their Own Generations](https://arxiv.org/abs/2404.13076)** — position, verbosity and self-preference biases; why the reviewer stays in the eval until calibrated against the coach.

## Docs to update as stages land

`ARCHITECTURE.md` (generation sequence), `CLAUDE.md` (the AUD-4 invariant once week 1 stops running alone; the new design / audit invariants), `docs/SCHEMA.md` (migration 0022 with Stage 2), `docs/RETRIEVAL_EVAL.md` (Stage 3 golden-set extension), the Makefile `AGENT_TESTS` list for each new no-key suite.
