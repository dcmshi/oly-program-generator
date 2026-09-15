# RAG Architecture Review — ingestion, chunking, vector store, retrieval

**Date:** 2026-09-15 · **Scope:** `oly-ingestion/` (extract → classify → chunk →
embed → load), `knowledge_chunks` + pgvector, `retrieve.py` → `generate.py` prompt
assembly, `programming_principles` selection in `plan.py`, and the retrieval eval.
**Method:** inline read of every module on the path (no subagents); two probes over
the PDFs in `oly-ingestion/sources/` (§9); SQL measurements against the local
corpus copy (3,368 chunks · 161 principles · pgvector 0.8.2 — §7 has the queries);
and a comparison of what the code does against current RAG practice (§3). Both
no-key test suites were green at the time of the review.

Findings are tracked as **RAG-H/M/L** items in `TODO.md` §11; this document is the
reasoning and evidence behind them. Roadmap items #17–#23 in
`TODO-audit-2026-07-03.md` are referenced where they overlap rather than re-filed.

---

## 1. Summary

The skeleton is right: content routing before chunking, a contextual preamble on
every chunk, hash dedup ahead of the embedding call, an HNSW cosine index, a
similarity floor, typed retries, resumable runs, and an eval query set. Seven
audit passes have made the code around it robust. What has never had a pass is
the retrieval *quality* path, and five things there are structural rather than
bugs:

1. **Half the corpus is chunked by PDF page, not by paragraph.** PyMuPDF's
   plain-text mode emits no blank lines and the pipeline classifies each page on
   its own, so for the five PyMuPDF sources (1,700 chunks, 50% of the corpus)
   **100% of chunks are single-paragraph** and chunk = page. Measured locally:
   ~90% of pages become exactly one chunk, 33–72% of pages end mid-sentence,
   running heads and page-number footers are embedded as text, and 97% of all
   chunks carry no chapter or section in their preamble. Medvedev (vision OCR,
   617 chunks, 18%) has the opposite defect: 269-character fragments. The
   keep-together, overlap and profile-sizing machinery never engages for any of
   them. The same class of bug was found and fixed for EPUB (198 → 587 Everett
   chunks) and HTML; the PDF path was never checked. (RAG-H1)
2. **Production retrieval hard-filters on `chunk_type`, a label assigned by a
   first-match substring scan of the first 800 characters — and `concept` is
   61% of the corpus.** Session-generation queries may only see
   `programming_rationale` + `periodization` = **497 of 3,368 chunks (15%)**.
   Of chunks that mention *deload*, 0 are in that set; of chunks mentioning
   *accumulation*, 3 of 97; *taper/peaking*, 10 of 43; *Prilepin*, 0 of 3. The
   `recovery_adaptation` label, checked before `periodization` in the keyword
   order, absorbs most periodisation text because it contains the word
   *adaptation*. (RAG-H2)
3. **Principle selection evaluates 2 of the condition fields it extracts and
   drops array-valued phases.** 73 of 161 principles carry a condition the
   planner never reads — `movement_family` on 60 of them, so snatch-specific
   rules are served on clean & jerk days and enforced by the validator. (RAG-H3)
4. **Retrieval runs once per program and every session prompt shows the same
   four 600-character snippets.** Chunks average 2,000–4,900 characters, so the
   model sees the head (preamble + topic sentence) of ~15% of each; prescriptions
   sit in the tail. Only 2 of 4 session templates are ever queried. Roadmap #19
   names the fix; the retrieval-unit / display-unit mismatch is why it should
   come first. (RAG-H4)
5. **Filtered HNSW is one planner decision from returning nothing.** Today the
   planner sequential-scans (exact search) because the table is small — which
   also means the HNSW index does no work. With the index forced and pgvector's
   default `hnsw.iterative_scan = off`, **46 of 60 fault-correction queries
   returned zero rows and 60 of 60 returned fewer than five**; with
   `relaxed_order`, 59 of 60 returned five. The corpus is about to grow
   (Catalyst re-ingest, Charniga, planned additions), which is exactly what flips
   the plan. One `SET LOCAL` fixes it. (RAG-H5)

Below those: no lexical channel (#21), word-count token estimation with no
oversized-paragraph split, a metadata-only preamble where contextual retrieval is
now standard, program templates parsed at LLM cost but never shown to the model,
nominal chunk traceability, a print-only eval, dead metadata columns, and no
embedding-model versioning. Full matrix in §4.

**Recommended order:** fix the PDF path and re-ingest (one paid pass, ~$1–2),
flip the `iterative_scan` setting the same day, then per-session retrieval with
wider context, then harden the eval so the remaining changes (soft filter,
relabel, hybrid, contextual embeddings, embedding upgrade) are accepted on
numbers.

---

## 2. What the pipeline does today

```
PDF ──PyMuPDF get_text("text")──┐
EPUB ─ebooklib + \n\n markers───┼─► per page / chapter: ContentClassifier
HTML ─block_text()──────────────┘        │  regex heuristics → LLM fallback (<0.6)
                                         ▼
              prose / mixed ──► SemanticChunker.chunk()       table / program / exercise
                                 • split on heading regexes    ──► structured_loader
                                 • paragraphs = split("\n\n")      (percentage_schemes,
                                 • profile 700–1100 est. tokens    program_templates,
                                 • keep-together + tail overlap    exercises)
                                 • preamble "[Source|Author]\n[Chapter|Section]"
                                 • keyword topics, density flag
                                         │
                                         ▼
              VectorLoader.load_chunks: sha256(raw_content) dedup → OpenAI
              text-embedding-3-small (batch 100) → INSERT knowledge_chunks
              (content = preamble+text, raw_content, chunk_type, topics, …)
              HNSW (m=16, ef_construction=64, vector_cosine_ops)

              mixed / principle ──► PrincipleExtractor (Claude, 8k windows)
                                     → programming_principles (condition / recommendation JSONB)
```

At generation time (`orchestrator.run`), **once per program**:

| Path | Query | Filter | What the model sees |
|---|---|---|---|
| A · faults | `exercises.faults_addressed && athlete faults`, per movement family | SQL | "Fault Correction Exercises" block; validate Check 8 |
| B · templates | `program_templates` by level / phase / frequency, `LIMIT 3` | SQL | **name + notes only** (`generate.py:447`); `program_structure` unused |
| C · vector | 2 session-template queries + 1 per fault + 1 per limiter; `top_k=5`, `min_similarity=0.45` | `chunk_type IN (…)` **hard** | first 4 by insertion order, `raw_content[:600]`, identical in all 16 sessions |
| principles | `plan._load_principles`: `phase` string-equality + `athlete_level`, top 20 by priority | SQL on JSONB | "Active Principles" (first 8); validate Check 5 |

---

## 3. Reference model — current practice at this scale

The yardstick for §4. Sources are named, not linked; each is a well-known public
result whose exact current numbers are worth re-checking.

| Layer | Current practice (2025–26) | Why it matters here |
|---|---|---|
| **Document parsing** | Structure-preserving extraction (PyMuPDF `blocks` / `dict`, PyMuPDF4LLM Markdown, Docling, Marker); strip running heads, footers and folios; de-hyphenate; treat the page as a rendering artefact | Coaching books are long arguments; page boundaries mean nothing to them |
| **Chunking** | Headings first, then size-bounded with a real tokenizer; either 256–512-token chunks for precision *or* larger chunks with a context header — but what the LLM is shown must be the retrieval unit | The design doc's "large contextual chunks" choice is defensible; showing 600 chars of them is not |
| **Contextualisation** | *Contextual retrieval* (Anthropic, 2024): prepend a 50–100-token LLM description situating the chunk in its document before embedding and BM25 indexing. Reported −35% retrieval failures alone, −49% with BM25, −67% with reranking | The static `[Source|Section]` preamble is the cheap half; here it is also mostly empty (97% no section) |
| **Embeddings** | OpenAI `text-embedding-3-large` truncated to 1,536 (Matryoshka) keeps the schema and beats `-small`; store model id per row; keep a re-embed script | Roadmap #22 |
| **Index** | pgvector HNSW is right at this size. Filtered queries need `hnsw.iterative_scan` (≥ 0.8) or partial indexes; otherwise ANN returns < k rows under selective WHERE | Every production query is filtered; measured 77% empty when the index is used |
| **Retrieval** | Hybrid dense + lexical (`tsvector` / BM25) fused with RRF; metadata as soft boosts, hard filters only on trustworthy fields; per-source diversity; a query per need, not per job | Exercise names, `%/reps` notation and Soviet abbreviations are lexical |
| **Reranking** | Cross-encoder or LLM rerank over top-20–50 → top-k; can stay in-vendor with a Haiku-class model on 20 candidates; local open-source rerankers exist but add a heavy dependency | Optional here; fixed-template queries reduce the win |
| **Prompt assembly** | Display unit = retrieval unit; cite-able chunk indices; static prefix first for prompt caching; retrieved text framed as untrusted reference data | 16 near-identical calls per program |
| **Rules** | Treat extracted if/then rules as a rule engine: evaluate every condition field against current state, per week | Only `phase` + `athlete_level` evaluated |
| **Evaluation** | Golden (query → relevant ids) set; recall@k, MRR / nDCG; production-parity queries; CI gate on regression; generation-side groundedness / citation coverage; log retrieved ids + scores per call | Eval is print-only and not production-shaped |
| **Ops** | Embedding-model column, re-embed job, pinned extension version, query-embedding cache | None present |

Deliberately **not** recommended for this system: GraphRAG (no entity-relation
questions), HyDE / query rewriting (queries are fixed templates), a separate
vector database (pgvector is correct below ~100k rows), a LangChain / LlamaIndex
rewrite (the hand-rolled pipeline is small and well tested), or any new
third-party processing vendor — everything below stays with the two API vendors
already in use plus Postgres-native and local libraries.

---

## 4. Gap matrix

Severity reflects impact on the *quality of generated programs*, not code risk.

| # | Area | Today (measured) | Gap vs §3 | Sev | TODO |
|---|---|---|---|---|---|
| 1 | PDF parsing → chunking | 5 PyMuPDF sources 100% single-paragraph; chunk = page; Medvedev 269-char fragments; 97% chunks no chapter/section | Structure lost; profile sizes moot; arguments severed; running heads embedded | **H** | RAG-H1 |
| 2 | `chunk_type` hard filter | `concept` 61%; session filter reaches 15%; deload 0/30, accumulation 3/97, taper 10/43 visible | Noisy label gates recall; `methodology` / `case_study` never assigned; `competition_strategy` = 1 chunk | **H** | RAG-H2 |
| 3 | Principle selection | 2 of 12 condition keys evaluated; 73/161 carry an unevaluated one; 2 array-phase rules dropped | Time- and lift-gated rules served unconditionally | **H** | RAG-H3 |
| 4 | Retrieval granularity | once per program; 2 of 4 templates; first 4 chunks; 600 chars of ~2–5k | Display unit ≠ retrieval unit | **H** | RAG-H4 (= #19) |
| 5 | Filtered ANN | planner seq-scans today; index forced ⇒ 46/60 empty, 60/60 < k; `iterative_scan` off; image unpinned | Latent zero-result failure; HNSW currently idle | **H** (latent) | RAG-H5 |
| 6 | Lexical channel | none | Exact terms / notation matched only densely | M | RAG-M1 (= #21) |
| 7 | Token accounting | words × 1.3; no oversized-paragraph split; 30k-char embed truncation | Numeric text under-counted 2–4×; head-only embedding of long paragraphs once H1 makes them real | M | RAG-M2 |
| 8 | Contextualisation | static preamble, mostly `[Source|Author]` only | No LLM chunk context | M | RAG-M3 |
| 9 | Program templates (Path B) | name + notes reach prompt; `program_structure`, `percentage_schemes`, `exercise_complexes` unread | Paid parsing the model never sees | M | RAG-M4 |
| 10 | Traceability | same 3 chunk ids on every exercise; retrieval set not logged | `source_chunk_ids` ≈ noise; roadmap #5 would learn from it | M | RAG-M5 |
| 11 | Evaluation | 22 queries, print-only, non-parity, no labels / metrics, not in CI | Cannot gate any change above | M | RAG-M6 (⊃ #20) |
| 12 | Metadata | `athlete_level_relevance` 0 set, `page_range` 0 set; `topics` / density never queried; 268 chunks no topics | Dead columns; 230-entry tagger with no consumer | M | RAG-M7 |
| 13 | Embedding versioning | no model column | Blocks #22 and M3 safely | M | RAG-M8 |
| 14 | Fault query text | `early_arm_bend` embedded raw | Minor recall loss | L | RAG-L1 (= #18a) |
| 15 | Query-embedding cache | none | 5–12 API calls / program for fixed strings | L | RAG-L2 |
| 16 | Prompt caching | static blocks after variable blocks | Cache prefix ≈ 0 | L | RAG-L3 |
| 17 | Untrusted-content framing | none | Scraped text is instruction-capable | L | RAG-L4 |
| 18 | Cross-source dedup | global sha256 | Provenance credits first source only | L | RAG-L5 |
| 19 | Extension pin | `pgvector/pgvector:pg16` (resolves to 0.8.2 locally) | Floating minor | L | RAG-L6 |
| 20 | Redundant index | `idx_chunks_hash` duplicates `knowledge_chunks_content_hash_key` | Write amplification, nothing else | L | RAG-L7 |
| 21 | Section title width | untruncated into VARCHAR(300) | Rare whole-section loss | L | RAG-L8 |
| 22 | Extraction schema drift | principle conditions include keys outside the prompt schema (`athlete_characteristics`, `exercise_type`, `delay_minutes`, `training_focus`) | Unenforceable rules | L | RAG-L9 |
| 23 | Docs drift | SCHEMA.md / README counts stale; eval O3 relies on the mechanism #2 disproves; CONTRIBUTING S5 "done" but index idle | — | L | RAG-L10 |

Clean under scrutiny (don't re-file): hash dedup before embedding with the
intra-batch guard; typed retry on embedding calls; empty / oversize guards (0
chunks over the 30k-char limit today); per-section rollback and resume;
principle window scanning + `UNIQUE(source_id, principle_name)`; per-row
savepoints in loaders; EPUB / HTML block separators (EPUB sources average 11–17
paragraphs per chunk — the fix worked); classifier LLM fallback reachability;
`min_similarity` applied in SQL before `LIMIT`; retrieval failures degrade to a
warning rather than aborting the paid run; HNSW build params sane for the row
count.

---

## 5. Evidence

### 5.1 PDF pages become chunks (RAG-H1)

**Corpus measurement** (local DB, `string_to_array(raw_content, E'\n\n')`):

| source_id | Source | Path | Chunks | Avg chars | Avg paragraphs / chunk | Single-paragraph chunks |
|---:|---|---|---:|---:|---:|---:|
| 52 | Drechsler — Weightlifting Encyclopedia | PyMuPDF | 603 | 4,883 | 1.0 | **100%** |
| 51 | Zatsiorsky — Science and Practice | PyMuPDF | 430 | 2,660 | 1.0 | **100%** |
| 506 | Dan John — Intervention | PyMuPDF | 266 | 1,190 | 1.0 | **100%** |
| 2 | Takano — Weightlifting Programming | PyMuPDF | 229 | 1,705 | 1.0 | **100%** |
| 502 | Everett — OW for Sports | PyMuPDF | 172 | 781 | 1.0 | **100%** |
| 501 | Medvedev — Multi-Year Training | vision OCR | 617 | **269** | 2.2 | 46% |
| 499 | Laputin — Managing the Training | vision OCR | 110 | 1,721 | 6.4 | 5% |
| 507 | Everett — OW Complete Guide | EPUB | 587 | 3,649 | 12.1 | 2% |
| 504 | Israetel — Sci. Principles Hypertrophy | EPUB | 206 | 3,665 | 10.8 | 0% |
| 505 | Starrett — Supple Leopard | EPUB | 137 | 4,316 | 17.3 | 4% |

Every EPUB source shows healthy paragraph structure; every PyMuPDF source shows
none. Drechsler's 4,883-char average (≈1,200 estimated tokens against an
1,100 target) is a dense encyclopedia page; Everett-for-Sports' 781 is a sparse
one — chunk size is set by the publisher's typesetting, not by the profile.
Medvedev's 269-char chunks (≈65 tokens) are the vision path's problem: the OCR
prompt asks for blank-line paragraphs and Medvedev is mostly tables, so each
table row-group becomes a paragraph and a chunk.

**Page-level probe** (`pdf_chunk_probe.py`, §9) runs the real
`ContentClassifier._split_into_sections` and `SemanticChunker.chunk` over each
page exactly as `pipeline.ingest` does, with no DB or API. Tokens use the
chunker's own estimator so they compare to the profile targets.

| Source (profile · target) | Pages | Pages with 0 blank-line breaks | Pages → exactly 1 chunk | Tokens/chunk mean · p90 · max | Chunks < 50 tok | Pages ending mid-sentence |
|---|---:|---:|---:|---|---:|---:|
| Takano (programming · 900) | 211 | **211** | 190 (90%) | 344 · 679 · 842 | 14 | 151 (72%) |
| Zatsiorsky (theory_heavy · 1,100) | 200 | **200** | 166 (83%) | 485 · 907 · 1,125 | 38 | 66 (33%) |
| Dan John (programming · 900) | 200 | 199 | 177 (89%) | 279 · 419 · 469 | 28 | 81 (41%) |

Running heads and folios ride along as text: Zatsiorsky pages alternate first
lines *"Science and Practice of Strength Training"* / *"Timing in Strength
Training"*; Takano program pages end in tonnage footers like `20:108:280`; Dan
John pages open with `part 2`, `111`, `Intervention`. Because sections are split
per page, `chapter` is empty on 3,252 of 3,368 chunks and `section` on 3,267 — so
the preamble that is supposed to disambiguate the embedding is `[Source |
Author]` and nothing else for 97% of the corpus.

**Why.** `pdf_extractor._extract_with_pymupdf` (line 115) calls
`page.get_text("text")`, which separates lines *and blocks* with a single `\n`.
`SemanticChunker._chunk_section` (chunker.py:658) splits paragraphs on `"\n\n"`,
and a first paragraph is always accepted whole, so a page is one chunk.
`pipeline.ingest` (line 313) then does `for page_text in pages:
classify_sections(page_text)`, so no chunk can span a page and heading context
never carries across one. The comment there — per-page processing is "critical
for EPUB/multi-chapter sources" — is right for EPUB chapters (logical units) and
wrong for PDF pages (rendering units).

**Fix feasibility** (`pdf_blocks_probe.py`, §9): extract with
`page.get_text("blocks")`, keep text blocks, join blocks with `\n\n`, join the
page run into one document, chunk once:

| Source, page run | Text blocks / page | Result | Tokens mean · p90 · max | < 50 tok |
|---|---:|---|---|---:|
| Zatsiorsky pp. 100–129 | 19.1 | 27 chunks / 30 pages | 1,039 · 1,111 · 1,124 (target 1,100) | 0 |
| Takano pp. 20–49 | 8.1 | 25 chunks / 30 pages | 796 · 895 · 915 (target 900) | 0 |

Chunks land on target, no fragments, paragraphs cross pages. Remaining work:
heading detection (PyMuPDF4LLM's Markdown output emits `#` headings that the
chunker's `SECTION_BREAK_PATTERNS` already recognise, and it is the same vendor
as PyMuPDF), running-head removal (drop any line recurring on > 30% of pages,
and bare numerals at page edges), line-end de-hyphenation, and carrying
`page_number` through metadata into the never-written `page_range` column. For
the vision path, join the OCR'd pages the same way and merge sub-100-token
paragraphs into their neighbours before chunking.

### 5.2 `chunk_type` is a noisy hard filter (RAG-H2)

`pipeline._infer_chunk_type` (line 427) probes `title or content[:800]` against
`CHUNK_TYPE_KEYWORDS` in dict order; first match wins:
`fault_correction` (`fault, error, correction, miss, common mistake`) →
`biomechanics` → `competition_strategy` → `recovery_adaptation` (`recovery,
adaptation, sleep, rest period, …`) → `nutrition_bodyweight` → `periodization`
→ `programming_rationale` (`rationale, reasoning, because, in order to`) →
`concept`.

Measured distribution:

| chunk_type | Chunks | Share | Reachable by session queries? | by fault queries? |
|---|---:|---:|:-:|:-:|
| concept | 2,048 | 60.8% | – | – |
| recovery_adaptation | 380 | 11.3% | – | – |
| programming_rationale | 363 | 10.8% | ✓ | – |
| fault_correction | 189 | 5.6% | – | ✓ |
| periodization | 134 | 4.0% | ✓ | – |
| nutrition_bodyweight | 130 | 3.9% | – | – |
| biomechanics | 123 | 3.7% | – | – |
| competition_strategy | 1 | 0.0% | – | – |
| methodology, case_study | 0 | – | (`methodology` is in the limiter filter) | – |

Where key programming content actually sits (word-boundary match on
`raw_content`):

| Term | Total chunks | In `periodization` + `programming_rationale` | Largest bucket |
|---|---:|---:|---|
| accumulation | 97 | **3** | recovery_adaptation 77 |
| deload | 32 | **0** | recovery_adaptation 27 |
| taper / tapering / peaking | 43 | 10 | recovery_adaptation 15, concept 13 |
| Prilepin | 3 | **0** | concept 2 |

`recovery_adaptation` wins because *adaptation* and *recovery* appear in the
first 800 characters of most periodisation writing and that label is tested
before `periodization`. The eval never shows this: `test_retrieval_eval.py`
searches unfiltered, and `RETRIEVAL_EVAL.md` O1 describes the production filter
as a *defence* against Soviet-abbreviation noise.

Fix, in order: (1) make the eval production-parity (RAG-M6) so the effect is
measured; (2) replace the hard filter with a score term (e.g. `+0.05` when the
type is in the preferred set) or apply type preference at rerank time; (3)
relabel `chunk_type` with a small LLM call per chunk (batched, ~3.4k × ~400
input tokens, a label + confidence out; a few dollars) and keep
`_infer_chunk_type` as the zero-cost fallback — at minimum reorder
`CHUNK_TYPE_KEYWORDS` so `periodization` precedes `recovery_adaptation`.

### 5.3 Principle conditions (RAG-H3)

`principle_extractor.EXTRACTION_PROMPT` (line 49) allows `"phase"` to be a string
**or an array**; `plan._load_principles` (line 273) tests `condition->>'phase' =
%s`. On a JSON array `->>` returns the array's text form, which is neither NULL
nor equal, so those rules are excluded (2 of 161 today). The larger problem is
what *is* served:

| Condition key | Principles carrying it | Evaluated by `plan.py`? |
|---|---:|:-:|
| movement_family | 60 | no |
| athlete_level | 25 | yes |
| phase | 25 | yes (string only) |
| weeks_out_from_competition | 9 | no |
| athlete_characteristics, exercise_type | 2 each | no (not in the prompt schema) |
| delay_minutes, training_focus, recent_make_rate, rpe_average_last_week, week_of_block, training_age_years | 1 each | no |

73 of 161 principles carry a condition nobody checks. A `movement_family:
"snatch"` rule is shown as an "Active Principle" on clean & jerk sessions and
its `max_exercises_per_session` / `competition_lifts_first` recommendation is
enforced by `validate.py` Check 5; a `weeks_out_from_competition: {"lte": 2}`
taper rule is served in week 1 of a 12-week-out block. Four keys are outside
the prompt's own schema — extraction drift that structured outputs (#17.4)
would close.

Fix: `(condition->'phase' @> to_jsonb(%s::text) OR condition->>'phase' = %s)`;
a `condition_matches(condition, state)` helper evaluating `lte / gte / lt / gt /
eq / between` over `weeks_to_competition`, `week_number`, `movement_family` (the
session's primary movement), level, and the previous outcome's make rate / RPE;
select principles **per session**, not per program; unit tests per operator and
for the array-phase fixture.

### 5.4 Retrieval unit vs display unit (RAG-H4)

- `retrieve.py:121` queries `plan.session_templates[:2]` — day 3 and 4
  templates ("Snatch Variations + Accessories", "C&J + Squat") get no retrieval.
- Results accumulate in insertion order; `generate.py:339–360` takes ≤ 2 fault
  chunks then fills to 4 from `programming_rationale` — insertion order, not
  similarity — and shows `raw_content[:600]`.
- Retrievable chunk types average 2,100–4,000 chars, so ~15–30% of a chunk is
  shown; the head is preamble + topic sentence, the prescription is usually in
  the tail.
- The same four snippets appear in all 16 session prompts, so retrieval cannot
  differentiate a snatch day from a squat day or week 1 from the deload.

Roadmap #19 (a–d) is the fix; the reason to do it first is that until the
display unit matches the chunk, no chunking or embedding improvement can show up
in a generated program. Concretely: a query per session template built from
`(primary_movement, secondary_movements, phase, week intensity band)`; top-4 by
similarity with a per-`source_id` cap of 2; show the whole chunk (or ≥ 1,500
chars); raise `PROMPT_LENGTH_WARN_CHARS`; label chunks `[C1]…[C4]` so the model
can cite them (feeds RAG-M5).

### 5.5 Filtered HNSW (RAG-H5)

pgvector's HNSW scan collects `hnsw.ef_search` (default 40) candidates and
*then* applies the WHERE clause. Measured on the local corpus with the index
forced (`enable_seqscan = off`), 60 query vectors drawn from `concept` chunks,
filtered to `chunk_type = 'fault_correction'` with the production
`min_similarity` predicate, `LIMIT 5`:

| Setting | Queries returning < 5 rows | Queries returning 0 rows | Mean rows |
|---|---:|---:|---:|
| `hnsw.iterative_scan = off` (default) | **60 / 60** | **46 / 60** | 0.32 |
| `hnsw.iterative_scan = relaxed_order` | 1 / 60 | 1 / 60 | 4.92 |

Today `EXPLAIN` shows a sequential scan + sort for the production query shape,
i.e. exact search: results are correct and the HNSW index is idle
(`docs/CONTRIBUTING.md` S5 marks the index "done"; it is built, not used). The
plan flips to the index as the table grows or if anyone tunes
`random_page_cost` / disables seq scans for speed — at which point 77% of fault
queries return nothing and the prompt says "(none retrieved)" with no error.
The corpus is about to double (Catalyst re-ingest) and then grow again
(Charniga, planned additions).

Fix: in `similarity_search`, inside the same transaction, `SET LOCAL
hnsw.iterative_scan = 'relaxed_order'` and `SET LOCAL hnsw.ef_search = 100`
(wrap in try/except for older pgvector); pin the image to an explicit
`pgvector/pgvector:0.8.x-pg16` tag (RAG-L6); drop `idx_chunks_hash` (RAG-L7).
Add a live-DB test that forces the index and asserts `len(results) == top_k`
for a filtered query. Longer term, partial HNSW indexes per `chunk_type` make
filtered scans exact.

### 5.6 Tokens and oversized paragraphs (RAG-M2)

`SemanticChunker._estimate_tokens` is `len(text.split()) * 1.3`. For prose that
is close; for this corpus's notation — `(85%/4)4 20:108:280`, `70%/3x3
75%/3x2`, `Sn. Pu.` — cl100k tokenises at 2–4× the word count, so a "900-token"
Soviet chunk can be 1,500+ real tokens while prose under-fills.
`_chunk_section` never splits a paragraph larger than `chunk_size`; today that
is masked because PDF "paragraphs" are pages, but after RAG-H1 real long
paragraphs (Drechsler has many) become single oversized chunks and
`vector_loader` embeds only their first 30,000 characters. Fix: `tiktoken`
(`cl100k_base`, the tokenizer for `text-embedding-3-*`; OpenAI package, no new
vendor) for sizing and for the 8,191-token cap; sentence-level fallback split
above the target; keep the word estimate as the dependency-free test fallback.

### 5.7 Contextual retrieval (RAG-M3)

The preamble is the metadata half of contextual retrieval, and for 97% of chunks
it is just `[Source | Author]`. The other half — a one- or two-sentence LLM
description of what the chunk is about *within its document* ("From Chapter 7
on the preparatory period; gives weekly lift counts for Class I lifters …") —
is what moved the published numbers. Cost is one short call per chunk with the
surrounding section as context (cacheable prefix per chapter); a Haiku-class
model over ~3.5k chunks is a few dollars once. Do it in the same re-ingest as
RAG-H1 so chunks are embedded once, store the text in its own column
(`context_prefix`) so `content` stays reconstructable, and include it in the
lexical index (RAG-M1).

### 5.8 Templates, traceability, evaluation (RAG-M4 / M5 / M6)

- **Templates.** `program_templates.program_structure` (17 templates, 16 from
  Takano, parsed by the incremental LLM parser) never reaches a prompt;
  `generate.py:447` prints `name — notes`. `percentage_schemes` and
  `exercise_complexes` have no reader in `oly-agent/`. Render the week matching
  the current week number compactly (`D1: Snatch 5×2@80, Back Squat 4×4@82 …`,
  capped), or stop paying to parse them.
- **Traceability.** `weight_resolver.attach_source_chunk_ids` (line 147)
  attaches the first three `programming_rationale` ids to every exercise, plus
  fault ids when the rationale contains `fault|address|correct|fix`.
  `source_chunk_ids` therefore cannot say which chunk informed which exercise,
  and `generation_log` stores the prompt text but not the retrieved
  `(chunk_id, similarity, query)` set. README roadmap #5 would train on this.
  Log the retrieval set per call as JSONB and ask the model to cite `[Cn]`
  indices in `selection_rationale` (trivial once structured outputs land).
- **Evaluation.** `tests/test_retrieval_eval.py` prints; it never asserts, has
  no exit code, calls `similarity_search` without the production `chunk_types`
  filter, and passes `require_numbers`, which production never does. No
  relevance labels, so no recall@k / MRR / nDCG; the design doc's
  source-diversity metric was never implemented. Build a golden set once:
  ~50 production-shaped queries (one per `FAULT_OPTIONS` value, phase ×
  primary movement, one per limiter, plus the current 22); take top-20 per
  query; LLM-grade relevance 0–2; skim; freeze to `tests/eval/golden.json`.
  Then `recall@5`, `MRR`, source diversity, a production-parity pass, failing
  under `INTEGRATION_TESTS=1` below the `RETRIEVAL_EVAL.md` baseline. Add one
  generation-side check from `generation_log`: validator-retry rate and
  citation coverage per program.

### 5.9 Smaller items

- **Dead metadata (RAG-M7).** `athlete_level_relevance` is set on 0 of 3,368
  chunks (nothing populates it), so its filter branch and index are dead;
  `page_range` is set on 0; `topics` (268 chunks empty), `information_density`
  and `contains_specific_numbers` are written and never read by `retrieve.py`.
  `KEYWORD_TO_TOPIC` (≈230 entries), `retag_chunks.py` and the GIN index support
  a `topics=` filter no production path uses. Either use topics as a soft boost
  after RAG-M1's scoring refactor — after switching `keyword_tag` to
  word-boundary matching (`rir`→*requiring*, `miss`→*mission*, `peak`→*speak*,
  `position`→everything; deferred in audit 5) — or retire the tagger.
- **Embedding versioning (RAG-M8).** Add `embedding_model TEXT NOT NULL DEFAULT
  'text-embedding-3-small'` (+ `embedded_at`) to `knowledge_chunks`, assert it in
  `similarity_search`, ship `reembed.py` that re-embeds from stored `content` in
  batches. Prerequisite for #22 and RAG-M3.
- **Query cache (RAG-L2).** Retrieval strings are templates; an LRU or a
  `query_embeddings(text_hash, model, embedding)` table removes 5–12 calls per
  program and makes the eval deterministic offline.
- **Prompt caching (RAG-L3).** The 16 calls per program share Athlete Profile,
  Maxes, Available Exercises (~2.3k chars), Principles, Context and Templates,
  but those blocks sit *after* the per-session blocks, so the cacheable prefix
  is a few hundred tokens. Reorder static-first and mark the last static block
  with `cache_control`. #17.6 judged caching not worth it at 2.6k tokens; the
  reorder plus RAG-H4's wider context changes that arithmetic.
- **Untrusted content (RAG-L4).** Retrieved text is pasted under "## Programming
  Context" with no data frame; web / Wayback content is instruction-capable.
  Delimit it and say once that it is reference material. The exercise-catalogue
  check and Checks 0–9 bound the blast radius, hence LOW.
- **Cross-source dedup (RAG-L5).** Global `sha256(raw_content)` credits text
  shared by Everett's two books, or a Catalyst reprint of a chapter, to whichever
  source ingested first. Intentional per CLAUDE.md; the provenance cost matters
  when reading `source_id` in the eval.
- **Section width (RAG-L8).** `section_title` from the heading regexes is
  inserted untruncated into `VARCHAR(300)`; a long line matching
  `^(?:Week|Phase|Block|Cycle)\s+\d+.*$` raises `StringDataRightTruncation` and
  the section-level handler drops the whole section. Truncate at insert.
- **Docs (RAG-L10).** `docs/SCHEMA.md` says 2,576 chunks / 82 principles;
  `README.md` says 167 rules and 275 tests (twice); `RETRIEVAL_EVAL.md` O3 relies
  on the chunk_type filter "blocking irrelevant content"; `docs/CONTRIBUTING.md`
  S5 calls the HNSW index done. The local corpus copy (3,368 / 161) also differs
  from the documented 3,796 / 151 — reconcile which DB the docs describe.

---

## 6. Recommended sequence

| Phase | Items | Needs | Cost / effort | Gate |
|---|---|---|---|---|
| 0 · Same-day safety | RAG-H5 (`SET LOCAL iterative_scan`), L6 pin, L7 drop index, L1 | none | 1 hour | live-DB test forcing the index returns `top_k` |
| 1 · Re-ingest correctly | RAG-H1, M2 (tokenizer + split), M8 (model column), L8 (+ M3 contextual prefix, since embedding happens once) | corpus DB machine, both keys | ½–1 day code; re-ingest 5 PyMuPDF + 2 OCR sources ≈ $1–2 embeddings + principle extraction; +$3–5 with M3 | per-source paragraph density ≫ 1; chunk sizes on target; eval re-run |
| 2 · Show the model what was retrieved | RAG-H4 (= #19), H2 step (2) soft filter + keyword reorder, L2, L3, L4 | none | ~1 day | validator-retry rate from `generation_log` before / after |
| 3 · Make quality measurable | RAG-M6 (⊃ #20), M5 retrieval log | one LLM grading pass (~$1) | ~1 day | golden set frozen; CI-gated under `INTEGRATION_TESTS=1` |
| 4 · Recall improvements, measured | RAG-M1 hybrid (= #21), H2 step (3) relabel, #22 embedding upgrade | Alembic on DB machine; ~$0.50 for #22 backfill | 1–2 days | recall@5 / MRR vs phase-3 baseline |
| 5 · Rules and templates | RAG-H3 conditions per session, M4 template rendering, M7 topics decision, L9 schema | none | ~1 day | unit tests per operator; prompt diff |

Phase 1 must precede everything that embeds (M3, #22) so the corpus is embedded
once. Phase 3 should precede phase 4 so hybrid / relabel / upgrade are accepted
on numbers. RAG-H3 is independent and can be done any time — it is a correctness
fix, not a retrieval-quality one. Ingest the planned new sources (#23) **after**
phase 1 so they are parsed correctly the first time.

---

## 7. Measurement queries

All numbers in §5 came from these against the local copy; re-run them on the
corpus DB machine before and after phase 1.

```sql
-- chunk_type distribution (RAG-H2)
SELECT chunk_type, count(*), round(100.0*count(*)/sum(count(*)) OVER (), 1) AS pct,
       round(avg(length(raw_content))) AS avg_chars
FROM knowledge_chunks GROUP BY 1 ORDER BY 2 DESC;

-- where on-target content lives (RAG-H2)
SELECT chunk_type, count(*) FROM knowledge_chunks
WHERE raw_content ~* '\m(taper|tapering|peaking|deload|accumulation|prilepin)\M'
GROUP BY 1 ORDER BY 2 DESC;

-- per-source paragraph density (RAG-H1)
SELECT s.id, left(s.title, 40), count(*) AS chunks, round(avg(length(k.raw_content))) AS avg_chars,
       round(avg(array_length(string_to_array(k.raw_content, E'\n\n'), 1)), 1) AS avg_paras,
       round(100.0*avg((array_length(string_to_array(k.raw_content, E'\n\n'), 1) = 1)::int)) AS pct_single
FROM knowledge_chunks k JOIN sources s ON s.id = k.source_id
WHERE s.source_type IN ('book', 'manual') GROUP BY 1, 2 ORDER BY 3 DESC;

-- principle condition keys and array phases (RAG-H3)
SELECT key, count(*) FROM programming_principles, jsonb_object_keys(condition) key GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) FROM programming_principles WHERE jsonb_typeof(condition->'phase') = 'array';

-- dead metadata (RAG-M7)
SELECT count(*) FILTER (WHERE athlete_level_relevance IS NOT NULL) AS level_set,
       count(*) FILTER (WHERE page_range IS NOT NULL) AS page_range_set,
       count(*) FILTER (WHERE topics = '{}') AS no_topics,
       count(*) FILTER (WHERE coalesce(chapter, '') = '') AS no_chapter,
       count(*) FILTER (WHERE coalesce(section, '') = '') AS no_section
FROM knowledge_chunks;

-- filtered HNSW recall with the index forced (RAG-H5); run once per setting
SET enable_seqscan = off;
SET hnsw.iterative_scan = off;            -- then: relaxed_order
WITH qs AS (SELECT id, embedding FROM knowledge_chunks WHERE chunk_type = 'concept' ORDER BY id LIMIT 60)
SELECT count(*) FILTER (WHERE n < 5) AS short_of_5, count(*) FILTER (WHERE n = 0) AS empty, round(avg(n), 2) AS avg_rows
FROM (SELECT (SELECT count(*) FROM (
        SELECT k.id FROM knowledge_chunks k
        WHERE k.chunk_type::text = ANY(ARRAY['fault_correction'])
          AND 1 - (k.embedding <=> qs.embedding) >= 0.45
        ORDER BY k.embedding <=> qs.embedding LIMIT 5) s) AS n
      FROM qs) t;
RESET hnsw.iterative_scan; RESET enable_seqscan;

-- is the planner using the index at all?
EXPLAIN (COSTS OFF) SELECT id FROM knowledge_chunks
WHERE chunk_type::text = ANY(ARRAY['fault_correction'])
ORDER BY embedding <=> (SELECT embedding FROM knowledge_chunks WHERE id = 1) LIMIT 5;

-- extension version + redundant index (RAG-H5 / L6 / L7)
SELECT extversion FROM pg_extension WHERE extname = 'vector';
SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_chunks' AND indexdef ~ 'content_hash';
```

---

## 8. Relationship to the existing roadmap (`TODO-audit-2026-07-03.md` #17–#23)

| Roadmap | Status after this review |
|---|---|
| #17 model migration / structured outputs | Unchanged; structured outputs also close RAG-L9 (condition-schema drift) and enable chunk citations (RAG-M5) |
| #18 quick fixes | (a) re-filed as RAG-L1; (c) resolved by RAG-H4 |
| #19 session-specific retrieval + wider snippets | Promoted to **RAG-H4** with the retrieval-unit / display-unit argument |
| #20 harden eval | Extended as **RAG-M6** (golden set, metrics, generation-side check) |
| #21 hybrid RRF | Kept as **RAG-M1**; index `raw_content` (+ `context_prefix`), not `content` |
| #22 `text-embedding-3-large` @ 1536 | Kept; depends on RAG-M8 (model column) and should follow RAG-H1 |
| #23 new sources | Unchanged; ingest **after** RAG-H1 lands |

---

## 9. Reproducing the probes

Both scripts run from `oly-ingestion/` with no DB or API keys in under a minute
and import the real chunker plus the classifier's pure section splitter. They
are short enough to keep here rather than in the tree.

```python
# pdf_chunk_probe.py — does the pipeline chunk PDFs by page?  (§5.1, second table)
import statistics, sys; sys.path.insert(0, ".")
import fitz
from processors.chunker import SemanticChunker
from processors.classifier import ContentClassifier

def probe(path, title, limit=None):
    pages = [p for p in (pg.get_text("text") for pg in fitz.open(path)) if p.strip()][:limit]
    chunker, split = SemanticChunker.for_source(title), ContentClassifier._split_into_sections
    counts, toks = [], []
    for p in pages:                                   # exactly what pipeline.ingest does per page
        n = 0
        for sec, meta in split(None, p):
            cs = chunker.chunk(sec, metadata={"chapter": meta["chapter"]}, source_title=title, author="x")
            n += len(cs); toks += [c.token_count for c in cs]
        counts.append(n)
    print(title, f"pages={len(pages)} zero_blank_lines={sum(p.count(chr(10)*2) == 0 for p in pages)}",
          f"one_chunk_pages={sum(c == 1 for c in counts)} tok_mean={statistics.mean(toks):.0f}",
          f"lt50={sum(t < 50 for t in toks)} mid_sentence_ends={sum(not p.rstrip().endswith(('.', '!', '?', ':', '\"')) for p in pages)}")

probe("sources/weightlifting_programming_a_winning_coachs_guide_bob_takano.pdf", "Weightlifting Programming")
```

```python
# pdf_blocks_probe.py — would block extraction + page joining fix it?  (§5.1, third table)
import statistics, sys; sys.path.insert(0, ".")
import fitz
from processors.chunker import SemanticChunker

def probe(path, title, start, n):
    doc, chunker = fitz.open(path), SemanticChunker.for_source(title)
    pages = ["\n\n".join(b[4].strip() for b in doc[i].get_text("blocks") if b[6] == 0 and b[4].strip())
             for i in range(start, start + n)]
    chunks = chunker.chunk("\n\n".join(pages), metadata={}, source_title=title, author="x")
    toks = [c.token_count for c in chunks]
    print(title, f"{len(chunks)} chunks / {n} pages; tok mean={statistics.mean(toks):.0f} max={max(toks)} lt50={sum(t < 50 for t in toks)}")

probe("sources/Fry, Andrew C._Kraemer, William J._Zaciorskij, Vladimir M - Science and practice of strength training (2021).pdf",
      "Science and Practice of Strength Training", 100, 30)
```
