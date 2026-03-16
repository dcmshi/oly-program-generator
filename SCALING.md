# Scaling & Production Readiness Tracker

Identified during pre-deployment architecture review (2026-03-16).

---

## Fix Before Going Live

| # | Issue | File | Status |
|---|-------|------|--------|
| S1 | Rate limiter uses in-memory storage — bypassed entirely with 2+ web instances | `oly-agent/web/deps.py` | ✅ Done (`_init_limiter()` already wires `REDIS_URL` into slowapi; set `REDIS_URL` in env) |
| S2 | No health check endpoint — load balancers and container orchestrators can't probe readiness | `oly-agent/web/routers/health.py` | ✅ Done (`GET /health` — checks DB + Redis, returns 200/503) |

## Fix at Moderate Scale (10+ concurrent users / multi-instance)

| # | Issue | File | Status |
|---|-------|------|--------|
| S3 | Static files served by the app — ties up the app process; should be handled by reverse proxy or CDN | `oly-agent/web/app.py` | ⬜ Open |
| S4 | DB connection pool vs Postgres `max_connections=100` — 10 web instances exhausts the limit; add PgBouncer | `oly-agent/web/async_db.py` | ✅ Done (PgBouncer transaction pooling on :5432; Postgres direct on :5433; `statement_cache_size=0` in asyncpg) |
| S5 | pgvector index — full table scan on every retrieval without HNSW/IVFFlat index; fine at 2,576 chunks, degrades at corpus scale | `schema.sql` | ✅ Done (HNSW index `idx_chunks_embedding` already in schema.sql — `m=16, ef_construction=64`, `vector_cosine_ops`) |

## Operational Gaps (before team / production SLA)

| # | Issue | File | Status |
|---|-------|------|--------|
| S6 | No database migration tooling — schema changes applied as raw SQL with no history or rollback; add Alembic | `schema.sql` / `athlete_schema.sql` | ⬜ Open |
| S7 | Unstructured logging — plain text logs don't integrate with aggregation tools (CloudWatch, Datadog, Loki); one `logging.config` change adds JSON output | `oly-agent/web/logging_config.py` | ✅ Done (`LOG_FORMAT=json` for prod; `text` default for dev; JSON formatter in `logging_config.py`) |
| S8 | No request ID / tracing — can't correlate a user's request across web server + ARQ worker logs; add `X-Request-ID` middleware | `oly-agent/web/app.py` | ✅ Done (`RequestIDMiddleware` stamps every request; contextvar propagates to all logs + ARQ worker jobs) |
| S9 | No backup strategy — `pgdata` Docker volume has no backup config; use managed Postgres (RDS, Cloud SQL, Supabase) with automated backups in production | `oly-ingestion/docker-compose.yml` | ⬜ Open |

## Low Priority / When Needed

| # | Issue | File | Status |
|---|-------|------|--------|
| S10 | ARQ `keep_result=3600` — job status returns "not found" after 1 hour; program is safely in Postgres but UI flow is slightly confusing | `oly-agent/web/worker.py` | ⬜ Open |
| S11 | No DB `command_timeout` — runaway query holds asyncpg connection indefinitely | `oly-agent/web/async_db.py` | ⬜ Open |
| S12 | `cost_limit_per_program` not per-user — global $1.00 cap in Settings; fine for small user base | `shared/config.py` | ⬜ Open |

---

## Notes

**S1 fix sketch** — wire `REDIS_URL` into slowapi in `deps.py`:
```python
limiter = Limiter(key_func=get_remote_address, storage_uri=get_settings().redis_url)
```

**S4 context** — each web instance gets up to `db_pool_max=10` connections. Postgres default `max_connections=100` → ceiling of ~10 instances before exhaustion. PgBouncer sits between the app and Postgres and multiplexes connections, raising the practical ceiling significantly.

**S5 context** — check index exists:
```sql
SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_chunks';
```
If missing, add: `CREATE INDEX ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);`

**S9 context** — in production, replace docker-compose Postgres with a managed service. All connection details flow through `DATABASE_URL` so no app code changes needed.
