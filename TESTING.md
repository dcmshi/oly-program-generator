# Test Coverage Tracker

Generated with `coverage.py 7.13.5` against no-API-key tests only.

Run commands:

```bash
# oly-ingestion
cd oly-ingestion
PYTHONUTF8=1 uv run coverage run -m pytest tests/test_chunker.py tests/test_classifier.py tests/test_pdf_extractor.py tests/test_epub_extractor.py tests/test_retag_chunks.py tests/test_html_extractor.py tests/test_pipeline_unit.py tests/test_structured_loader_unit.py -q
PYTHONUTF8=1 uv run coverage report

# oly-agent
cd oly-agent
PYTHONUTF8=1 uv run coverage run -m pytest tests/test_validate.py tests/test_phase_profiles.py tests/test_weight_resolver.py tests/test_generate_utils.py tests/test_assess.py tests/test_plan.py tests/test_retrieve.py tests/test_explain.py tests/test_orchestrator.py -q
PYTHONUTF8=1 uv run coverage report
```

> Modules excluded from coverage totals (require live DB / API keys): `pipeline.py`, `ingest_web.py`, `loaders/vector_loader.py`, `loaders/structured_loader.py` (main paths), `processors/principle_extractor.py`, `feedback.py`, `log.py`, `setup_auth.py`.

---

## Baseline — 2026-03-19

### oly-ingestion

| Module | Stmts | Miss | Cover |
|--------|------:|-----:|------:|
| `config.py` | 7 | 0 | 100% |
| `extractors/epub_extractor.py` | 28 | 0 | 100% |
| `retag_chunks.py` | 38 | 0 | 100% |
| `processors/classifier.py` | 92 | 7 | 92% |
| `processors/chunker.py` | 186 | 45 | 76% |
| `extractors/pdf_extractor.py` | 91 | 32 | 65% |
| `extractors/html_extractor.py` | 18 | 18 | 0% |
| `processors/ocr_corrections.py` | 7 | 7 | 0% |
| `loaders/vector_loader.py` | 122 | 122 | 0% *(needs DB + key)* |
| `loaders/structured_loader.py` | 162 | 162 | 0% *(needs DB)* |
| `pipeline.py` | 243 | 243 | 0% *(needs DB + key)* |
| `ingest_web.py` | 193 | 193 | 0% *(needs network)* |
| `processors/principle_extractor.py` | 39 | 39 | 0% *(needs key)* |
| **TOTAL** | **1226** | **868** | **29%** |

### oly-agent

| Module | Stmts | Miss | Cover |
|--------|------:|-----:|------:|
| `models.py` | 26 | 0 | 100% |
| `weight_resolver.py` | 61 | 0 | 100% |
| `explain.py` | 30 | 0 | 100% |
| `assess.py` | 41 | 1 | 98% |
| `plan.py` | 113 | 3 | 97% |
| `phase_profiles.py` | 32 | 1 | 97% |
| `validate.py` | 103 | 4 | 96% |
| `orchestrator.py` | 164 | 30 | 82% |
| `retrieve.py` | 78 | 9 | 88% |
| `generate.py` | 232 | 74 | 68% |
| `session_templates.py` | 6 | 2 | 67% |
| `feedback.py` | 98 | 98 | 0% *(needs DB)* |
| `log.py` | 281 | 281 | 0% *(CLI, no tests)* |
| `setup_auth.py` | 24 | 24 | 0% *(no tests)* |
| **TOTAL** | **1289** | **527** | **59%** |

---

## After T1–T9 — 2026-03-19

### oly-ingestion

| Module | Stmts | Miss | Cover | Δ |
|--------|------:|-----:|------:|---|
| `config.py` | 7 | 0 | 100% | — |
| `extractors/epub_extractor.py` | 28 | 0 | 100% | — |
| `retag_chunks.py` | 38 | 0 | 100% | — |
| `processors/classifier.py` | 92 | 7 | 92% | — |
| `processors/chunker.py` | 186 | 21 | 89% | +13% |
| `extractors/html_extractor.py` | 18 | 2 | 89% | +89% (T6) |
| `extractors/pdf_extractor.py` | 91 | 32 | 65% | — |
| `loaders/structured_loader.py` | 162 | 123 | 24% | +24% (T7) |
| `pipeline.py` | 243 | 168 | 31% | +31% (T2) |
| `processors/principle_extractor.py` | 39 | 28 | 28% | +28% |
| `loaders/vector_loader.py` | 122 | 107 | 12% | — |
| `ingest_web.py` | 193 | 193 | 0% | — |
| `processors/ocr_corrections.py` | 7 | 7 | 0% | — |
| **TOTAL** | **1226** | **688** | **44%** | **+15%** |

### oly-agent

| Module | Stmts | Miss | Cover | Δ |
|--------|------:|-----:|------:|---|
| `models.py` | 26 | 0 | 100% | — |
| `weight_resolver.py` | 61 | 0 | 100% | — |
| `explain.py` | 30 | 0 | 100% | — |
| `session_templates.py` | 6 | 0 | 100% | +33% (T9) |
| `assess.py` | 41 | 1 | 98% | — |
| `plan.py` | 113 | 3 | 97% | — |
| `phase_profiles.py` | 32 | 1 | 97% | — |
| `validate.py` | 103 | 1 | 99% | +3% (T3) |
| `retrieve.py` | 78 | 3 | 96% | +8% (T1) |
| `generate.py` | 232 | 28 | 88% | +20% (T4+T5) |
| `orchestrator.py` | 164 | 30 | 82% | — |
| `feedback.py` | 98 | 98 | 0% | — |
| `log.py` | 281 | 281 | 0% | — |
| `setup_auth.py` | 24 | 24 | 0% | — |
| **TOTAL** | **1289** | **470** | **64%** | **+5%** |

---

## Gap Tracker

Items ordered by priority. All T1–T9 completed 2026-03-19.

### P1 — HIGH (correctness risk, recently added code)

#### [x] T1 — `retrieve.py`: `min_similarity` passthrough not asserted
- **File**: `oly-agent/tests/test_retrieve.py`
- **Tests added**: 6 — min_similarity kwarg asserted for all 3 search paths; exception-caught tests for all 3 paths
- **Lines closed**: 133–134, 151–152, 168–172

#### [x] T2 — `pipeline.py`: continuation chunking not tested
- **File**: `oly-ingestion/tests/test_pipeline_unit.py` *(new)*
- **Tests added**: 6 — short section (single call), long section (continuation), seen_weeks dedup, duration_weeks inference, sessions_per_week inference, LLM failure safe return
- **Lines closed**: `_parse_program_template` fully exercised via mock self

---

### P2 — MEDIUM (logic gaps in well-exercised modules)

#### [x] T3 — `validate.py`: deload week bypasses Prilepin check
- **File**: `oly-agent/tests/test_validate.py`
- **Tests added**: 3 — sub-55% deload (zone=None), WeekTarget dataclass conversion, intensity_pct=None skip
- **Lines closed**: 80–81, 99, 109

#### [x] T4 — `generate.py`: `parse_llm_response` edge cases
- **File**: `oly-agent/tests/test_generate_utils.py`
- **Tests added**: 3 — bare object wraps in list, invalid array falls to object branch, both invalid raises ValueError
- **Lines closed**: 69–70, 75–80

#### [x] T5 — `generate.py`: `generate_session_with_retries` retry paths
- **File**: `oly-agent/tests/test_generate_utils.py`
- **Tests added**: 4 — success first attempt, parse error retries with JSON reminder, name error retries with error list, all retries exhausted → status=failed
- **Lines closed**: majority of lines 495–614

---

### P3 — LOW (0% modules with no external dependencies)

#### [x] T6 — `extractors/html_extractor.py`: zero coverage
- **File**: `oly-ingestion/tests/test_html_extractor.py` *(pre-existing — added to coverage run)*
- **Tests**: 10 pre-existing tests cover all main paths
- **Lines closed**: 89% coverage (2 missed lines in edge branch)

#### [x] T7 — `loaders/structured_loader.py`: boundary validation (no DB path)
- **File**: `oly-ingestion/tests/test_structured_loader_unit.py` *(new)*
- **Tests added**: 7 — duration_weeks=0/−1 → None, sessions_per_week=0/15 → None, sessions_per_week=1/14 → valid, valid program → calls execute
- **Lines closed**: validation guard fully covered

#### [x] T8 — `processors/chunker.py`: boundary and keep-together edge cases
- **File**: `oly-ingestion/tests/test_chunker.py`
- **Tests added**: 9 — validate_chunk edge cases (too long, many rep schemes, table-like, starts lowercase), _chunk_section empty input, _would_split_pattern true/false, _get_overlap suffix + empty
- **Lines closed**: 618, 620–622, 636–643, 669–679, 736–738 (chunker.py 76% → 89%)

#### [x] T9 — `session_templates.py`: fallback branch
- **File**: `oly-agent/tests/test_phase_profiles.py`
- **Tests added**: 6 — exact matches (3/4/5), fallback below min (1→3), fallback above max (6→5), required keys validation
- **Lines closed**: 142–143 (100% coverage)

---

## Notes

- `ocr_corrections.py` (0%) is a pure data dict — no logic to test; intentionally skipped.
- `feedback.py` (0%), `log.py` (0%), `setup_auth.py` (0%) all require live DB or are CLI entry points. Covered by manual testing / integration tests.
- `loaders/vector_loader.py` (12%), `pipeline.py` (31%) require DB and API keys for remaining lines. Covered by `test_vector_loader.py` and `test_pipeline.py` integration suites.
- `ingest_web.py` (0%) requires network access; tested manually.
- Re-run coverage after each test addition to confirm lines close.
