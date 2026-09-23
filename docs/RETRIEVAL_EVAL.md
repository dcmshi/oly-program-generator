# Retrieval Quality Tracker

## Golden-set harness (2026-09-15, RAG-M6) — the gate

`oly-agent/eval/` scores the live retriever under **production settings**
(`HYBRID_SEARCH_ENABLED` (dense-only since AUD-3), chunk_type preference,
`min_similarity`, `top_k`, `RERANK_ENABLED`) against a graded relevance set, and
fails on regression:

```bash
cd oly-agent
PYTHONUTF8=1 uv run python -m eval.build_golden --dry-run      # 57 queries: 35 production-shaped + 22 legacy
PYTHONUTF8=1 uv run python -m eval.build_golden                # LLM grades dense ∪ hybrid candidates 0/1/2 → eval/golden.json (~$1, Haiku-class)
PYTHONUTF8=1 uv run python -m eval.run_eval --update-baseline  # freeze eval/baseline.json
PYTHONUTF8=1 uv run python -m eval.run_eval                    # gate: nDCG@5 + normalised recall@5; exit 1 on regression
PYTHONUTF8=1 uv run python -m eval.run_eval --dense-only       # ablation
PYTHONUTF8=1 uv run python -m eval.run_eval --hybrid --lexical-weight 0.2 --rrf-k 20 --candidates-per-leg 40   # fusion sweep
PYTHONUTF8=1 uv run python -m eval.run_eval --rerank           # + listwise rerank by the light model (~$0.01)
PYTHONUTF8=1 uv run python -m eval.context_diversity 29        # what generation received: slots, distinct chunks, source share
PYTHONUTF8=1 uv run python -m eval.context_simulate 29 12      # what it would receive now: replays the logged session queries
INTEGRATION_TESTS=1 uv run pytest tests/test_eval_harness.py   # the same gate as a test
```

Production-shaped queries are built with `retrieve.py`'s own query builders
(`build_session_query` per phase × session template, `build_fault_query` per
`FAULT_OPTIONS` value, `build_limiter_query` per limiter), so the eval measures
exactly what generation receives. **Status:** `golden.json` (57 queries, Haiku 4.5
grades over dense ∪ hybrid candidate pools) and `baseline.json` were built on the dev
copy on 2026-09-16 after the full re-ingest — see the section below. Grades are tied to
chunk ids, so rebuild both after any corpus change.

## AUD-3 part 2 — 2026-09-22: dense-only production, rerank measured (off), composed-context diversity (current `baseline.json`)

Same golden set (57 queries, GLM grades, union pool) and embeddings (`-large`) as the
freeze below. Gate = nDCG@5 + grade-2 nrecall@5; run-to-run noise ≈ ±0.005 nDCG for the
retriever (HNSW + query-embedding jitter; two identical dense runs gave 0.796 / 0.796).

**1. Fusion.** `similarity_search` now takes `lexical_weight` / `rrf_k` /
`candidates_per_leg` (defaults `HYBRID_LEXICAL_WEIGHT`, `RRF_K`,
`HYBRID_CANDIDATES_PER_LEG`); the fused score is `1/(k + vec_rank) + w/(k + lex_rank)`.
Sweep (all 57 queries; session family in the last column):

| variant | nDCG@5 | nrecall@5 | MRR | src share | session nDCG / nR |
|---|--:|--:|--:|--:|--:|
| **dense-only (production now)** | **0.796** | **0.644** | 0.974 | 0.488 | 0.907 / 0.825 |
| hybrid w=1.0 k=60 per=20 (old production) | 0.712 | 0.527 | 0.921 | 0.565 | 0.805 / 0.600 |
| hybrid w=1.0 k=60 per=40 | 0.651 | 0.475 | 0.934 | 0.590 | 0.779 / 0.588 |
| hybrid w=1.0 k=20 per=20 | 0.697 | 0.516 | 0.930 | 0.554 | 0.794 / 0.588 |
| hybrid w=0.5 k=60 per=20 | 0.749 | 0.573 | 0.936 | 0.523 | 0.839 / 0.650 |
| hybrid w=0.5 k=20 per=40 | 0.762 | 0.609 | 0.974 | 0.533 | 0.871 / 0.725 |
| hybrid w=0.3 k=20 per=20 | 0.776 | 0.629 | 0.965 | 0.523 | 0.886 / 0.762 |
| hybrid w=0.2 k=60 per=40 | 0.781 | 0.627 | 0.962 | 0.505 | 0.875 / 0.750 |
| hybrid w=0.2 k=20 per=40 | 0.777 | 0.629 | 0.965 | 0.495 | 0.901 / 0.800 |
| hybrid w=0.1 k=60 per=20 | 0.782 | 0.639 | 0.953 | 0.488 | 0.901 / 0.787 |
| hybrid w=0.1 k=20 per=40 | 0.780 | 0.633 | 0.974 | 0.505 | 0.900 / 0.812 |

(20 variants run; the rest sit between their neighbours.) Every step down in lexical
weight helps and none reaches dense-only, so under `-large` the lexical leg only costs
rank. **`HYBRID_SEARCH_ENABLED = False`**; `HYBRID_LEXICAL_WEIGHT = 0.1` (the best hybrid
row) applies if it is switched back on; the lexical path and its tests stay.
`build_golden` still builds its hybrid pool at weight 1.0 so lexical-only candidates keep
being graded. `baseline.json` re-frozen dense-only: **nDCG@5 0.796 · nrecall@5 0.644** ·
nrecall g≥1 0.930 · recall@5 0.213 · MRR 0.974 · src share 0.488 (+0.080 / +0.114 over the
hybrid freeze below).

**2. Listwise rerank (`oly-agent/rerank.py`, `RERANK_ENABLED = False`).** One
schema-constrained call per query (`{"ranking": [passage numbers]}`, `thinking_kwargs(model,
"disabled")` as base, `message_text`), 400-char query-focused excerpts, cached per (model,
query, candidate ids); any error or malformed reply keeps the retrieval order. Reranking
the dense top 20 surfaced 37 never-graded chunks into the top 5 (scored 0), so the fair
depth is the graded one, **top 15** (`RERANK_TOP_N`):

| rerank of dense top-N | nDCG@5 | nrecall@5 | session | fault | limiter | legacy | $ / 57 queries |
|---|--:|--:|--:|--:|--:|--:|--:|
| none (dense-only) | 0.796 | 0.644 | 0.907 / 0.825 | 0.759 / 0.619 | 0.772 / 0.533 | 0.742 / 0.556 | 0 |
| DeepSeek V4.1 Flash, top 20 (biased) | 0.741 | 0.633 | 0.771 / 0.675 | 0.694 / 0.550 | 0.644 / 0.633 | 0.773 / 0.651 | 0.018 |
| DeepSeek V4.1 Flash (light), top 15, run 1 | 0.817 | 0.686 | 0.893 / 0.812 | 0.731 / 0.554 | 0.861 / 0.700 | 0.802 / 0.670 | 0.012 |
| DeepSeek V4.1 Flash (light), top 15, run 2 | 0.818 | 0.657 | 0.853 / 0.713 | 0.758 / 0.554 | 0.836 / 0.667 | 0.824 / 0.674 | 0.009 |
| GLM-5.3 Flash (judge), top 15 | 0.825 | 0.682 | 0.902 / 0.825 | 0.780 / 0.650 | 0.842 / 0.633 | 0.791 / 0.611 | 0.009 |

The aggregate gain (+0.02 nDCG) comes from the legacy free-form and limiter queries. On the
**session family — which fills most of every composed context — the light-model rerank
lost 0.014 and 0.054 nDCG in two runs** (its own run-to-run spread — 0.04 on session nDCG,
0.03 on aggregate nrecall — is as large as the aggregate gain), and it moved fault nrecall down. GLM
matches dense on sessions and gains on fault/limiter, but GLM is the golden set's judge —
reranking with the grader is circular — it failed (fell back) on 11 of 57 calls, and it
takes ~12 s per call on the prefetch path. **Left off.** Cost would be ≈ $0.0002 per query
(≈ $0.003 per program at 12–16 distinct queries), inside the $0.01 budget, if a future
judge-independent reranker is worth it. Total LLM spend for these measurements: ≈ $0.05.

**3. Composed-context diversity.** `compose_session_context(state=ContextDiversityState)`
keeps per-program use counts (in the orchestrator's per-program query cache under
`CONTEXT_STATE_KEY`, lock-guarded; retrieval runs on the main thread in (week, day) order —
AUD-4 prefetch — so it is also deterministic): (a) each fault's ranked list is walked
least-shown-first, so fault slots rotate; (b) a source holding
`MAX_SOURCE_SHARE_IN_PROGRAM` (0.4) of the program's slots is passed over while another
candidate can fill the slot — a second pass fills any slot left empty, so the cap never
starves a session (DOG-1). Measured with `eval.context_simulate` (the real composition
over each program's logged `session_query`s, dense-only, no generation):

| program | composition | distinct chunks / slots | sources | max source share | chunks in ≥ 50 % of sessions | mean session-chunk similarity |
|---|---|--:|--:|--:|--:|--:|
| 29 | logged (hybrid, 2026-09-21) | 13 / 96 (0.135) | 8 | 0.688 (Everett) | 2 (fault C1/C2 24/24) | — |
| 29 | stateless, replayed | 9 / 96 (0.094) | 3 | 0.500 | 4 (fault C1/C2 24/24) | 0.628 |
| 29 | rotation only (share 1.0) | 12 / 96 (0.125) | 3 | 0.677 | 2 | 0.628 |
| 29 | share 0.5 | 17 / 96 (0.177) | 7 | 0.490 | 0 | 0.623 |
| 29 | **rotation + share 0.4 (production)** | **16 / 96 (0.167)** | **7** | **0.385** | **0** | 0.619 |
| 12 | stateless, replayed | 11 / 64 (0.172) | 5 | 0.500 | 3 | 0.623 |
| 12 | **rotation + share 0.4 (production)** | **17 / 64 (0.266)** | **7** | **0.375** | **0** | 0.616 |

The cost is ≈ 0.01 mean cosine similarity on the session chunks (all above
`VECTOR_SEARCH_MIN_SIMILARITY`); fault chunks still lead every context and the `[Cn]`
order is faults first, then session chunks by rank (RAG-M5). The remaining repetition is
session-side: 12 distinct session queries share most of their top chunks.

## The gate since 2026-09-22 (AUD-3): nDCG@5 + normalised recall; composed-context diversity

**Why the gate moved.** The union-graded golden set (57 queries) holds **23.7 relevant
chunks per query** (grade ≥ 1; 10.3 at grade 2), and 56 of 57 queries have ≥ 5 of them.
With `k = 5`, plain recall@5 = hits / relevant is capped at 5 / 23.7 ≈ 0.21 whatever the
ranking does: it read 0.206 hybrid and 0.213 dense-only, although dense-only is clearly the
better ranker. hit@5 is 1.0 on every query and MRR is 0.93–0.97, so neither of them
registers a change either. Only nDCG@5 moved.

**What is gated now** (`run_eval.GATED_METRICS`, `REGRESSION_TOLERANCE = 0.02` absolute):

- `ndcg_at_k`: graded nDCG@5, unchanged.
- `nrecall_at_k`: **grade-2 recall normalised by min(k, relevant)**
  (`metrics.recall_at_k_normalized`). A top 5 made entirely of directly useful chunks scores
  1.0. The grade-2 set averages 10.3 and is under 5 on only 6 queries, so the metric
  still has range.

Reported but **not gated** (`INFORMATIONAL_METRICS`, kept for continuity with the older
tables below): recall@5, MRR, hit@5, max source share, and `nrecall_g1_at_k`, the same
normalisation over grade ≥ 1. The grade ≥ 1 variant saturates like MRR (0.90 hybrid, 0.93
dense). When relevant ≥ k it is simply precision@5, and nearly every candidate the judge
saw is at least partially relevant. A baseline frozen before a gated metric existed is
skipped on that metric rather than failed. `--update-baseline` now also records the gate
(`"gate": {gated_metrics, tolerance, top_k, hybrid}`).

Re-frozen 2026-09-22 on the same golden set and embeddings (`-large`). The old metrics
reproduce the EMBED-1 freeze to the decimal:

| | nDCG@5 | nrecall@5 (g2) | nrecall@5 (g≥1) | recall@5 | MRR | src share |
|---|--:|--:|--:|--:|--:|--:|
| **hybrid, baseline.json** | **0.716** | **0.530** | 0.902 | 0.206 | 0.930 | 0.572 |
| · session (16) | 0.810 | 0.613 | 0.988 | 0.178 | 1.000 | 0.675 |
| · fault (13) | 0.757 | 0.615 | 0.892 | 0.232 | 0.923 | 0.569 |
| · limiter (6) | 0.786 | 0.567 | 1.000 | 0.181 | 1.000 | 0.467 |
| · legacy (22) | 0.605 | 0.411 | 0.818 | 0.218 | 0.864 | 0.527 |
| dense-only (ablation) | 0.796 | 0.644 | 0.930 | 0.213 | 0.974 | 0.488 |
| · session (16) | 0.908 | 0.825 | 1.000 | 0.180 | 1.000 | 0.538 |

Going from hybrid to dense-only moves the new metric by **+0.114** (session queries
+0.21) and plain recall@5 by +0.007. That gap is the resolution the old gate lacked, and
the reranker / fusion-weight work in AUD-3 is judged on it.

**Composed-context diversity (`eval/context_diversity.py`).** The golden set scores one
query at a time, so it cannot see that a whole program is fed the same few chunks. The
script reads `generation_log.retrieval_set` for a program (read-only; one attempt per
(week, day), the last successful one) and reports the number of slots, the distinct
chunks and distinct-chunk ratio, the largest share of slots filled from one source, and
the chunks shown in ≥ 50 % of sessions (`REPEAT_SESSION_FRACTION`). These are the
**"before" numbers for the context-diversity fix**, measured 2026-09-22:

| Program | sessions | slots | distinct chunks | distinct ratio | sources | max source share | chunks in ≥ 50 % of sessions |
|---|--:|--:|--:|--:|--:|--:|---|
| 29 (advanced, 2026-09-21) | 24 | 96 | 13 | 0.135 | 8 | 0.688 (Everett, 66 slots) | 4534 + 4533 (Everett `fault_correction`), as C1/C2 in 24/24 |
| 12 (2026-09-16) | 16 | 64 | 10 | 0.156 | 4 | 0.844 (Everett, 54 slots) | 4534 + 4533, as C1/C2 in 16/16 |

Program 23 was also requested but has no rows: it no longer exists, and 12 and 29 are the
only programs with logged retrieval sets. In both programs the fault-first
composition puts the same two Everett fault chunks in C1–C2 of every session, which is half
of every context. The fix should raise the distinct ratio and cut the repeat list without
lowering the gated retrieval metrics.

## Embedding upgrade — 2026-09-22 (EMBED-1): `text-embedding-3-small` → `-large` @ 1536 (hybrid freeze, superseded by AUD-3 part 2)

All 6,839 chunks re-embedded with `text-embedding-3-large` (Matryoshka-truncated to the
existing `vector(1536)`; ~7M tokens ≈ $0.90; `reembed.py`). To compare the two models on
the same labels, the golden set was **extended, not rebuilt**: `build_golden --extend`
kept the 1,140 existing grades and had the GLM judge grade only the 477 candidates `-large`
surfaced that `-small` never had (every `-small` pool member was already graded). Both
models were then scored on that union (the vectors were swapped through a backup table,
`emb_backup_small_20260922`, kept for rollback):

| nDCG@5 (union golden, k = 5) | `-small` | `-large` |
|---|--:|--:|
| hybrid (production) — all 57 | 0.664 | **0.721** |
| · session (16) | 0.747 | 0.821 |
| · fault (13) | 0.620 | 0.759 |
| · limiter (6) | 0.685 | 0.786 |
| · legacy free-form (22) | 0.623 | 0.609 |
| dense-only — all 57 | 0.695 | **0.796** |

MRR under hybrid dipped 0.980 → 0.947 (all on legacy queries); recall@5 is capped by the
set (AUD-3) and moved 0.202 → 0.206. The similarity distributions are near-identical
(median top-1 0.628 vs 0.641; 14 vs 15 of 855 top-15 rows below the 0.45 cutoff), so
`VECTOR_SEARCH_MIN_SIMILARITY` is unchanged. **Under `-large`, dense-only beats hybrid
(0.796 vs 0.721)** — the lexical leg now costs rank; re-weighting or dropping it is an
AUD-3 decision, gated on the fixed metrics. Baseline re-frozen under `-large` hybrid:
recall@5 0.206 · MRR 0.930 · nDCG@5 0.716 · max source share 0.572 (re-runs vary ≈ ±0.005:
approximate HNSW + query-embedding jitter). **Rollback:** `UPDATE knowledge_chunks k SET
embedding=b.embedding, embedding_model=b.embedding_model, embedded_at=b.embedded_at FROM
emb_backup_small_20260922 b WHERE b.id=k.id`, set `EMBEDDING_MODEL=text-embedding-3-small`,
restore `golden.json` / `baseline.json` from git.

## Judge study — 2026-09-21 (JUDGE-1): the grader moved from Haiku 4.5 to GLM-5.3 Flash

Every production role had left Anthropic but the golden set was still graded by Claude
Haiku 4.5. Two tools now make a judge change a measured decision rather than a swap:

- `eval/judge_agreement.py --model X` re-grades the *existing* pool with a candidate and
  reports exact agreement, linear-weighted Cohen's κ on the 0/1/2 scale, and binary
  (relevant-or-not) agreement against the stored grades; `--write` keeps the candidate's
  grades as `eval/golden_<model>.json`.
- `eval/judge_adjudicate.py` builds the inter-judge κ matrix over every judge on disk, sends
  each (query, chunk) pair the judges *disagree* on to a strong adjudicator (Opus 5, blind to
  the judges' grades), and scores every judge against that adjudication.

Agreement with Haiku alone was misleading — the two best judges scored "worst" on it:

| Judge | κ vs Haiku | accuracy vs Opus 5 (601 disputed) | κ vs Opus | relevant/not vs Opus | over / under | $ per regrade |
|---|---:|---:|---:|---:|---:|---:|
| Kimi K3 | 0.651 | 0.812 | 0.479 | 0.923 | 111 / 105 | ~$1.00 |
| **GLM-5.3 Flash** | 0.620 | **0.813** | 0.475 | 0.919 | 117 / 98 | **~$0.06** |
| Haiku 4.5 (incumbent) | — | 0.750 | 0.336 | 0.872 | 91 / 196 | ~$0.60 |
| DeepSeek V4.1 Flash | 0.592 | 0.690 | 0.232 | 0.832 | 49 / 307 | ~$0.10 |

Inter-judge κ: Haiku–Kimi 0.65, Haiku–GLM 0.62, Haiku–DeepSeek 0.59, Kimi–GLM 0.69,
DeepSeek–anyone ≤ 0.57. 546 of 1,147 pairs were unanimous; on the 601 disputed ones Opus
sided with Kimi and GLM 81 % of the time and with Haiku 75 %. Haiku's and DeepSeek's errors
are almost all *under*-grading (196 and 307 "under" vs 91 / 49 "over"): they call directly
useful chunks "partially useful". Kimi and GLM are balanced and statistically tied; GLM was
chosen on cost (≈ 17× cheaper than Kimi, 10× cheaper than Haiku) and vendor independence.
Caveats recorded: the adjudicator is itself a Claude model (it nonetheless disagreed with
Haiku more than with the open models), and the pool it judged was Haiku-built.

`JUDGE_MODEL` in `.env` (blank = `LIGHT_MODEL`) now names the grader; `build_golden.py`
reads it. Changing it means re-freezing — the baselines below the next heading are the last
Haiku-era numbers and are not comparable to the decimal with the GLM-era ones above them.

## Golden-set baseline — 2026-09-21, GLM judge (superseded)

First freeze under the new judge: same 57 queries, fresh pools (15 candidates per
retriever), graded by GLM-5.3 Flash — 1,140 graded ids, 934 relevant (GLM grades more
chunks relevant than Haiku did: 82 % vs 75 %, consistent with the adjudication finding
that Haiku under-graded). Corpus 6,835 chunks, Haiku labels, `k=5`, hybrid on. Grading cost
≈ $0.06 (GLM's mandatory 1,024-token thinking budget makes it slower than Haiku, ~15 min for
the pool, not dearer).

| Query family | n | recall@5 | MRR | nDCG@5 | max source share |
|---|---:|---:|---:|---:|---:|
| **all (baseline.json)** | 57 | **0.281** | **0.980** | **0.669** | 0.554 |
| fault | 13 | 0.287 | 0.962 | 0.618 | 0.631 |
| limiter | 6 | 0.268 | 1.000 | 0.687 | 0.433 |
| session | 16 | 0.259 | 1.000 | 0.736 | 0.525 |
| legacy (22 free-form) | 22 | 0.298 | 0.970 | 0.646 | 0.564 |

Read against the Haiku-era line below (0.307 / 0.942 / 0.653): recall@5 is lower only because
the denominator grew (more chunks count as relevant under a judge that doesn't under-grade),
while MRR and nDCG — which reward putting a relevant chunk first — both rose. Session queries
lead on nDCG (0.736) with MRR 1.0. Not comparable to the decimal with the Haiku-era numbers;
the regression gate now compares against this freeze.

## Golden-set baseline — 2026-09-21 (corpus 6,835 chunks; Haiku judge — superseded by the GLM re-freeze above)

Rebuilt after the 2026-09-20/21 additions (Charniga stubs, SBS, JTS, Pendlay, Pritchard,
13 open-access papers, Roman, Verkhoshansky, Vorobyev, Bompa, four Charniga volumes, Kono —
`docs/CORPUS.md` rows 12–27; 4,607 → 6,835 chunks, 2,234 → 4,182 principles). Fresh pools,
15 candidates per retriever, graded by the same Haiku 4.5 judge (kept deliberately so the
corpus is the only change — JUDGE-1): 1,158 graded ids, 869 relevant. New rows went through
the Jev quarantine pass at ingest, `dedupe_principles.py` (88 restatements marked) and
`relabel_chunk_types.py --min-id 10562` before grading. Haiku labels, `k=5`, hybrid on.

| Query family | n | recall@5 | MRR | nDCG@5 | max source share |
|---|---:|---:|---:|---:|---:|
| **all (baseline.json)** | 57 | **0.307** | **0.942** | **0.653** | 0.554 |
| fault | 13 | 0.337 | 0.962 | 0.674 | 0.631 |
| limiter | 6 | 0.261 | 1.000 | 0.744 | 0.433 |
| session | 16 | 0.275 | 0.969 | 0.653 | 0.525 |
| legacy (22 free-form) | 22 | 0.324 | 0.894 | 0.615 | 0.564 |

Against the 2026-09-20 freeze (0.282 / 0.950 / 0.644): recall +0.025, nDCG +0.009, MRR
−0.008, and **max source share 0.614 → 0.554** — the new sources are being retrieved rather
than sitting behind Everett/Catalyst. Session queries gained most (recall 0.239 → 0.275); the
one soft spot is `session:intensification:d2:clean` (MRR 0.5, nDCG 0.33), where the top hit
is a Bompa general-strength chunk rather than a clean-specific one — a case for the reranker
noted under the label A/B below. Pools are fresh (not a union with the previous set), so the
absolute numbers are comparable only in direction, not to the decimal.

## Golden-set baseline — 2026-09-20 (union-graded pool; superseded by the 2026-09-21 rebuild)

The golden set was re-graded on the **union** of the candidate pools under two chunk_type
label sets (Haiku's, in production, and Jev's — see JEV-1 in `TODO.md`), additions graded by
the same Haiku 4.5 judge: 1,269 graded ids (was 1,160). Recall drops against the earlier
freeze only because more relevant chunks are now known. Haiku labels, `k=5`, hybrid on.

| Query family | n | recall@5 | MRR | nDCG@5 | max source share |
|---|---:|---:|---:|---:|---:|
| **all (baseline.json, after quarantine)** | 57 | **0.282** | **0.950** | **0.644** | 0.614 |
| all, before quarantine | 57 | 0.279 | 0.950 | 0.640 | 0.621 |
| fault | 13 | 0.303 | 0.962 | 0.662 | 0.646 |
| limiter | 6 | 0.241 | 1.000 | 0.828 | 0.467 |
| session | 16 | 0.239 | 1.000 | 0.643 | 0.663 |
| legacy (22 free-form) | 22 | 0.305 | 0.894 | 0.574 | 0.618 |

Dense-only ablation: 0.274 / 0.965 / 0.649 — same picture as before (hybrid wins recall,
dense edges MRR/nDCG). **Label A/B on this pool:** Jev's labels score 0.255 / 0.936 / 0.614;
the session and limiter families are identical under both label sets, the gap is entirely in
the free-form legacy queries (0.313 → 0.253 recall) and fault queries (0.303 → 0.285), even
though a Sonnet 5 adjudication of the 914 disagreements finds Jev's label the better one in
565 cases vs Haiku's 234 (115 neither). Reading: the chunk-type preference boost benefits from
Haiku's *looser* `programming_rationale` / `fault_correction` sets; more precise labels shrink
the boosted set without a ranking gain. A precision label wants a different lever (a reranker),
not a better labeller.

## Golden-set baseline — 2026-09-20, earlier freeze (superseded by the union-graded pool above)

Corpus: 4,607 chunks · 2,234 principles · 612 sources · 39 templates. Since 2026-09-16:
Medvedev re-chunked (613 × 324 chars → 143 × 1,430), `relabel_chunk_types.py` applied
(`concept` 2,314 → 1,018; `methodology` 0 → 226, `case_study` 0 → 350), Takano and Medvedev
template fragments deduped. The golden set was rebuilt (50 of its graded ids had been Medvedev
chunks) with the same Haiku 4.5 judge, reached through OpenRouter (`LLM_PROVIDER=openrouter`);
57 queries, `k=5`, hybrid RRF on, session chunk-type preference on.

| Query family | n | recall@5 | MRR | nDCG@5 | max source share |
|---|---:|---:|---:|---:|---:|
| **all (baseline.json)** | 57 | **0.307** | **0.950** | **0.655** | 0.611 |
| fault | 13 | 0.344 | 0.962 | 0.674 | 0.662 |
| limiter | 6 | 0.256 | 1.000 | 0.828 | 0.467 |
| session | 16 | 0.255 | 1.000 | 0.643 | 0.663 |
| legacy (22 free-form) | 22 | 0.337 | 0.894 | 0.605 | 0.582 |

Dense-only ablation (`--dense-only`, same golden set): all 0.297 / 0.965 / 0.659; fault
0.323 / 1.000 / 0.674; limiter 0.236 / 1.000 / 0.753; session 0.248 / 1.000 / 0.639; legacy
0.334 / 0.909 / 0.639. Hybrid still wins recall overall and on the limiter / session families
(+0.02 / +0.01 recall, +0.07 nDCG on limiter); dense edges MRR and the legacy nDCG, as on
2026-09-16. The 2026-09-16 numbers below are a different graded pool (re-graded after the
corpus change), so read the change as directional: every family moved up, and the relabel is
what session queries needed (0.210 → 0.255 recall, 0.534 → 0.643 nDCG).

## Golden-set baseline — 2026-09-16 (dev copy, post re-ingest; superseded)

Corpus: 5,077 chunks · 2,135 principles · 612 sources · 49 templates (all seven PDF sources
re-chunked on joined pages with `--contextualize`; Catalyst 428 articles → 1,055 chunks;
Charniga 168 articles → 1,001 chunks). Settings: `top_k=5`, `min_similarity=0.45`, hybrid
RRF on, session chunk-type preference on. `k=5`, 57 queries.

| Query family | n | recall@5 | MRR | nDCG@5 | max source share |
|---|---:|---:|---:|---:|---:|
| **all (baseline.json)** | 57 | **0.255** | **0.904** | **0.574** | 0.614 |
| fault | 13 | 0.300 | 0.962 | 0.669 | 0.708 |
| limiter | 6 | 0.256 | 1.000 | 0.760 | 0.567 |
| session | 16 | 0.210 | 0.906 | 0.534 | 0.625 |
| legacy (22 free-form) | 22 | 0.260 | 0.841 | 0.497 | 0.564 |

Dense-only ablation (`--dense-only`, same golden set): all 0.269 / 0.915 / 0.594; fault
0.317 / 1.000 / 0.715; limiter 0.210 / 1.000 / 0.612; session 0.189 / 0.875 / 0.515; legacy
0.315 / 0.871 / 0.575. **Reading (RAG-M1 acceptance):** hybrid fusion helps the two
production-shaped families that carry structured vocabulary — session queries (+0.02
recall, +0.02 nDCG) and limiter queries (+0.05 recall, +0.15 nDCG) — and costs the
free-form legacy questions (−0.05 recall, −0.08 nDCG), whose wording the lexical leg
matches on incidental terms. The production retriever stays hybrid; the legacy numbers are
the case for a per-family fusion weight (RRF_K or lexical share by query kind) rather than
for switching it off. recall@5 is low everywhere because the grader marks 4–11 chunks
relevant per query and `top_k` is 5 — hit@5 is 0.98 and MRR 0.90, i.e. the first result
is almost always relevant; recall grows with `top_k`, not with ranking changes.

## Legacy 22-query report (print-only)

Run: `cd oly-ingestion && PYTHONUTF8=1 uv run python tests/test_retrieval_eval.py [--dense-only]`

Since 2026-09-15 this report also searches under production settings (hybrid +
session preference); the 2026-03-18 numbers below were taken unfiltered and
dense-only.

## Baseline — 2026-03-18

Corpus: 3,796 chunks · 151 principles · 11 sources (incl. Takano, source_id=2)
Settings: `top_k=5`, `min_similarity=0.45`

| Q# | Query (abbreviated) | Sim range | Topic hits | Type hits | Notes |
|----|---------------------|-----------|------------|-----------|-------|
| 1 | Accumulation volume structure | 0.472–0.511 | ✅ | ✅ | Top result still Soviet abbreviation noise (`concept` type); blocked in agent by chunk_type filter. |
| 2 | Snatch misses forward — exercises | 0.681–0.694 | ✅ | ✅ | |
| 3 | Weeks out for volume reduction | 0.482–0.590 | ✅ | ✅ | 3 results (2 filtered by threshold). Chunks correctly `concept` type. |
| 4 | Beginner → structured programming | — | n/a | n/a | 0 results after threshold — corpus lacks strong content here. Honest. |
| 5 | Squat strength vs C&J | 0.611–0.679 | ✅ | ✅ | |
| 6 | Jerk press-out cause & fix | 0.653–0.680 | ✅ | ✅ | |
| 7 | Clean catch + thoracic mobility | 0.553–0.583 | ✅ | ✅ | |
| 8 | Recovery between heavy sessions | 0.643–0.698 | ✅ | ✅ | Chunks correctly `recovery_adaptation` type. |
| 9 | Competition attempt selection | 0.602–0.655 | ✅ | ✅ | |
| 10 | Multi-year training structure | 0.619–0.664 | ✅ | ✅ | |
| 11 | Sessions/week for intermediate | 0.601–0.621 | ✅ | ✅ | |
| 12 | Prilepin 80–90% sets/reps | 0.461–0.497 | ✅ | ✅ | Low sim expected — Prilepin lives in structured table, not vector store. |
| 13 | Upper back hypertrophy volume | 0.539–0.638 | ✅ | ✅ | Top result now Catalyst back-training article (improved after threshold). Content gap documented in O2. |
| 14 | Hypertrophy alongside comp lifts | 0.580–0.640 | ✅ | ✅ | |
| 15 | Hip mobility for clean receive | 0.588–0.607 | ✅ | ✅ | |
| 16 | Overhead mobility for snatch | 0.700–0.714 | ✅ | ✅ | |
| 17 | Ankle mobility → squat fold | 0.568–0.602 | ✅ | ✅ | |
| 18 | Off-season GPP | 0.605–0.631 | ✅ | ✅ | |
| 19 | Loaded carries + conditioning | 0.582–0.603 | ✅ | ✅ | |
| 20 | RPE autoregulation | 0.496–0.540 | ✅ | ✅ | |
| 21 | Barbell curl (negative) | 0.504–0.569 | n/a | n/a | Returns 5 results — all above threshold. Inherent vector search limitation (see O3). |
| 22 | Back squat percentage (ambiguous) | 0.556–0.607 | n/a | n/a | Multi-source, expected. |

## Open Issues

### O1 — Soviet abbreviation noise (Q1) — RESOLVED 2026-03-18
**Symptom**: Q1 top result was a Medvedev chunk of unexpanded abbreviations at sim=0.511.

**Resolution**: Two-layer defence implemented:
1. `VECTOR_SEARCH_MIN_SIMILARITY = 0.45` added to `shared/constants.py`; `min_similarity` param added to `vector_loader.similarity_search()` (SQL WHERE filter, doesn't count against top_k)
2. ~~Agent's session/limiter searches already filter to `programming_rationale`/`periodization` chunk_types — Soviet `concept` chunks never reach the agent regardless of threshold~~ **Reversed 2026-09-15 (RAG-H2):** that hard filter left session generation 15% of the corpus (`concept` is 61%; 0 of the deload chunks were reachable). `chunk_type` is now a *soft preference* (`preferred_chunk_types` → `CHUNK_TYPE_PREFERENCE_BOOST` on a similarity-ranked candidate pool), so abbreviation-noise chunks are outranked, not excluded. The RAG-H1 re-ingest also replaces the Medvedev fragment chunks that produced this noise.

The Q1 abbreviation chunk (0.511, `concept` type) still appears in the unfiltered eval; in production it now competes on score rather than being filtered out. Re-embedding with expanded abbreviations not worth the cost.

### O2 — Israelit per-muscle volume prescriptions absent (Q13) — CLOSED (content gap)
**Symptom**: No source has specific per-muscle MEV/MAV/MRV volume tables.

**Resolution**: Accepted. Israetel covers the framework; per-muscle tables don't exist in the corpus. Eval expectation updated to not require numbers. Q13 top result improved to Catalyst "Back Training for Weightlifting" (sim=0.638) after threshold filtering removed low-quality chunks. Adding a new source with muscle-specific tables (e.g. RP app data) would close the gap — low priority for a weightlifting tool.

### O3 — Q21 negative test (barbell curl) always returns results — CLOSED (inherent limitation)
**Symptom**: Vector search returns 5 results (sim 0.504–0.569) for out-of-domain queries.
**Resolution**: Expected — `similarity_search` always returns top_k. The 0.45 threshold doesn't help here since all results are above it. A binary relevance gate would be needed but adds significant complexity. ~~In practice the agent's chunk_type filters block most irrelevant content anyway.~~ (2026-09-15: the chunk_type filter is gone — see O1 — so this is no longer a mitigation; production queries are fixed templates that never ask out-of-domain questions, which is the real reason this doesn't matter.)
