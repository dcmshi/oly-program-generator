# TODO — 2026-06-12 Repo Audit Findings

Work order: top to bottom. Check items off as they land.

## 1. High — authorization gaps (web layer)

- [x] **Session logging ownership checks** (`oly-agent/web/routers/log_session.py`)
  - `GET /log/{session_id}` never verifies the session belongs to the logged-in athlete
  - `POST /log/{session_id}` same — accepts any session ID
  - `POST /log/{log_id}/exercise` and `POST /log/{log_id}/exercise/{tle_id}` never verify log ownership
  - `DELETE /log/{log_id}/exercise/{tle_id}` has no `athlete_id` dependency at all
  - Fix: add `get_current_athlete_id` where missing; compare `session["athlete_id"]` / `log["athlete_id"]` and 404 on mismatch
- [x] **Program activate missing ownership check** (`oly-agent/web/routers/program.py:52`, `queries/program.py:344`)
  - Router never fetches the program to check ownership; query's second UPDATE is `WHERE id = $1` with no athlete scoping
- [x] **Program complete missing ownership check** (`oly-agent/web/routers/program.py:69`)
  - Fetches program, checks existence, never compares `program["athlete_id"]` to session athlete
- [x] **Scope `abandon_program()` by athlete_id** (`queries/program.py:209`) — router checks ownership; add scoping to the query for defense-in-depth

## 2. Medium — portfolio/devex

- [x] **CI**: add `.github/workflows/ci.yml` running the no-key/no-DB test suites for both subsystems + ruff; add badge to README
- [x] **Linting**: add ruff config to both `pyproject.toml`s, fix any findings, add `make lint` target

## 3. Medium — robustness

- [ ] **Anthropic retry/backoff in ingestion** — `processors/principle_extractor.py`, `processors/classifier.py`, `pipeline.py:_llm_call` call `messages.create` bare; add shared retry helper (exponential backoff on rate-limit/overload/timeout)
- [ ] **`explain.py` retry + cost logging** — failures currently return a placeholder with no retry and no token logging
- [ ] **Consolidate phase-advancement thresholds** — adherence 70%, make-rate 75%, RPE-dev 1.5/1.0, excellent 90%/85% duplicated between `plan.py` and `feedback.py`; move to `shared/constants.py`
- [ ] **Orchestrator partial-failure handling** (`orchestrator.py`) — failed session generation stores an empty session and continues silently; mark program status accordingly and log clearly
- [ ] **Security headers middleware** (`web/app.py`) — X-Content-Type-Options, X-Frame-Options, Referrer-Policy (+ HSTS when HTTPS_ONLY)
- [ ] **Custom error pages** — 404/500 templates + exception handlers in `web/app.py`

## 4. Low priority / polish

- [ ] **Prilepin zone boundary overlap** (`shared/prilepin.py:10-16`) — 65 and 70 each match two zones; make boundaries exclusive
- [ ] **Floor-clamp `intensity_ceiling`** (`plan.py:267`) — clamps at 100 but not at 0
- [ ] **Fix dedup stats math** (`loaders/structured_loader.py:365`) — `chunks_skipped_dedup` derived from values that don't track dedup; have `vector_loader.load_chunks()` return the skipped count
- [ ] **Pin dependency major versions** — both `pyproject.toml`s use bare `>=`; add upper bounds for anthropic/openai/fastapi etc.
- [ ] **Pagination on unbounded lists** — program list + exercise history have no LIMIT

## Discarded during verification (false positives)

- `history.py` trend division-by-zero — guarded by `len(weights) >= 4`, slice always non-empty
- docker-compose missing `version:` key — obsolete in Compose v2, intentionally omitted
