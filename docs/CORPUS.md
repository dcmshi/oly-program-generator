# Knowledge Corpus

State of the ingested source material backing retrieval. Update this file after
any ingest, re-ingest, or bulk delete.

**Totals:** 3,796 chunks · 151 principles · 439 sources (corpus DB machine, 2026-03-18).
A dev copy measured on 2026-09-15 held 3,368 chunks · 161 principles · 439 sources ·
17 templates — the two diverged after the Catalyst delete / later principle runs.
**Dev copy 2026-09-20:** 4,607 chunks · 2,234 principles · 612 sources · 39 templates —
Medvedev re-chunked (613 × 324 chars → 143 × 1,430; the OCR'd session labels were being read
as headings), `relabel_chunk_types.py` applied (`concept` 50 % → 22 % of the corpus), Takano /
Medvedev page-fragment templates deleted. Golden set + baseline rebuilt the same day.
**Dev copy after the free-source additions, 2026-09-20 (evening):** 5,662 chunks (5,395 live, 267
quarantined) · 2,726 principles · 723 sources · 40 templates — rows 12–18 below. The new rows have
not yet been through `quarantine_chunks.py`, `dedupe_principles.py` or `relabel_chunk_types.py`,
and the golden set / baseline predate them (TODO CORPUS-FREE).

**Dev copy after the full re-ingest, 2026-09-16:** 5,077 chunks · 2,135 principles · 612
sources · 49 templates. All seven PDF sources re-chunked on joined pages with
`--contextualize` (runbook §8b), Catalyst re-crawled (428 articles → 1,055 chunks, 2.5 per
article, was ≈ 1), Charniga ingested from the Wayback Machine (168 of 209 articles → 1,001
chunks; 41 snapshots are 51-char stubs and stay pending). Every chunk written that day carries
a `context_prefix`. The corpus DB machine still holds the 2026-03-18 state until the runbook is
applied there; reconcile `README.md` and `docs/SCHEMA.md` when it is.

---

## Ingested Sources

Listed in ingestion order. `source_id` is the row in `sources`; the profile is
the `SOURCE_PROFILE_MAP` entry in `processors/chunker.py` that controls chunk
size.

| # | Source | Format | `source_id` | Profile | Chunks | Principles |
|---|--------|--------|------------:|---------|-------:|-----------:|
| 1 | Everett — *Olympic Weightlifting* | EPUB | 507 | programming | 587 | 76 |
| 2 | Zatsiorsky — *Science and Practice of Strength Training* | PDF | 51 | theory_heavy | 333 (was 430 page-chunks; 22.6 paras/chunk) | 347 |
| 3 | Drechsler — *Weightlifting Encyclopedia* | PDF | 52 | theory_heavy | 790 (was 603 page-chunks; 14.6 paras/chunk) | 459 |
| 4 | Catalyst Athletics articles | Web | — (415 rows with chunks) | web (dynamic) | 1,055 (was 446) | 495 |
| 5 | Laputin — *Managing the Training of Weightlifters* | PDF (vision OCR) | 499 | soviet | 101 (was 110; 8.9 paras/chunk) | 125 |
| 6 | Takano — *Weightlifting Programming* | PDF | 2 | programming | 106 (was 218; 8.7 paras/chunk) | 141 |
| 7 | Medvedev — *A Program of Multi-Year Training in Weightlifting* (1986; the `sources` row was titled *A System of…* until 2026-09-20) | PDF (vision OCR) | 501 | soviet | 613 (was 617; still 2.3 paras / 324 chars — see note) | 89 |
| 8 | Everett — *Olympic Weightlifting for Sports* | PDF | 502 | programming | 25 (was 172; 10.5 paras/chunk) | 40 |
| 9 | Israetel — *Scientific Principles of Hypertrophy Training* | EPUB | 504 | programming | 206 | 21 |
| 10 | Starrett — *Becoming a Supple Leopard* | EPUB | 505 | theory_heavy | 137 | 16 |
| 11 | Dan John — *Intervention* | PDF | 506 | programming | 123 (was 266; 17.0 paras/chunk) | 66 |
| 12 | Charniga — sportivnypress.com (Wayback) | Web | — (208 rows) | web (dynamic) | 1,245 (was 1,001; 40 bot-check stubs recovered 2026-09-20) | 344 |
| 13 | Stronger by Science — 29 curated articles (`url_lists/sbs.json`) | Web | — (29 rows) | web (dynamic) | 285 | 140 |
| 14 | JTS / Max Aita — 24 articles of 98 listed (`url_lists/jts.json`) | Web | — (24 rows) | web (dynamic) | 82 | 20 |
| 15 | Pendlay — beginner program (Lift Vault mirror) | Web | — | web (dynamic) | 7 | 10 |
| 16 | Pritchard — taper thesis + 2 accepted manuscripts | PDF | 742–744 | research | 132 | 100 |
| 17 | Research sweep — 13 open-access papers (table below) | PDF / Europe PMC text | 745–752, 794–798 | research | 303 (+1 template) | 138 |
| 18 | Kono — *Championship Weightlifting* excerpt (Catalyst) | Web | — | web (dynamic) | 2 | 0 |

Takano (#6) produced 16 generic program templates in March and 18 chapter-titled ones on the
2026-09-16 re-ingest (both sets kept for now — several windows were truncated at 4,096 output
tokens; `create_message_growing` fixes that for the next run; review and dedupe per runbook §8b).
Everett *for Sports* (#8) produced 11 exercises in March; the 2026-09-16 run upserted 6 existing
names and created none. The principle counts jumped (161 → 748) because the joined-page
sections reach the extractor whole — Zatsiorsky alone yields 347; cross-source near-duplicates
are expected and worth a dedupe pass (see TODO §11).

Medvedev (#7) did not get bigger chunks from the page-joining fix: the book is a catalogue of
day-by-day sessions (`1. P. Cl.: 70 x 5, 80 x 2 x 2 …`), each session a paragraph group the
sectioner keeps separate, so 639 sections → 613 chunks of ~324 chars. That is program data,
not prose — 16 sections did route to the template parser (12 templates) — and the right fix is
to merge consecutive session blocks into week-sized chunks in the `soviet` profile, not to
raise the profile size (TODO §11).

### Files on disk (`oly-ingestion/sources/`, gitignored)

Renamed 2026-09-20 to `Author - Title (Year, Publisher).ext` so a title in this table maps
to one file; the OCR cache is keyed by file hash, so renames cost nothing. Keep the scheme
for new books, and make the `--title` you pass match the `SOURCE_PROFILE_MAP` key.

| # | File |
|---|---|
| 1 | `Greg Everett - Olympic Weightlifting_ A Complete Guide for Athletes & Coaches (2016, Catalyst Athletics).epub` |
| 2 | `Vladimir Zatsiorsky, William Kraemer, Andrew Fry - Science and Practice of Strength Training (2021, 3rd ed, Human Kinetics).pdf` |
| 3 | `Arthur Drechsler - The Weightlifting Encyclopedia (1998, A is A Communications).pdf` |
| 5 | `N.P. Laputin, V.G. Oleshko - Managing the Training of Weightlifters (1982, Sportivny Press).pdf` |
| 6 | `Bob Takano - Weightlifting Programming_ A Winning Coach's Guide (2012, Catalyst Athletics).pdf` |
| 7 | `A.S. Medvedev - A Program of Multi-Year Training in Weightlifting (1986, Sportivny Press 1995).pdf` — plus the `… (OCR text reconstructed 2026-09-20).txt` the 2026-09-20 re-chunk ran from |
| 8 | `Greg Everett - Olympic Weightlifting for Sports (2012, Catalyst Athletics).pdf` |
| 9 | `Mike Israetel - Scientific Principles of Hypertrophy Training (2021, Renaissance Periodization).epub` |
| 10 | `Kelly Starrett - Becoming a Supple Leopard (2015, 2nd ed, Victory Belt).epub` |
| 11 | `Dan John - Intervention (2013, On Target Publications).pdf` |
| 16–17 | `research/<Author Year - short title (journal)>.pdf|.txt` |

`sources` row 53 is an empty March 2026 Laputin attempt (one failed run, no chunks) kept for
the run history; row 499 is the live one.

### Notes

- **Everett (#1)** was re-ingested 2026-03-18 after the EPUB paragraph-extraction
  fix (was 198 chunks / 44 principles at `source_id=1`).
- **Catalyst (#4)** was ingested *before* the HTML paragraph fix, so it averages
  ≈1 chunk per article instead of several. A full re-ingest is pending on the
  corpus DB machine — see [DB-MACHINE-RUNBOOK.md](DB-MACHINE-RUNBOOK.md).
- **All seven PDF sources (#2, #3, #5, #6, #7, #8, #11) were ingested *before* the
  PDF page-join fix (RAG-H1, 2026-09-15)** and are chunked by page: 100% of the
  PyMuPDF chunks are single-paragraph, Medvedev is 269-char fragments. Re-ingest
  is pending on the corpus DB machine — [DB-MACHINE-RUNBOOK.md §8b](DB-MACHINE-RUNBOOK.md).
  Expect fewer, larger chunks (Takano 229 → ~160 at mean ~590 est. tokens;
  Zatsiorsky ~0.9 chunks/page at mean ~810) and chapter metadata on nearly every
  chunk instead of 3%. Evidence and measurements: [RAG_RESEARCH.md §5.1](RAG_RESEARCH.md).

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

- **The `research` profile (rows 3–4) exists since 2026-09-20** at `soviet`'s 700/150;
  the three Pritchard titles are in `SOURCE_PROFILE_MAP`.
- **Rows 2, 5 and 6 are curated URL lists**, not crawls: `sources/url_lists/{sbs,jts,pendlay}.json`
  (committed — the rest of `sources/` is ignored), ingested with
  `ingest_web.py --site urls --url-file …`. `fetch_generic_article` picks the tightest
  WordPress container holding the article, drops share/related/affiliate/TOC widgets and
  the trailing related-posts block, takes the author from `<meta name="author">` or the
  byline, and skips pages under `GENERIC_MIN_WORDS` (JTS video landing pages). To add
  articles, append URLs to the list and re-run; `sources/urls_progress.json` skips the rest.
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

### Status of rows 1–12 (2026-09-20)

Rows 1–6 are ingested on the dev copy (numbers in the Ingested Sources table). Rows
7–12 all need a purchase; nothing in them has been obtained. What the sweep found:

- **Rows 7, 8, 9 — and Verkhoshansky (row 10) too — are all in one place.** Sportivny
  Press moved the *Russian Weightlifting Library* to ebooks in 2019–2020 (Kobo, Google
  Play, Nook, Apple, Amazon; publisher Andrew Charniga): Roman *The Training of the
  Weightlifter* and *The Snatch, the Clean and Jerk*; Medvedyev *A System of…* and
  *A Program of…* (row 8: `source_id=501` is *A Program of…* — its title page says so; the DB title was wrong until 2026-09-20 — so buy *A System of…*,
  [B08HYBSGWS](https://www.amazon.com/System-Multi-Year-Training-Weightlifting-Russian-ebook/dp/B08HYBSGWS));
  Vorobyev *Weightlifting: Textbook for the Institutes of Sport of the USSR*;
  Verkhoshansky *Fundamentals of Special Strength Training in Sport* **and**
  *Programming and Organization of Training* (the latter is the periodization text —
  closer to this corpus's gap than *Special Strength Training: Manual for Coaches*);
  Laputin & Oleshko (a clean-text replacement for the OCR'd `source_id=499`); and the
  Zhekov/Lukashev compilation *Weightlifting Training and Technique* (biomechanics).
  Buy the EPUBs from Kobo (Canada storefront works) — EPUB extracts cleanly, no OCR.
- **Row 11 (Kono)** — the free Catalyst excerpt is ingested (`sources/url_lists/extras.json`);
  measure `fault_correction` retrieval before buying the print books.
- **Row 12 (Bompa)** — unchanged; Kindle only.

### Sweep 2026-09-20 — added beyond the plan

Open-access research that speaks directly to the taper / block-periodization gaps,
all under the `research` profile in `sources/research/` (gitignored):

| Source | How obtained | Why |
|---|---|---|
| Winwood, Travis, Barnes, Keogh & Pritchard 2026 — *Tapering and Peaking in the Weight Lifting Sports: a systematic review of athletes' self-reported strategies* (Sports Med) | Springer OA PDF | Subsumes the paywalled 2023 JSCR paper that was in "Chasing" — 146 weightlifters, taper length 8.0 ± 4.4 d, linear/step, last heavy session ~5.9 d out |
| Travis et al. 2021 — step vs exponential taper in strength athletes (Front Physiol) | Europe PMC JATS → text | Concrete 6-week peaking block with the week-by-week table |
| Hornsby et al. 2017 — strength/RFD/power in weightlifters across five months (Sports) | Europe PMC | Stone-group block periodization with the actual weightlifting program |
| Suarez et al. 2019 — phase-specific RFD changes through a block cycle in weightlifters (Sports) | Europe PMC | Same group, second block program with loading percentages |
| Huebner et al. 2022 — how master weightlifters train (IJERPH) | Europe PMC | Frequency / volume / concurrent-training practices by age band |
| Soriano et al. 2019 — weightlifting overhead pressing derivatives review (Sports Med) | Europe PMC | Exercise selection for jerk / overhead work |
| Stavropoulos et al. 2025 — light vs heavy priming the day before competition (JFMK) | Europe PMC | Competition-week prescription |
| Huebner et al. 2019 — performance development youth → senior, age of peak (Front Physiol) | Frontiers PDF | Long-term development context for the `novice` / masters bands |
| Suchomel, Comfort & Stone 2015 — *Weightlifting Pulling Derivatives: rationale for implementation and application* (Sports Med) | Salford repository AM, saved by hand | Pull-variation selection and loading |
| Suchomel et al. 2021 — *Training for Muscular Strength: methods for monitoring and adjusting training intensity* (Sports Med) | ECU repository AM, saved by hand | Autoregulation / RPE / velocity prescriptions |
| Stone et al. 2021 — *Periodization and Block Periodization in Sports: emphasis on strength-power training* (JSCR) | ECU repository AM, saved by hand | The block-periodization argument behind the Hornsby / Suarez programs |
| DeWeese, Hornsby, Stone & Stone 2015 — *The training process: planning for strength–power training in track and field*, Parts 1 + 2 (J Sport Health Sci, OA) | ScienceDirect, saved by hand | The practical Stone-model write-up (phases, emphasis, loading by block) |

Europe PMC full text is fetched with `extractors/jats_extractor.py` (`python -m
extractors.jats_extractor <PMCID> <dest.txt>`): JATS XML → `#` headings, `

`
paragraphs, tab-separated table rows, references dropped. Prefer it to a publisher PDF
whenever a paper has a PMCID — no columns, running heads or hyphenation to undo. The
repositories (ECU, Salford, ScienceDirect, MDPI, Springer) refuse scripted downloads
(403); a paper without a PMCID has to be saved from a browser into `sources/research/`
and ingested with `--type article`.

### Chasing — paywalled, author request the only route

- **Storey & Smith 2012** — Auckland ResearchSpace holds abstract only (Springer
  copyright). ResearchGate or a direct author request.
- ~~**Winwood, Keogh, Travis & Pritchard 2023**~~ — superseded by the 2026 open-access
  systematic review above, which reports the same survey.

### Considered and skipped

- **Chidlovski Lift Up** — history and meet results; almost no prescriptive content.
- **Mash Elite** free hybrid/super-total programs — noisy, weakly structured.
- **Saxon / Sandow** public-domain books — wrong era for this corpus.
- **Catalyst training programs** (`/olympic-weightlifting-training-program/`, 79) —
  now app-only ($39–59); the public page is a blurb. The free daily-workout archive
  is the remaining Catalyst program data if ever wanted, but it is uncurated.
- **Torokhtiy guides** (torokhtiy.com, ~100 weightlifting posts) — SEO copy; the
  "N-day / N-week program" pages describe phases without a program, and shop banners
  leak into the text. Not worth the noise next to Catalyst + JTS.
- **USA Weightlifting / IWF coaching manuals** — only pirated copies circulate.
- **JTS video posts** — 74 of the 98 listed URLs are video landing pages and fall under
  `GENERIC_MIN_WORDS`; 24 articles ingested.

---

## Chunk Sizing Profiles

| Profile | Used for | Chunk size | Overlap |
|---------|----------|-----------:|--------:|
| `theory_heavy` | Zatsiorsky, Drechsler, Starrett | 1100 tokens | 250 |
| `programming` | Everett, Takano, Israetel, Dan John | 900 tokens | 200 |
| `soviet` | Laputin, Medvedev (data-dense, OCR'd) | 700 tokens | 150 |
| `research` | Pritchard thesis + manuscripts (papers: short dense paragraphs, numbers throughout) | 700 tokens | 150 |
| web article | Catalyst, Charniga | 500–1100 (dynamic) | 100–250 |

Sizes are counted with tiktoken `cl100k_base` since 2026-09-15 (RAG-M2). Every
source above was ingested under the earlier words × 1.3 estimate, which
under-counted numeric notation ~5×, so re-ingested sources will report different
chunk counts even before the RAG-H1 page-join effect.

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
