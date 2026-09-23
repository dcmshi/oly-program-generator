# Contributing Reference

Covers three areas relevant to anyone developing or deploying the project: security, production readiness, and test coverage.

---

## Security

Pre-deployment audit completed 2026-03-16. All issues resolved.

### Critical

| # | Issue | File | Status |
|---|-------|------|--------|
| C1 | `GET /program/{id}` — no ownership check, any authenticated user can view any program | `web/routers/program.py:30` | ✅ Fixed |
| C2 | `POST /program/{id}/abandon` — no ownership check | `web/routers/program.py:97` | ✅ Fixed |
| C3 | `GET /generate/status/{job_id}` — unauthenticated; job dict also leaks athlete_id + program_id | `web/routers/generate.py:45` | ✅ Fixed |
| C4 | Full Python traceback stored in job dict and rendered in browser via `<pre>{{ job.error }}</pre>` | `web/jobs.py:66`, `templates/partials/generate_result.html:28` | ✅ Fixed |

### Medium

| # | Issue | File | Status |
|---|-------|------|--------|
| M1 | `https_only=False` hardcoded in `SessionMiddleware` — session cookies lack `Secure` flag over HTTPS | `web/app.py:92` | ✅ Fixed |
| M2 | `SECRET_KEY` auto-generates randomly at startup if unset — sessions invalidate on restart, breaks multi-instance | `shared/config.py:102` | ✅ Fixed |
| M3 | `DATABASE_URL` silently falls back to `localhost` default if unset — masks misconfiguration | `shared/config.py:89` | ⚠ Mitigated — the fallback remains for local dev but logs a warning; production must set `DATABASE_URL` explicitly |
| M4 | In-process `ThreadPoolExecutor` job queue — jobs lost on restart, incompatible with multi-worker/multi-instance deployments | `web/jobs.py:16` | ✅ Fixed (ARQ + Redis — see `web/worker.py`) |

### Infrastructure

| # | Issue | File | Status |
|---|-------|------|--------|
| I1 | `ports: "5432:5432"` exposes Postgres on all interfaces (`0.0.0.0`) — direct DB access from internet if firewall misconfigured | `oly-ingestion/docker-compose.yml:14` | ✅ Fixed |
| I2 | Default Postgres credentials `oly:oly` — trivial password in dev compose | `oly-ingestion/docker-compose.yml:11` | ✅ Fixed (compose uses `${POSTGRES_PASSWORD:-oly}` — override via env or `.env`) |

### Low

| # | Issue | File | Status |
|---|-------|------|--------|
| L1 | `back=` query param in `/history` is an unvalidated URL — open redirect risk | `web/routers/history.py:21` | ✅ Fixed |
| L2 | Failed login logs the attempted username — enumerates valid usernames if logs are exposed | `web/routers/auth.py` | ✅ Fixed |

### Required production environment variables

```
SECRET_KEY          — session signing (generate: python -c "import secrets; print(secrets.token_hex(32))")
DATABASE_URL        — Postgres connection string with strong credentials
REDIS_URL           — Redis connection string
POSTGRES_PASSWORD   — overrides the oly dev default in docker-compose
HTTPS_ONLY=true     — enables Secure cookie flag behind a TLS-terminating proxy
```

---

## Production Readiness

Architecture review completed 2026-03-16. All 12 items resolved.

### Fix Before Going Live

| # | Issue | File | Status |
|---|-------|------|--------|
| S1 | Rate limiter uses in-memory storage — bypassed entirely with 2+ web instances | `oly-agent/web/deps.py` | ✅ Done (`_init_limiter()` wires `REDIS_URL` into slowapi; set `REDIS_URL` in env) |
| S2 | No health check endpoint — load balancers and container orchestrators can't probe readiness | `oly-agent/web/routers/health.py` | ✅ Done (`GET /health` — checks DB + Redis, returns 200/503) |

### Fix at Moderate Scale (10+ concurrent users / multi-instance)

| # | Issue | File | Status |
|---|-------|------|--------|
| S3 | Static files served by the app — ties up the app process; should be handled by the reverse proxy | `oly-agent/web/app.py` | ⬜ Open — and now larger: since the CDN assets were vendored (audit 7 / FE-L3) `/static` also serves htmx, Chart.js, `tailwind.css` and 8 woff2 files (~470 KB). All immutable and safe for the proxy to serve directly with a long `Cache-Control`; a public CDN is *not* wanted here, since removing that third-party dependency was the point |
| S4 | DB connection pool vs Postgres `max_connections=100` — 10 web instances exhausts the limit | `oly-agent/web/async_db.py` | ✅ Done (PgBouncer transaction pooling on :5432; Postgres direct on :5433; `statement_cache_size=0` in asyncpg) |
| S5 | pgvector full table scan on every retrieval without HNSW/IVFFlat index | `schema.sql` | ✅ Done (HNSW index `idx_chunks_embedding` — `m=16, ef_construction=64`, `vector_cosine_ops`). Note: at the current row count the planner still seq-scans (exact); once it switches to the index, filtered queries depend on `hnsw.iterative_scan` + `ef_search`, which `VectorLoader.similarity_search` sets per transaction (RAG-H5) |

### Operational Gaps

| # | Issue | File | Status |
|---|-------|------|--------|
| S6 | No database migration tooling — schema changes applied as raw SQL | `schema.sql` | ✅ Done (Alembic in `oly-agent/migrations/`; `ALEMBIC_DATABASE_URL` override for direct Postgres port) |
| S7 | Unstructured logging — plain text logs don't integrate with aggregation tools | `oly-agent/web/logging_config.py` | ✅ Done (`LOG_FORMAT=json` for prod; `text` default for dev) |
| S8 | No request ID / tracing — can't correlate requests across web server + ARQ worker | `oly-agent/web/app.py` | ✅ Done (`RequestIDMiddleware`; contextvar propagates to ARQ worker jobs) |
| S9 | No backup strategy — `pgdata` Docker volume has no backup config | `oly-ingestion/docker-compose.yml` | ✅ Done (local `pg_dump -Fc` procedure documented below) |

### Low Priority

| # | Issue | File | Status |
|---|-------|------|--------|
| S10 | ARQ `keep_result` TTL — job status returns "not found" after expiry | `oly-agent/web/worker.py` | ✅ Done (`keep_result=86400`; `job_owner` TTL matched) |
| S11 | No DB `command_timeout` — runaway query holds asyncpg connection indefinitely | `oly-agent/web/async_db.py` | ✅ Done (30 s default; override with `DB_COMMAND_TIMEOUT` env var) |
| S12 | `cost_limit_per_program` not per-user — global $1.00 cap | `shared/config.py` | ✅ Done (`cost_limit_usd` column on `athletes`; NULL falls back to global; migration `0002`) |

### S9 — Backup procedure

```bash
# Binary format — compact, faithful pgvector support, fastest restore
docker exec oly-postgres pg_dump -U oly -Fc oly_programming > oly_backup_$(date +%Y%m%d).dump

# Plain SQL — human-readable, portable
docker exec oly-postgres pg_dump -U oly oly_programming > oly_backup_$(date +%Y%m%d).sql

# Restore from binary dump (stdin — the file lives on the host, not in the container)
docker exec -i oly-postgres pg_restore -U oly -d oly_programming --clean --if-exists < oly_backup_YYYYMMDD.dump

# Full restore after volume wipe (docker compose down -v):
# 1. docker compose up -d
# 2. docker exec -i oly-postgres pg_restore -U oly -d oly_programming --clean --if-exists < oly_backup_YYYYMMDD.dump
```

What's included: schema, indexes, knowledge_chunks (with pgvector embeddings), athlete data, Prilepin seed data.
Not included: `.env`, Redis state (ephemeral).

On Windows, `$(date +%Y%m%d)` works in Git Bash. Use a literal date in CMD/PowerShell.

---

## Test Coverage

Coverage is measured over the no-key, no-DB suites, the same ones `make test` runs. Generate a fresh report instead of trusting a number written down here, because per-module figures change with every commit:

```bash
make coverage             # both subsystems
make coverage-agent       # oly-agent only
make coverage-ingestion   # oly-ingestion only
```

Each target runs `coverage run -m pytest` over that subsystem's list in the root `Makefile` (`AGENT_TESTS` / `INGESTION_TESTS`, which is the canonical list of suites that need no DB or keys) and then prints `coverage report` with the missing lines. Scope is set by each subsystem's `.coveragerc`: the source is the subsystem directory, and `tests/` and the venv are omitted. `oly-agent` also omits `web/` (the router tests exercise it, but it is not counted). `shared/` is outside both sources.

Low coverage is expected for code that only runs against a live DB, a paid API or the network: the main paths of `pipeline.py` and `ingest_web.py`, `loaders/vector_loader.py`, `processors/principle_extractor.py`, and the CLI entry points `feedback.py`, `log.py` and `setup_auth.py`. The DB- and key-backed suites that cover them are listed in [SETUP.md → Running Tests](SETUP.md#running-tests).

Integration-gated tests inside otherwise-mocked files, such as the live vision-OCR test in `test_pdf_extractor.py` and the live-DB test in `test_retag_chunks.py`, call `pytest.skip` through `_integration_only()`. They are reported as **SKIPPED** unless `INTEGRATION_TESTS=1` is set.
