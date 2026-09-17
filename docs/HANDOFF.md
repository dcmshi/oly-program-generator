# Handoff — model bump (Sonnet 4.6 → Claude 5 family) + dogfooding

Written 2026-09-15, updated 2026-09-16 so the next session can resume without the transcript.
**The open-item list now lives in `TODO.md` §12 (status ledger)** — this file keeps the local DB
state and tooling pitfalls. Delete it when TODO §12's CORPUS-DB and RELABEL items are done. Standing constraints from the user:
**no subagents**; one commit per fix/feature with tests, gated on the pytest exit code (never on
a pipe); run the Makefile's `AGENT_TESTS` / `INGESTION_TESTS` lists with `PYTHONUTF8=1 uv run
pytest …` from the subsystem dir (`make` is not on PATH in Git Bash); lint with
`uvx ruff@0.15.22 check .`. Org policy: never suggest signing up for a new hosted vendor without
Fellow Security approval; never print API keys.

## State (user: "let's do the dogfood items first before reingestion")

| # | Item | State |
|---|------|-------|
| 1 | `light_model` role (Haiku 4.5 for cheap LLM tasks) | **done** `c355552` |
| 2 | Claude 5-safe request/response surface + per-model pricing | **done** `97f54a3` |
| 3 | `eval/model_baseline.py` + `orchestrator.run(max_sessions=)` | **done** `2167a18` |
| 4 | DOG-1: import the real General Strength CSV, generate the next block, review | **done** — `import_program_csv.py` `48dd63b`; program 6 (imported, completed) + program 7 (generated, $0.72); 5 fixes + 4 follow-ups recorded in `TODO.md` §11 → Dogfooding |
| 5 | Model baseline against athlete 1 (Sonnet 4.6 vs Sonnet 5 ± thinking) | **done 2026-09-16** — table in `TODO.md` MODEL-1, raw report `oly-agent/eval/model_baseline_20260916T140755Z.json`; truncation fix `1c0a9d6`; agent roles moved to `claude-sonnet-5` + thinking disabled (`DEFAULT_GENERATION_MODEL`). Ingestion `llm_model` stays on 4.6 until its call sites pass `thinking_kwargs` |
| 6 | DOG-2: Catalyst re-crawl test | **done** (dry-run 428 URLs; `--limit 5` → 11 chunks) — recorded in `TODO.md` DOG-2; the full re-crawl is item 8 |
| 7 | TODO item for other providers | **done** — `TODO.md` MODEL-2 |
| 8 | Corpus ops: Catalyst full re-crawl (runbook §5–7), Charniga (§8), 7-PDF re-ingest (§8b), catalogue DELETE + `relabel_chunk_types.py` + `retag_chunks.py` + `eval.build_golden` + `eval.run_eval --update-baseline` (§9) | **done on the dev copy 2026-09-16** (≈ $28 actual across both batches): seven PDFs, Catalyst, Charniga, retag, golden set + baseline (`docs/RETRIEVAL_EVAL.md`). Not run: `relabel_chunk_types.py` (≈ $3–4). The corpus DB machine still needs the runbook applied. Original estimate ≈ $18–22 on the Anthropic key (Laputin + Medvedev need `--vision` OCR ≈ $8, `--contextualize` on ~2,400 chunks ≈ $4, principle extraction ≈ $2, Catalyst ≈ $3, Charniga ≈ $1.5, golden set ≈ $1–2), < $0.50 on OpenAI. Neither key can read its balance (Anthropic needs an Admin key; OpenAI needs `api.usage.read`) — confirm in the consoles |

DOG-1e–h landed 2026-09-16 (`0625e65`, `1fc2398` + migration 0014, `c25a825`, `54b5559`); the
athlete's block was re-imported as program **11** and program **12** generated on the new defaults
(22 exercises, 0 validation errors, $0.42). Still open: MODEL-2 (open-weight providers) and the
MODEL-1 note about moving ingestion's `llm_model`.

## Local DB state (dev copy, 2026-09-16)

- Athlete 1 (`dshi`): program 5 (old draft), **11** = imported General Strength block
  (`completed`, `general_prep`, 11 wks × 4, logs 2026-07-01 → 09-13, outcome adherence 100% /
  make 100% / verdict → accumulation; replaced program 6 after the 0014 catalogue seed), **12** =
  the current generated accumulation block (draft, Sonnet 5, all DOG-1 fixes), plus stale drafts
  7 (pre-fix generation) and 8–10 (`… [baseline: <config>]`, 8 sessions each). Delete 7–10 from
  the UI or with `eval.model_baseline … --delete`-style SQL once reviewed.
- `exercises`: 72 rows here (45 seed + 27 from migration 0014; the 8 chapter-heading rows are
  deleted). The corpus DB needs `make migrate` (0014) and runbook §9's DELETE.
- `knowledge_chunks`: 5,077 after the 2026-09-16 re-ingest (2,135 principles, 612 sources, 49
  templates); backup of the pre-run state in `backups/pre_pdf_reingest_20260916.dump`.
  `eval/golden.json` + `eval/baseline.json` are committed and match this copy's chunk ids.

## What the model-bump groundwork looks like (for whoever flips the default)

- `shared/config.py`: `llm_model` / `light_model` / `generation_model` / `explanation_model`
  (+ env), `generation_thinking` / `generation_effort` / `explanation_thinking` /
  `explanation_effort` (blank = model default), `generation_max_tokens` 4096,
  `explanation_max_tokens` 1024.
- `shared/llm.py`: `MODEL_PRICING_PER_MTOK`, `estimate_cost(..., cache_read_tokens=,
  cache_creation_tokens=)`, `message_text`, `sampling_kwargs`, `thinking_kwargs`, `usage_tokens`,
  `light_model_for`.
- `generate.py` / `explain.py`: on `stop_reason == "max_tokens"` the next attempt doubles
  `max_tokens` up to `LLM_MAX_TOKENS_CEILING` (16,384) — adaptive thinking counts against the
  budget, and Sonnet 5 at 4,096 produced no text at all.
- Agent roles now default to `DEFAULT_GENERATION_MODEL = "claude-sonnet-5"` with
  `DEFAULT_GENERATION_THINKING = "disabled"`; to move ingestion's `llm_model` too, first pass
  `thinking_kwargs(settings.llm_model, "disabled")` in `pipeline._llm_call`, `principle_extractor`
  and `pdf_extractor` (vision), then flip `DEFAULT_LLM_MODEL` and re-run one ingestion.

## Tooling pitfalls (this machine / this harness)

- The Bash tool mangles quoted heredocs that contain `\\` **or** many single quotes: `\\n` in a
  Python patch script arrives as a real newline, `\\s` as `\s`. Write patch scripts and test
  fragments with the Write tool into the scratchpad dir and run them with `python <script>`;
  build backslashes with `chr(92)` when a one-liner is unavoidable. Memory note:
  `feedback-bash-heredoc-backslashes.md`.
- `Path.write_text` on Windows writes CRLF unless `newline="\n"` is passed; git normalises on
  commit (`.gitattributes eol=lf`) but pass it anyway.
- Insert new tests **before** a file's `if __name__ == "__main__":` runner block.
- `docker exec oly-postgres psql -U oly -d oly_programming -At -c "…"` for quick DB checks (no
  `-it`). Inside a bash double-quoted `-c`, `E'\\s+'` reaches psql as `E'\s+'` = the letter `s`
  — every "s" in the output vanishes and looks like a corpus defect. Use `chr(10)` / `replace()`
  instead of regex escapes.
- Long runs (baseline, ingestion): `run_in_background` + an `until grep -q "^rc=" file` waiter.
