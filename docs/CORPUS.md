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

## Planned Additions

Task #23 in [../TODO-audit-2026-07-03.md](../TODO-audit-2026-07-03.md). Everything
here is **not yet ingested**; all of it needs the corpus DB machine and both API
keys. Ordered by ingest priority, which puts the free material first — those rows
need no purchase decision and can go in as soon as the Catalyst re-ingest and
migrations are done.

The gaps this is meant to close: concrete Soviet loading prescriptions beyond
Medvedev, competition tapering/peaking (nothing in the corpus covers it), and
beginner-level structured programming (`RETRIEVAL_EVAL` Q4 returns 0 hits).

Half of these need buying, and one is print-only, so the last column records what
to fall back on when the primary route doesn't work out — a blocked row shouldn't
silently stall the gap it was meant to fill.

| # | Source | Fills | Route · profile | Primary access | Substitute if that falls through |
|--:|--------|-------|-----------------|----------------|----------------------------------|
| 1 | **Charniga** — translated essays (~215) | Soviet methodology, restoration, technique | `ingest_web.py --site charniga` · web (dynamic) | **Free.** Wayback CDX — sportivnypress.com is defunct (DNS gone; Charniga died Jan 2025). Steps in [DB-MACHINE-RUNBOOK.md](DB-MACHINE-RUNBOOK.md) §8 | None needed — the Archive *is* the source of record |
| 2 | **Stronger by Science** (Nuckols) articles | Periodization + tapering, modern evidence base | `ingest_web.py --site sbs` (site config still to write) · web | **Free** HTML | #5 covers programming but not tapering; #3/#4 cover tapering but not periodization |
| 3 | **Pritchard** — *Tapering Strategies to Enhance Maximal Strength* (PhD thesis, AUT 2017) | Tapering/peaking — the largest single gap | PDF · `research` | **Free** open-access PDF ([AUT repository](https://openrepository.aut.ac.nz/items/8319a680-1486-4c46-96dc-5736e3a7d732)) | #4, which is much narrower |
| 4 | **Pritchard et al.** — short-term training cessation as a taper (accepted manuscript) | Tapering — cessation protocols | PDF · `research` | **Free** ([Bond repository](https://pure.bond.edu.au/ws/files/27624950/AM_Short_term_training_cessation_as_a_method_of_tapering_to_improve_maximal_strength.pdf)) | Largely subsumed by #3 if that lands; also a [higher-vs-lower intensity taper AM](https://pure.bond.edu.au/ws/files/29759929/AM_Higher_vs._Lower_Intensity_Strength_Training_Taper_Effects_on_Neuromuscular_Performance.pdf) |
| 5 | **JTS / Max Aita** programming articles | Beginner → structured programming (`RETRIEVAL_EVAL` Q4 = 0 hits) | `ingest_web.py --site jts`, or a curated URL list · web | **Free** HTML ([jtsstrength.com](https://www.jtsstrength.com/programming-for-weightlifting/)) | #6, for the beginner gap only |
| 6 | **Pendlay** — beginner program (archived) | Same beginner gap as #5 | single-page web or PDF · web | **Free** via a [Lift Vault](https://liftvault.com/programs/olympic/glenn-pendlay-beginner-olympic-weightlifting-program-spreadsheet/) mirror of the old Pendlay.com article | #5 |
| 7 | **R.A. Roman** — *The Training of the Weightlifter* | Concrete %/volume/frequency tables feeding Prilepin-style validation | book · `soviet` | **Buy.** Russian Weightlifting Library ebook compilation (Amazon/Kobo/Google Play) — cleanest text; print in stock at [EliteFTS](https://elitefts.com/collections/best-books-to-read) | **#1 partially covers this** — the Charniga essays include Roman and Prilepin pieces formerly on Sportivny Press. Ingest #1 first and re-measure the gap before buying |
| 8 | **A.S. Medvedev** — whichever volume is not yet ingested | The second Medvedev volume | book · `soviet` | **Buy** Kindle: [B08HYBSGWS](https://www.amazon.com/System-Multi-Year-Training-Weightlifting-Russian-ebook/dp/B08HYBSGWS) *(A System of Multi-Year Training)* or [B08H8S4WT4](https://www.amazon.com/Program-Multi-Training-Weightlifting-Russian-ebook/dp/B08H8S4WT4) *(A Program of…)* — whichever is **not** `source_id=501` | None. #1 is thin on Medvedev specifically |
| 9 | **A.N. Vorobyev** — *A Textbook on Weightlifting* | Soviet theory | book · `theory_heavy` | **Buy** ([Amazon B0007BVA9M](https://www.amazon.com/textbook-weightlifting-N-Vorobyev/dp/B0007BVA9M)) | Library / interlibrary loan, or archive.org controlled digital lending |
| 10 | **Verkhoshansky** — *Special Strength Training: Manual for Coaches* | The strength-limiter query family | book · `theory_heavy` | **Buy** the official ebook from [verkhoshansky.com](https://www.verkhoshansky.com/) (~€55, bundled with Block Training System) — clean text | Used print, ISBN 9788890403828 (~$30–54, [BooksRun](https://booksrun.com/9788890403828-special-strength-training-manual-for-coaches)) + `--vision` OCR |
| 11 | **Tommy Kono** — *Weightlifting, Olympic Style* + *Championship Weightlifting* | Thin `fault_correction` retrieval | book · `programming` | **Print only — no legitimate ebook exists.** Both self-published in Honolulu ([Densho](https://encyclopedia.densho.org/Tommy_Kono/)); used copies on eBay, then `--vision` OCR of the owned copy | Free single-chapter [Catalyst excerpt](https://www.catalystathletics.com/article/63/Championship-Weightlifting-by-Tommy-Kono-Book-Excerpt/) — enough to test the retrieval gain before paying ~$120 |
| 12 | **Bompa & Buzzichelli** — *Periodization* | `periodization` chunk_type coverage | book · `programming` | **Buy** Kindle: *Periodization of Strength Training for Sports*, 4th ed. 2021 ([171820308X](https://www.amazon.com.be/-/en/Periodization-Strength-Training-Sports-Tudor/dp/171820308X)) | *Periodization Training for Sports*, 3rd ed. ([1450469434](https://www.amazon.com.be/-/en/Tudor-Bompa/dp/1450469434)). Avoid the Human Kinetics app ebook either way — Kindle extracts far more cleanly |

### Prerequisites and rules

- **The `research` profile in rows 3–4 does not exist yet.** `CHUNK_PROFILES` in
  `processors/chunker.py` defines only `theory_heavy`, `programming` and `soviet`.
  Add it (papers are dense and short — start near `soviet`'s 700/150) before
  ingesting either PDF.
- **Add every book title to `SOURCE_PROFILE_MAP` before ingesting it** — see the
  profile section below. Rows 1–2 and 5–6 are web ingests and never consult it.
- **Buy clean ebook text; don't OCR.** Compare the Everett/Israetel EPUBs against
  the vision-OCR'd Laputin/Medvedev. Reserve `--vision` for scanned copies you own,
  which is why row 11 is last among the books despite filling a real gap.
- **Pirated PDFs are out of scope**, as are the public-domain Saxon/Sandow texts
  (wrong era to be useful here).
- **Re-run the retrieval eval after each batch** and update
  [RETRIEVAL_EVAL.md](RETRIEVAL_EVAL.md), the Ingested Sources table above, and the
  corpus table in `README.md`.

### Chasing — paywalled, author request the only route

- **Storey & Smith 2012** — Auckland ResearchSpace holds abstract only (Springer
  copyright). ResearchGate or a direct author request.
- **Winwood, Keogh, Travis & Pritchard 2023** — *The Tapering Practices of
  Competitive Weightlifters* (JSCR). The most on-target tapering paper there is;
  paywalled. Rows 3–4 are the working substitute until it arrives.

### Considered and skipped

- **Chidlovski Lift Up** — history and meet results; almost no prescriptive content.
- **Mash Elite** free hybrid/super-total programs — noisy, weakly structured.
- **Saxon / Sandow** public-domain books — wrong era for this corpus.

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
