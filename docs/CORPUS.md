# Knowledge Corpus

State of the ingested source material backing retrieval. Update this file after
any ingest, re-ingest, or bulk delete.

**Totals:** 3,796 chunks · 151 principles · 439 sources

---

## Ingested Sources

Listed in ingestion order. `source_id` is the row in `sources`; the profile is
the `SOURCE_PROFILE_MAP` entry in `processors/chunker.py` that controls chunk
size.

| # | Source | Format | `source_id` | Profile | Chunks | Principles |
|---|--------|--------|------------:|---------|-------:|-----------:|
| 1 | Everett — *Olympic Weightlifting* | EPUB | 507 | programming | 587 | 76 |
| 2 | Zatsiorsky — *Science and Practice of Strength Training* | PDF | 51 | theory_heavy | 430 | 7 |
| 3 | Drechsler — *Weightlifting Encyclopedia* | PDF | 52 | theory_heavy | 603 | 6 |
| 4 | Catalyst Athletics articles | Web | — (418 rows) | web (dynamic) | 446 | 22 |
| 5 | Laputin — *Managing the Training of Weightlifters* | PDF (vision OCR) | 499 | soviet | 110 | 3 |
| 6 | Takano — *Weightlifting Programming* | PDF | 2 | programming | 218 | 0 |
| 7 | Medvedev — *Multi-Year Training in Weightlifting* | PDF (vision OCR) | 501 | soviet | 617 | 0 |
| 8 | Everett — *Olympic Weightlifting for Sports* | PDF | 502 | programming | 172 | 0 |
| 9 | Israetel — *Scientific Principles of Hypertrophy Training* | EPUB | 504 | programming | 206 | 21 |
| 10 | Starrett — *Becoming a Supple Leopard* | EPUB | 505 | theory_heavy | 137 | 16 |
| 11 | Dan John — *Intervention* | PDF | 506 | programming | 266 | 0 |

Takano (#6) also produced 16 program templates; Everett *for Sports* (#8)
produced 11 exercises.

### Notes

- **Everett (#1)** was re-ingested 2026-03-18 after the EPUB paragraph-extraction
  fix (was 198 chunks / 44 principles at `source_id=1`).
- **Catalyst (#4)** was ingested *before* the HTML paragraph fix, so it averages
  ≈1 chunk per article instead of several. A full re-ingest is pending on the
  corpus DB machine — see [DB-MACHINE-RUNBOOK.md](DB-MACHINE-RUNBOOK.md).

---

## Chunk Sizing Profiles

| Profile | Used for | Chunk size | Overlap |
|---------|----------|-----------:|--------:|
| `theory_heavy` | Zatsiorsky, Drechsler, Starrett | 1100 tokens | 250 |
| `programming` | Everett, Takano, Israetel, Dan John | 900 tokens | 200 |
| `soviet` | Laputin, Medvedev (data-dense, OCR'd) | 700 tokens | 150 |
| web article | Catalyst, Charniga | 500–1100 (dynamic) | 100–250 |

**Always add a new source title to `SOURCE_PROFILE_MAP` in
`oly-ingestion/processors/chunker.py` before ingesting it.** Matching is by
substring against the source title; an unrecognised title silently falls back to
the `programming` profile (900 tokens).

This applies to books (`pipeline.py`) only. The web path (`ingest_web.py`) sizes
each article dynamically with `for_web_article(word_count)` and never reads
`SOURCE_PROFILE_MAP`.

---

## Retrieval

Chunks are retrieved by pgvector cosine similarity with
`VECTOR_SEARCH_MIN_SIMILARITY = 0.45` (`shared/constants.py`), filtered in SQL
before `top_k` is applied. Baseline scores for the 22 evaluation queries live in
[RETRIEVAL_EVAL.md](RETRIEVAL_EVAL.md) — re-run `test_retrieval_eval.py` and
update that table after any corpus or retrieval change.
