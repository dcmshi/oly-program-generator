# Security Issues Tracker

Identified during pre-deployment audit (2026-03-16).

---

## Critical

| # | Issue | File | Status |
|---|-------|------|--------|
| C1 | `GET /program/{id}` — no ownership check, any authenticated user can view any program | `web/routers/program.py:30` | ✅ Fixed |
| C2 | `POST /program/{id}/abandon` — no ownership check | `web/routers/program.py:97` | ✅ Fixed |
| C3 | `GET /generate/status/{job_id}` — unauthenticated; job dict also leaks athlete_id + program_id | `web/routers/generate.py:45` | ✅ Fixed |
| C4 | Full Python traceback stored in job dict and rendered in browser via `<pre>{{ job.error }}</pre>` | `web/jobs.py:66`, `templates/partials/generate_result.html:28` | ✅ Fixed |

## Medium

| # | Issue | File | Status |
|---|-------|------|--------|
| M1 | `https_only=False` hardcoded in `SessionMiddleware` — session cookies lack `Secure` flag over HTTPS | `web/app.py:92` | ✅ Fixed |
| M2 | `SECRET_KEY` auto-generates randomly at startup if unset — sessions invalidate on restart, breaks multi-instance | `shared/config.py:102` | ✅ Fixed |
| M3 | `DATABASE_URL` silently falls back to `localhost` default if unset — masks misconfiguration | `shared/config.py:89` | ✅ Fixed |
| M4 | In-process `ThreadPoolExecutor` job queue — jobs lost on restart, incompatible with multi-worker/multi-instance deployments | `web/jobs.py:16` | ⚠️ Documented (architectural — needs Redis + worker for cloud scale) |

## Infrastructure

| # | Issue | File | Status |
|---|-------|------|--------|
| I1 | `ports: "5432:5432"` exposes Postgres on all interfaces (`0.0.0.0`) — direct DB access from internet if firewall misconfigured | `oly-ingestion/docker-compose.yml:14` | ✅ Fixed |
| I2 | Default Postgres credentials `oly:oly` — trivial password in dev compose | `oly-ingestion/docker-compose.yml:11` | ⚠️ Documented (override via `DATABASE_URL` in production) |

## Low

| # | Issue | File | Status |
|---|-------|------|--------|
| L1 | `back=` query param in `/history` is an unvalidated URL — open redirect risk | `web/routers/history.py:21` | ✅ Fixed |
| L2 | Failed login logs the attempted username — enumerates valid usernames if logs are exposed | `web/routers/auth.py` | ✅ Fixed |

---

## Known Limitations (not fixed — architectural)

**M4 — In-process job queue**: `_jobs` is a plain dict in process memory. In production with `gunicorn --workers N` or any auto-scaling setup, each worker has its own dict — a status poll routed to a different worker returns "not found". For a single-process deployment (`uvicorn --workers 1`) this is fine. For cloud scale, replace with Redis-backed queue (e.g. ARQ or Celery).

**I2 — Default DB password**: The dev compose uses `POSTGRES_PASSWORD=oly`. For production, set a strong password and override `DATABASE_URL` in the environment — do not commit credentials. The `config.py` startup check (M3 fix) will catch a missing `DATABASE_URL` at boot.
